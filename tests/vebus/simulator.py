"""A scripted fake MK3 interface with a MultiPlus-II behind it, for tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from custom_components.victron_vebus.vebus.frame import (
    Frame,
    FrameDecoder,
    checksum,
    encode_frame,
)
from custom_components.victron_vebus.vebus.models import RamVarInfo

# Raw (scale, offset) pairs as reported by a MultiPlus-II.
MP2_RAM_VAR_RAW: dict[int, tuple[int, int]] = {
    0: (32668, 0),
    1: (0x10000 - 32668, 0),
    2: (32668, 0),
    3: (32668, 0),  # reported unsigned although the values are signed
    4: (32668, 0),
    5: (0x10000 - 32758, 0),
    6: (32668, 0),
    7: (30807, 256),
    8: (31791, 0),
    11: (5, 0x8000),
    12: (6, 0x8000),
    13: (32568, 0),
    14: (0xFFFF, 0),
    15: (0xFFFF, 0),
    16: (0xFFFF, 0),
}
MP2_RAM_VAR_INFO: dict[int, RamVarInfo | None] = {
    var: RamVarInfo.from_raw(*raw) for var, raw in MP2_RAM_VAR_RAW.items()
}
# Raw (scale, offset, default, min, max).
MP2_SETTING_INFO_RAW: dict[int, tuple[int, int, int, int, int]] = {
    0: (1, 0, 0x89B0, 0, 0x6FFC),  # flags: maximum = mask of supported bits
    2: (0x10000 - 100, 0, 2880, 2400, 3200),
    3: (0x10000 - 100, 0, 2760, 2400, 3200),
    4: (1, 0, 70, 0, 70),
    6: (0x10000 - 10, 0, 320, 10, 500),
    49: (0x10000 - 10, 0, 160, 10, 500),  # Quattro only
    60: (1, 0, 0x0210, 0, 0x0FFF),
    64: (1, 0, 0, 0, 9999),
    88: (0x10000 - 100, 0, 2760, 2400, 3200),
}


def _u16(value: int) -> list[int]:
    value &= 0xFFFF
    return [value & 0xFF, value >> 8]


@dataclass
class SimulatedMultiPlus:
    """State of the simulated device. Raw values use the scaling above."""

    mk3_version: int = 1170216
    firmware: int = 2623506
    phases: tuple[int, ...] = (1,)
    asleep: bool = False
    supports_h: bool = True
    device_state: int = 9
    charge_sub_state: int = 3
    leds_on: int = 0x09
    leds_blink: int = 0x00
    switch_register: int = 0x30 | 0x03 | 0x04
    current_limit: int = 160  # 0.1 A
    min_limit: int = 30
    max_limit: int = 500
    interface_flags: int = 0x05
    ram: dict[int, int] = field(
        default_factory=lambda: {
            0: 23012,
            1: 550,
            2: 23012,
            3: (-120) & 0xFFFF,
            4: 2641,
            5: 0,
            6: 5,
            7: 0x88,  # inverter period
            8: 195,  # mains period
            9: 0,
            11: 0x00,
            12: 0x20,
            13: 170,
            14: 300,
            15: (-350) & 0xFFFF,
            16: 50,
        }
    )
    settings: dict[int, int] = field(
        default_factory=lambda: {
            0: 0x89B0,  # PowerAssist on, UPS on, weak AC off
            2: 2880,
            3: 2760,
            4: 70,
            6: 160,
            60: 0x0210,  # solar & wind priority on
            64: 200,
            88: 2760,
        }
    )
    # Winmon mode 2 (long frames) selected with the 'S' command.
    long_frames: bool = False
    # Cut mode 1 setting info replies after three records, as some devices do.
    truncate_setting_info: bool = False
    ack_writes: bool = True
    access_level_settings: set[int] = field(default_factory=set)
    # (flags, setting, value) of every WriteViaID.
    writes: list[tuple[int, int, int]] = field(default_factory=list)
    forced: list[int] = field(default_factory=list)
    # VE.Bus address selected with 'A'; other units by address with their
    # RAM values (address 0 is this device).
    address: int = 0
    units: dict[int, dict[int, int]] = field(default_factory=dict)
    # False for devices that do not answer the 'F' info frames (Compact).
    info_frames: bool = True
    # A Quattro has a second AC input (setting 49, two inputs in 'F' 5).
    quattro: bool = False

    def __post_init__(self) -> None:
        """Add the Quattro AC input 2 limit."""
        if self.quattro:
            self.settings[49] = 120

    dc_charger_current: int = 123  # 0.1 A
    dc_inverter_current: int = 0
    received: list[bytes] = field(default_factory=list)

    def handle(self, frame: Frame) -> list[bytes]:
        """Return the reply frames for a request."""
        self.received.append(frame.to_bytes())
        if self.asleep or not frame.is_mk:
            return []
        cmd = chr(frame.command or 0)
        data = frame.data
        if cmd == "V":
            return [
                encode_frame(ord("V"), [*self.mk3_version.to_bytes(4, "little"), 0x42])
            ]
        if cmd == "A":
            if data[0] & 0x01:
                self.address = data[1]
            return [encode_frame(ord("A"), [data[0], self.address])]
        if cmd == "D":
            return [encode_frame(ord("D"), [3, *_u16(self.current_limit), 0, 0])]
        if cmd == "S":
            flags = data[4] if len(data) > 4 else 0
            if data and data[0] and not flags & 0x10:
                self._apply_switch(data)
            if len(data) > 6:
                self.long_frames = not data[6] & 0x01
            return [encode_frame(ord("S"))]
        if cmd == "H":
            if not self.supports_h:
                return []
            if data:
                self.interface_flags = data[0]
            return [encode_frame(ord("H"), [self.interface_flags])]
        if cmd == "L":
            return [encode_frame(ord("L"), [self.leds_on, self.leds_blink])]
        if cmd == "F":
            return self._special(data[0])
        if cmd in "WXYZ":
            if self.address != 0 and self.address not in self.units:
                return []  # nobody at this address
            reply = self._winmon(data)
            return [encode_frame(frame.command or 0, reply)] if reply else []
        return []

    def _apply_switch(self, data: bytes) -> None:
        charge, invert = {1: (1, 0), 2: (0, 1), 3: (1, 1), 4: (0, 0)}[data[0]]
        reg = self.switch_register & ~0x33
        reg |= 0x01 * charge | 0x02 * invert | 0x10 * charge | 0x20 * invert
        self.switch_register = reg
        limit = data[1] | data[2] << 8
        if limit < 0x8000:
            self.current_limit = max(self.min_limit, min(limit, self.max_limit))

    def _special(self, kind: int) -> list[bytes]:
        if not self.info_frames and kind <= 4:
            return []
        if kind == 0:
            d = [0, 0, 0, 0, 0x0C, *_u16(self.ram[4])]
            d += list(self.dc_inverter_current.to_bytes(3, "little"))
            d += list(self.dc_charger_current.to_bytes(3, "little"))
            d.append(0x88)
            return [_raw_frame(0x20, d)]
        if 1 <= kind <= 4:
            if kind not in self.phases:
                return []
            phase_info = 0x08 + len(self.phases) - 1 if kind == 1 else 0x08 + 1 - kind
            d = [1, 1, 0, self.device_state, phase_info]
            d += (
                _u16(self.ram[0])
                + _u16(self.ram[1])
                + _u16(self.ram[2])
                + _u16(self.ram[3])
            )
            d.append(0xC3)
            return [_raw_frame(0x20, d)]
        if kind == 5:
            inputs = 0x20 if self.quattro else 0x10
            d = [0x10, self.leds_on, self.leds_blink, 0, inputs]
            d += _u16(self.min_limit) + _u16(self.max_limit) + _u16(self.current_limit)
            d.append(self.switch_register)
            return [_raw_frame(0x41, d)]
        return []

    def _winmon(self, data: bytes) -> list[int]:
        cmd = data[0]
        args = data[1:]
        if cmd == 0x05:
            return [0x82, *_u16(self.firmware)]
        if cmd == 0x06:
            return [0x83, *_u16(self.firmware >> 16)]
        if cmd == 0x0E:
            if args and args[0]:
                self.forced.append(args[0])
                self.device_state = 9
                self.charge_sub_state = {1: 7, 2: 6, 3: 3}[args[0]]
            return [0x94, self.device_state, self.charge_sub_state]
        if cmd == 0x37:
            if not self.long_frames:
                return [0x80, 0x00, 0x00]
            flags, setting, value = args[0], args[1], args[2] | args[3] << 8
            if setting in self.access_level_settings:
                return [0x9B, 0x00, 0x00]
            if setting not in self.settings:
                return [0x80, 0x02, 0x00]
            self.writes.append((flags, setting, value))
            self.settings[setting] = value
            return [0x88, 0x00, 0x00] if self.ack_writes else []
        if cmd == 0x30:
            ram = self.units[self.address] if self.address else self.ram
            values: list[int] = []
            for var in args:
                if var not in ram:
                    return [0x90, 0, 0]
                values += _u16(ram[var])
            return [0x85, *values]
        if cmd == 0x36:
            var = args[0] | args[1] << 8
            if var not in MP2_RAM_VAR_RAW:
                return [0x90, 0, 0]
            scale, offset = MP2_RAM_VAR_RAW[var]
            return [0x8E, *_u16(scale), 0x8F, *_u16(offset)]
        if cmd == 0x35:
            setting = args[0] | args[1] << 8
            if setting not in MP2_SETTING_INFO_RAW or (
                setting == 49 and not self.quattro
            ):
                return [0x91, 0, 0]
            sc, off, default, minimum, maximum = MP2_SETTING_INFO_RAW[setting]
            if self.long_frames:
                return [
                    0x89, *_u16(sc), *_u16(off), *_u16(default),
                    *_u16(minimum), *_u16(maximum), 0x00,
                ]  # fmt: skip
            if self.truncate_setting_info:
                return [0x89, *_u16(sc), 0x8A, *_u16(off), 0x8B, *_u16(default)]
            return [
                0x89, *_u16(sc), 0x8A, *_u16(off), 0x8B, *_u16(default),
                0x8C, *_u16(minimum), 0x8D, *_u16(maximum),
            ]  # fmt: skip
        if cmd == 0x31:
            setting = args[0] | args[1] << 8
            if setting not in self.settings:
                return [0x91, 0, 0]
            return [0x86, *_u16(self.settings[setting])]
        return [0x80, 0, 0]


def _raw_frame(marker: int, data: list[int]) -> bytes:
    head = bytes([len(data) + 1, marker, *data])
    return head + bytes([checksum(head)])


class FakeWriter:
    """Stream writer that answers requests through the simulated device."""

    def __init__(
        self, device: SimulatedMultiPlus, reader: asyncio.StreamReader
    ) -> None:
        self._device = device
        self._reader = reader
        self._decoder = FrameDecoder()
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise OSError("closed")
        loop = asyncio.get_running_loop()
        for frame in self._decoder.feed(data):
            for reply in self._device.handle(frame):
                loop.call_soon(self._feed, reply)

    def _feed(self, data: bytes) -> None:
        if not self._closed and not self._reader.at_eof():
            self._reader.feed_data(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self._closed = True

    def is_closing(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        return None

    def disconnect(self) -> None:
        """Simulate unplugging the interface."""
        self._closed = True
        self._reader.feed_eof()


class FakeMK3:
    """Opener for VeBusClient that connects to a simulated device."""

    def __init__(self, device: SimulatedMultiPlus | None = None) -> None:
        self.device = device or SimulatedMultiPlus()
        self.writers: list[FakeWriter] = []
        self.fail_open = False

    async def __call__(self, url: str) -> tuple[asyncio.StreamReader, FakeWriter]:
        if self.fail_open:
            raise FileNotFoundError(url)
        reader = asyncio.StreamReader()
        writer = FakeWriter(self.device, reader)
        self.writers.append(writer)
        return reader, writer
