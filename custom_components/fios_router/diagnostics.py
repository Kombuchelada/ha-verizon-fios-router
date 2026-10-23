"""Diagnostics with the password, serial and addresses redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import FiosConfigEntry

REDACT = {CONF_PASSWORD, "serial", "public_ipv4", "public_ipv6", "delegated_prefix", "mac", "ip", "name"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FiosConfigEntry) -> dict[str, Any]:
    state = entry.runtime_data.data
    snapshot = asdict(state.snapshot)
    snapshot["clients"] = list(snapshot["clients"].values())
    return {
        "entry": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "snapshot": async_redact_data(snapshot, REDACT),
        "download_mbps": state.download_mbps,
        "upload_mbps": state.upload_mbps,
    }
