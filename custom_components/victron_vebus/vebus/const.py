"""Constants of the VE.Bus / MK2 / MK3 protocol.

Reference: Victron Energy, "Interfacing with VE.Bus products - MK2 Protocol",
revision 3.14. Section numbers in comments refer to that document.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag, StrEnum

BAUD_RATE = 2400

# Frames longer than this are treated as noise while resynchronising.
MAX_FRAME_LENGTH = 0x30

# Marker byte that follows the length byte of frames originating from the MK2/MK3.
MARKER_MK = 0xFF
# Marker bytes of VE.Bus frames forwarded verbatim by the interface.
MARKER_INFO = 0x20
MARKER_MASTER_MULTI_LED = 0x41
MARKER_PANEL = 0x40
MARKER_BOOT = 0x00

# Interface commands (ASCII letters).
CMD_ADDRESS = ord("A")
CMD_INTERFACE_STATE = ord("D")  # undocumented, returns the remote panel state
CMD_SPECIAL = ord("F")
CMD_INTERFACE = ord("H")  # MK3 only, undocumented
CMD_LED = ord("L")
CMD_RESET = ord("R")
CMD_STATE = ord("S")
CMD_VERSION = ord("V")
CMD_WINMON = ord("W")
# Same as 'W' but echoed back in the reply, used to match replies to requests.
CMD_WINMON_TAGGED = (ord("X"), ord("Y"), ord("Z"))

# 'F' frame types (section 6).
F_DC_INFO = 0
F_AC_L1 = 1
F_AC_L2 = 2
F_AC_L3 = 3
F_AC_L4 = 4
F_MASTER_MULTI_LED = 5

# Phase info byte of an info frame (section 6.1).
PHASE_INFO_DC = 0x0C
PHASE_INFO_L4 = 0x05
PHASE_INFO_L3 = 0x06
PHASE_INFO_L2 = 0x07
PHASE_INFO_L1_1PH = 0x08
PHASE_INFO_L1_2PH = 0x09
PHASE_INFO_L1_3PH = 0x0A
PHASE_INFO_L1_4PH = 0x0B

# 'S' command flags (section 5).
S_FLAG_AUTO_SEND = 0x01
S_FLAG_LED_APPEND = 0x02
S_FLAG_DONT_SEND = 0x10
S_FLAG_FORWARD_PANEL = 0x40
S_FLAG_ABSOLUTE_LIMIT = 0x80
S_FLAGS_EXT0_WINMON_MODE1 = 0x01
# Limit values >= 0x8000 mean "leave the set point as it is".
S_LIMIT_IGNORE = 0x8000

# 'S' data after the three state bytes read with 'D' that switches the
# interface to Winmon mode 2 (long frames) without changing the remote panel
# state: absolute limit format, forward panel frames, do not send the panel
# state, FlagsExt0 0x06 (bit 0 clear = long frames). This is what VEConfigure
# sends; taken from j9brown/victron-mk3-hass PR #15, which was tested on a
# MultiPlus-II.
S_LONG_FRAMES_TAIL = (0x01, 0xD0, 0x3E, 0x06)

# 'A' command action.
A_ACTION_SET = 0x01

# WriteViaID (0x37) flags: bit 0 = setting (not RAM var), bit 1 = RAM only
# (do not write the EEPROM, needs firmware 412 or later).
WRITE_VIA_ID_SETTING = 0x01
WRITE_VIA_ID_RAM_ONLY = 0x02

# Minimum device firmware revisions (the CCC part of AABBCCC).
MIN_FIRMWARE_FORCE_CHARGE = 400
MIN_FIRMWARE_RAM_ONLY_WRITE = 412
MIN_FIRMWARE_SOLAR_WIND_PRIORITY = 506


class WinmonCommand(IntEnum):
    """Winmon sub commands (section 7.3)."""

    SEND_SOFTWARE_VERSION_PART0 = 0x05
    SEND_SOFTWARE_VERSION_PART1 = 0x06
    GET_SET_DEVICE_STATE = 0x0E
    READ_RAM_VAR = 0x30
    READ_SETTING = 0x31
    WRITE_RAM_VAR = 0x32
    WRITE_SETTING = 0x33
    WRITE_DATA = 0x34
    GET_SETTING_INFO = 0x35
    GET_RAM_VAR_INFO = 0x36
    WRITE_VIA_ID = 0x37
    READ_SNAPSHOT = 0x38


class WinmonReply(IntEnum):
    """Winmon reply codes (section 7.3)."""

    UNKNOWN_COMMAND = 0x80
    SOFTWARE_VERSION_PART0 = 0x82
    SOFTWARE_VERSION_PART1 = 0x83
    READ_RAM_OK = 0x85
    READ_SETTING_OK = 0x86
    WRITE_RAM_OK = 0x87
    WRITE_SETTING_OK = 0x88
    SETTING_INFO_SCALE = 0x89
    SETTING_INFO_OFFSET = 0x8A
    SETTING_INFO_DEFAULT = 0x8B
    SETTING_INFO_MINIMUM = 0x8C
    SETTING_INFO_MAXIMUM = 0x8D
    RAM_VAR_INFO_SCALE = 0x8E
    RAM_VAR_INFO_OFFSET = 0x8F
    RAM_VAR_NOT_SUPPORTED = 0x90
    SETTING_NOT_SUPPORTED = 0x91
    DEVICE_STATE = 0x94
    SNAPSHOT = 0x99
    ACCESS_LEVEL_REQUIRED = 0x9B


class RamVar(IntEnum):
    """RAM variable identifiers (section 7.3.13.1)."""

    U_MAINS_RMS = 0
    I_MAINS_RMS = 1
    U_INVERTER_RMS = 2
    I_INVERTER_RMS = 3
    U_BAT = 4
    I_BAT = 5
    U_BAT_RMS = 6  # battery ripple
    INVERTER_PERIOD = 7
    MAINS_PERIOD = 8
    SIGNED_AC_LOAD_CURRENT = 9
    VIRTUAL_SWITCH_POSITION = 10
    IGNORE_AC_INPUT_STATE = 11
    MULTI_FUNCTIONAL_RELAY_STATE = 12
    CHARGE_STATE = 13  # battery monitor state of charge
    INVERTER_POWER_1 = 14  # filtered, DC side
    INVERTER_POWER_2 = 15  # filtered, AC input side
    OUTPUT_POWER = 16  # filtered, AC output


class Setting(IntEnum):
    """Setting identifiers (section 7.3.13.2)."""

    FLAGS_0 = 0
    FLAGS_1 = 1
    U_BAT_ABSORPTION = 2
    U_BAT_FLOAT = 3
    I_BAT_BULK = 4
    U_INV_SETPOINT = 5
    I_MAINS_LIMIT_AC1 = 6
    REPEATED_ABSORPTION_TIME = 7
    REPEATED_ABSORPTION_INTERVAL = 8
    ABSORPTION_DURATION = 9
    CHARGE_CHARACTERISTIC = 10
    U_BAT_LOW_LIMIT = 11
    U_BAT_LOW_HYSTERESIS = 12
    NUMBER_OF_SLAVES = 13
    THREE_PHASE_SETTING = 14
    I_MAINS_LIMIT_AC2 = 49
    FLAGS_2 = 60
    BATTERY_CAPACITY = 64
    SOC_WHEN_BULK_FINISHED = 65
    SOLAR_WIND_PRIORITY_VOLTAGE = 88


class SettingFlag(IntFlag):
    """Bits of the flag settings that can be changed (section 7.3.13.3)."""

    # Setting 0 (Flags0).
    DISABLE_WAVE_CHECK = 1 << 3  # set = UPS function off
    ASSIST_ENABLED = 1 << 5  # PowerAssist
    WEAK_AC_INPUT = 1 << 14
    # Setting 60 (Flags2), from xcellsior/ve-bus-programming and PR #15.
    SOLAR_WIND_PRIORITY = 1 << 9


class ForceChargeState(IntEnum):
    """Request values of GetSetDeviceState (0x0E) that force a charge state."""

    EQUALISE = 1
    ABSORPTION = 2
    FLOAT = 3


class SwitchState(IntEnum):
    """Remote switch state used by the 'S' command (section 5)."""

    CHARGER_ONLY = 1
    INVERTER_ONLY = 2
    ON = 3
    OFF = 4


class Mode(StrEnum):
    """Operating mode of the remote panel / front switch."""

    OFF = "off"
    ON = "on"
    CHARGER_ONLY = "charger_only"
    INVERTER_ONLY = "inverter_only"


MODE_TO_SWITCH_STATE: dict[Mode, SwitchState] = {
    Mode.OFF: SwitchState.OFF,
    Mode.ON: SwitchState.ON,
    Mode.CHARGER_ONLY: SwitchState.CHARGER_ONLY,
    Mode.INVERTER_ONLY: SwitchState.INVERTER_ONLY,
}


class DeviceState(StrEnum):
    """VE.Bus device state (AC info frame byte 3 and Winmon 0x94)."""

    DOWN = "down"
    STARTUP = "startup"
    OFF = "off"
    SLAVE = "slave"
    INVERT_FULL = "invert_full"
    INVERT_HALF = "invert_half"
    INVERT_AES = "invert_aes"
    POWER_ASSIST = "power_assist"
    BYPASS = "bypass"
    CHARGE = "charge"


DEVICE_STATES: tuple[DeviceState, ...] = tuple(DeviceState)


class ChargeState(StrEnum):
    """Charger sub state reported by Winmon 0x94 when the device state is charge."""

    INIT = "init"
    BULK = "bulk"
    ABSORPTION = "absorption"
    FLOAT = "float"
    STORAGE = "storage"
    REPEATED_ABSORPTION = "repeated_absorption"
    FORCED_ABSORPTION = "forced_absorption"
    EQUALISE = "equalise"
    BULK_STOPPED = "bulk_stopped"


CHARGE_STATES: tuple[ChargeState, ...] = tuple(ChargeState)


class Led(IntFlag):
    """Front panel LEDs ('L' command and MasterMultiLED frame)."""

    MAINS = 0x01
    ABSORPTION = 0x02
    BULK = 0x04
    FLOAT = 0x08
    INVERTER = 0x10
    OVERLOAD = 0x20
    LOW_BATTERY = 0x40
    TEMPERATURE = 0x80


class LedState(StrEnum):
    """State of a single LED."""

    OFF = "off"
    ON = "on"
    BLINK = "blink"


class AlarmState(StrEnum):
    """Alarm level derived from an alarm LED."""

    OK = "ok"
    WARNING = "warning"
    ALARM = "alarm"


class InterfaceFlag(IntFlag):
    """MK3 interface flags set by the undocumented 'H' command."""

    PANEL_DETECT = 0x01
    STANDBY = 0x02
    UNKNOWN_04 = 0x04


class SwitchRegister(IntFlag):
    """Switch register of the MasterMultiLED frame (section 6.2)."""

    DIRECT_REMOTE_SWITCH_CHARGE = 0x01
    DIRECT_REMOTE_SWITCH_INVERT = 0x02
    FRONT_SWITCH_UP = 0x04
    FRONT_SWITCH_DOWN = 0x08
    SWITCH_CHARGE = 0x10
    SWITCH_INVERT = 0x20
    ONBOARD_REMOTE_INVERT_SWITCH = 0x40
    REMOTE_GENERATOR_SELECTED = 0x80
