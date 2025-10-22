from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform, __version__ as ha_version
from .const import DOMAIN, PLATFORMS

from .ai_task import ComfyUITaskEntity

# https://github.com/loryanstrant/HA-Azure-AI-tasks/blob/main/custom_components/azure_ai_tasks/__init__.py
PLATFORMS: list[Platform] = [Platform.AI_TASK]
MIN_HA_VERSION = "2025.10.0"

def _check_ha_version() -> None:
    """Check if Home Assistant version meets minimum requirements."""
    from packaging import version
    
    try:
        current_version = version.parse(ha_version.split(".dev")[0])  # Remove .dev suffix if present
        min_version = version.parse(MIN_HA_VERSION)
        
        if current_version < min_version:
            raise ConfigEntryNotReady(
                f"Home Assistant {MIN_HA_VERSION} or newer is required. "
                f"Current version: {ha_version}"
            )
    except Exception as err:
        _LOGGER.warning(
            "Unable to verify Home Assistant version compatibility: %s. "
            "Integration may not work correctly if running on older versions.",
            err
        )

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    
    _check_ha_version()

    task = ComfyUITaskEntity(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = task

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, Platform.AI_TASK)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok