"""UPnP, guest Wi-Fi, per-device internet access, reboot — against the fake only."""

from __future__ import annotations

import pytest
from aiohttp import web
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import tick
from .fake_router import FakeRouter

READ_ONLY = ("/CRSP/get", "/CRSP/tr181/getParameterValues", "/CRSP/open/", "/CRSP/logout", "/CreateXSRFToken")
MAC = "02:00:00:00:00:06"


async def _call(hass: HomeAssistant, domain: str, service: str, entity_id: str) -> None:
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


def _writes(router: FakeRouter) -> list[tuple[str, object]]:
    return [(p, b) for _, p, b in router.calls if not p.startswith(READ_ONLY)]


async def test_polling_never_writes(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    for _ in range(3):
        await tick(hass, seconds=61)
    assert _writes(router) == []


async def test_upnp_switch_round_trip(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    entity_id = "switch.fios_router_upnp"
    assert hass.states.get(entity_id).state == STATE_OFF
    await _call(hass, "switch", "turn_on", entity_id)
    assert hass.states.get(entity_id).state == STATE_ON
    await _call(hass, "switch", "turn_off", entity_id)
    assert hass.states.get(entity_id).state == STATE_OFF
    assert _writes(router) == [
        ("/CRSP/updateUpnpConfig", {"enabled": True, "autoCleanEnabled": True}),
        ("/CRSP/updateUpnpConfig", {"enabled": False, "autoCleanEnabled": True}),
    ]


async def test_guest_wifi_with_son_writes_only_the_son_band(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    entity_id = "switch.fios_router_guest_wi_fi"
    assert hass.states.get(entity_id).state == STATE_OFF
    await _call(hass, "switch", "turn_on", entity_id)
    # Held at the requested state while the radios settle...
    assert hass.states.get(entity_id).state == STATE_ON
    assert _writes(router) == [
        (
            "/CRSP/updateWifiDetailsByNetworkType",
            {"networkType": 1, "wifiInfoList": [{"radioAlias": "WLAN_24G", "enabled": True}]},
        )
    ]
    # ...then read back from the router.
    await tick(hass, seconds=16)
    assert hass.states.get(entity_id).state == STATE_ON
    assert all(w["enabled"] for w in router.data["/CRSP/getWifiDetailsByNetworkType"]["wifiInfoList"])


async def test_guest_wifi_without_son_writes_every_band(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.data["/CRSP/getWifiDetailsByNetworkType"]["sonEnabled"] = False
    await tick(hass, seconds=61)
    await _call(hass, "switch", "turn_on", "switch.fios_router_guest_wi_fi")
    (path, body), = _writes(router)
    assert sorted(b["radioAlias"] for b in body["wifiInfoList"]) == ["WLAN_24G", "WLAN_5G", "WLAN_6G"]
    assert all(b == {"radioAlias": b["radioAlias"], "enabled": True} for b in body["wifiInfoList"])


async def test_internet_access_switch_blocks_one_device(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("switch", "fios_router", f"TEST0000SERIAL_internet_{MAC.replace(':', '').lower()}")
    assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_ON
    await _call(hass, "switch", "turn_off", entity_id)
    assert hass.states.get(entity_id).state == STATE_OFF
    await _call(hass, "switch", "turn_on", entity_id)
    assert hass.states.get(entity_id).state == STATE_ON
    assert _writes(router) == [
        (f"/CRSP/updateDeviceDetails/{MAC}", {"blocked": True}),
        (f"/CRSP/updateDeviceDetails/{MAC}", {"blocked": False}),
    ]


async def test_reboot_sends_only_a_reboot(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    await _call(hass, "button", "press", "button.fios_router_reboot")
    assert router.reboots == 1
    # action 1/2 on this endpoint are factory resets; the body must be exactly this.
    assert _writes(router) == [("/CRSP/updateRouterDetails", {"action": 0})]


async def test_refused_write_surfaces_as_an_error(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    def refuse(_body, _path):
        raise web.HTTPInternalServerError

    router.writes["/CRSP/updateUpnpConfig"] = refuse
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: "switch.fios_router_upnp"}, blocking=True
        )


async def test_client_refuses_any_other_router_details_body(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    with pytest.raises(ValueError):
        await loaded.runtime_data.client.call("/CRSP/updateRouterDetails", {"action": 1})
    assert _writes(router) == []


async def test_guest_wifi_second_press_restarts_the_wait(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    entity_id = "switch.fios_router_guest_wi_fi"
    await _call(hass, "switch", "turn_on", entity_id)
    await tick(hass, seconds=10)
    await _call(hass, "switch", "turn_off", entity_id)
    await tick(hass, seconds=6)  # the first press's timer would have fired here
    assert hass.states.get(entity_id).state == STATE_OFF
    await tick(hass, seconds=10)
    assert hass.states.get(entity_id).state == STATE_OFF
