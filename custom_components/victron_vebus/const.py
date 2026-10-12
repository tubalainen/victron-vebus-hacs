"""Constants for the Victron VE.Bus integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "victron_vebus"
MANUFACTURER: Final = "Victron Energy"
DEFAULT_NAME: Final = "Victron VE.Bus"

CONF_ENABLE_CONTROLS: Final = "enable_controls"
# How the integration may be used, chosen when it is added or reconfigured.
CONF_USAGE: Final = "usage"
USAGE_MONITOR: Final = "monitor"
USAGE_CONTROL: Final = "monitor_and_control"
CONF_ACKNOWLEDGE: Final = "acknowledge"
# Battery Operational Limits: once sent, the Multi stops charging and inverting
# if they are not refreshed within 5 minutes, until it is restarted.
CONF_ENABLE_BATTERY_LIMITS: Final = "enable_battery_limits"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 2
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 30

# The VE.Bus address of the system's master (the L1 phase master).
DEFAULT_ADDRESS: Final = 0

# The ESS setpoint is written again at least this often (seconds) while
# external control is on; the assistant falls back without updates.
ESS_REFRESH_INTERVAL: Final = 10
ESS_SETPOINT_LIMIT: Final = 30000

# Raise a repair issue when the device has been unreachable for this long.
UNAVAILABLE_ISSUE_DELAY: Final = 300

# Time allowed for probing the interface and initialising the device.
PROBE_TIMEOUT: Final = 15
SETUP_TIMEOUT: Final = 30

SERVICE_SET_REMOTE_PANEL_STATE: Final = "set_remote_panel_state"
ATTR_MODE: Final = "mode"
ATTR_CURRENT_LIMIT: Final = "current_limit"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_ACKNOWLEDGE_RISK: Final = "acknowledge_risk"
