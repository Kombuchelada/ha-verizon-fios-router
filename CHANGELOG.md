# Changelog

## 0.1.0 — 2026-10-22

First public release.

- WAN state, public IPv4/IPv6, per-port link, speed, error and discard counters, WAN throughput and hourly/daily usage, active devices, last boot, firmware and firmware-upgrade-in-progress.
- Guest Wi-Fi, UPnP and per-device internet access switches; reboot button.
- `new_device` event and a device tracker per client.
- Config flow with reauth, adjustable poll interval, redacted diagnostics.
- Tested on the Verizon CHR30A, firmware 4.1.7.8822. Requires Home Assistant 2026.8.0 or newer.
