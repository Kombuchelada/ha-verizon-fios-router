"""Base entity: everything hangs off one device, the router."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FiosConfigEntry, FiosCoordinator


@callback
def async_add_per_client(
    entry: FiosConfigEntry,
    add: Callable[[Iterable[Entity]], None],
    factory: Callable[[FiosCoordinator, str], Entity],
) -> None:
    """One entity per client MAC, now and for every MAC that appears later."""
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [m for m in coordinator.data.snapshot.clients if m not in added]
        added.update(new)
        add(factory(coordinator, mac) for mac in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def client_name(coordinator: FiosCoordinator, mac: str) -> str:
    client = coordinator.data.snapshot.clients.get(mac)
    return client.name if client else mac


class FiosEntity(CoordinatorEntity[FiosCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FiosCoordinator, key: str) -> None:
        super().__init__(coordinator)
        snap = coordinator.data.snapshot
        self._attr_unique_id = f"{snap.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, snap.serial)},
            manufacturer="Verizon",
            model=snap.model,
            name="Fios router",
            serial_number=snap.serial,
            sw_version=snap.firmware,
            hw_version=snap.hardware,
            configuration_url=coordinator.client.base_url,
        )
