"""Client for the Fios CHR30A's /CRSP/* JSON API.

One instance holds one router session. See docs/API.md for the login flow.
Every call is a POST, and calls go out one at a time. The rules
that matter for the router's login lockout:

- A rejected login raises FiosAuthError and is never retried here.
- A 403/419 means a stale XSRF token and only refreshes it.
  A 401 means the session is gone and logs in again, at most once per call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import aiohttp

from .const import TR181_PORT_INTERFACE, TR181_PORT_STATS

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)
SESSION_GONE = 401
STALE_XSRF = (403, 419)

# updateRouterDetails is also the factory-reset endpoint: action 1 restores
# defaults and action 2 restores them keeping the admin password. Only 0 reboots,
# and call() refuses any other body for that path.
REBOOT_PATH = "/CRSP/updateRouterDetails"

GUEST_NETWORK = 1  # networkType: 0 primary, 1 guest, 2 IoT


class FiosError(Exception):
    """Base error."""


class FiosConnectionError(FiosError):
    """Router unreachable or answered garbage."""


class FiosAuthError(FiosError):
    """Router rejected the credentials. Never retry automatically."""


class FiosApiError(FiosError):
    """Router answered an authenticated call with an error."""

    def __init__(self, path: str, status: int, body: str) -> None:
        super().__init__(f"{path} -> {status}: {body[:200]}")
        self.status = status


class FiosClient:
    """One router session; calls are serialised."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, username: str, password: str
    ) -> None:
        self._session = session
        self._base = host if "://" in host else f"https://{host}"
        self._auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self._xsrf: str | None = None
        self._lock = asyncio.Lock()

    # --- session ---------------------------------------------------------------

    async def _raw(
        self, path: str, body: Any = None, headers: dict[str, str] | None = None, method: str = "POST"
    ) -> tuple[int, str, Any]:
        hdrs = {"Content-Type": "application/json"}
        if self._xsrf:
            hdrs["x-xsrf-token"] = self._xsrf
        hdrs.update(headers or {})
        data = b"" if body is None else json.dumps(body).encode()
        try:
            async with self._session.request(
                method, self._base + path, data=data, headers=hdrs, timeout=TIMEOUT
            ) as resp:
                return resp.status, await resp.text(), resp.headers
        except (aiohttp.ClientError, TimeoutError) as err:
            raise FiosConnectionError(f"{path}: {err!r}") from err

    async def _login(self) -> None:
        self._xsrf = None
        await self._raw("/CRSP/open/getLoginToken")
        status, text, _ = await self._raw(
            "/CRSP/open/login", {"keepMeSignIn": False}, {"Authorization": self._auth}
        )
        if 400 <= status < 500:
            raise FiosAuthError(f"login rejected ({status}, {_failed_count(text)})")
        if status != 200:  # a router mid-boot answers 5xx; that's not a bad password
            raise FiosConnectionError(f"login answered {status}")
        await self._fetch_xsrf()
        _LOGGER.debug("logged in to %s", self._base)

    async def _fetch_xsrf(self) -> None:
        # Every authenticated call needs this header; without it they all 403.
        status, _, headers = await self._raw("/CreateXSRFToken", method="GET")
        token = headers.get("x-xsrf-token")
        if not token:
            raise FiosConnectionError(f"no XSRF token ({status})")
        self._xsrf = token

    async def async_login(self) -> None:
        """Log in. Raises FiosAuthError on rejection; the caller must not loop."""
        async with self._lock:
            await self._login()

    async def async_logout(self) -> None:
        async with self._lock:
            if not self._xsrf:
                return
            try:
                await self._raw("/CRSP/logout")
            except FiosConnectionError:
                pass
            self._xsrf = None

    @property
    def base_url(self) -> str:
        return self._base

    def detach(self) -> None:
        """Release the HTTP session without closing HA's shared connector."""
        self._session.detach()

    async def call(self, path: str, body: Any = None) -> Any:
        """Authenticated POST; logs in if needed, and once more if the session is gone."""
        if path == REBOOT_PATH and body != {"action": 0}:
            raise ValueError(f"refusing {path} with {body}: only action 0 is a reboot")
        async with self._lock:
            logged_in = False
            if not self._xsrf:
                await self._login()
                logged_in = True
            status, text, _ = await self._raw(path, body)
            if status in STALE_XSRF:
                _LOGGER.debug("%s answered %s; refreshing the XSRF token", path, status)
                await self._fetch_xsrf()
                status, text, _ = await self._raw(path, body)
            if status == SESSION_GONE and not logged_in:
                _LOGGER.debug("%s answered 401; logging in again", path)
                await self._login()
                status, text, _ = await self._raw(path, body)
        if status != 200:
            raise FiosApiError(path, status, text)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as err:
            raise FiosConnectionError(f"{path}: not JSON") from err

    # --- reads -----------------------------------------------------------------

    async def router_details(self) -> dict[str, Any]:
        return await self.call("/CRSP/getRouterDetails")

    async def port_config(self) -> dict[str, Any]:
        return await self.call("/CRSP/getPortConfig")

    async def network_connections(self) -> dict[str, Any]:
        return await self.call("/CRSP/getNetworkConnections")

    async def bandwidth(self) -> dict[str, Any]:
        return await self.call("/CRSP/getBandwidthData")

    async def device_details(self) -> dict[str, Any]:
        return await self.call("/CRSP/getDeviceDetails")

    async def upnp(self) -> dict[str, Any]:
        return await self.call("/CRSP/getUpnpConfig")

    async def guest_wifi(self) -> dict[str, Any]:
        return await self.call("/CRSP/getWifiDetailsByNetworkType", {"networkType": GUEST_NETWORK})

    async def firmware_upgrade_status(self) -> dict[str, Any]:
        return await self.call("/CRSP/getFirmwareUpgradeStatus", {"kind": 0})

    async def port_stats(self) -> dict[str, Any]:
        """Per-port error/discard counters, which only tr181 exposes."""
        params = [
            f"Device.Ethernet.Interface.{i}.Stats.{s}"
            for i in TR181_PORT_INTERFACE.values()
            for s in TR181_PORT_STATS
        ]
        resp = await self.call(
            "/CRSP/tr181/getParameterValues",
            {"parameters": params, "reportFormat": "NameValuePair"},
        )
        return resp.get("report", {})

    # --- writes ----------------------------------------------------------------

    async def set_upnp(self, enabled: bool, auto_clean: bool) -> None:
        await self.call("/CRSP/updateUpnpConfig", {"enabled": enabled, "autoCleanEnabled": auto_clean})

    async def set_guest_wifi(self, enabled: bool, radio_aliases: list[str]) -> None:
        # A partial update: only the bands and the field changed.
        await self.call(
            "/CRSP/updateWifiDetailsByNetworkType",
            {
                "networkType": GUEST_NETWORK,
                "wifiInfoList": [{"radioAlias": a, "enabled": enabled} for a in radio_aliases],
            },
        )

    async def set_blocked(self, mac: str, blocked: bool) -> None:
        """Block or allow one device's internet access."""
        await self.call(f"/CRSP/updateDeviceDetails/{mac}", {"blocked": blocked})

    async def reboot(self) -> None:
        await self.call(REBOOT_PATH, {"action": 0})


def _failed_count(text: str) -> str:
    try:
        return f"failedCount={json.loads(text).get('failedCount')}"
    except (ValueError, AttributeError):
        return "no detail"
