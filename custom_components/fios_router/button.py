"""Reboot the router."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import FiosConnectionError, FiosError
from .coordinator import FiosConfigEntry, FiosCoordinator
from .entity import FiosEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: FiosConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RebootButton(entry.runtime_data)])


class RebootButton(FiosEntity, ButtonEntity):
    _attr_name = "Reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FiosCoordinator) -> None:
        super().__init__(coordinator, "reboot")

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.reboot()
        except FiosConnectionError:
            pass  # the router may drop the connection as it goes down
        except FiosError as err:
            raise HomeAssistantError(f"Router refused to reboot: {err}") from err
