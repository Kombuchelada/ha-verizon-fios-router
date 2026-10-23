#!/usr/bin/env python3
"""live_check.py — run the integration's client against a real router, read-only.

One login, one full poll through the same client and parser the integration
uses, a summary with no addresses or names, then logout. Run it after a
firmware update to see whether Verizon broke anything, or on a router model
that isn't listed as tested to see which calls it answers.

    FIOS_PASSWORD=… python scripts/live_check.py [--dump DIR]

FIOS_HOST defaults to 192.168.1.1 and FIOS_USERNAME to admin. Without
FIOS_PASSWORD it prompts. Run it from the repo root in the dev venv (see the
README): importing the integration's package imports Home Assistant.

--dump DIR writes each raw response to DIR. Those files hold your Wi-Fi
passphrase, MACs, serial and public IP: run scripts/scrub.py over them and
read the result before sharing it anywhere.

Every call is a read. The script never sends a write, and logs in exactly
once, so a wrong password costs one attempt against the router's lockout.
"""

import argparse
import asyncio
import getpass
import json
import os
import pathlib
import sys

import aiohttp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from custom_components.fios_router.api import FiosAuthError, FiosClient, FiosError  # noqa: E402
from custom_components.fios_router.const import PORT_NAMES  # noqa: E402
from custom_components.fios_router.models import build_snapshot  # noqa: E402

# Snapshot key -> (client method, file name the fake router serves it under).
READS = {
    "router_details": ("router_details", "CRSP_getRouterDetails"),
    "port_config": ("port_config", "CRSP_getPortConfig"),
    "port_stats": ("port_stats", "tr181"),
    "network_connections": ("network_connections", "CRSP_getNetworkConnections"),
    "bandwidth": ("bandwidth", "CRSP_getBandwidthData"),
    "device_details": ("device_details", "CRSP_getDeviceDetails"),
    "upnp": ("upnp", "CRSP_getUpnpConfig"),
    "guest_wifi": ("guest_wifi", "CRSP_getWifiDetailsByNetworkType"),
    "firmware_upgrade": ("firmware_upgrade_status", "CRSP_getFirmwareUpgradeStatus"),
}


async def main(dump: pathlib.Path | None) -> int:
    host = os.environ.get("FIOS_HOST", "192.168.1.1")
    user = os.environ.get("FIOS_USERNAME", "admin")
    password = os.environ.get("FIOS_PASSWORD") or getpass.getpass(f"Admin password for {host}: ")

    connector = aiohttp.TCPConnector(ssl=False)  # the router's certificate is self-signed
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        client = FiosClient(session, host, user, password)
        try:
            await client.async_login()
        except FiosAuthError as err:
            print(f"login rejected: {err}. Not retrying; check the password before running again.")
            return 1
        try:
            raw, failed = {}, []
            for key, (method, name) in READS.items():
                try:
                    raw[key] = await getattr(client, method)()
                except FiosError as err:
                    failed.append(key)
                    print(f"FAILED {key}: {err}")
                    continue
                if dump:
                    body = {"report": raw[key]} if key == "port_stats" else raw[key]
                    (dump / f"{name}.json").write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
            if failed:
                print(f"{len(failed)} of {len(READS)} reads failed; the integration needs all but port_stats.")
                return 1

            s = build_snapshot(raw)
            print(f"model {s.model}  firmware {s.firmware}  hardware {s.hardware}  uptime {s.uptime_s // 3600} h")
            print(f"wan up {s.wan_up}  ipv4 {'yes' if s.public_ipv4 else 'no'}  ipv6 {'yes' if s.public_ipv6 else 'no'}")
            for p in s.ports.values():
                print(f"port {PORT_NAMES[p.key]:10} link {p.connected!s:5} speed {p.speed_mbps}  errors {p.errors}  discards {p.discards}")
            print(f"wan bytes {s.wan_rx_bytes is not None}  usage windows {sorted(k for k, v in s.wan_bytes.items() if v is not None)}")
            print(f"clients {len(s.clients)} known, {s.connected_clients} online, {sum(c.blocked for c in s.clients.values())} blocked")
            print(f"upnp {s.upnp_enabled}/{s.upnp_auto_clean}  guest wifi {s.guest_wifi.enabled} via {s.guest_wifi.radio_aliases}")
            print(f"firmware upgrading {s.firmware_upgrading}")
            return 0
        finally:
            await client.async_logout()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dump", type=pathlib.Path, help="write raw responses here (unscrubbed!)")
    args = parser.parse_args()
    if args.dump:
        args.dump.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(args.dump)))
