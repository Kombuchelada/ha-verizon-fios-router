"""Raw router responses → one immutable snapshot. Pure; no I/O.

Wi-Fi keys never make it past this module: nothing below copies them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import PORT_TYPES, TR181_PORT_INTERFACE

# getDeviceDetails connectionType codes.
_CONNECTION = {
    **dict.fromkeys((1, 255), "ethernet"),
    **dict.fromkeys((2, 258), "coax"),
    **dict.fromkeys((3, 4, 7, 251, 253, 254), "5 GHz"),
    **dict.fromkeys((5, 252), "2.4 GHz"),
    **dict.fromkeys((8, 257), "6 GHz"),
    **dict.fromkeys((6, 256), "2.4 GHz guest"),
    **dict.fromkeys((10, 11, 12, 14, 15, 16), "5 GHz guest"),
    **dict.fromkeys((13, 17), "6 GHz guest"),
    **dict.fromkeys((9, 250), "2.4 GHz IoT"),
}

FIRMWARE_UPGRADE_IN_PROGRESS = 1  # getFirmwareUpgradeStatus: 0 idle, 1 in progress, 2 failed, 3 invalid


@dataclass(frozen=True)
class Port:
    key: str
    connected: bool
    speed_mbps: int | None
    errors: int | None = None
    discards: int | None = None


@dataclass(frozen=True)
class Client:
    mac: str
    name: str
    ip: str | None
    connected: bool
    blocked: bool
    connection: str


@dataclass(frozen=True)
class GuestWifi:
    enabled: bool
    radio_aliases: tuple[str, ...]  # the bands an on/off write must name


@dataclass(frozen=True)
class Snapshot:
    serial: str
    model: str
    firmware: str
    hardware: str
    uptime_s: int
    wan_up: bool
    public_ipv4: str | None
    public_ipv6: str | None
    delegated_prefix: str | None
    ports: dict[str, Port]
    wan_rx_bytes: int | None
    wan_tx_bytes: int | None
    counters_ms: int | None
    wan_bytes: dict[str, int | None]  # downloaded_hour, uploaded_hour, downloaded_day, uploaded_day
    clients: dict[str, Client]
    upnp_enabled: bool
    upnp_auto_clean: bool
    guest_wifi: GuestWifi
    firmware_upgrading: bool

    @property
    def connected_clients(self) -> int:
        return sum(1 for c in self.clients.values() if c.connected)


def parse_router_details(details: dict[str, Any]) -> dict[str, Any]:
    wan_up = details.get("broadbandIpv4Status") == "Up"
    v6_up = details.get("broadbandIpv6Status") == "Up"
    return {
        "serial": details.get("serialNumber", ""),
        "model": details.get("deviceModel") or details.get("productClass", "CHR30A"),
        "firmware": details.get("softwareVersion", ""),
        "hardware": details.get("hardwareVersion", ""),
        "uptime_s": int(details.get("upTime", 0)),
        "wan_up": wan_up,
        # A WAN address only counts while its family is Up.
        "public_ipv4": (details.get("ipv4AddressWan") or None) if wan_up else None,
        "public_ipv6": (details.get("ipv6AddressWan") or None) if v6_up else None,
        "delegated_prefix": (details.get("delegatedPrefix") or None) if v6_up else None,
    }


def parse_ports(port_config: dict[str, Any], stats: dict[str, Any]) -> dict[str, Port]:
    ports: dict[str, Port] = {}
    for p in port_config.get("portConfigList", []):
        key = PORT_TYPES.get(p.get("portType"))
        if key is None:
            continue
        connected = bool(p.get("isConnected"))
        speed = (p.get("activeService") or {}).get("speedMbps")
        prefix = f"Device.Ethernet.Interface.{TR181_PORT_INTERFACE[key]}.Stats."
        ports[key] = Port(
            key=key,
            connected=connected,
            speed_mbps=speed if connected and isinstance(speed, int) and speed > 0 else None,
            errors=_sum(stats, prefix, "ErrorsReceived", "ErrorsSent"),
            discards=_sum(stats, prefix, "DiscardPacketsReceived", "DiscardPacketsSent"),
        )
    return {k: ports[k] for k in PORT_TYPES.values() if k in ports}


def _sum(stats: dict[str, Any], prefix: str, *names: str) -> int | None:
    values = [stats.get(prefix + n) for n in names]
    if any(not isinstance(v, int) for v in values):
        return None
    return sum(values)


def parse_clients(device_details: dict[str, Any]) -> dict[str, Client]:
    clients: dict[str, Client] = {}
    for d in device_details.get("deviceList", []):
        mac = (d.get("mac") or "").upper()
        if not mac or d.get("isExtender"):
            continue
        clients[mac] = Client(
            mac=mac,
            name=d.get("hostName") or d.get("deviceName") or mac,
            ip=d.get("ipv4Address") or None,
            connected=d.get("status") == 1,
            blocked=bool(d.get("blocked")),
            connection=_CONNECTION.get(d.get("connectionType"), "unknown"),
        )
    return clients


def parse_guest_wifi(details: dict[str, Any]) -> GuestWifi:
    bands = [w for w in details.get("wifiInfoList", []) if w.get("radioAlias")]
    # With SON (one SSID steered across bands) only the SON band is written.
    if details.get("sonEnabled") and details.get("sonRadioAlias"):
        aliases: tuple[str, ...] = (details["sonRadioAlias"],)
    else:
        aliases = tuple(w["radioAlias"] for w in bands)
    return GuestWifi(enabled=any(w.get("enabled") for w in bands), radio_aliases=aliases)


def parse_wan_counters(connections: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    for c in connections.get("networkConnectionsList", []):
        if c.get("alias") == "broadband":
            return c.get("rxBytes"), c.get("txBytes"), connections.get("timestamp")
    return None, None, None


def parse_wan_bytes(bandwidth: dict[str, Any]) -> dict[str, int | None]:
    wan = bandwidth.get("wan", {})
    down, up = wan.get("downloadData", {}), wan.get("uploadData", {})
    return {
        "downloaded_hour": down.get("oneHour"),
        "uploaded_hour": up.get("oneHour"),
        "downloaded_day": down.get("oneDay"),
        "uploaded_day": up.get("oneDay"),
    }


def build_snapshot(raw: dict[str, Any]) -> Snapshot:
    rx, tx, ts = parse_wan_counters(raw["network_connections"])
    upnp = raw["upnp"]
    return Snapshot(
        **parse_router_details(raw["router_details"]),
        ports=parse_ports(raw["port_config"], raw.get("port_stats") or {}),
        wan_rx_bytes=rx,
        wan_tx_bytes=tx,
        counters_ms=ts,
        wan_bytes=parse_wan_bytes(raw["bandwidth"]),
        clients=parse_clients(raw["device_details"]),
        upnp_enabled=bool(upnp.get("enabled")),
        upnp_auto_clean=bool(upnp.get("autoCleanEnabled", True)),
        guest_wifi=parse_guest_wifi(raw["guest_wifi"]),
        firmware_upgrading=raw["firmware_upgrade"].get("status") == FIRMWARE_UPGRADE_IN_PROGRESS,
    )
