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
# Show active alarms (overload, low battery, temperature) as repair issues.
CONF_ALARM_ISSUES: Final = "alarm_issues"

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
# Link quality: share of answered requests over this window (seconds). A
# repair issue is raised below the first rate (with enough requests to be
# sure) and removed again above the second.
LINK_QUALITY_WINDOW: Final = 300
LINK_ISSUE_MIN_REQUESTS: Final = 100
LINK_ISSUE_BELOW: Final = 90.0
LINK_ISSUE_CLEAR: Final = 95.0
# "Allow critical changes" turns itself off again after this many seconds.
CRITICAL_UNLOCK_DURATION: Final = 600
# Power samples further apart than this (or 5 polling intervals, if longer)
# are not integrated into the energy sensors.
ENERGY_MIN_GAP: Final = 60

# Time allowed for probing the interface and initialising the device.
PROBE_TIMEOUT: Final = 15
SETUP_TIMEOUT: Final = 30

SERVICE_SET_REMOTE_PANEL_STATE: Final = "set_remote_panel_state"
SERVICE_SET_MODE: Final = "set_mode"
SERVICE_SET_SETTING: Final = "set_setting"
SERVICE_SET_FEATURE: Final = "set_feature"
SERVICE_EQUALISE: Final = "equalise"
SERVICE_RESTART: Final = "restart"
SERVICE_SAVE_SETTINGS: Final = "save_settings"
ATTR_MODE: Final = "mode"
ATTR_CURRENT_LIMIT: Final = "current_limit"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_ACKNOWLEDGE_RISK: Final = "acknowledge_risk"
ATTR_SETTING: Final = "setting"
ATTR_VALUE: Final = "value"
ATTR_FEATURE: Final = "feature"
ATTR_ENABLED: Final = "enabled"
ATTR_PERSISTENT: Final = "persistent"

# Sent when setting ranges read after start-up allow more entities.
SIGNAL_RANGES: Final = "victron_vebus_ranges_{}"
# Missing setting ranges are read again after this many seconds, then at
# RANGES_RETRY_INTERVAL.
RANGES_FIRST_RETRY: Final = 60
RANGES_RETRY_INTERVAL: Final = 300
