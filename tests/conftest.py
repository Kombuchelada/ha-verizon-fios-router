"""Shared fixtures: a running fake router and a config entry pointed at it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fios_router.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .fake_router import PASSWORD, USER, FakeRouter, load_fixture, self_signed_context


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(scope="session")
def ssl_ctx(tmp_path_factory: pytest.TempPathFactory):
    return self_signed_context(tmp_path_factory.mktemp("tls"))


@pytest.fixture
async def router(ssl_ctx, socket_enabled: None) -> AsyncIterator[FakeRouter]:
    fake = FakeRouter()
    await fake.start(ssl_ctx)
    yield fake
    await fake.stop()


@pytest.fixture
def entry_data(router: FakeRouter) -> dict[str, Any]:
    return {CONF_HOST: router.base_url, CONF_USERNAME: USER, CONF_PASSWORD: PASSWORD}


@pytest.fixture
def entry(hass: HomeAssistant, entry_data: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id=load_fixture("CRSP_getRouterDetails")["serialNumber"], title="Fios router")
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded(hass: HomeAssistant, entry: MockConfigEntry, router: FakeRouter) -> MockConfigEntry:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
