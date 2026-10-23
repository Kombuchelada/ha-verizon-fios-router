#!/usr/bin/env python3
"""scrub.py — turn raw router responses into committable test fixtures.

Replaces every MAC with a stable fake, public addresses with documentation
ranges, LAN client addresses with 192.168.1.1xx, names and serials with
placeholders, device make/model details with their class, and drops Wi-Fi keys. The same
input value always maps to the same fake, across all files in one run, so
cross-references between endpoints (a MAC in getDeviceDetails and in
getARPTable) still line up.

Usage:  scrub.py <raw dir> <fixtures dir>

Read the output before sharing it. This catches the fields seen on a CHR30A;
another model may carry personal data under keys it has never seen.
"""
import json
import pathlib
import re
import sys

MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
BARE_MAC = re.compile(r"^[0-9a-f]{12}$")
PUBLIC_V4 = re.compile(r"\b(?!192\.168\.|10\.|127\.|0\.|255\.)\d{1,3}(?:\.\d{1,3}){3}\b")
GLOBAL_V6 = re.compile(r"\b2[0-9a-f]{3}:[0-9a-f]{1,4}:[0-9a-f]{1,4}:[0-9a-f]{1,4}")
LINK_LOCAL = re.compile(r"\bfe80::[0-9a-f:]+", re.I)  # EUI-64: the MAC is inside
DROP = {"key", "password", "passphrase", "wpaKey", "presharedKey"}
NAMES = {"deviceName", "hostName", "hostname", "connectedVia", "serialNumber", "name"}
SSIDS = {"ssid", "connectedSSID"}
LAN_V4 = re.compile(r"^(?:192\.168|10|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}$")
KEEP_V4 = {"MinAddress", "MaxAddress", "SubnetMask", "subnetMask"}  # DHCP pool bounds, masks
GATEWAY_V4 = re.compile(r"^(?:192\.168\.\d{1,3}|10\.\d{1,3}\.\d{1,3})\.1$")
INVENTORY = {"make", "model", "product", "subModel", "firmwareVersion", "operatingSystem"}
SAFE_NAMES = re.compile(r"(Wi-Fi|Ethernet|Broadband|Network|Bridge|Router|Access Point|Coax|LAN|WAN)", re.I)

macs, v4s, v6s, lls, names, lans = {}, {}, {}, {}, {}, {}


def fake_mac(m):
    k = m.upper()
    if k not in macs:
        macs[k] = "02:00:00:00:%02X:%02X" % divmod(len(macs) + 1, 256)
    return macs[k]


def fake_name(v):
    if not v or SAFE_NAMES.search(v):
        return v
    if v not in names:
        names[v] = f"device-{len(names) + 1}"
    return names[v]


def scrub_str(key, v):
    if LAN_V4.match(v) and key not in KEEP_V4 and not GATEWAY_V4.match(v):
        return lans.setdefault(v, f"192.168.1.{101 + len(lans)}")
    if key in NAMES:
        v = fake_name(v)
    if key in SSIDS and v:
        v = re.sub(r"^[^-]+", "FiosTest", v)  # keep the -Guest/-IoT suffix
    v = MAC.sub(lambda m: fake_mac(m.group()), v)
    if BARE_MAC.match(v):
        v = fake_mac(":".join(v[i:i + 2] for i in range(0, 12, 2))).replace(":", "").lower()
    v = PUBLIC_V4.sub(lambda m: v4s.setdefault(m.group(), f"203.0.113.{len(v4s) + 1}"), v)
    v = GLOBAL_V6.sub(lambda m: v6s.setdefault(m.group(), f"2001:db8:{len(v6s) + 1:x}:0"), v)
    v = LINK_LOCAL.sub(lambda m: lls.setdefault(m.group().lower(), f"fe80::{len(lls) + 1:x}"), v)
    return v


def scrub(node, key=""):
    if isinstance(node, dict):
        out = {k: scrub(v, k) for k, v in node.items() if k not in DROP}
        for k in INVENTORY & out.keys():  # a household's device list is a fingerprint
            if isinstance(out[k], str) and out[k]:
                out[k] = node.get("deviceClass") or "Device" if k in ("model", "product") else "n/a"
        return out
    if isinstance(node, list):
        return [scrub(v, key) for v in node]
    if isinstance(node, str):
        return scrub_str(key, node)
    return node


def main():
    src, dst = map(pathlib.Path, sys.argv[1:3])
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.json")):
        data = json.loads(f.read_text())
        (dst / f.name).write_text(json.dumps(scrub(data), indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
