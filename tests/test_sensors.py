"""Network health: ports, throughput, usage, clients, boot, firmware, diagnostics."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fios_router.diagnostics import async_get_config_entry_diagnostics
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import tick
from .fake_router import FakeRouter


def _broadband(router: FakeRouter) -> dict:
    conns = router.data["/CRSP/getNetworkConnections"]
    return next(c for c in conns["networkConnectionsList"] if c["alias"] == "broadband")


def _advance_counters(router: FakeRouter, seconds: int, rx: int, tx: int) -> None:
    router.data["/CRSP/getNetworkConnections"]["timestamp"] += seconds * 1000
    _broadband(router)["rxBytes"] += rx
    _broadband(router)["txBytes"] += tx


async def test_port_links_and_speeds(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert hass.states.get("binary_sensor.fios_router_wan_link").state == STATE_ON
    assert hass.states.get("binary_sensor.fios_router_10g_lan_link").state == STATE_OFF
    assert hass.states.get("binary_sensor.fios_router_2_5g_lan1_link").state == STATE_ON
    assert hass.states.get("sensor.fios_router_wan_speed").state == "1000"
    assert hass.states.get("sensor.fios_router_10g_lan_speed").state == "0"
    assert hass.states.get("sensor.fios_router_2_5g_lan1_speed").state == "1000"
    assert hass.states.get("sensor.fios_router_2_5g_lan2_speed").state == "100"


async def test_port_dropping_to_100_shows(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    lan1 = next(p for p in router.data["/CRSP/getPortConfig"]["portConfigList"] if p["portType"] == 2)
    lan1["activeService"]["speedMbps"] = 100
    await tick(hass, seconds=61)
    assert hass.states.get("sensor.fios_router_2_5g_lan1_speed").state == "100"


async def test_port_error_counters_from_tr181(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    assert hass.states.get("sensor.fios_router_10g_lan_errors").state == "0"
    # Interface 1 is the 10G LAN port; 2 is the port getPortConfig calls portType 2.
    router.tr181["Device.Ethernet.Interface.1.Stats.ErrorsReceived"] = 5
    router.tr181["Device.Ethernet.Interface.1.Stats.ErrorsSent"] = 2
    router.tr181["Device.Ethernet.Interface.2.Stats.DiscardPacketsReceived"] = 9
    await tick(hass, seconds=61)
    assert hass.states.get("sensor.fios_router_10g_lan_errors").state == "7"
    assert hass.states.get("sensor.fios_router_2_5g_lan1_discards").state == "9"


async def test_wan_throughput_from_counter_deltas(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    assert hass.states.get("sensor.fios_router_wan_download").state == STATE_UNKNOWN
    _advance_counters(router, 60, rx=75_000_000, tx=7_500_000)
    await tick(hass, seconds=61)
    assert hass.states.get("sensor.fios_router_wan_download").state == "10.0"
    assert hass.states.get("sensor.fios_router_wan_upload").state == "1.0"


async def test_counter_reset_gives_no_rate(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    _advance_counters(router, 60, rx=75_000_000, tx=0)
    await tick(hass, seconds=61)
    router.data["/CRSP/getNetworkConnections"]["timestamp"] += 60_000
    _broadband(router)["rxBytes"] = 1000
    await tick(hass, seconds=61)
    assert hass.states.get("sensor.fios_router_wan_download").state == STATE_UNKNOWN


async def test_wan_usage_windows(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    # 45,152,498,654 bytes uploaded in the hour, shown in GB.
    assert hass.states.get("sensor.fios_router_wan_uploaded_last_hour").state == "45.152498654"


async def test_active_devices(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    assert hass.states.get("sensor.fios_router_active_devices").state == "7"
    router.data["/CRSP/getDeviceDetails"]["deviceList"][0]["status"] = 1
    await tick(hass, seconds=61)
    assert hass.states.get("sensor.fios_router_active_devices").state == "8"


async def test_last_boot_holds_steady_then_moves_on_reboot(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    first = dt_util.parse_datetime(hass.states.get("sensor.fios_router_last_boot").state)
    uptime = router.data["/CRSP/getRouterDetails"]["upTime"]
    assert abs((dt_util.utcnow() - first).total_seconds() - uptime) < 5
    router.data["/CRSP/getRouterDetails"]["upTime"] = uptime + 20  # jitter, not a reboot
    await tick(hass, seconds=61)
    assert hass.states.get("sensor.fios_router_last_boot").state == first.isoformat()
    router.data["/CRSP/getRouterDetails"]["upTime"] = 120
    await tick(hass, seconds=61)
    rebooted = dt_util.parse_datetime(hass.states.get("sensor.fios_router_last_boot").state)
    assert abs((dt_util.utcnow() - rebooted).total_seconds() - 120) < 5


async def test_firmware(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    assert hass.states.get("sensor.fios_router_firmware").state == "4.1.7.8822"
    assert hass.states.get("binary_sensor.fios_router_firmware_upgrading").state == STATE_OFF
    router.data["/CRSP/getFirmwareUpgradeStatus"] = {"status": 1}
    router.data["/CRSP/getRouterDetails"]["softwareVersion"] = "4.2.0.1"
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_firmware_upgrading").state == STATE_ON
    assert hass.states.get("sensor.fios_router_firmware").state == "4.2.0.1"


async def test_wan_down_blanks_public_address(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.data["/CRSP/getRouterDetails"]["broadbandIpv4Status"] = "Down"
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_OFF
    assert hass.states.get("sensor.fios_router_public_ipv4").state == STATE_UNKNOWN


async def test_diagnostics_carry_no_secrets(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    for band in router.data["/CRSP/getWifiDetailsByNetworkType"]["wifiInfoList"]:
        band["key"] = "guest-passphrase"
    await tick(hass, seconds=61)
    dump = json.dumps(await async_get_config_entry_diagnostics(hass, loaded), default=str)
    assert router.password not in dump
    assert "guest-passphrase" not in dump
    assert router.data["/CRSP/getRouterDetails"]["serialNumber"] not in dump
    assert "203.0.113" not in dump
    assert "02:00:00" not in dump
