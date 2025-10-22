from __future__ import annotations
import os
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType
)

from .const import (
    DOMAIN,
    CONF_BASE_URL, CONF_TIMEOUT,
    CONF_PROMPT_PLACEHOLDER, DEFAULT_PLACEHOLDER,
    DEFAULT_TIMEOUT, DEFAULT_AI_TASK_NAME,
    CONF_WORKFLOW_MODE, CONF_WORKFLOW_PATH, CONF_WORKFLOW_PROMPT_NODE_ID, 
    CONF_WORKFLOW_RESOLUTION_NODE_ID, CONF_SEED_NODE_ID, CONF_IMAGE_W, CONF_IMAGE_H,
    CONF_WORKFLOW_TITLE
)

# support for additional workflow modes
WORKFLOW_MODES = ["file"]

# -------- User schema
def _schema_user(defaults: dict | None = None):
    defaults = defaults or {}
    return vol.Schema({
        vol.Optional(CONF_WORKFLOW_TITLE, default=defaults.get(CONF_WORKFLOW_TITLE, DEFAULT_AI_TASK_NAME)): str,
        vol.Required(CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, "")): str,
        vol.Optional(CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): int,
        vol.Required(CONF_WORKFLOW_PROMPT_NODE_ID, default=defaults.get(CONF_WORKFLOW_PROMPT_NODE_ID, "")): str,
        vol.Required(CONF_WORKFLOW_RESOLUTION_NODE_ID, default=defaults.get(CONF_WORKFLOW_RESOLUTION_NODE_ID, "")): str,
        vol.Required(CONF_IMAGE_W, default=defaults.get(CONF_IMAGE_W, 800)): int,
        vol.Required(CONF_IMAGE_H, default=defaults.get(CONF_IMAGE_H, 480)): int,
        vol.Required(CONF_SEED_NODE_ID, default=defaults.get(CONF_SEED_NODE_ID, "")): str,
        vol.Required(CONF_WORKFLOW_PATH, default=defaults.get(CONF_WORKFLOW_PATH, "")): str,
    })

# -------- Default entry for config
class ComfyUIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    MINOR_VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_schema_user(),
            )
        
        if user_input is not None:
            p = user_input.get(CONF_WORKFLOW_PATH, "").strip()
            if not p or not p.startswith("/config/"):
                return self.async_show_form(
                    step_id="user",
                    data_schema=_schema_user(user_input),
                    errors={"base": "invalid_file_path"},
                    description_placeholders={"msg": "Provide an absolute path under /config"}
                )
            if not os.path.exists(p):
                return self.async_show_form(
                    step_id="user",
                    data_schema=_schema_user(user_input),
                    errors={"base": "file_not_found"},
                )
            return self.async_create_entry(title=user_input.get(CONF_WORKFLOW_TITLE, "".strip()), data=user_input)
        

        return self.async_show_form(step_id="user", data_schema=_schema_user())

    async def async_step_import(self, user_input=None) -> FlowResult:  # optional
        return await self.async_step_user(user_input)


# -------- Options Flow so you can edit later in UI, currently unused
class ComfyUIOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        data = {**self.config_entry.data, **(self.config_entry.options or {})}
        return self.async_show_form(step_id="init", data_schema=_schema_user(data))


async def async_get_options_flow(config_entry):
    return ComfyUIOptionsFlowHandler(config_entry)
