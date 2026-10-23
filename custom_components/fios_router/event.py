"""Fires when a MAC the integration has never seen appears on the router."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FiosConfigEntry, FiosCoordinator, RouterState
from .entity import FiosEntity

NEW_DEVICE = "new_device"

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FiosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NewDeviceEvent(entry.runtime_data)])


class NewDeviceEvent(FiosEntity, EventEntity):
    _attr_name = "New device"
    _attr_icon = "mdi:devices"
    _attr_event_types = [NEW_DEVICE]

    def __init__(self, coordinator: FiosCoordinator) -> None:
        super().__init__(coordinator, NEW_DEVICE)
        self._seen: RouterState | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Devices that joined while HA was down surface in the first poll.
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        state = self.coordinator.data
        if state is not self._seen:  # a failed poll keeps the old data; don't refire it
            self._seen = state
            for client in state.new_clients:
                self._trigger_event(
                    NEW_DEVICE,
                    {"mac": client.mac, "hostname": client.name, "ip": client.ip, "connection": client.connection},
                )
                self.async_write_ha_state()  # one state change per device
            if state.new_clients:
                return
        self.async_write_ha_state()
