"""A tracker per client the router knows, keyed by MAC.

Presence is the router's own online flag. Sleeping phones drop off Wi-Fi, so
these are better at "what is on the network" than at "who is home".
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FiosConfigEntry, FiosCoordinator
from .entity import async_add_per_client, client_name
from .models import Client

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FiosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_per_client(entry, async_add_entities, FiosTracker)


class FiosTracker(CoordinatorEntity[FiosCoordinator], ScannerEntity):
    def __init__(self, coordinator: FiosCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_mac_address = mac
        self._attr_name = self._attr_hostname = client_name(coordinator, mac)

    @property
    def _client(self) -> Client | None:
        return self.coordinator.data.snapshot.clients.get(self._mac)

    @property
    def is_connected(self) -> bool:
        client = self._client
        return bool(client and client.connected)

    @property
    def ip_address(self) -> str | None:
        client = self._client
        return client.ip if client else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self._client
        return {"connection": client.connection if client else None}
