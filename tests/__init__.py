"""Tests for the Fios router integration."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


async def tick(hass: HomeAssistant, **delta: float) -> None:
    """Move time on (cumulatively) and wait for the poll it triggers, which runs
    as a background task."""
    offset = hass.data["fios_test_clock"] = hass.data.get("fios_test_clock", timedelta()) + timedelta(**delta)
    async_fire_time_changed(hass, dt_util.utcnow() + offset)
    await hass.async_block_till_done(wait_background_tasks=True)
