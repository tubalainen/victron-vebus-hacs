"""Constants for the Victron VE.Bus integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "victron_vebus"
MANUFACTURER: Final = "Victron Energy"
DEFAULT_NAME: Final = "Victron VE.Bus"

CONF_ENABLE_CONTROLS: Final = "enable_controls"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 2
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 30

# The VE.Bus address of the device talked to. Multi-unit systems are future work.
DEFAULT_ADDRESS: Final = 0

# Raise a repair issue when the device has been unreachable for this long.
UNAVAILABLE_ISSUE_DELAY: Final = 300

# Time allowed for probing the interface and initialising the device.
PROBE_TIMEOUT: Final = 15
SETUP_TIMEOUT: Final = 30

SERVICE_SET_REMOTE_PANEL_STATE: Final = "set_remote_panel_state"
ATTR_MODE: Final = "mode"
ATTR_CURRENT_LIMIT: Final = "current_limit"
