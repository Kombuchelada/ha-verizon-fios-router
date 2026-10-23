# The Fios router's JSON API

The JSON API served by the Verizon Fios CHR30A's admin interface, as this integration uses it. The behavior below is as observed on a CHR30A running firmware 4.1.7.8822. Verizon can change any of it with a pushed firmware update.

The router only listens on 80/443: no SSH, Telnet or SNMP. Its HTTPS certificate is self-signed, for both `192.168.1.1` and `mynetworksettings.com`.

Send every call as a POST (a body-less one for plain reads), one at a time. GET works for most reads, but not all: `getNodeSpeedTestResult` answers 405 to it.

## Login flow

1. `POST /CRSP/open/getLoginToken` with an empty body. It returns `{"failedCount": N}`.
2. `POST /CRSP/open/login` with body `{"keepMeSignIn": false}` and header `Authorization: Basic base64("admin:<password>")`. A 200 sets an `authToken` cookie.
3. `GET /CreateXSRFToken`. Read the `x-xsrf-token` response header and send it on every later call. Without it, every call returns 403.
4. `POST /CRSP/logout` when done. Idle sessions expire after 600 s.

- A rejected login answers 401 `{"failedCount": N}`, and failures count toward a lockout (`getSystemSettings` shows `banTime: 10`). Never retry a failed login in a loop.
- A 403 or 419 means the XSRF token is stale: fetch a new one. A 401 means the session is gone.
- The router allows 10 concurrent sessions (`getSystemSettings` `sessionMax`).
- The `authToken` cookie is set for a bare IP address, which some HTTP clients' cookie jars refuse by default (aiohttp needs `CookieJar(unsafe=True)`).

## Reads

| Purpose | Call |
|---|---|
| Model, firmware, serial, uptime (s), WAN state, public IPv4/IPv6 | `POST /CRSP/getRouterDetails` |
| Port status (link up, negotiated speed) | `POST /CRSP/getPortConfig`. `portType` 0 = WAN, 1 = 10G LAN, 2 = 2.5G LAN1, 3 = 2.5G LAN2 |
| Interface byte/packet counters (`broadband` = WAN) | `POST /CRSP/getNetworkConnections`. No endpoint reports a rate; take the difference between two reads, which carry a `timestamp` in ms |
| WAN and per-client bytes over the last hour, 12 h, day, week, month | `POST /CRSP/getBandwidthData` |
| Every client the router remembers | `POST /CRSP/getDeviceDetails`. `status` 1 = online. `blocked` is the per-device internet block. `connectionType`: 1 Ethernet, 5 2.4 GHz, 3/4/7 5 GHz, 8 6 GHz, 6/10–17 guest bands, 9 IoT |
| UPnP state | `POST /CRSP/getUpnpConfig` → `{"enabled":…,"autoCleanEnabled":…}` |
| Guest Wi-Fi state | `POST /CRSP/getWifiDetailsByNetworkType` `{"networkType":1}` (0 primary, 1 guest, 2 IoT). **The response includes the Wi-Fi passphrase in `key`.** The integration never copies it |
| Firmware upgrade running? | `POST /CRSP/getFirmwareUpgradeStatus` `{"kind":0}` → `{"status":0}`: 0 idle, 1 in progress, 2 failed, 3 invalid image. Nothing reports whether an update is *available*; `getUpdateCheckStatus` is 404 on the CHR30A |
| DHCP leases and reservations | `POST /CRSP/getVendorClassTable` `{"isConnList":true,"filterGuestDevices":false}`. Leases are in `connDHCPList`, reservations in `connStaticList`. Not used by the integration |
| Logs | `POST /CRSP/getLogs` `{"logType":"system","limit":100,"offset":0,"requestId":"<uuid>","startDateTime":"YYYY-MM-DDTHH:MM:SS","endDateTime":"…"}`. Not used by the integration |
| Per-port error/discard counters | `POST /CRSP/tr181/getParameterValues`, see below |

### TR-181

`POST /CRSP/tr181/getParameterValues` `{"parameters":["Device.…", …],"reportFormat":"NameValuePair"}` → `{"report":{"<path>":value}}`.

- One unreadable path fails the whole request with 400 "Set TR181 Info Error", so probe one path at a time.
- `*` wildcards fail. A trailing `.` returns a subtree.
- Readable: `Device.Ethernet.Interface.{1..6}.` (status, `CurrentBitRate`) and `Device.Ethernet.Interface.N.Stats.` (bytes, packets, errors, discards). Interfaces 1–4 are 10G LAN, 2.5G LAN1, 2.5G LAN2 and WAN; 5 is the unused SFP WAN cage, 6 the CPU port. The router's own descriptions are off by one from these labels (interface 2 calls itself `2.5G_LAN2`), but the speeds line up with `getPortConfig`'s `portType`.
- Not readable: `Device.DeviceInfo.` and its children (memory, CPU, temperature, firmware images), `Device.WiFi.Radio.N.Stats.`, `Device.Ethernet.Link.N.`.
- The integration reads errors and discards for interfaces 1–4 in one call (16 parameters). If that call fails, only the error and discard sensors go unknown.

## Writes the integration makes

Nothing else is ever written.

| Action | Call |
|---|---|
| UPnP on/off | `POST /CRSP/updateUpnpConfig` `{"enabled":…,"autoCleanEnabled":…}`. Off stops the whole UPnP daemon (SSDP and the IGD), so Home Assistant's own UPnP/IGD integration can't work while it's off |
| Guest Wi-Fi on/off | `POST /CRSP/updateWifiDetailsByNetworkType` `{"networkType":1,"wifiInfoList":[{"radioAlias":"WLAN_24G","enabled":true}]}`. A partial update, one entry per band. With SON on (`sonEnabled`, one SSID steered across bands) send only the `sonRadioAlias` band. The radios take 10–15 s to settle |
| Block/allow one device's internet | `POST /CRSP/updateDeviceDetails/<MAC>` `{"blocked":true}` or `false`. The same block as the admin page's Devices → Block switch, not a parental-control rule |
| Reboot | `POST /CRSP/updateRouterDetails` `{"action":0}` |

## Dangerous endpoints

- **`updateRouterDetails` is also the factory reset.** `action` 1 restores defaults; `action` 2 restores them keeping the admin password. The integration's client refuses any body on that path other than `{"action":0}`, and a test pins it.
- Firmware upload/upgrade, backup/restore, Wi-Fi radio and password settings, port forwarding, DNS and DHCP changes are also in the API. The integration calls none of them.

## Things that look useful and aren't

- **Speed test.** `startNodeSpeedTest {"ipAddress":…}` tests the link to a mesh extender, not the WAN. `getSpeedTestMode` is 0 ("not supported") without extenders.
- `getWifiStatus` and `getUpdateCheckStatus` are 404 on the CHR30A.
