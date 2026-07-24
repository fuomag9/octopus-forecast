"""The Octopus Forecast integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import OctopusForecastCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type OctopusForecastConfigEntry = ConfigEntry[OctopusForecastCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: OctopusForecastConfigEntry
) -> bool:
    """Set up Octopus Forecast from a config entry."""
    coordinator = OctopusForecastCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OctopusForecastConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant, entry: OctopusForecastConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
