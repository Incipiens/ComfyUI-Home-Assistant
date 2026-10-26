from __future__ import annotations

import asyncio
import logging
from types import MappingProxyType

import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BASE_URL,
    CONF_TIMEOUT,
    CONF_WORKFLOW_TITLE,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SUBENTRY_AI_TASK,
    SUBENTRY_KEYS,
)

_LOGGER = logging.getLogger(__name__)
# https://github.com/loryanstrant/HA-Azure-AI-tasks/blob/main/custom_components/azure_ai_tasks/__init__.py
PLATFORMS: list[Platform] = [Platform.AI_TASK]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CONNECT_TIMEOUT = 10


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await _async_migrate_to_subentries(hass)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url = entry.data[CONF_BASE_URL]
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            async with async_get_clientsession(hass).get(f"{base_url}/system_stats") as resp:
                resp.raise_for_status()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"base_url": base_url, "error": str(err)},
        ) from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True

# Also fires when a workflow subentry is added or reconfigured
async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

async def _async_migrate_to_subentries(hass: HomeAssistant) -> None:
    """Turn v1 entries (one per workflow) into one entry per server with a subentry per workflow"""
    # Enabled entries first so they become the parent
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda e: e.disabled_by is not None,
    )
    if not any(entry.version == 1 for entry in entries):
        return

    parents: dict[str, ConfigEntry] = {
        entry.data[CONF_BASE_URL]: entry for entry in entries if entry.version > 1
    }

    for entry in entries:
        if entry.version != 1:
            continue

        old = {**entry.data, **entry.options}
        base_url = old[CONF_BASE_URL].rstrip("/")
        subentry = ConfigSubentry(
            data=MappingProxyType({key: old.get(key) for key in SUBENTRY_KEYS}),
            subentry_type=SUBENTRY_AI_TASK,
            title=old.get(CONF_WORKFLOW_TITLE) or entry.title or DEFAULT_AI_TASK_NAME,
            unique_id=None,
        )

        # no registry entries to move
        if (parent := parents.get(base_url)) is not None:
            hass.config_entries.async_add_subentry(parent, subentry)
            await hass.config_entries.async_remove(entry.entry_id)
            continue

        parents[base_url] = entry
        hass.config_entries.async_update_entry(
            entry,
            title=base_url,
            data={CONF_BASE_URL: base_url},
            options={CONF_TIMEOUT: old.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)},
            version=2,
            minor_version=1,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        _LOGGER.info("Migrated ComfyUI entry %s to a server entry with workflow subentries", base_url)
