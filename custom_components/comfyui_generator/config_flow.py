from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

_LOGGER = logging.getLogger(__name__)

from .const import (
    CONF_BASE_URL,
    CONF_IMAGE_H,
    CONF_IMAGE_W,
    CONF_SEED_NODE_ID,
    CONF_TIMEOUT,
    CONF_WORKFLOW_PATH,
    CONF_WORKFLOW_PROMPT_NODE_ID,
    CONF_WORKFLOW_RESOLUTION_NODE_ID,
    CONF_WORKFLOW_TITLE,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SUBENTRY_AI_TASK,
)


def _parse_workflow_nodes(workflow_text: str) -> dict[str, Any]:
    """Parse API-format workflow JSON, return node_id: node_dict pair.

    Raises ValueError if the workflow is in UI/graph format and surfaces to user
    """
    wf = json.loads(workflow_text)
    if "nodes" in wf and isinstance(wf.get("nodes"), list):
        raise ValueError("wrong_workflow_format")
    nodes = wf.get("prompt", wf)
    return {k: v for k, v in nodes.items() if isinstance(v, dict) and "class_type" in v}


def _read_workflow_nodes(path: str) -> dict[str, Any]:
    """Runs in the executor, raises ValueError with a translation key"""
    if not os.path.exists(path):
        raise ValueError("file_not_found")
    with open(path, "r", encoding="utf-8") as f:
        workflow_text = f.read()
    return _parse_workflow_nodes(workflow_text)


def _is_prompt_node(inputs: dict[str, Any], _node: dict[str, Any]) -> bool:
    return "text" in inputs


def _is_resolution_node(inputs: dict[str, Any], _node: dict[str, Any]) -> bool:
    return "width" in inputs and "height" in inputs


def _is_seed_node(inputs: dict[str, Any], _node: dict[str, Any]) -> bool:
    # RandomNoise and KSamplerAdvanced use noise_seed
    return "seed" in inputs or "noise_seed" in inputs


def _build_node_options(
    nodes: dict[str, Any], filter_fn: callable
) -> list[SelectOptionDict]:
    options = []
    for node_id, node in nodes.items():
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and filter_fn(inputs, node):
            class_type = node.get("class_type", "Unknown")
            options.append(
                SelectOptionDict(value=node_id, label=f"Node {node_id}: {class_type}")
            )
    return options


# Check system_stats endpoint
async def _test_comfyui_connection(hass: HomeAssistant, base_url: str) -> bool:
    try:
        async with asyncio.timeout(10):
            async with async_get_clientsession(hass).get(f"{base_url}/system_stats") as resp:
                resp.raise_for_status()
                return True
    except (aiohttp.ClientError, TimeoutError):
        return False


async def _async_validate_workflow(
    hass: HomeAssistant, path: str
) -> tuple[dict[str, str], dict[str, Any]]:
    """Shared by the config and subentry flows, returns errors and parsed nodes"""
    if not path or not path.startswith("/config/"):
        return {"base": "invalid_file_path"}, {}

    try:
        nodes = await hass.async_add_executor_job(_read_workflow_nodes, path)
    except ValueError as exc:
        # JSONDecodeError is a ValueError but not a translation key
        if isinstance(exc, json.JSONDecodeError):
            _LOGGER.error("Failed to parse workflow file: %s", exc)
            return {"base": "invalid_workflow"}, {}
        return {"base": str(exc)}, {}
    except Exception as exc:
        _LOGGER.error("Failed to parse workflow file: %s", exc)
        return {"base": "invalid_workflow"}, {}

    if not _build_node_options(nodes, _is_prompt_node):
        return {"base": "no_prompt_node"}, {}

    return {}, nodes


def _workflow_fields(d: dict[str, Any]) -> dict[vol.Marker, Any]:
    return {
        vol.Required(
            CONF_WORKFLOW_TITLE,
            default=d.get(CONF_WORKFLOW_TITLE, DEFAULT_AI_TASK_NAME),
        ): str,
        vol.Required(
            CONF_WORKFLOW_PATH, default=d.get(CONF_WORKFLOW_PATH, "")
        ): str,
    }


# Server + first workflow, shown on initial setup
def _schema_user(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_URL, default=d.get(CONF_BASE_URL, "")
            ): str,
            vol.Optional(
                CONF_TIMEOUT, default=d.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            ): int,
            **_workflow_fields(d),
        }
    )


def _schema_nodes(
    nodes: dict[str, Any], defaults: dict[str, Any] | None = None
) -> vol.Schema:
    """Node dropdown + image res"""
    d = defaults or {}

    prompt_options = _build_node_options(nodes, _is_prompt_node)
    resolution_options = _build_node_options(nodes, _is_resolution_node)
    seed_options = _build_node_options(nodes, _is_seed_node)

    def _preselect(options: list[SelectOptionDict], key: str) -> str | None:
        """Return saved node if still valid, or the only option"""
        saved = d.get(key)
        if saved and any(o["value"] == saved for o in options):
            return saved
        if len(options) == 1:
            return options[0]["value"]
        return None

    def _dropdown(options: list[SelectOptionDict]) -> SelectSelector:
        return SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
        )

    prompt_default = _preselect(prompt_options, CONF_WORKFLOW_PROMPT_NODE_ID)
    schema_dict: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_WORKFLOW_PROMPT_NODE_ID,
            **({"default": prompt_default} if prompt_default else {}),
        ): _dropdown(prompt_options),
    }

    # suggested_value instead of default so the user can clear them
    if resolution_options:
        schema_dict[vol.Optional(
            CONF_WORKFLOW_RESOLUTION_NODE_ID,
            description={"suggested_value": _preselect(resolution_options, CONF_WORKFLOW_RESOLUTION_NODE_ID)},
        )] = _dropdown(resolution_options)
        schema_dict[vol.Required(CONF_IMAGE_W, default=d.get(CONF_IMAGE_W, 800))] = int
        schema_dict[vol.Required(CONF_IMAGE_H, default=d.get(CONF_IMAGE_H, 480))] = int

    if seed_options:
        schema_dict[vol.Optional(
            CONF_SEED_NODE_ID,
            description={"suggested_value": _preselect(seed_options, CONF_SEED_NODE_ID)},
        )] = _dropdown(seed_options)

    return vol.Schema(schema_dict)


class ComfyUIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow"""

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._base_url = ""
        self._timeout = DEFAULT_TIMEOUT
        self._task_title = ""
        self._workflow_path = ""
        self._workflow_nodes: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ComfyUIOptionsFlowHandler:
        return ComfyUIOptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_AI_TASK: ComfyUIWorkflowSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema_user(),
            )

        base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
        self._async_abort_entries_match({CONF_BASE_URL: base_url})

        errors: dict[str, str] = {}
        if not await _test_comfyui_connection(self.hass, base_url):
            errors["base"] = "connection_failed"
        else:
            path = user_input[CONF_WORKFLOW_PATH].strip()
            errors, self._workflow_nodes = await _async_validate_workflow(self.hass, path)

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema_user(user_input),
                errors=errors,
            )

        self._base_url = base_url
        self._timeout = user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        self._task_title = user_input[CONF_WORKFLOW_TITLE].strip()
        self._workflow_path = path
        return await self.async_step_nodes()

    async def async_step_nodes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="nodes",
                data_schema=_schema_nodes(self._workflow_nodes),
            )

        return self.async_create_entry(
            title=self._base_url,
            data={CONF_BASE_URL: self._base_url},
            options={CONF_TIMEOUT: self._timeout},
            subentries=[
                {
                    "subentry_type": SUBENTRY_AI_TASK,
                    "title": self._task_title,
                    "data": {CONF_WORKFLOW_PATH: self._workflow_path, **user_input},
                    "unique_id": None,
                }
            ],
        )

    # Change the server URL, workflows are reconfigured on their subentries
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            if base_url != entry.data[CONF_BASE_URL]:
                self._async_abort_entries_match({CONF_BASE_URL: base_url})

            if not await _test_comfyui_connection(self.hass, base_url):
                errors["base"] = "connection_failed"
            else:
                # Keep a custom title, but follow the URL if it was never renamed
                title = base_url if entry.title == entry.data[CONF_BASE_URL] else entry.title
                return self.async_update_and_abort(
                    entry, title=title, data_updates={CONF_BASE_URL: base_url}
                )

        current = user_input or entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_BASE_URL, default=current.get(CONF_BASE_URL, "")): str}
            ),
            errors=errors,
        )


class ComfyUIOptionsFlowHandler(OptionsFlow):

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        timeout = self.config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_TIMEOUT, default=timeout): int}),
        )


class ComfyUIWorkflowSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a workflow on an existing server"""

    def __init__(self) -> None:
        super().__init__()
        self._title = ""
        self._workflow_path = ""
        self._workflow_nodes: dict[str, Any] = {}

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    def _current(self) -> dict[str, Any]:
        if self._is_new:
            return {}
        subentry = self._get_reconfigure_subentry()
        return {CONF_WORKFLOW_TITLE: subentry.title, **subentry.data}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            path = user_input[CONF_WORKFLOW_PATH].strip()
            errors, self._workflow_nodes = await _async_validate_workflow(self.hass, path)
            if not errors:
                self._title = user_input[CONF_WORKFLOW_TITLE].strip()
                self._workflow_path = path
                return await self.async_step_nodes()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(_workflow_fields(user_input or self._current())),
            errors=errors,
        )

    async_step_reconfigure = async_step_user

    async def async_step_nodes(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="nodes",
                data_schema=_schema_nodes(self._workflow_nodes, self._current()),
            )

        data = {CONF_WORKFLOW_PATH: self._workflow_path, **user_input}
        if self._is_new:
            return self.async_create_entry(title=self._title, data=data)

        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            title=self._title,
            data=data,
        )
