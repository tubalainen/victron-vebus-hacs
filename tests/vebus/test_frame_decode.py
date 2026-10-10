"""Tests for frame encoding and decoding, using the examples from the MK2 spec."""

from __future__ import annotations

import pytest

from custom_components.victron_vebus.vebus.const import (
    AlarmState,
    ChargeState,
    DeviceState,
    Led,
    LedState,
    Mode,
    RamVar,
)
from custom_components.victron_vebus.vebus.decode import (
    DecodeError,
    decode_ac_info,
    decode_dc_info,
    decode_device_state,
    decode_master_multi_led,
    decode_ram_values,
    decode_ram_var_info,
    decode_setting_info,
    info_frame_phase,
)
from custom_components.victron_vebus.vebus.frame import (
    Frame,
    FrameDecoder,
    encode_frame,
)
from custom_components.victron_vebus.vebus.models import (
    FirmwareVersion,
    LedInfo,
    RamVarInfo,
    SettingInfo,
)

from .simulator import MP2_RAM_VAR_INFO


def _decode_one(hex_bytes: str) -> Frame:
    frames = FrameDecoder().feed(bytes.fromhex(hex_bytes))
    assert len(frames) == 1
    return frames[0]


@pytest.mark.parametrize(
    ("command", "data", "expected"),
    [
        ("V", [], "02 FF 56 A9"),
        ("A", [0x01, 0x00], "04 FF 41 01 00 BB"),
        ("S", [0x03, 0x3B, 0x01, 0x01, 0x80], "07 FF 53 03 3B 01 01 80 E7"),
        ("S", [0x03, 0xC0, 0x10, 0x01, 0x01], "07 FF 53 03 C0 10 01 01 D2"),
        ("F", [0x00], "03 FF 46 00 B8"),
        ("W", [0x05, 0x00, 0x00], "05 FF 57 05 00 00 A0"),
        ("W", [0x06, 0x00, 0x00], "05 FF 57 06 00 00 9F"),
        ("W", [0x30, 0x0D, 0x00], "05 FF 57 30 0D 00 68"),
        ("W", [0x36, 0x04, 0x00], "05 FF 57 36 04 00 6B"),
        ("W", [0x32, 0x81, 0x00], "05 FF 57 32 81 00 F2"),
        ("W", [0x34, 0xC8, 0x00], "05 FF 57 34 C8 00 A9"),
        ("R", [], "02 FF 52 AD"),
    ],
)
def test_encode_spec_examples(command: str, data: list[int], expected: str) -> None:
    """Encoded frames match the examples of the specification."""
    assert encode_frame(ord(command), data) == bytes.fromhex(expected)


def test_decoder_splits_and_resyncs() -> None:
    """Garbage and partial frames are skipped, split frames are reassembled."""
    decoder = FrameDecoder()
    version = bytes.fromhex("07 FF 56 28 DB 11 00 42 4E")
    stream = b"\x13\x37\x99" + version[:4]
    assert decoder.feed(stream) == []
    frames = decoder.feed(version[4:] + version)
    assert len(frames) == 2
    assert frames[0].command == ord("V")
    assert int.from_bytes(frames[0].data[:4], "little") == 1170216
    assert decoder.discarded_bytes == 3


def test_decoder_rejects_corrupted_frame() -> None:
    """A frame with a bad checksum is dropped and decoding continues."""
    decoder = FrameDecoder()
    bad = bytearray.fromhex("07 FF 56 28 DB 11 00 42 4E")
    bad[4] ^= 0x01
    good = bytes.fromhex("02 FF 56 A9")
    frames = decoder.feed(bytes(bad) + good)
    assert [f.command for f in frames] == [ord("V")]
    assert frames[0].data == b""
    assert decoder.checksum_errors >= 1


def test_ram_var_info_scaling() -> None:
    """Scale/offset semantics from section 7.3.11."""
    voltage = RamVarInfo.from_raw(32668, 0)
    assert voltage is not None
    assert voltage.scale == pytest.approx(0.01)
    assert not voltage.signed
    assert voltage.decode(bytes.fromhex("37 53")) == pytest.approx(213.03)

    current = RamVarInfo.from_raw(0x10000 - 32668, 0)
    assert current is not None and current.signed
    assert current.decode((-150 & 0xFFFF).to_bytes(2, "little")) == pytest.approx(-1.5)

    power = RamVarInfo.from_raw(0xFFFF, 0)  # Sc = -1
    assert power is not None and power.signed and power.scale == 1
    assert power.decode((-1234 & 0xFFFF).to_bytes(2, "little")) == -1234

    bit = RamVarInfo.from_raw(5, 0x8000)
    assert bit is not None and bit.bit == 4
    assert bit.decode(bytes([0x10, 0x00])) == 1.0
    assert bit.decode(bytes([0x0F, 0x00])) == 0.0

    assert RamVarInfo.from_raw(0, 0) is None


def test_setting_info_scaling() -> None:
    """Settings use a different scale convention than RAM variables."""
    absorption = SettingInfo.from_raw(0x10000 - 100, 0)
    assert absorption is not None
    assert absorption.decode(1440) == pytest.approx(14.40)
    minutes = SettingInfo.from_raw(15, 0)
    assert minutes is not None and minutes.decode(4) == 60
    assert SettingInfo.from_raw(0, 0) is None


def test_decode_dc_info_spec_example() -> None:
    """DC info frame example from appendix 2."""
    frame = _decode_one(
        "0F 20 7F 9A 81 79 0C 51 0A 00 00 00 00 00 00 88"
        + " "
        + _checksum_of("0F 20 7F 9A 81 79 0C 51 0A 00 00 00 00 00 00 88")
    )
    assert info_frame_phase(frame) == 0
    dc = decode_dc_info(frame, MP2_RAM_VAR_INFO)
    assert dc.voltage == pytest.approx(26.41)
    assert dc.inverter_current == 0
    assert dc.charger_current == 0
    assert dc.inverter_frequency == pytest.approx(50.0, abs=0.05)


def test_decode_ac_info_spec_example() -> None:
    """AC info frame example from appendix 2."""
    body = "0F 20 01 01 81 79 08 37 53 49 00 37 53 27 00 C3"
    frame = _decode_one(body + " " + _checksum_of(body))
    assert info_frame_phase(frame) == 1
    ac = decode_ac_info(frame, MP2_RAM_VAR_INFO)
    assert ac.phase == 1
    assert ac.phase_count == 1
    assert ac.mains_voltage == pytest.approx(213.03)
    assert ac.mains_current == pytest.approx(0.73)
    assert ac.inverter_voltage == pytest.approx(213.03)
    assert ac.inverter_current == pytest.approx(0.39)
    assert ac.mains_frequency == pytest.approx(50.08, abs=0.05)
    # 0x79 is not a valid state (old firmware), it must not raise.
    assert ac.device_state is None
    assert ac.raw_device_state == 0x79


def test_decode_ac_info_requires_scaling() -> None:
    """Missing RAM var info raises a DecodeError rather than crashing."""
    body = "0F 20 01 01 81 79 08 37 53 49 00 37 53 27 00 C3"
    frame = _decode_one(body + " " + _checksum_of(body))
    with pytest.raises(DecodeError):
        decode_ac_info(frame, {})


def test_decode_master_multi_led_spec_example() -> None:
    """MasterMultiLED example from section 6.2 (with a switch register added)."""
    body = "0D 41 10 09 00 00 00 3E 00 E8 03 F4 01 30"
    frame = _decode_one(body + " " + _checksum_of(body))
    config = decode_master_multi_led(frame)
    assert config.leds.on == Led.MAINS | Led.FLOAT
    assert config.minimum_current_limit == pytest.approx(6.2)
    assert config.maximum_current_limit == pytest.approx(100.0)
    assert config.actual_current_limit == pytest.approx(50.0)
    assert config.mode is Mode.ON
    assert config.front_switch_mode is Mode.OFF


def test_led_states() -> None:
    """Blinking takes precedence and maps to a warning."""
    leds = LedInfo(on=Led.MAINS | Led.OVERLOAD, blink=Led.LOW_BATTERY)
    assert leds.state(Led.MAINS) is LedState.ON
    assert leds.state(Led.FLOAT) is LedState.OFF
    assert leds.alarm(Led.OVERLOAD) is AlarmState.ALARM
    assert leds.alarm(Led.LOW_BATTERY) is AlarmState.WARNING
    assert leds.alarm(Led.TEMPERATURE) is AlarmState.OK


def test_winmon_reply_decoders() -> None:
    """Decoding of Winmon replies."""
    info = decode_ram_var_info(bytes.fromhex("8E 9C 7F 8F 00 00"))
    assert info is not None and info.scale == pytest.approx(0.01)
    assert decode_ram_var_info(bytes.fromhex("90 00 00")) is None

    setting = decode_setting_info(
        bytes.fromhex("89 9C FF 8A 00 00 8B A0 05 8C B0 04 8D 40 06")
    )
    assert setting is not None
    assert setting.decode(1440) == pytest.approx(14.4)
    assert setting.default == 1440
    assert decode_setting_info(bytes.fromhex("91 00 00")) is None

    values = decode_ram_values(bytes.fromhex("85 32 00 52 5A"), 2)
    assert values == [bytes.fromhex("32 00"), bytes.fromhex("52 5A")]
    assert decode_ram_values(bytes.fromhex("85 32 00"), 2) == [
        bytes.fromhex("32 00"),
        None,
    ]
    assert decode_ram_values(bytes.fromhex("90 00 00"), 1) == [None]

    state = decode_device_state(bytes.fromhex("94 09 03"))
    assert state.state is DeviceState.CHARGE
    assert state.charge_state is ChargeState.FLOAT
    unknown = decode_device_state(bytes.fromhex("94 2A 07"))
    assert unknown.state is None and unknown.charge_state is None


def test_firmware_version() -> None:
    """Firmware numbers are AABBCCC."""
    firmware = FirmwareVersion(2623506)
    assert firmware.version == 506
    assert firmware.product_id == "2623"
    assert str(firmware) == "506 (2623506)"


def test_ram_var_enum_matches_spec() -> None:
    """Sanity check of a few RAM variable ids."""
    assert RamVar.CHARGE_STATE == 13
    assert RamVar.OUTPUT_POWER == 16


def _checksum_of(hex_bytes: str) -> str:
    return f"{-sum(bytes.fromhex(hex_bytes)) & 0xFF:02X}"
