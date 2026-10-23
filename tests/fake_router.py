"""A fake CHR30A: HTTPS on localhost, the real auth rules, scrubbed fixtures.

The integration talks to it exactly as it talks to the router — same paths,
same Basic-auth login, same authToken cookie and x-xsrf-token header — so tests
exercise the whole stack from config flow to HTTP. Everything the integration
sends is recorded in `calls`.
"""

from __future__ import annotations

import base64
import copy
import datetime
import ipaddress
import json
import pathlib
import secrets
import ssl
from typing import Any, Callable

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
USER = "admin"
PASSWORD = "correct-horse"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def self_signed_context(tmp: pathlib.Path) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-router")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "cert.pem", tmp / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


class FakeRouter:
    """State plus an aiohttp app. Tests mutate `data` and the flags directly."""

    def __init__(self) -> None:
        self.password = PASSWORD
        self.failed_count = 0
        self.sessions: dict[str, str] = {}  # authToken -> xsrf token
        self.calls: list[tuple[str, str, Any]] = []
        self.offline = False
        self.login_status_override: int | None = None  # e.g. 503 while "booting"
        # Raw responses keyed by path, served for GET (and bodiless POST).
        self.data: dict[str, Any] = {}
        for f in sorted(FIXTURES.glob("CRSP_*.json")):
            self.data["/" + f.stem.replace("_", "/")] = json.loads(f.read_text())
        # Every tr181 parameter the fake knows; asking for any other fails the
        # whole request, as on the real router.
        self.tr181: dict[str, Any] = load_fixture("tr181")["report"]
        # Writes: path prefix -> handler(body, path) returning a response body.
        self.writes: dict[str, Callable[[Any, str], Any]] = {
            "/CRSP/updateUpnpConfig": self._update_upnp,
            "/CRSP/updateWifiDetailsByNetworkType": self._update_wifi,
            "/CRSP/updateDeviceDetails/": self._update_device,
            "/CRSP/updateRouterDetails": self._update_router,
        }
        # POST reads that need a body: path -> handler(body) returning a response.
        self.reads_with_body: dict[str, Callable[[Any], Any]] = {
            "/CRSP/tr181/getParameterValues": self._tr181,
            "/CRSP/getWifiDetailsByNetworkType": self._wifi_details,
            "/CRSP/getFirmwareUpgradeStatus": self._firmware_status,
        }
        self.reboots = 0
        self.base_url = ""
        self._runner: web.AppRunner | None = None

    # --- test helpers ---------------------------------------------------------

    def expire_sessions(self) -> None:
        self.sessions.clear()

    def rotate_xsrf(self) -> None:
        """Sessions stay valid but every XSRF token goes stale (the router 403s)."""
        for token in self.sessions:
            self.sessions[token] = "stale"

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method in (None, m)]

    def count(self, path: str) -> int:
        return sum(1 for _, p, _ in self.calls if p == path)

    def bodies(self, path: str) -> list[Any]:
        return [b for _, p, b in self.calls if p == path]

    # --- server ---------------------------------------------------------------

    async def start(self, ssl_ctx: ssl.SSLContext) -> None:
        app = web.Application(middlewares=[self._middleware])
        app.router.add_route("*", "/{tail:.*}", self._dispatch)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self.base_url = f"127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _middleware(self, request: web.Request, handler):
        if self.offline:
            request.transport.close()
            raise web.HTTPServiceUnavailable
        body = None
        if request.can_read_body:
            raw = await request.read()
            body = json.loads(raw) if raw else None
        request["body"] = body
        self.calls.append((request.method, request.path, body))
        return await handler(request)

    async def _dispatch(self, request: web.Request) -> web.Response:
        path, method, body = request.path, request.method, request["body"]

        if path == "/CRSP/open/getLoginToken":
            return web.json_response({"failedCount": self.failed_count})
        if path == "/CRSP/open/login":
            return self._login(request)
        if path in ("/CRSP/open/isDeviceAlive",):
            return web.json_response({"timestamp": 1})

        token = request.cookies.get("authToken")
        if token not in self.sessions:
            return web.json_response({"errorCode": "401 Unauthorized"}, status=401)
        if path == "/CreateXSRFToken":
            xsrf = secrets.token_hex(8)
            self.sessions[token] = xsrf
            return web.Response(headers={"x-xsrf-token": xsrf})
        if request.headers.get("x-xsrf-token") != self.sessions[token]:
            return web.json_response({"errorCode": "403 Forbidden"}, status=403)
        if path == "/CRSP/logout":
            self.sessions.pop(token, None)
            return web.json_response({})

        for prefix, handler in self.writes.items():
            if path.startswith(prefix):
                return web.json_response(handler(body, path) or {})
        if path in self.reads_with_body:
            if body is None:
                return web.json_response({"errorCode": "400 Bad Request"}, status=400)
            result = self.reads_with_body[path](body)
            if result is None:
                return web.json_response({"errorCode": "400 Bad Request"}, status=400)
            return web.json_response(result)
        if path in self.data:
            return web.json_response(copy.deepcopy(self.data[path]))
        return web.json_response({"errorCode": "404 Not Found"}, status=404)

    def _login(self, request: web.Request) -> web.Response:
        if self.login_status_override:
            return web.Response(status=self.login_status_override)
        expected = "Basic " + base64.b64encode(f"{USER}:{self.password}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            self.failed_count += 1
            return web.json_response({"failedCount": self.failed_count}, status=401)
        token = secrets.token_hex(16)
        self.sessions[token] = ""
        resp = web.json_response({})
        resp.set_cookie("authToken", token, httponly=True, secure=True)
        return resp

    # --- writes ---------------------------------------------------------------

    def _tr181(self, body: Any) -> Any:
        params = body.get("parameters", [])
        if body.get("reportFormat") != "NameValuePair" or any(p not in self.tr181 for p in params):
            return None
        return {"report": {p: self.tr181[p] for p in params}, "reportFormat": "NameValuePair"}

    def _wifi_details(self, body: Any) -> Any:
        if body.get("networkType") != 1:
            return None
        return copy.deepcopy(self.data["/CRSP/getWifiDetailsByNetworkType"])

    def _firmware_status(self, body: Any) -> Any:
        return copy.deepcopy(self.data["/CRSP/getFirmwareUpgradeStatus"]) if "kind" in body else None

    def _update_wifi(self, body: Any, _path: str) -> Any:
        assert body["networkType"] == 1, body
        bands = {w["radioAlias"]: w for w in body["wifiInfoList"]}
        details = self.data["/CRSP/getWifiDetailsByNetworkType"]
        for w in details["wifiInfoList"]:
            # With SON on, the router applies the SON band's setting to every band.
            src = bands.get(details["sonRadioAlias"]) if details.get("sonEnabled") else bands.get(w["radioAlias"])
            if src:
                w["enabled"] = src["enabled"]
        return {"isConnectedViaWifi": False}

    def _update_device(self, body: Any, path: str) -> Any:
        mac = path.rsplit("/", 1)[1]
        for d in self.data["/CRSP/getDeviceDetails"]["deviceList"]:
            if d["mac"] == mac:
                d["blocked"] = body["blocked"]
                return {}
        raise web.HTTPNotFound

    def _update_router(self, body: Any, _path: str) -> Any:
        # action 1 and 2 are factory resets. Anything but a plain reboot is a bug.
        assert body == {"action": 0}, f"updateRouterDetails sent {body}"
        self.reboots += 1
        return {"status": 0}

    def _update_upnp(self, body: Any, _path: str) -> Any:
        self.data["/CRSP/getUpnpConfig"] = {
            "enabled": body["enabled"],
            "autoCleanEnabled": body["autoCleanEnabled"],
        }
        return {}
