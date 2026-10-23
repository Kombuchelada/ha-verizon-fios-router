"""One poll of the router per interval, in one session."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from aiohttp import CookieJar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FiosApiError, FiosAuthError, FiosClient, FiosError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import Client, Snapshot, build_snapshot

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
LAST_BOOT_TOLERANCE = timedelta(minutes=1)

type FiosConfigEntry = ConfigEntry[FiosCoordinator]


def create_client(hass: HomeAssistant, data: Mapping[str, Any], auto_cleanup: bool = True) -> FiosClient:
    """A client on its own session: the certificate is self-signed and the router
    lives on an IP address, whose cookies aiohttp's default jar refuses."""
    session = async_create_clientsession(
        hass, verify_ssl=False, auto_cleanup=auto_cleanup, cookie_jar=CookieJar(unsafe=True)
    )
    return FiosClient(session, data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD])


@dataclass
class RouterState:
    """A snapshot plus what only comparing polls can tell."""

    snapshot: Snapshot
    download_mbps: float | None = None
    upload_mbps: float | None = None
    last_boot: datetime | None = None
    new_clients: list[Client] = field(default_factory=list)


class FiosCoordinator(DataUpdateCoordinator[RouterState]):
    config_entry: FiosConfigEntry

    def __init__(self, hass: HomeAssistant, entry: FiosConfigEntry, client: FiosClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self._store: Store[dict] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.known_macs")
        self._known_macs: set[str] | None = None

    async def _async_update_data(self) -> RouterState:
        c = self.client
        try:
            raw = {
                "router_details": await c.router_details(),
                "port_config": await c.port_config(),
                "port_stats": await self._port_stats(),
                "network_connections": await c.network_connections(),
                "bandwidth": await c.bandwidth(),
                "device_details": await c.device_details(),
                "upnp": await c.upnp(),
                "guest_wifi": await c.guest_wifi(),
                "firmware_upgrade": await c.firmware_upgrade_status(),
            }
        except FiosAuthError as err:
            # Stops polling and opens a reauth flow; the router's lockout counter
            # never sees a second attempt with the same bad password.
            raise ConfigEntryAuthFailed(str(err)) from err
        except FiosError as err:
            raise UpdateFailed(str(err)) from err

        snap = build_snapshot(raw)
        state = RouterState(snapshot=snap)
        self._derive_rates(state)
        self._derive_last_boot(state)
        state.new_clients = await self._new_clients(snap)
        self._update_device_firmware(snap)
        return state

    async def _port_stats(self) -> dict:
        """tr181 is the most fragile read (one unreadable path fails the whole
        call), so losing it costs the error counters, not every entity."""
        try:
            return await self.client.port_stats()
        except FiosApiError as err:
            _LOGGER.debug("tr181 port stats unavailable: %s", err)
            return {}

    def _derive_rates(self, state: RouterState) -> None:
        prev = self.data.snapshot if self.data else None
        cur = state.snapshot
        counters = (prev.counters_ms, cur.counters_ms, prev.wan_rx_bytes, cur.wan_rx_bytes,
                    prev.wan_tx_bytes, cur.wan_tx_bytes) if prev else (None,)
        if None in counters:
            return
        seconds = (cur.counters_ms - prev.counters_ms) / 1000
        drx, dtx = cur.wan_rx_bytes - prev.wan_rx_bytes, cur.wan_tx_bytes - prev.wan_tx_bytes
        if seconds <= 0 or drx < 0 or dtx < 0:  # counter reset: the router rebooted
            return
        state.download_mbps = round(drx * 8 / seconds / 1e6, 2)
        state.upload_mbps = round(dtx * 8 / seconds / 1e6, 2)

    def _derive_last_boot(self, state: RouterState) -> None:
        boot = dt_util.utcnow().replace(microsecond=0) - timedelta(seconds=state.snapshot.uptime_s)
        prev = self.data.last_boot if self.data else None
        state.last_boot = prev if prev and abs(boot - prev) < LAST_BOOT_TOLERANCE else boot

    async def _async_setup(self) -> None:
        stored = await self._store.async_load()
        self._known_macs = set(stored["macs"]) if stored else None

    async def _new_clients(self, snap: Snapshot) -> list[Client]:
        if self._known_macs is None:
            # First run ever: learn what's already here without announcing it.
            self._known_macs = set(snap.clients)
            await self._store.async_save({"macs": sorted(self._known_macs)})
            return []
        new = [c for mac, c in snap.clients.items() if mac not in self._known_macs]
        if new:
            self._known_macs.update(c.mac for c in new)
            await self._store.async_save({"macs": sorted(self._known_macs)})
        return new

    def _update_device_firmware(self, snap: Snapshot) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier((DOMAIN, snap.serial), self.config_entry.entry_id)
        if device and device.sw_version != snap.firmware:
            registry.async_update_device(device.id, sw_version=snap.firmware)
