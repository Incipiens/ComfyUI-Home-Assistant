from __future__ import annotations

import json
import logging
import os
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
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
)

# Cleared explicitly so options can override entry.data
OPTIONAL_NODE_KEYS = (CONF_WORKFLOW_RESOLUTION_NODE_ID, CONF_SEED_NODE_ID)


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
async def _test_comfyui_connection(base_url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url.rstrip('/')}/system_stats",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                return True
    except Exception:
        return False


async def _async_validate_connection(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Shared by config and options flows, returns errors and parsed nodes"""
    p = user_input.get(CONF_WORKFLOW_PATH, "").strip()
    if not p or not p.startswith("/config/"):
        return {"base": "invalid_file_path"}, {}

    base_url = user_input.get(CONF_BASE_URL, "").rstrip("/")
    if not await _test_comfyui_connection(base_url):
        return {"base": "connection_failed"}, {}

    try:
        nodes = await hass.async_add_executor_job(_read_workflow_nodes, p)
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


# Connection + workflow
def _schema_connection(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_WORKFLOW_TITLE,
                default=d.get(CONF_WORKFLOW_TITLE, DEFAULT_AI_TASK_NAME),
            ): str,
            vol.Required(
                CONF_BASE_URL, default=d.get(CONF_BASE_URL, "")
            ): str,
            vol.Optional(
                CONF_TIMEOUT, default=d.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            ): int,
            vol.Required(
                CONF_WORKFLOW_PATH, default=d.get(CONF_WORKFLOW_PATH, "")
            ): str,
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


def _merge_node_input(step1_data: dict[str, Any], user_input: dict[str, Any]) -> dict[str, Any]:
    """Store cleared optional nodes as None"""
    return {
        **step1_data,
        **{key: None for key in OPTIONAL_NODE_KEYS},
        **user_input,
    }


class ComfyUIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow"""

    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._step1_data: dict[str, Any] = {}
        self._workflow_nodes: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ComfyUIOptionsFlowHandler:
        return ComfyUIOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema_connection(),
            )

        errors, self._workflow_nodes = await _async_validate_connection(self.hass, user_input)

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema_connection(user_input),
                errors=errors,
            )

        self._step1_data = user_input
        return await self.async_step_nodes()

    async def async_step_nodes(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="nodes",
                data_schema=_schema_nodes(self._workflow_nodes),
            )

        merged = _merge_node_input(self._step1_data, user_input)
        return self.async_create_entry(
            title=merged.get(CONF_WORKFLOW_TITLE, DEFAULT_AI_TASK_NAME).strip(),
            data=merged,
        )

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_user(user_input)


class ComfyUIOptionsFlowHandler(config_entries.OptionsFlow):

    _step1_data: dict[str, Any]
    _workflow_nodes: dict[str, Any]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        current = {**self.config_entry.data, **(self.config_entry.options or {})}

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=_schema_connection(current),
            )

        errors, self._workflow_nodes = await _async_validate_connection(self.hass, user_input)

        if errors:
            return self.async_show_form(
                step_id="init",
                data_schema=_schema_connection(user_input),
                errors=errors,
            )

        self._step1_data = user_input
        return await self.async_step_nodes()

    # Dropdown node selector
    async def async_step_nodes(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        current = {**self.config_entry.data, **(self.config_entry.options or {})}

        if user_input is None:
            return self.async_show_form(
                step_id="nodes",
                data_schema=_schema_nodes(self._workflow_nodes, current),
            )

        merged = _merge_node_input(self._step1_data, user_input)
        return self.async_create_entry(data=merged)
