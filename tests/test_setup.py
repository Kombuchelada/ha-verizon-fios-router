"""Log in once, report the WAN, and never hammer the login."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.fios_router.const import DOMAIN
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import tick
from .fake_router import USER, FakeRouter

LOGIN = "/CRSP/open/login"


async def test_config_flow_creates_entry(hass: HomeAssistant, router: FakeRouter) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: router.base_url, CONF_USERNAME: USER, CONF_PASSWORD: router.password},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == router.base_url
    # The flow's validation session was logged out; the one left is the entry's.
    assert router.count("/CRSP/logout") == 1
    assert len(router.sessions) == 1


async def test_config_flow_wrong_password_tries_once(hass: HomeAssistant, router: FakeRouter) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: router.base_url, CONF_USERNAME: USER, CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert router.count(LOGIN) == 1


async def test_config_flow_unreachable(hass: HomeAssistant, router: FakeRouter) -> None:
    router.offline = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: router.base_url, CONF_USERNAME: USER, CONF_PASSWORD: "x"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_wan_and_public_addresses(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_ON
    assert hass.states.get("sensor.fios_router_public_ipv4").state == "203.0.113.1"
    assert hass.states.get("sensor.fios_router_public_ipv6").state.startswith("2001:db8:")


async def test_polls_reuse_one_session(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    for _ in range(3):
        await tick(hass, seconds=61)
    assert router.count("/CRSP/getRouterDetails") == 4
    assert router.count(LOGIN) == 1


async def test_expired_session_logs_in_again_once(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.expire_sessions()
    await tick(hass, seconds=61)
    assert router.count(LOGIN) == 2
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_ON


async def test_password_changed_starts_reauth_and_stops(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.password = "changed"
    router.expire_sessions()
    for _ in range(3):
        await tick(hass, seconds=61)
    # One original login, one rejected re-login, then nothing.
    assert router.count(LOGIN) == 2
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == config_entries.SOURCE_REAUTH for f in flows)


async def test_reauth_flow_updates_password(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.password = "changed"
    result = await loaded.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "changed"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert loaded.data[CONF_PASSWORD] == "changed"


async def test_wrong_password_at_startup_never_retries(
    hass: HomeAssistant, entry: MockConfigEntry, router: FakeRouter
) -> None:
    router.password = "changed"
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    for _ in range(3):
        await tick(hass, minutes=5)
    assert router.count(LOGIN) == 1


async def test_unreachable_router_goes_unavailable(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.offline = True
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_UNAVAILABLE
    router.offline = False
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_ON


async def test_unload_logs_out(hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter) -> None:
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert router.count("/CRSP/logout") == 1
    assert router.sessions == {}


async def test_options_poll_interval_minimum(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 10})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 45})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded.options["scan_interval"] == 45
    assert loaded.state is ConfigEntryState.LOADED
    assert loaded.runtime_data.update_interval.total_seconds() == 45


async def test_router_mid_boot_is_not_a_bad_password(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.expire_sessions()
    router.login_status_override = 503
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_UNAVAILABLE
    router.login_status_override = None
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_ON
    assert not any(
        f["context"]["source"] == config_entries.SOURCE_REAUTH for f in hass.config_entries.flow.async_progress()
    )


async def test_tr181_failure_costs_only_the_port_counters(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.tr181.pop("Device.Ethernet.Interface.1.Stats.ErrorsSent")
    await tick(hass, seconds=61)
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_ON
    assert hass.states.get("sensor.fios_router_10g_lan_errors").state == "unknown"


async def test_failed_setup_logs_out(hass: HomeAssistant, entry: MockConfigEntry, router: FakeRouter) -> None:
    router.data.pop("/CRSP/getPortConfig")  # 404 -> setup not ready
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert router.count("/CRSP/logout") == 1
    assert router.sessions == {}


async def test_stale_xsrf_refreshes_token_without_logging_in(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.rotate_xsrf()
    await tick(hass, seconds=61)
    assert router.count(LOGIN) == 1
    assert len(router.sessions) == 1
    assert hass.states.get("binary_sensor.fios_router_wan").state == STATE_ON


async def test_failed_flow_does_not_echo_the_password(hass: HomeAssistant, router: FakeRouter) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: router.base_url, CONF_USERNAME: USER, CONF_PASSWORD: "typo-secret"}
    )
    assert "typo-secret" not in repr(result)


async def test_reauth_for_a_different_router_is_refused(
    hass: HomeAssistant, loaded: MockConfigEntry, router: FakeRouter
) -> None:
    router.data["/CRSP/getRouterDetails"]["serialNumber"] = "SOMEONE-ELSE"
    result = await loaded.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: router.password})
    assert result["reason"] == "unique_id_mismatch"
