"""Public addresses, port speeds and counters, WAN throughput and usage, clients, boot, firmware."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfDataRate, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FiosConfigEntry, FiosCoordinator, RouterState
from .entity import FiosEntity
from .const import PORT_NAMES

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FiosSensorDescription(SensorEntityDescription):
    value: Callable[[RouterState], Any]
    attrs: Callable[[RouterState], dict[str, Any]] | None = None


def _port(state: RouterState, port: str):
    return state.snapshot.ports.get(port)


def _port_sensors(port: str) -> tuple[FiosSensorDescription, ...]:
    name = PORT_NAMES[port]
    return (
        FiosSensorDescription(
            key=f"{port}_speed",
            name=f"{name} speed",
            device_class=SensorDeviceClass.DATA_RATE,
            native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
            entity_category=EntityCategory.DIAGNOSTIC,
            # Unplugged reads 0, not unknown, so a link that drops shows in history.
            value=lambda s, p=port: (_port(s, p).speed_mbps or 0) if _port(s, p) else None,
        ),
        FiosSensorDescription(
            key=f"{port}_errors",
            name=f"{name} errors",
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:alert-circle-outline",
            value=lambda s, p=port: _port(s, p).errors if _port(s, p) else None,
        ),
        FiosSensorDescription(
            key=f"{port}_discards",
            name=f"{name} discards",
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            icon="mdi:delete-alert-outline",
            value=lambda s, p=port: _port(s, p).discards if _port(s, p) else None,
        ),
    )


def _wan_bytes(key: str, name: str) -> FiosSensorDescription:
    return FiosSensorDescription(
        key=f"wan_{key}",
        name=name,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        value=lambda s, k=key: s.snapshot.wan_bytes.get(k),
    )


DESCRIPTIONS = (
    FiosSensorDescription(
        key="public_ipv4",
        name="Public IPv4",
        icon="mdi:ip-network",
        value=lambda s: s.snapshot.public_ipv4,
    ),
    FiosSensorDescription(
        key="public_ipv6",
        name="Public IPv6",
        icon="mdi:ip-network",
        value=lambda s: s.snapshot.public_ipv6,
        attrs=lambda s: {"delegated_prefix": s.snapshot.delegated_prefix},
    ),
    FiosSensorDescription(
        key="wan_download_rate",
        name="WAN download",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.download_mbps,
    ),
    FiosSensorDescription(
        key="wan_upload_rate",
        name="WAN upload",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.upload_mbps,
    ),
    _wan_bytes("downloaded_hour", "WAN downloaded last hour"),
    _wan_bytes("uploaded_hour", "WAN uploaded last hour"),
    _wan_bytes("downloaded_day", "WAN downloaded last day"),
    _wan_bytes("uploaded_day", "WAN uploaded last day"),
    FiosSensorDescription(
        key="active_devices",
        name="Active devices",
        icon="mdi:devices",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.snapshot.connected_clients,
    ),
    FiosSensorDescription(
        key="last_boot",
        name="Last boot",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.last_boot,
    ),
    FiosSensorDescription(
        key="firmware",
        name="Firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.snapshot.firmware,
    ),
    *(d for p in PORT_NAMES for d in _port_sensors(p)),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FiosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(FiosSensor(entry.runtime_data, d) for d in DESCRIPTIONS)


class FiosSensor(FiosEntity, SensorEntity):
    entity_description: FiosSensorDescription

    def __init__(self, coordinator: FiosCoordinator, description: FiosSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | float | datetime | None:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs is None:
            return None
        return self.entity_description.attrs(self.coordinator.data)
