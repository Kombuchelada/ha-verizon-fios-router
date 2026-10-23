"""Constants for the Fios router integration."""

DOMAIN = "fios_router"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30  # seconds

DEFAULT_HOST = "192.168.1.1"
DEFAULT_USERNAME = "admin"

# After a guest Wi-Fi write the radios take 10–15 s to settle.
WIFI_SETTLE_SECONDS = 15

# getPortConfig portType → port key, in display order, and display names.
PORT_TYPES = {0: "wan", 1: "lan_10g", 2: "lan1", 3: "lan2"}
PORT_NAMES = {"wan": "WAN", "lan_10g": "10G LAN", "lan1": "2.5G LAN1", "lan2": "2.5G LAN2"}

# tr181 interface index for each port. The router's own descriptions are off by
# one against its labels (interface 2 calls itself 2.5G_LAN2); the speeds match
# getPortConfig's portType, which is what this follows.
TR181_PORT_INTERFACE = {"wan": 4, "lan_10g": 1, "lan1": 2, "lan2": 3}
TR181_PORT_STATS = ("ErrorsReceived", "ErrorsSent", "DiscardPacketsReceived", "DiscardPacketsSent")
