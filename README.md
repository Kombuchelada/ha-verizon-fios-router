# Verizon Fios Router for Home Assistant (CHR30A)

A Home Assistant custom integration for the **Verizon Fios** router, installable through **HACS**. It reads WAN status, public IP, per-port link speed and error counters, WAN throughput and data usage, connected devices and firmware from the router's local web API, and gives you guest Wi-Fi, UPnP, per-device internet block and reboot controls. Tested on the **Verizon Wi-Fi Access Point CHR30A**; the **CR1000A / CR1000B** "Verizon Router" may work (see [Supported hardware](#supported-hardware)). Local polling, no cloud, no Verizon account.

[![Validate](https://github.com/Kombuchelada/ha-verizon-fios-router/actions/workflows/validate.yml/badge.svg)](https://github.com/Kombuchelada/ha-verizon-fios-router/actions/workflows/validate.yml)
[![Tests](https://github.com/Kombuchelada/ha-verizon-fios-router/actions/workflows/tests.yml/badge.svg)](https://github.com/Kombuchelada/ha-verizon-fios-router/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)

Verizon publishes no API, SNMP or SSH for these routers. This integration speaks the same undocumented `/CRSP/*` JSON API the router's own admin page uses, documented in [docs/API.md](docs/API.md).

## Entities

Everything hangs off one device, **Fios router**, so entity IDs start `fios_router_`.

| Platform | Entities |
|---|---|
| Binary sensor | **WAN** (internet up). Diagnostic: link state of each port (WAN, 10G LAN, 2.5G LAN1, 2.5G LAN2), **Firmware upgrading** |
| Sensor | **Public IPv4**, **Public IPv6** (delegated prefix as an attribute), **WAN download** / **WAN upload** (Mbit/s, from counter deltas between polls), **WAN downloaded/uploaded last hour** and **last day**, **Active devices**. Diagnostic: **Last boot**, **Firmware**, and per port: negotiated **speed** (0 when unplugged, so drops show in history), **errors**, **discards** |
| Switch | **Guest Wi-Fi** (all guest bands together), **UPnP**, and **`<device>` internet access** per client: off = the router's per-device Block. Per-client switches are disabled by default |
| Button | **Reboot** |
| Event | **New device**: fires `new_device` with `mac`, `hostname`, `ip` and `connection` the first time a MAC the integration has never seen joins. Devices already present at setup aren't announced; devices that joined while HA was down are announced on start |
| Device tracker | One per client MAC, home while the router reports it online. Disabled by default unless Home Assistant already knows the MAC from another integration |

The trackers answer "what is on the network", not "who is home": sleeping phones drop off Wi-Fi within minutes. Use the Companion app for presence.

There's no speed test and no "update available" sensor: the router's speed test only measures links to mesh extenders, and no endpoint reports a pending update. **Firmware**, **Firmware upgrading** and **Last boot** together show a pushed update after it happens.

## Supported hardware

| Model | Status |
|---|---|
| Verizon Wi-Fi Access Point **CHR30A** (as the main router), firmware 4.1.7.8822 | **Tested**, used daily |
| Verizon Router **CR1000A**, **CR1000B** | **Unverified.** Same web UI family, so the API may match. The port entities assume the CHR30A's ports (WAN, 10G LAN, 2.5G LAN1, 2.5G LAN2) and will be wrong on other layouts |
| Fios Router G3100, G1100 and older | Unknown |

If you try another model, please [report it](#reporting-a-new-router-model), whether it works or not.

Verizon pushes firmware without asking, and the API is undocumented, so any update can break something. If entities go unavailable right after the **Firmware** sensor changes, that's the likely cause; please open an issue with diagnostics.

Requires Home Assistant **2026.8.0** or newer.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories**. Add `https://github.com/Kombuchelada/ha-verizon-fios-router`, type **Integration**.
2. Find **Verizon Fios Router** in HACS, download it, and restart Home Assistant.

### Manual

Copy `custom_components/fios_router/` from the [latest release](https://github.com/Kombuchelada/ha-verizon-fios-router/releases/latest) into `<config>/custom_components/`, so you have `<config>/custom_components/fios_router/manifest.json`. Restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → Verizon Fios Router.**

| Field | Default | Notes |
|---|---|---|
| Host | `192.168.1.1` | The router's LAN address. `https://` is assumed; `mynetworksettings.com` also works if your DNS resolves it to the router |
| Username | `admin` | |
| Password | | The router's admin password: printed on its label unless you changed it. Not your Verizon account password |

Setup logs in once to check the password. A wrong password costs one attempt against the router's lockout and is never retried automatically.

**Options:** poll interval, default 60 s, minimum 30 s.

If the password later changes, polling stops and Home Assistant asks you to re-authenticate, rather than hammering the router with a bad password.

## Security notes

- **It holds an admin session open.** One login, kept alive by polling, re-established only when the router drops it. The router allows 10 concurrent sessions; this integration uses one per configured router. It logs out on unload and on Home Assistant shutdown.
- **HTTPS certificate checking is off, for the router only.** The router serves a self-signed certificate that can't be validated. The integration uses its own HTTP session for the router host you configure; Home Assistant's shared session and every other integration keep verifying certificates. Anyone who can intercept traffic on your LAN between Home Assistant and the router could read the admin password.
- **The admin password is stored in Home Assistant's config entry**, as for any integration with a login, so it is in your Home Assistant backups. Keep backup encryption on.
- **Diagnostics are redacted:** password, serial, public addresses, MACs, IPs and device names are removed.
- **Wi-Fi passphrases are never kept.** The router includes the guest passphrase in the guest Wi-Fi response; the parser drops it before anything is stored or logged.

### What it never calls

The integration writes exactly four things, each the same request the router's own admin page makes: guest Wi-Fi on/off, UPnP on/off, one device's internet block/allow, and reboot. Nothing else is reachable from Home Assistant:

- **Factory reset.** The router's reboot endpoint, `updateRouterDetails`, is also its factory reset (`action` 1 and 2). The client refuses any body on that path other than the reboot, `{"action": 0}`, and a test pins it.
- Firmware upload or upgrade, backup/restore, Wi-Fi radio or password changes, port forwarding, DNS and DHCP settings.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Could not reach the router" | Wrong host, or Home Assistant can't reach the router's HTTPS port. Check `https://<host>` loads the router's login page from the same network |
| "The router rejected the username or password" | Wrong password, or the router's lockout after repeated failures. Wait at least 10 minutes before trying again |
| Router web UI says too many sessions | Each integration instance holds one; browser sessions expire after 10 minutes idle. Log out of other browser tabs |
| WAN download/upload unknown | Normal for the first poll: rates need two readings. Also unknown for one poll after a router reboot |
| Everything unavailable after a firmware update | Verizon changed the API. Open an issue with diagnostics |
| Port errors/discards unknown, everything else fine | The router refused the TR-181 counter read; the rest of the poll is unaffected |

For a debug log: the integration's page → **Enable debug logging**, reproduce, then **Disable debug logging** to download it. Router addresses can appear in it, so read it before posting.

## Reporting a new router model

1. **If setup works:** device page → ⋮ → **Download diagnostics**. Attach the file to a [new router model issue](https://github.com/Kombuchelada/ha-verizon-fios-router/issues/new?template=new_router_model.yml) with the model and firmware, and which entities look wrong.
2. **If setup fails:** clone this repo, set up the [dev environment](#development), and run

   ```sh
   FIOS_HOST=192.168.1.1 python scripts/live_check.py
   ```

   It logs in once (prompting for the password), makes the integration's reads without writing anything, and prints which calls failed plus a summary with no addresses or names. Paste that output into the issue.

   `--dump DIR` also saves the raw responses, which is what new test fixtures are built from. Raw responses contain your Wi-Fi passphrase, MACs, serial and public IP: run `python scripts/scrub.py DIR fixtures/` and read the result before sharing any of it.

## Development

Python 3.14 (Home Assistant 2026.10's requirement):

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

The tests run Home Assistant with the integration against an HTTPS fake router (`tests/fake_router.py`) that enforces the real login, cookie and XSRF rules and serves sanitized sample responses from a CHR30A (`tests/fixtures/`). They never contact a real router. CI runs them on the pinned Home Assistant release and on the oldest supported one, plus hassfest and HACS validation.

## License

[MIT](LICENSE). This is an independent project, not affiliated with or endorsed by Verizon. "Verizon" and "Fios" are trademarks of their owner, used here only to describe compatibility.
