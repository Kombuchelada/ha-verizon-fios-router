"""UPnP, guest Wi-Fi, and per-device internet access."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import FiosError
from .const import WIFI_SETTLE_SECONDS
from .coordinator import FiosConfigEntry, FiosCoordinator
from .entity import FiosEntity, async_add_per_client, client_name

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: FiosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([UpnpSwitch(coordinator), GuestWifiSwitch(coordinator)])
    async_add_per_client(entry, async_add_entities, InternetAccessSwitch)


async def _write(coro) -> None:
    try:
        await coro
    except FiosError as err:
        raise HomeAssistantError(f"Router refused the change: {err}") from err


class UpnpSwitch(FiosEntity, SwitchEntity):
    _attr_name = "UPnP"
    _attr_icon = "mdi:router-network"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FiosCoordinator) -> None:
        super().__init__(coordinator, "upnp")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.snapshot.upnp_enabled

    async def _set(self, on: bool) -> None:
        snap = self.coordinator.data.snapshot
        await _write(self.coordinator.client.set_upnp(on, snap.upnp_auto_clean))
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class GuestWifiSwitch(FiosEntity, SwitchEntity):
    """All guest SSIDs together. The radios take a while to settle, so the switch
    holds the requested state until a poll after they have."""

    _attr_name = "Guest Wi-Fi"
    _attr_icon = "mdi:wifi-star"

    def __init__(self, coordinator: FiosCoordinator) -> None:
        super().__init__(coordinator, "guest_wifi")
        self._pending: bool | None = None
        self._settle_unsub: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        if self._pending is not None:
            return self._pending
        return self.coordinator.data.snapshot.guest_wifi.enabled

    async def _set(self, on: bool) -> None:
        guest = self.coordinator.data.snapshot.guest_wifi
        await _write(self.coordinator.client.set_guest_wifi(on, list(guest.radio_aliases)))
        self._pending = on
        self.async_write_ha_state()

        async def _refresh(_now) -> None:
            self._settle_unsub = None
            self._pending = None
            await self.coordinator.async_refresh()

        # A second press restarts the wait rather than racing the first one.
        self._cancel_settle()
        self._settle_unsub = async_call_later(self.hass, WIFI_SETTLE_SECONDS, _refresh)

    def _cancel_settle(self) -> None:
        if self._settle_unsub:
            self._settle_unsub()
            self._settle_unsub = None

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_settle()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class InternetAccessSwitch(FiosEntity, SwitchEntity):
    """On = allowed. Off = the router's per-device internet block."""

    _attr_icon = "mdi:web"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: FiosCoordinator, mac: str) -> None:
        super().__init__(coordinator, f"internet_{mac.replace(':', '').lower()}")
        self._mac = mac
        self._attr_name = f"{client_name(coordinator, mac)} internet access"

    @property
    def available(self) -> bool:
        return super().available and self._mac in self.coordinator.data.snapshot.clients

    @property
    def is_on(self) -> bool:
        client = self.coordinator.data.snapshot.clients.get(self._mac)
        return not (client and client.blocked)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"mac": self._mac}

    async def _set(self, allowed: bool) -> None:
        await _write(self.coordinator.client.set_blocked(self._mac, not allowed))
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
