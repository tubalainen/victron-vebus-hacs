"""Decoders for frames received from the MK2/MK3 interface."""

from __future__ import annotations

from collections.abc import Mapping

from .const import (
    CHARGE_STATES,
    DEVICE_STATES,
    MARKER_INFO,
    MARKER_MASTER_MULTI_LED,
    PHASE_INFO_DC,
    PHASE_INFO_L1_1PH,
    PHASE_INFO_L1_4PH,
    PHASE_INFO_L4,
    DeviceState,
    Led,
    RamVar,
    SwitchRegister,
    WinmonReply,
)
from .frame import Frame
from .models import (
    AcInfo,
    ConfigInfo,
    DcInfo,
    DeviceStateInfo,
    LedInfo,
    RamVarInfo,
    SettingInfo,
)

INFO_FRAME_LENGTH = 14
# Raw period values that do not represent a valid measurement.
_INVALID_PERIODS = (0x00, 0xFF)

INFO_FRAME_VARS = (
    RamVar.U_MAINS_RMS,
    RamVar.I_MAINS_RMS,
    RamVar.U_INVERTER_RMS,
    RamVar.I_INVERTER_RMS,
    RamVar.U_BAT,
    RamVar.I_BAT,
    RamVar.INVERTER_PERIOD,
    RamVar.MAINS_PERIOD,
)


class DecodeError(ValueError):
    """Raised when a frame cannot be decoded."""


def device_state_from_raw(raw: int) -> DeviceState | None:
    """Map a raw device state to the enum, None if unknown."""
    if 0 <= raw < len(DEVICE_STATES):
        return DEVICE_STATES[raw]
    return None


def phase_from_info(phase_info: int) -> tuple[int, int | None] | None:
    """Return (phase number, phase count) for an AC phase info byte."""
    if PHASE_INFO_L1_1PH <= phase_info <= PHASE_INFO_L1_4PH:
        return 1, phase_info - PHASE_INFO_L1_1PH + 1
    if PHASE_INFO_L4 <= phase_info < PHASE_INFO_L1_1PH:
        return PHASE_INFO_L1_1PH + 1 - phase_info, None
    return None


def info_frame_phase(frame: Frame) -> int | None:
    """Return 0 for a DC info frame, 1..4 for an AC frame, None otherwise."""
    if frame.marker != MARKER_INFO or len(frame.payload) < INFO_FRAME_LENGTH:
        return None
    phase_info = frame.payload[4]
    if phase_info == PHASE_INFO_DC:
        return 0
    phase = phase_from_info(phase_info)
    return phase[0] if phase else None


def _frequency(info: RamVarInfo, raw: int) -> float | None:
    if raw in _INVALID_PERIODS:
        return None
    period = info.decode(bytes([raw]))
    if period <= 0:
        return None
    return round(10.0 / period, 2)


def _require(infos: Mapping[int, RamVarInfo | None], var: RamVar) -> RamVarInfo:
    info = infos.get(var)
    if info is None:
        raise DecodeError(f"Scaling of RAM variable {var.name} is unknown")
    return info


def decode_dc_info(frame: Frame, infos: Mapping[int, RamVarInfo | None]) -> DcInfo:
    """Decode a DC info frame."""
    d = frame.payload
    if info_frame_phase(frame) != 0:
        raise DecodeError("Not a DC info frame")
    u_bat = _require(infos, RamVar.U_BAT)
    i_bat = _require(infos, RamVar.I_BAT)
    period = _require(infos, RamVar.INVERTER_PERIOD)
    return DcInfo(
        voltage=u_bat.decode(d[5:7]),
        inverter_current=i_bat.decode(d[7:10]),
        charger_current=i_bat.decode(d[10:13]),
        inverter_frequency=_frequency(period, d[13]),
    )


def decode_ac_info(frame: Frame, infos: Mapping[int, RamVarInfo | None]) -> AcInfo:
    """Decode an AC info frame."""
    d = frame.payload
    if frame.marker != MARKER_INFO or len(d) < INFO_FRAME_LENGTH:
        raise DecodeError("Not an info frame")
    phase = phase_from_info(d[4])
    if phase is None:
        raise DecodeError("Not an AC info frame")
    # Some firmware versions report a factor of 0, treat that as a single unit.
    mains_factor = d[0] or 1
    inverter_factor = d[1] or 1
    return AcInfo(
        phase=phase[0],
        phase_count=phase[1],
        mains_voltage=_require(infos, RamVar.U_MAINS_RMS).decode(d[5:7]),
        mains_current=_require(infos, RamVar.I_MAINS_RMS).decode(d[7:9]) * mains_factor,
        inverter_voltage=_require(infos, RamVar.U_INVERTER_RMS).decode(d[9:11]),
        inverter_current=_require(infos, RamVar.I_INVERTER_RMS).decode(d[11:13])
        * inverter_factor,
        mains_frequency=_frequency(_require(infos, RamVar.MAINS_PERIOD), d[13]),
        device_state=device_state_from_raw(d[3]),
        raw_device_state=d[3],
    )


def decode_led(frame: Frame) -> LedInfo:
    """Decode the reply to the 'L' command."""
    d = frame.data
    if len(d) < 2:
        raise DecodeError("LED frame too short")
    return LedInfo(on=Led(d[0]), blink=Led(d[1]))


def decode_master_multi_led(frame: Frame) -> ConfigInfo:
    """Decode a MasterMultiLED frame (reply to 'F' 5)."""
    d = frame.payload
    if frame.marker != MARKER_MASTER_MULTI_LED or len(d) < 11:
        raise DecodeError("Not a MasterMultiLED frame")
    config = d[4]
    return ConfigInfo(
        leds=LedInfo(on=Led(d[1]), blink=Led(d[2])),
        status=d[3],
        last_active_ac_input=config & 0x03,
        panel_can_override_limit=bool(config & 0x04),
        dmc_dedicated=bool(config & 0x08),
        num_ac_inputs=(config >> 4) & 0x07,
        remote_panel_detected=bool(config & 0x80),
        minimum_current_limit=int.from_bytes(d[5:7], "little") / 10,
        maximum_current_limit=int.from_bytes(d[7:9], "little") / 10,
        actual_current_limit=int.from_bytes(d[9:11], "little") / 10,
        switch_register=SwitchRegister(d[11]) if len(d) > 11 else None,
    )


def decode_ram_var_info(reply: bytes) -> RamVarInfo | None:
    """Decode the reply to GetRAMVarInfo (data after the W/X/Y/Z letter)."""
    if not reply or reply[0] == WinmonReply.RAM_VAR_NOT_SUPPORTED:
        return None
    if reply[0] != WinmonReply.RAM_VAR_INFO_SCALE or len(reply) < 3:
        raise DecodeError(f"Unexpected RAM var info reply {reply.hex()}")
    raw_scale = int.from_bytes(reply[1:3], "little")
    raw_offset = 0
    if len(reply) >= 6 and reply[3] == WinmonReply.RAM_VAR_INFO_OFFSET:
        raw_offset = int.from_bytes(reply[4:6], "little")
    elif len(reply) >= 5:
        # Winmon mode 2 format: scale and offset without a second reply code.
        raw_offset = int.from_bytes(reply[3:5], "little")
    return RamVarInfo.from_raw(raw_scale, raw_offset)


def decode_setting_info(reply: bytes) -> SettingInfo | None:
    """Decode the reply to GetSettingInfo in Winmon mode 1 (3-byte records)."""
    if not reply or reply[0] in (
        WinmonReply.SETTING_NOT_SUPPORTED,
        WinmonReply.READ_SETTING_OK,
    ):
        return None
    values: dict[int, int] = {}
    for pos in range(0, len(reply) - 2, 3):
        values[reply[pos]] = int.from_bytes(reply[pos + 1 : pos + 3], "little")
    if WinmonReply.SETTING_INFO_SCALE not in values:
        raise DecodeError(f"Unexpected setting info reply {reply.hex()}")
    return SettingInfo.from_raw(
        values[WinmonReply.SETTING_INFO_SCALE],
        values.get(WinmonReply.SETTING_INFO_OFFSET, 0),
        values.get(WinmonReply.SETTING_INFO_DEFAULT),
        values.get(WinmonReply.SETTING_INFO_MINIMUM),
        values.get(WinmonReply.SETTING_INFO_MAXIMUM),
    )


def decode_ram_values(reply: bytes, count: int) -> list[bytes | None]:
    """Split a ReadRAMVar reply into raw 16-bit values."""
    if not reply or reply[0] != WinmonReply.READ_RAM_OK:
        return [None] * count
    values: list[bytes | None] = []
    for index in range(count):
        start = 1 + 2 * index
        chunk = reply[start : start + 2]
        values.append(bytes(chunk) if len(chunk) == 2 else None)
    return values


def decode_device_state(reply: bytes) -> DeviceStateInfo:
    """Decode the reply to GetSetDeviceState."""
    if len(reply) < 3 or reply[0] != WinmonReply.DEVICE_STATE:
        raise DecodeError(f"Unexpected device state reply {reply.hex()}")
    state = device_state_from_raw(reply[1])
    sub = reply[2]
    charge = None
    if state is DeviceState.CHARGE and 0 <= sub < len(CHARGE_STATES):
        charge = CHARGE_STATES[sub]
    return DeviceStateInfo(
        state=state, raw_state=reply[1], charge_state=charge, raw_sub_state=sub
    )
