"""WAN state, port links, firmware upgrade in progress."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FiosConfigEntry, FiosCoordinator
from .entity import FiosEntity
from .const import PORT_NAMES
from .models import Snapshot

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FiosBinarySensorDescription(BinarySensorEntityDescription):
    is_on: Callable[[Snapshot], bool | None]


def _port_link(port: str) -> FiosBinarySensorDescription:
    return FiosBinarySensorDescription(
        key=f"{port}_link",
        name=f"{PORT_NAMES[port]} link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on=lambda s, p=port: s.ports[p].connected if p in s.ports else None,
    )


DESCRIPTIONS = (
    FiosBinarySensorDescription(
        key="wan",
        name="WAN",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on=lambda s: s.wan_up,
    ),
    *(_port_link(p) for p in PORT_NAMES),
    FiosBinarySensorDescription(
        key="firmware_upgrading",
        name="Firmware upgrading",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:package-down",
        is_on=lambda s: s.firmware_upgrading,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FiosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(FiosBinarySensor(entry.runtime_data, d) for d in DESCRIPTIONS)


class FiosBinarySensor(FiosEntity, BinarySensorEntity):
    entity_description: FiosBinarySensorDescription

    def __init__(self, coordinator: FiosCoordinator, description: FiosBinarySensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on(self.coordinator.data.snapshot)
