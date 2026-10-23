"""The Fios router (Verizon CHR30A) integration."""

from __future__ import annotations

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .coordinator import FiosConfigEntry, FiosCoordinator, create_client

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: FiosConfigEntry) -> bool:
    coordinator = FiosCoordinator(hass, entry, create_client(hass, dict(entry.data)))
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Setup retries build a new client each time; don't leave this one's
        # session open on the router, which allows only ten.
        await coordinator.client.async_logout()
        raise
    entry.runtime_data = coordinator

    async def _logout(_: Event) -> None:
        await coordinator.client.async_logout()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _logout))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FiosConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_logout()
    return unloaded
