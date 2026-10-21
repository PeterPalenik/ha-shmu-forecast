"""SHMU Forecast custom integration."""
from homeassistant.const import Platform

from .coordinator import ShmuCoordinator

PLATFORMS = [Platform.WEATHER]


async def async_setup_entry(hass, entry):
    """Set up a new snapshot, then forward the native weather platform."""
    coordinator = ShmuCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    """Fail closed even if HA cannot unload the old platform."""
    entry.runtime_data.last_update_success = False
    entry.runtime_data.async_update_listeners()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
