from __future__ import annotations

import json
import logging
import os
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
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


def _parse_workflow_nodes(workflow_text: str) -> dict[str, Any]:
    """Parse API-format workflow JSON, return node_id: node_dict pair.

    Raises ValueError if the workflow is in UI/graph format and surfaces to user
    """
    wf = json.loads(workflow_text)
    if "nodes" in wf and isinstance(wf.get("nodes"), list):
        raise ValueError("wrong_workflow_format")
    nodes = wf.get("prompt", wf)
    return {k: v for k, v in nodes.items() if isinstance(v, dict) and "class_type" in v}


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

    prompt_options = _build_node_options(
        nodes, lambda inputs, _: "text" in inputs
    )

    resolution_options = _build_node_options(
        nodes, lambda inputs, _: "width" in inputs and "height" in inputs
    )

    seed_options = _build_node_options(
        nodes, lambda inputs, _: "seed" in inputs
    )

    def _default_for(options: list[SelectOptionDict], key: str) -> dict:
        """Return default kwarg if one option"""
        saved = d.get(key)
        if saved and any(o["value"] == saved for o in options):
            return {"default": saved}
        if len(options) == 1:
            return {"default": options[0]["value"]}
        return {}

    schema_dict: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_WORKFLOW_PROMPT_NODE_ID,
            **_default_for(prompt_options, CONF_WORKFLOW_PROMPT_NODE_ID),
        ): SelectSelector(
            SelectSelectorConfig(
                options=prompt_options, mode=SelectSelectorMode.DROPDOWN
            )
        ),
        vol.Required(
            CONF_WORKFLOW_RESOLUTION_NODE_ID,
            **_default_for(resolution_options, CONF_WORKFLOW_RESOLUTION_NODE_ID),
        ): SelectSelector(
            SelectSelectorConfig(
                options=resolution_options, mode=SelectSelectorMode.DROPDOWN
            )
        ),
        vol.Required(
            CONF_SEED_NODE_ID,
            **_default_for(seed_options, CONF_SEED_NODE_ID),
        ): SelectSelector(
            SelectSelectorConfig(
                options=seed_options, mode=SelectSelectorMode.DROPDOWN
            )
        ),
        vol.Required(CONF_IMAGE_W, default=d.get(CONF_IMAGE_W, 800)): int,
        vol.Required(CONF_IMAGE_H, default=d.get(CONF_IMAGE_H, 480)): int,
    }

    return vol.Schema(schema_dict)


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

    # Check system_stats endpoint
    async def _test_comfyui_connection(self, base_url: str) -> bool:
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema_connection(),
            )

        errors: dict[str, str] = {}

        p = user_input.get(CONF_WORKFLOW_PATH, "").strip()
        if not p or not p.startswith("/config/"):
            errors["base"] = "invalid_file_path"
        elif not os.path.exists(p):
            errors["base"] = "file_not_found"

        if not errors:
            base_url = user_input.get(CONF_BASE_URL, "").rstrip("/")
            if not await self._test_comfyui_connection(base_url):
                errors["base"] = "connection_failed"

        if not errors:
            # Parse workflow and validate
            try:
                with open(p, "r", encoding="utf-8") as f:
                    workflow_text = f.read()
                self._workflow_nodes = _parse_workflow_nodes(workflow_text)
            except ValueError as exc:
                errors["base"] = str(exc)
            except Exception as exc:
                _LOGGER.error("Failed to parse workflow file: %s", exc)
                errors["base"] = "invalid_workflow"

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

        # Merge both sets
        merged = {**self._step1_data, **user_input}
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

    async def _test_comfyui_connection(self, base_url: str) -> bool:
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        current = {**self.config_entry.data, **(self.config_entry.options or {})}

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=_schema_connection(current),
            )

        errors: dict[str, str] = {}

        p = user_input.get(CONF_WORKFLOW_PATH, "").strip()
        if not p or not p.startswith("/config/"):
            errors["base"] = "invalid_file_path"
        elif not os.path.exists(p):
            errors["base"] = "file_not_found"

        if not errors:
            base_url = user_input.get(CONF_BASE_URL, "").rstrip("/")
            if not await self._test_comfyui_connection(base_url):
                errors["base"] = "connection_failed"

        if not errors:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    workflow_text = f.read()
                self._workflow_nodes = _parse_workflow_nodes(workflow_text)
            except ValueError as exc:
                errors["base"] = str(exc)
            except Exception as exc:
                _LOGGER.error("Failed to parse workflow file: %s", exc)
                errors["base"] = "invalid_workflow"

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

        merged = {**self._step1_data, **user_input}
        return self.async_create_entry(data=merged)
