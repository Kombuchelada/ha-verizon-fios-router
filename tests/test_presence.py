"""Device trackers and the new-device event."""

from __future__ import annotations

import copy

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import tick
from .fake_router import FakeRouter

ONLINE_MAC = "02:00:00:00:00:06"  # device-1, wired, 192.168.1.101 in the fixture
NEW_MAC = "02:00:00:00:AA:01"


def _tracker_id(hass: HomeAssistant, mac: str) -> str:
    return er.async_get(hass).async_get_entity_id("device_tracker", "fios_router", mac)


def _join(router: FakeRouter, mac: str, name: str) -> None:
    devices = router.data["/CRSP/getDeviceDetails"]["deviceList"]
    new = copy.deepcopy(devices[0])
    new.update(mac=mac, hostName=name, deviceName=name, status=1, ipv4Address="192.168.1.99", connectionType=8)
    devices.append(new)


class _Announcements:
    """New-device event state changes from the moment this is created."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._events = async_capture_events(hass, "state_changed")

    def macs(self) -> list[str]:
        return [
            e.data["new_state"].attributes["mac"]
            for e in self._events
            if e.data["entity_id"] == "event.fios_router_new_device" and "mac" in e.data["new_state"].attributes
        ]


async def test_a_tracker_per_client_disabled_by_default(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    entity_id = _tracker_id(hass, ONLINE_MAC)
    assert entity_id is not None
    assert er.async_get(hass).async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_tracker_follows_router_status(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    registry = er.async_get(hass)
    entity_id = _tracker_id(hass, ONLINE_MAC)
    registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_HOME
    assert state.attributes["ip"] == "192.168.1.101"
    assert state.attributes["connection"] == "ethernet"

    for d in router.data["/CRSP/getDeviceDetails"]["deviceList"]:
        if d["mac"] == ONLINE_MAC:
            d["status"] = 0
    await tick(hass, seconds=61)
    assert hass.states.get(entity_id).state == STATE_NOT_HOME


async def test_first_setup_announces_nothing(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    state = hass.states.get("event.fios_router_new_device")
    assert state is not None
    assert "mac" not in state.attributes


async def test_new_mac_fires_once_and_gets_a_tracker(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    announced = _Announcements(hass)
    _join(router, NEW_MAC, "stranger-phone")
    await tick(hass, seconds=61)
    await tick(hass, seconds=61)

    assert announced.macs() == [NEW_MAC]
    attrs = hass.states.get("event.fios_router_new_device").attributes
    assert attrs["event_type"] == "new_device"
    assert attrs["mac"] == NEW_MAC
    assert attrs["hostname"] == "stranger-phone"
    assert attrs["ip"] == "192.168.1.99"
    assert attrs["connection"] == "6 GHz"
    assert _tracker_id(hass, NEW_MAC) is not None


async def test_two_new_macs_fire_two_events(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    announced = _Announcements(hass)
    _join(router, NEW_MAC, "a")
    _join(router, "02:00:00:00:AA:02", "b")
    await tick(hass, seconds=61)
    assert announced.macs() == [NEW_MAC, "02:00:00:00:AA:02"]


async def test_restart_does_not_reannounce(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    _join(router, NEW_MAC, "stranger-phone")
    await tick(hass, seconds=61)
    await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()
    announced = _Announcements(hass)
    await tick(hass, seconds=61)
    assert announced.macs() == []


async def test_device_that_joined_while_down_is_announced_on_start(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    await hass.config_entries.async_unload(loaded.entry_id)
    _join(router, NEW_MAC, "arrived-overnight")
    await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("event.fios_router_new_device").attributes.get("mac") == NEW_MAC
