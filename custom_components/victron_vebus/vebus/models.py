"""Data models for values read from a VE.Bus device."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from .const import (
    AlarmState,
    ChargeState,
    DeviceState,
    Led,
    LedState,
    Mode,
    SwitchRegister,
)


@dataclass(frozen=True, slots=True)
class RamVarInfo:
    """Scaling information of a RAM variable (Winmon 0x36, section 7.3.11)."""

    scale: float
    offset: int
    signed: bool
    bit: int | None = None

    @classmethod
    def from_raw(cls, raw_scale: int, raw_offset: int) -> RamVarInfo | None:
        """Build from the raw 16-bit values, return None if the var is unsupported."""
        sc = raw_scale - 0x10000 if raw_scale >= 0x8000 else raw_scale
        if sc == 0:
            return None
        if raw_offset == 0x8000:
            # Single bit variable: scale holds the bit number + 1.
            return cls(scale=1.0, offset=0, signed=False, bit=abs(sc) - 1)
        signed = sc < 0
        magnitude = abs(sc)
        scale = 1.0 / (0x8000 - magnitude) if magnitude >= 0x4000 else float(magnitude)
        offset = raw_offset - 0x10000 if raw_offset >= 0x8000 else raw_offset
        return cls(scale=scale, offset=offset, signed=signed)

    def decode(self, raw: bytes | bytearray) -> float:
        """Decode a little-endian raw value of 1 to 4 bytes."""
        value = int.from_bytes(raw, "little", signed=False)
        if self.bit is not None:
            return float((value >> self.bit) & 1)
        if self.signed:
            bits = 8 * len(raw)
            if value >= 1 << (bits - 1):
                value -= 1 << bits
        return self.scale * (value + self.offset)

    def decode_int(self, value: int) -> float:
        """Decode a 16-bit value."""
        return self.decode((value & 0xFFFF).to_bytes(2, "little"))


@dataclass(frozen=True, slots=True)
class SettingInfo:
    """Scaling information of a setting (Winmon 0x35, section 7.3.12)."""

    scale: float
    offset: int
    default: int | None = None
    minimum: int | None = None
    maximum: int | None = None

    @classmethod
    def from_raw(
        cls,
        raw_scale: int,
        raw_offset: int,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> SettingInfo | None:
        """Build from raw values, return None if the setting is unsupported."""
        sc = raw_scale - 0x10000 if raw_scale >= 0x8000 else raw_scale
        if sc == 0:
            return None
        scale = float(sc) if sc > 0 else 1.0 / -sc
        offset = raw_offset - 0x10000 if raw_offset >= 0x8000 else raw_offset
        return cls(scale, offset, default, minimum, maximum)

    def decode(self, raw: int) -> float:
        """Decode a raw unsigned 16-bit setting value."""
        return self.scale * (raw + self.offset)


@dataclass(frozen=True, slots=True)
class FirmwareVersion:
    """VE.Bus device firmware version, formatted as AABBCCC."""

    raw: int

    @property
    def version(self) -> int:
        """Return the firmware revision (CCC)."""
        return self.raw % 1000

    @property
    def product_id(self) -> str:
        """Return the product type and model digits (AABB)."""
        return f"{self.raw // 1000:04d}"

    def __str__(self) -> str:
        """Return a readable representation."""
        return f"{self.version} ({self.raw})"


@dataclass(frozen=True, slots=True)
class DcInfo:
    """DC info frame (section 6.1)."""

    voltage: float
    inverter_current: float
    charger_current: float
    inverter_frequency: float | None

    @property
    def battery_current(self) -> float:
        """Net battery current, positive when charging."""
        return self.charger_current - self.inverter_current


@dataclass(frozen=True, slots=True)
class AcInfo:
    """AC info frame for one phase (section 6.1)."""

    phase: int
    phase_count: int | None
    mains_voltage: float
    mains_current: float
    inverter_voltage: float
    inverter_current: float
    mains_frequency: float | None
    device_state: DeviceState | None
    raw_device_state: int


@dataclass(frozen=True, slots=True)
class LedInfo:
    """LED status (section 4)."""

    on: Led
    blink: Led

    def state(self, led: Led) -> LedState:
        """Return the state of a single LED."""
        if self.blink & led:
            return LedState.BLINK
        if self.on & led:
            return LedState.ON
        return LedState.OFF

    def alarm(self, led: Led) -> AlarmState:
        """Return the alarm state of an alarm LED (blinking means pre-alarm)."""
        state = self.state(led)
        if state is LedState.ON:
            return AlarmState.ALARM
        if state is LedState.BLINK:
            return AlarmState.WARNING
        return AlarmState.OK


def _mode_from_bits(charge: bool, invert: bool) -> Mode:
    if charge and invert:
        return Mode.ON
    if charge:
        return Mode.CHARGER_ONLY
    if invert:
        return Mode.INVERTER_ONLY
    return Mode.OFF


@dataclass(frozen=True, slots=True)
class ConfigInfo:
    """MasterMultiLED frame: current limits and switch state (section 6.2)."""

    leds: LedInfo
    status: int
    last_active_ac_input: int
    panel_can_override_limit: bool
    dmc_dedicated: bool
    num_ac_inputs: int
    remote_panel_detected: bool
    minimum_current_limit: float
    maximum_current_limit: float
    actual_current_limit: float
    switch_register: SwitchRegister | None

    @property
    def mode(self) -> Mode | None:
        """Return the mode actually in effect."""
        if self.switch_register is None:
            return None
        reg = self.switch_register
        return _mode_from_bits(
            bool(reg & SwitchRegister.SWITCH_CHARGE),
            bool(reg & SwitchRegister.SWITCH_INVERT),
        )

    @property
    def remote_mode(self) -> Mode | None:
        """Return the mode requested by this remote interface."""
        if self.switch_register is None:
            return None
        reg = self.switch_register
        return _mode_from_bits(
            bool(reg & SwitchRegister.DIRECT_REMOTE_SWITCH_CHARGE),
            bool(reg & SwitchRegister.DIRECT_REMOTE_SWITCH_INVERT),
        )

    @property
    def front_switch_mode(self) -> Mode | None:
        """Return the position of the front panel switch."""
        if self.switch_register is None:
            return None
        reg = self.switch_register
        if reg & SwitchRegister.FRONT_SWITCH_UP:
            return Mode.ON
        if reg & SwitchRegister.FRONT_SWITCH_DOWN:
            return Mode.CHARGER_ONLY
        return Mode.OFF


@dataclass(frozen=True, slots=True)
class DeviceStateInfo:
    """Winmon device state reply (0x94)."""

    state: DeviceState | None
    raw_state: int
    charge_state: ChargeState | None
    raw_sub_state: int


@dataclass(slots=True)
class VeBusState:
    """Snapshot of everything read from a VE.Bus device."""

    mk3_version: int | None = None
    firmware: FirmwareVersion | None = None
    phases: tuple[int, ...] = (1,)
    dc: DcInfo | None = None
    ac: dict[int, AcInfo] = field(default_factory=dict)
    leds: LedInfo | None = None
    config: ConfigInfo | None = None
    device_state: DeviceStateInfo | None = None
    ram: dict[int, float | None] = field(default_factory=dict)
    settings: dict[int, float | None] = field(default_factory=dict)
    interface_flags: int | None = None
    timestamp: float = field(default_factory=time.monotonic)

    def ram_value(self, var: int) -> float | None:
        """Return a decoded RAM variable."""
        return self.ram.get(var)

    def setting_value(self, setting: int) -> float | None:
        """Return a decoded setting."""
        return self.settings.get(setting)
