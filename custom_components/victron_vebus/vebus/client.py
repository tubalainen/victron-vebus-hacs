"""Asynchronous client for VE.Bus devices connected through an MK2/MK3 interface."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
import contextlib
from dataclasses import dataclass, field, replace
import functools
import itertools
import logging
import time

from . import transport
from .const import (
    A_ACTION_SET,
    CMD_ADDRESS,
    CMD_INTERFACE,
    CMD_INTERFACE_STATE,
    CMD_LED,
    CMD_SPECIAL,
    CMD_STATE,
    CMD_VERSION,
    CMD_WINMON_TAGGED,
    F_DC_INFO,
    F_MASTER_MULTI_LED,
    MARKER_BOOT,
    MARKER_MASTER_MULTI_LED,
    MIN_FIRMWARE_FORCE_CHARGE,
    MIN_FIRMWARE_RAM_ONLY_WRITE,
    MODE_TO_SWITCH_STATE,
    S_FLAG_ABSOLUTE_LIMIT,
    S_FLAG_DONT_SEND,
    S_FLAGS_EXT0_WINMON_MODE1,
    S_LIMIT_IGNORE,
    S_LONG_FRAMES_TAIL,
    WRITE_VIA_ID_RAM_ONLY,
    WRITE_VIA_ID_SETTING,
    ForceChargeState,
    InterfaceFlag,
    Mode,
    RamVar,
    Setting,
    WinmonCommand,
    WinmonReply,
)
from .decode import (
    INFO_FRAME_VARS,
    DecodeError,
    decode_ac_info,
    decode_dc_info,
    decode_device_state,
    decode_led,
    decode_master_multi_led,
    decode_ram_values,
    decode_ram_var_info,
    decode_setting_info,
    info_frame_phase,
)
from .frame import Frame, FrameDecoder, encode_frame
from .models import (
    AcInfo,
    ConfigInfo,
    DcInfo,
    DeviceStateInfo,
    FirmwareVersion,
    LedInfo,
    RamVarInfo,
    SettingInfo,
    UnitState,
    VeBusState,
)
from .transport import CONNECTION_ERRORS, Opener

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 0.5
MASTER_MULTI_LED_TIMEOUT = 1.0
# The interface sends a version frame about once per second when idle. Without
# any frame for this long the VE.Bus device is asleep or the interface unplugged.
IDLE_TIMEOUT = 5.0
# Number of attempts made for each request during initialisation.
INIT_ATTEMPTS = 3
# Consecutive failed polls before the device is considered unreachable.
MAX_FAILED_POLLS = 3

RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0

# RAM variables read on every poll.
FAST_RAM_VARS: tuple[RamVar, ...] = (
    RamVar.INVERTER_POWER_1,
    RamVar.INVERTER_POWER_2,
    RamVar.OUTPUT_POWER,
)
# RAM variables and settings read round-robin, one group per poll.
SLOW_RAM_VARS: tuple[tuple[RamVar, ...], ...] = (
    (RamVar.U_BAT_RMS,),
    (RamVar.CHARGE_STATE,),
    (RamVar.IGNORE_AC_INPUT_STATE, RamVar.MULTI_FUNCTIONAL_RELAY_STATE),
)
SLOW_SETTINGS: tuple[Setting, ...] = (
    Setting.U_BAT_ABSORPTION,
    Setting.U_BAT_FLOAT,
    Setting.I_BAT_BULK,
    Setting.I_MAINS_LIMIT_AC1,
    Setting.I_MAINS_LIMIT_AC2,
    Setting.BATTERY_CAPACITY,
    Setting.SOLAR_WIND_PRIORITY_VOLTAGE,
)
# Flag settings, kept as raw values.
SLOW_RAW_SETTINGS: tuple[Setting, ...] = (Setting.FLAGS_0, Setting.FLAGS_2)
SETTING_INFO_IDS: tuple[Setting, ...] = (*SLOW_RAW_SETTINGS, *SLOW_SETTINGS)
RAM_VARS: tuple[RamVar, ...] = (
    *INFO_FRAME_VARS,
    *FAST_RAM_VARS,
    *itertools.chain.from_iterable(SLOW_RAM_VARS),
)
# RAM variables read from every unit of a multi-unit system, in groups of three.
UNIT_RAM_VARS: tuple[tuple[RamVar, ...], ...] = (
    (RamVar.U_MAINS_RMS, RamVar.I_MAINS_RMS, RamVar.U_INVERTER_RMS),
    (RamVar.I_INVERTER_RMS, RamVar.U_BAT, RamVar.I_BAT),
    FAST_RAM_VARS,
)
# RAM variables replacing the info frames on devices that do not send them.
INFO_RAM_VARS: tuple[tuple[RamVar, ...], ...] = (
    (RamVar.U_MAINS_RMS, RamVar.I_MAINS_RMS, RamVar.U_INVERTER_RMS),
    (RamVar.I_INVERTER_RMS, RamVar.U_BAT, RamVar.I_BAT),
    (RamVar.INVERTER_PERIOD, RamVar.MAINS_PERIOD),
)
# VE.Bus addresses are 0..31. A scan stops after this many missing addresses
# in a row (section 7.2).
MAX_ADDRESS = 0x1F
SCAN_MISSES = 3
# The MultiPlus-II reports the inverter current as unsigned but sends negative
# values while charging, so it is always decoded as signed.
FORCE_SIGNED_RAM_VARS = frozenset({RamVar.I_INVERTER_RMS})
# 'S' data that selects Winmon mode 1 (short frames) without changing the
# remote panel state: absolute limit format and do not send the panel state.
S_MODE1_DATA = (
    0x00,
    0x00,
    0x00,
    0x01,
    S_FLAG_ABSOLUTE_LIMIT | S_FLAG_DONT_SEND,
    0x00,
    S_FLAGS_EXT0_WINMON_MODE1,
)
# Errors ignored while closing the connection.
CLOSE_ERRORS: tuple[type[BaseException], ...] = (
    *CONNECTION_ERRORS,
    asyncio.CancelledError,
)


class VeBusError(Exception):
    """Base class for errors raised by the client."""


class VeBusConnectionError(VeBusError):
    """The interface is not connected or the connection was lost."""


class VeBusTimeoutError(VeBusError):
    """The interface or the VE.Bus device did not reply in time."""


class VeBusNotSupportedError(VeBusError):
    """The device does not support the requested command or value."""


class VeBusWriteError(VeBusError):
    """The device did not accept a setting change."""


@dataclass(slots=True)
class ClientStatistics:
    """Counters exposed through diagnostics."""

    frames_received: int = 0
    frames_sent: int = 0
    unsolicited_frames: int = 0
    timeouts: int = 0
    connects: int = 0
    connection_errors: int = 0
    boot_frames: int = 0
    last_frames: deque[str] = field(default_factory=lambda: deque(maxlen=20))


@dataclass(slots=True)
class _Waiter:
    predicate: Callable[[Frame], bool]
    future: asyncio.Future[Frame]


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Information gathered while initialising the interface."""

    mk3_version: int | None
    firmware: FirmwareVersion | None
    phases: tuple[int, ...]
    units: tuple[int, ...] = (0,)


class VeBusClient:
    """Talks to one VE.Bus device through an MK2/MK3 interface.

    All requests are serialised with a lock, so callers may issue commands
    concurrently with the polling loop.
    """

    def __init__(
        self,
        url: str,
        *,
        address: int = 0,
        opener: Opener | None = None,
        request_timeout: float | None = None,
    ) -> None:
        """Initialize the client."""
        self.url = url
        self.address = address
        self._opener = opener
        self._request_timeout = request_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._decoder = FrameDecoder()
        self._waiters: list[_Waiter] = []
        self._lock = asyncio.Lock()
        self._tags = itertools.cycle(CMD_WINMON_TAGGED)
        self._last_rx = 0.0
        self._readdress = False
        self._long_frames = False
        self._connection_lost = False
        self.stats = ClientStatistics()
        self.mk3_version: int | None = None
        self.firmware: FirmwareVersion | None = None
        self.phases: tuple[int, ...] = (1,)
        self.ram_var_info: dict[int, RamVarInfo | None] = {}
        self.setting_info: dict[int, SettingInfo | None] = {}
        self.interface_flags_supported = True
        self._interface_flag_failures = 0
        self.state = VeBusState()
        self._slow_queue: deque[tuple[str, tuple[int, ...]]] = deque()
        # Addresses of all units; just the home address for a single unit.
        self.units: tuple[int, ...] = (address,)
        self.unit_firmware: dict[int, FirmwareVersion | None] = {}
        self._unit_queue: deque[int] = deque()
        # False when the device does not answer info frames ('F' 0..4).
        self.info_frames = True

    # ------------------------------------------------------------------
    # Connection handling

    @property
    def connected(self) -> bool:
        """Return True while the serial connection is open."""
        return self._writer is not None and not self._connection_lost

    @property
    def idle(self) -> bool:
        """Return True if nothing has been received for a while."""
        return time.monotonic() - self._last_rx > IDLE_TIMEOUT

    async def connect(self) -> None:
        """Open the serial connection and start reading."""
        if self._writer is not None:
            return
        try:
            opener = self._opener or transport.open_serial
            self._reader, self._writer = await opener(self.url)
        except CONNECTION_ERRORS as err:
            self.stats.connection_errors += 1
            raise VeBusConnectionError(f"Cannot open {self.url}: {err}") from err
        self.stats.connects += 1
        self._connection_lost = False
        self._decoder.reset()
        self._last_rx = time.monotonic()
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"vebus reader {self.url}"
        )

    async def close(self) -> None:
        """Close the connection."""
        task, self._reader_task = self._reader_task, None
        writer, self._writer = self._writer, None
        self._reader = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if writer is not None:
            writer.close()
            with contextlib.suppress(*CLOSE_ERRORS):
                await writer.wait_closed()
        self._fail_waiters(VeBusConnectionError("Connection closed"))

    async def _read_loop(self) -> None:
        assert self._reader is not None
        reader = self._reader
        try:
            while True:
                data = await reader.read(256)
                if not data:
                    raise EOFError("Serial port closed")
                self._last_rx = time.monotonic()
                for frame in self._decoder.feed(data):
                    self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except CONNECTION_ERRORS as err:
            _LOGGER.debug("Connection to %s lost: %s", self.url, err)
            self.stats.connection_errors += 1
        except Exception:
            _LOGGER.exception("Unexpected error reading from %s", self.url)
        self._connection_lost = True
        self._fail_waiters(VeBusConnectionError("Connection lost"))

    def _dispatch(self, frame: Frame) -> None:
        self.stats.frames_received += 1
        self.stats.last_frames.append("<" + frame.to_bytes().hex(" "))
        if frame.is_mk and frame.command == CMD_VERSION and len(frame.data) >= 4:
            self.mk3_version = int.from_bytes(frame.data[0:4], "little")
        elif frame.marker == MARKER_BOOT:
            # A VE.Bus device restarted, the address must be selected again.
            self.stats.boot_frames += 1
            self._readdress = True
        for waiter in self._waiters:
            if waiter.future.done():
                continue
            try:
                matched = waiter.predicate(frame)
            except IndexError:
                matched = False
            if matched:
                waiter.future.set_result(frame)
                return
        if not (frame.is_mk and frame.command == CMD_VERSION):
            self.stats.unsolicited_frames += 1

    def _fail_waiters(self, err: Exception) -> None:
        for waiter in self._waiters:
            if not waiter.future.done():
                waiter.future.set_exception(err)

    # ------------------------------------------------------------------
    # Request primitives

    async def _send(self, frame: bytes) -> None:
        if self._writer is None or self._connection_lost:
            raise VeBusConnectionError("Not connected")
        self.stats.frames_sent += 1
        self.stats.last_frames.append(">" + frame.hex(" "))
        try:
            self._writer.write(frame)
            await self._writer.drain()
        except CONNECTION_ERRORS as err:
            self._connection_lost = True
            raise VeBusConnectionError(f"Write failed: {err}") from err

    async def _request(
        self,
        frame: bytes,
        predicate: Callable[[Frame], bool] | None,
        timeout: float | None = None,
    ) -> Frame | None:
        """Send a frame and wait for the matching reply.

        Must be called with the lock held. A ``predicate`` of None sends the
        frame without waiting for a reply.
        """
        if predicate is None:
            await self._send(frame)
            return None
        loop = asyncio.get_running_loop()
        waiter = _Waiter(predicate, loop.create_future())
        self._waiters.append(waiter)
        try:
            await self._send(frame)
            async with asyncio.timeout(
                timeout or self._request_timeout or REQUEST_TIMEOUT
            ):
                return await waiter.future
        except TimeoutError as err:
            self.stats.timeouts += 1
            raise VeBusTimeoutError(f"No reply to {frame.hex(' ')}") from err
        finally:
            self._waiters.remove(waiter)

    async def _command(
        self,
        command: int,
        data: Iterable[int] = b"",
        predicate: Callable[[Frame], bool] | None = None,
        timeout: float | None = None,
    ) -> Frame | None:
        async with self._lock:
            await self._prepare()
            return await self._request(encode_frame(command, data), predicate, timeout)

    async def _prepare(self) -> None:
        """Restore short frames and the address before a request (lock held)."""
        if self._long_frames:
            await self._request(
                encode_frame(CMD_STATE, S_MODE1_DATA), _mk_reply(CMD_STATE)
            )
            self._long_frames = False
        await self._ensure_address()

    async def _ensure_address(self) -> None:
        if not self._readdress:
            return
        self._readdress = False
        await self._request(
            encode_frame(CMD_ADDRESS, [A_ACTION_SET, self.address]),
            _mk_reply(CMD_ADDRESS),
        )

    async def _winmon(self, command: WinmonCommand, *args: int) -> bytes:
        """Send a tagged Winmon command and return the reply after the tag."""
        async with self._lock:
            await self._prepare()
            return await self._winmon_locked(command, *args)

    async def _winmon_locked(self, command: WinmonCommand, *args: int) -> bytes:
        """Like _winmon, for callers that already hold the lock."""
        tag = next(self._tags)
        reply = await self._request(encode_frame(tag, [command, *args]), _mk_reply(tag))
        assert reply is not None
        data = reply.data
        if data and data[0] == WinmonReply.UNKNOWN_COMMAND:
            raise VeBusNotSupportedError(f"Command {command.name} not supported")
        return data

    # ------------------------------------------------------------------
    # Commands

    async def get_version(self) -> int:
        """Return the firmware version of the interface ('V')."""
        frame = await self._command(CMD_VERSION, b"", _mk_reply(CMD_VERSION, 4))
        assert frame is not None
        self.mk3_version = int.from_bytes(frame.data[0:4], "little")
        return self.mk3_version

    async def set_address(self, address: int) -> None:
        """Select the VE.Bus device all further commands are sent to ('A')."""
        async with self._lock:
            await self._request(
                encode_frame(CMD_ADDRESS, [A_ACTION_SET, address]),
                _mk_reply(CMD_ADDRESS),
            )
            self._readdress = False
        self.address = address

    async def set_winmon_mode1(self) -> None:
        """Reset the interface to Winmon mode 1 without changing the remote state.

        Other tools such as VictronConnect may leave the interface in mode 2.
        Flags 0x90 = absolute current limit format and do not send panel state.
        """
        await self._command(CMD_STATE, S_MODE1_DATA, _mk_reply(CMD_STATE))

    async def set_remote_panel_state(
        self, mode: Mode, current_limit: float | None = None
    ) -> None:
        """Set the remote panel switch state and input current limit ('S').

        A ``current_limit`` of None leaves the current limit unchanged.
        """
        switch_state = MODE_TO_SWITCH_STATE[Mode(mode)]
        if current_limit is None:
            limit = S_LIMIT_IGNORE
        else:
            limit = max(0, min(round(current_limit * 10), S_LIMIT_IGNORE - 1))
        await self._command(
            CMD_STATE,
            [switch_state, limit & 0xFF, limit >> 8, 0x01, S_FLAG_ABSOLUTE_LIMIT],
            _mk_reply(CMD_STATE),
        )

    async def set_interface_flags(self, flags: InterfaceFlag) -> int:
        """Set the MK3 interface flags ('H'), e.g. to keep the device awake."""
        frame = await self._command(
            CMD_INTERFACE, [int(flags)], _mk_reply(CMD_INTERFACE, 1)
        )
        assert frame is not None
        return frame.data[0]

    async def read_led(self) -> LedInfo:
        """Read the LED status ('L')."""
        frame = await self._command(CMD_LED, b"", _mk_reply(CMD_LED, 2))
        assert frame is not None
        return decode_led(frame)

    async def read_dc_info(self) -> DcInfo:
        """Read the DC info frame ('F' 0)."""
        frame = await self._command(
            CMD_SPECIAL, [F_DC_INFO], lambda f: info_frame_phase(f) == 0
        )
        assert frame is not None
        return decode_dc_info(frame, self.ram_var_info)

    async def read_ac_info(self, phase: int) -> AcInfo:
        """Read the AC info frame of a phase ('F' 1..4)."""
        if not 1 <= phase <= 4:
            raise ValueError("phase must be 1..4")
        frame = await self._command(
            CMD_SPECIAL, [phase], lambda f: info_frame_phase(f) == phase
        )
        assert frame is not None
        return decode_ac_info(frame, self.ram_var_info)

    async def read_config(self) -> ConfigInfo:
        """Read the MasterMultiLED frame ('F' 5): current limits and switch state."""
        frame = await self._command(
            CMD_SPECIAL,
            [F_MASTER_MULTI_LED],
            lambda f: f.marker == MARKER_MASTER_MULTI_LED,
            MASTER_MULTI_LED_TIMEOUT,
        )
        assert frame is not None
        return decode_master_multi_led(frame)

    async def read_firmware(self) -> FirmwareVersion:
        """Read the firmware version of the VE.Bus device."""
        low = await self._winmon(WinmonCommand.SEND_SOFTWARE_VERSION_PART0, 0, 0)
        high = await self._winmon(WinmonCommand.SEND_SOFTWARE_VERSION_PART1, 0, 0)
        if (
            len(low) < 3
            or len(high) < 3
            or low[0] != WinmonReply.SOFTWARE_VERSION_PART0
            or high[0] != WinmonReply.SOFTWARE_VERSION_PART1
        ):
            raise VeBusNotSupportedError("Firmware version not available")
        raw = (
            int.from_bytes(low[1:3], "little")
            | int.from_bytes(high[1:3], "little") << 16
        )
        return FirmwareVersion(raw)

    async def read_device_state(self) -> DeviceStateInfo:
        """Read the device state and charger sub state."""
        reply = await self._winmon(WinmonCommand.GET_SET_DEVICE_STATE, 0, 0)
        return decode_device_state(reply)

    async def get_ram_var_info(self, var: int) -> RamVarInfo | None:
        """Read the scaling of a RAM variable, None if unsupported."""
        reply = await self._winmon(WinmonCommand.GET_RAM_VAR_INFO, var & 0xFF, var >> 8)
        info = decode_ram_var_info(reply)
        if info is not None and var in FORCE_SIGNED_RAM_VARS and info.bit is None:
            info = replace(info, signed=True)
        return info

    async def get_setting_info(self, setting: int) -> SettingInfo | None:
        """Read the scaling of a setting, None if unsupported."""
        reply = await self._winmon(
            WinmonCommand.GET_SETTING_INFO, setting & 0xFF, setting >> 8
        )
        return decode_setting_info(reply)

    async def read_ram_vars(self, variables: Iterable[int]) -> dict[int, float | None]:
        """Read up to three RAM variables in one request and decode them."""
        async with self._lock:
            await self._prepare()
            return await self._read_ram_vars_locked(variables)

    async def _read_ram_vars_locked(
        self, variables: Iterable[int]
    ) -> dict[int, float | None]:
        ids = list(variables)
        if not 1 <= len(ids) <= 3:
            raise ValueError("between 1 and 3 variables can be read at once")
        # A single ID is sent as a 16-bit value, which every firmware understands.
        args = [ids[0], 0] if len(ids) == 1 else ids
        reply = await self._winmon_locked(WinmonCommand.READ_RAM_VAR, *args)
        result: dict[int, float | None] = {}
        for var, raw in zip(ids, decode_ram_values(reply, len(ids)), strict=True):
            info = self.ram_var_info.get(var)
            result[var] = (
                info.decode(raw) if info is not None and raw is not None else None
            )
        return result

    async def read_setting_raw(self, setting: int) -> int | None:
        """Read the raw 16-bit value of a setting, None if unsupported."""
        reply = await self._winmon(
            WinmonCommand.READ_SETTING, setting & 0xFF, setting >> 8
        )
        return _setting_value(reply)

    async def read_setting(self, setting: int) -> float | None:
        """Read a setting and decode it, None if unsupported."""
        info = self.setting_info.get(setting)
        if info is None:
            return None
        raw = await self.read_setting_raw(setting)
        return None if raw is None else info.decode(raw)

    # ------------------------------------------------------------------
    # Long frames (Winmon mode 2): RAM-only setting writes

    @contextlib.asynccontextmanager
    async def _long_frame_session(self) -> AsyncIterator[None]:
        """Switch the interface to long frames for the duration of the block.

        WriteViaID (0x37) and the long form of GetSettingInfo are Winmon
        mode 2 only. The current remote panel state is read with 'D' and sent
        back unchanged. Afterwards mode 1 is restored; if that fails it is
        retried before the next request. Holds the lock for the whole block,
        so polling pauses meanwhile.
        """
        async with self._lock:
            await self._prepare()
            reply = await self._request(
                encode_frame(CMD_INTERFACE_STATE), _mk_reply(CMD_INTERFACE_STATE, 3)
            )
            assert reply is not None
            self._long_frames = True
            await self._request(
                encode_frame(CMD_STATE, [*reply.data[0:3], *S_LONG_FRAMES_TAIL]),
                _mk_reply(CMD_STATE),
            )
            try:
                yield
            finally:
                for _ in range(2):
                    try:
                        await self._request(
                            encode_frame(CMD_STATE, S_MODE1_DATA), _mk_reply(CMD_STATE)
                        )
                    except VeBusError:
                        continue
                    self._long_frames = False
                    break
                else:
                    _LOGGER.warning(
                        "Could not switch the interface back to short frames"
                    )

    def _require_firmware(self, minimum: int, feature: str) -> None:
        version = self.firmware.version if self.firmware else None
        if version is None or version < minimum:
            raise VeBusNotSupportedError(
                f"{feature} needs device firmware {minimum} or later"
                f" (device reports {version if version is not None else 'unknown'})"
            )

    async def complete_setting_info(self, settings: Iterable[int]) -> None:
        """Re-read setting info in long-frame mode where mode 1 gave no range.

        The Winmon mode 1 reply can be cut short before the minimum and
        maximum, which writable settings need.
        """
        # Settings without any info are not supported by the device.
        missing = [
            setting
            for setting in settings
            if (info := self.setting_info.get(setting)) is not None
            and (info.minimum is None or info.maximum is None)
        ]
        if not missing:
            return
        async with self._long_frame_session():
            for setting in missing:
                try:
                    reply = await self._winmon_locked(
                        WinmonCommand.GET_SETTING_INFO, setting & 0xFF, setting >> 8
                    )
                    info = decode_setting_info(reply, long_form=True)
                except (VeBusTimeoutError, VeBusNotSupportedError, DecodeError) as err:
                    _LOGGER.debug("Setting info %s not available: %s", setting, err)
                    continue
                if info is not None:
                    self.setting_info[setting] = info

    async def _write_setting_locked(self, setting: int, raw: int) -> int:
        """Write a setting to RAM only and verify it by reading it back."""
        if not 0 <= setting <= 0xFF:
            raise ValueError("WriteViaID only addresses settings 0..255")
        if not 0 <= raw <= 0xFFFF:
            raise ValueError("Setting values are 16 bit")
        try:
            reply = await self._winmon_locked(
                WinmonCommand.WRITE_VIA_ID,
                WRITE_VIA_ID_SETTING | WRITE_VIA_ID_RAM_ONLY,
                setting,
                raw & 0xFF,
                raw >> 8,
            )
        except VeBusTimeoutError:
            # Not every device acknowledges; the read back decides.
            reply = b""
        if reply and reply[0] == WinmonReply.ACCESS_LEVEL_REQUIRED:
            raise VeBusWriteError(f"Setting {setting} needs a higher access level")
        readback = _setting_value(
            await self._winmon_locked(WinmonCommand.READ_SETTING, setting, 0)
        )
        if readback != raw:
            raise VeBusWriteError(
                f"Setting {setting} reads back {readback} after writing {raw}"
            )
        self._store_setting(setting, raw)
        return raw

    def _store_setting(self, setting: int, raw: int) -> None:
        settings_raw = dict(self.state.settings_raw)
        settings = dict(self.state.settings)
        if setting in SLOW_RAW_SETTINGS:
            settings_raw[setting] = raw
        info = self.setting_info.get(setting)
        if info is not None and setting in SLOW_SETTINGS:
            settings[setting] = info.decode(raw)
        self.state = replace(self.state, settings=settings, settings_raw=settings_raw)

    async def write_setting(self, setting: int, value: float) -> float:
        """Change a setting in RAM only (lost when the device restarts).

        The value is checked against the range the device reports. Returns
        the value read back from the device.
        """
        self._require_firmware(MIN_FIRMWARE_RAM_ONLY_WRITE, "Changing settings")
        info = self.setting_info.get(setting)
        if info is None or info.minimum is None or info.maximum is None:
            raise VeBusNotSupportedError(f"Range of setting {setting} is unknown")
        raw = info.encode(value)
        if not info.minimum <= raw <= info.maximum:
            raise ValueError(
                f"{value} is outside {info.decode(info.minimum)}"
                f"..{info.decode(info.maximum)}"
            )
        async with self._long_frame_session():
            raw = await self._write_setting_locked(setting, raw)
        return info.decode(raw)

    async def set_setting_flag(self, setting: int, flag: int, enabled: bool) -> int:
        """Set or clear bits of a flag setting in RAM only (read-modify-write).

        Bits that the device reports as unsupported are refused (the maximum
        of a flag setting is the mask of its supported bits). Returns the new
        raw value.
        """
        self._require_firmware(MIN_FIRMWARE_RAM_ONLY_WRITE, "Changing settings")
        info = self.setting_info.get(setting)
        if info is not None and info.maximum is not None and flag & ~info.maximum:
            raise VeBusNotSupportedError(
                f"Setting {setting} does not support bits 0x{flag:04x}"
            )
        async with self._long_frame_session():
            current = _setting_value(
                await self._winmon_locked(WinmonCommand.READ_SETTING, setting, 0)
            )
            if current is None:
                raise VeBusNotSupportedError(f"Setting {setting} cannot be read")
            new = current | flag if enabled else current & ~flag
            if new == current:
                self._store_setting(setting, current)
                return current
            return await self._write_setting_locked(setting, new)

    async def force_charge_state(self, state: ForceChargeState) -> DeviceStateInfo:
        """Force the charger into equalise, absorption or float."""
        self._require_firmware(MIN_FIRMWARE_FORCE_CHARGE, "Forcing a charge state")
        reply = await self._winmon(WinmonCommand.GET_SET_DEVICE_STATE, int(state), 0)
        info = decode_device_state(reply)
        self.state = replace(self.state, device_state=info)
        return info

    # ------------------------------------------------------------------
    # Multi-unit systems

    @contextlib.asynccontextmanager
    async def _at_address(self, address: int) -> AsyncIterator[None]:
        """Talk to another unit for the duration of the block (lock held).

        The home address is selected again afterwards.
        """
        async with self._lock:
            await self._prepare()
            if address == self.address:
                yield
                return
            try:
                await self._request(
                    encode_frame(CMD_ADDRESS, [A_ACTION_SET, address]),
                    _mk_reply(CMD_ADDRESS),
                )
                yield
            finally:
                try:
                    await self._request(
                        encode_frame(CMD_ADDRESS, [A_ACTION_SET, self.address]),
                        _mk_reply(CMD_ADDRESS),
                    )
                except VeBusError:
                    self._readdress = True

    async def _read_firmware_locked(self) -> FirmwareVersion | None:
        low = await self._winmon_locked(WinmonCommand.SEND_SOFTWARE_VERSION_PART0, 0, 0)
        high = await self._winmon_locked(
            WinmonCommand.SEND_SOFTWARE_VERSION_PART1, 0, 0
        )
        if (
            len(low) < 3
            or len(high) < 3
            or low[0] != WinmonReply.SOFTWARE_VERSION_PART0
            or high[0] != WinmonReply.SOFTWARE_VERSION_PART1
        ):
            return None
        return FirmwareVersion(
            int.from_bytes(low[1:3], "little")
            | int.from_bytes(high[1:3], "little") << 16
        )

    async def scan_units(self) -> tuple[int, ...]:
        """Find all units on the VE.Bus (section 7.2).

        Every address is asked for its firmware version; the scan stops after
        three addresses in a row do not answer.
        """
        found = [self.address]
        self.unit_firmware = {self.address: self.firmware}
        misses = 0
        for address in range(MAX_ADDRESS + 1):
            if address == self.address:
                continue
            try:
                async with self._at_address(address):
                    firmware = await self._read_firmware_locked()
            except VeBusTimeoutError:
                misses += 1
                if misses >= SCAN_MISSES:
                    break
                continue
            except VeBusNotSupportedError:
                firmware = None
            misses = 0
            found.append(address)
            self.unit_firmware[address] = firmware
        return tuple(sorted(found))

    async def read_unit(self, address: int) -> UnitState:
        """Read the values of one unit of a multi-unit system."""
        unit = UnitState(address, firmware=self.unit_firmware.get(address))
        async with self._at_address(address):
            for group in UNIT_RAM_VARS:
                unit.ram.update(await self._read_ram_vars_locked(group))
            try:
                unit.device_state = decode_device_state(
                    await self._winmon_locked(WinmonCommand.GET_SET_DEVICE_STATE, 0, 0)
                )
            except VeBusNotSupportedError, DecodeError:
                unit.device_state = None
        return unit

    # ------------------------------------------------------------------
    # Devices without info frames

    async def read_info_from_ram(self) -> tuple[DcInfo, AcInfo]:
        """Build the DC and AC info from RAM variables.

        Used for devices that do not answer the 'F' info frames, such as the
        Phoenix/Compact inverters (j9brown/victron-mk3-hass issue #11).
        """
        values: dict[int, float | None] = {}
        for group in INFO_RAM_VARS:
            values.update(await self.read_ram_vars(group))

        def required(var: RamVar) -> float:
            value = values.get(var)
            if value is None:
                raise DecodeError(f"RAM variable {var.name} not available")
            return value

        battery_current = values.get(RamVar.I_BAT) or 0.0
        dc = DcInfo(
            voltage=required(RamVar.U_BAT),
            inverter_current=max(-battery_current, 0.0),
            charger_current=max(battery_current, 0.0),
            inverter_frequency=_frequency(values.get(RamVar.INVERTER_PERIOD)),
        )
        ac = AcInfo(
            phase=1,
            phase_count=1,
            mains_voltage=values.get(RamVar.U_MAINS_RMS) or 0.0,
            mains_current=values.get(RamVar.I_MAINS_RMS) or 0.0,
            inverter_voltage=required(RamVar.U_INVERTER_RMS),
            inverter_current=values.get(RamVar.I_INVERTER_RMS) or 0.0,
            mains_frequency=_frequency(values.get(RamVar.MAINS_PERIOD)),
            device_state=None,
            raw_device_state=0xFF,
        )
        return dc, ac

    # ------------------------------------------------------------------
    # High level operations

    async def initialize(self) -> DeviceIdentity:
        """Prepare the interface and learn about the connected device.

        Raises VeBusTimeoutError if the device does not respond, e.g. because
        it is asleep.
        """
        await _retry(self.get_version)
        await _retry(self.set_winmon_mode1)
        await _retry(functools.partial(self.set_address, self.address))

        for var in RAM_VARS:
            if var not in self.ram_var_info:
                self.ram_var_info[var] = await _retry(
                    functools.partial(self.get_ram_var_info, var)
                )
        missing = [v.name for v in INFO_FRAME_VARS if self.ram_var_info.get(v) is None]
        if missing:
            raise VeBusNotSupportedError(f"Device does not describe {missing}")

        for setting in SETTING_INFO_IDS:
            if setting not in self.setting_info:
                try:
                    self.setting_info[setting] = await _retry(
                        functools.partial(self.get_setting_info, setting)
                    )
                except VeBusTimeoutError, VeBusNotSupportedError, DecodeError:
                    self.setting_info[setting] = None

        try:
            self.firmware = await _retry(self.read_firmware)
        except VeBusTimeoutError, VeBusNotSupportedError:
            _LOGGER.debug("Device did not report its firmware version")

        try:
            await _retry(self.read_dc_info)
        except VeBusTimeoutError:
            # Some devices only answer Winmon commands; use RAM variables.
            await self.read_info_from_ram()
            _LOGGER.info("Device does not send info frames, reading RAM variables")
            self.info_frames = False
            self.phases = (1,)
        else:
            self.info_frames = True
            phases = [1]
            await _retry(lambda: self.read_ac_info(1))
            for phase in (2, 3, 4):
                try:
                    await self.read_ac_info(phase)
                except VeBusTimeoutError:
                    break
                phases.append(phase)
            self.phases = tuple(phases)

        self.units = await self.scan_units()
        self._unit_queue.clear()
        if len(self.units) > 1:
            _LOGGER.info("Found VE.Bus units at addresses %s", self.units)

        # Read everything once so the state is complete from the start.
        self._slow_queue.clear()
        self.state = VeBusState(
            mk3_version=self.mk3_version,
            firmware=self.firmware,
            phases=self.phases,
            info_frames=self.info_frames,
        )
        await self.poll(full=True)
        return DeviceIdentity(self.mk3_version, self.firmware, self.phases, self.units)

    def _refill_slow_queue(self) -> None:
        for group in SLOW_RAM_VARS:
            self._slow_queue.append(("ram", tuple(group)))
        for setting in SLOW_SETTINGS:
            if self.setting_info.get(setting) is not None:
                self._slow_queue.append(("setting", (setting,)))
        for setting in SLOW_RAW_SETTINGS:
            self._slow_queue.append(("setting_raw", (setting,)))

    async def poll(
        self, *, full: bool = False, interface_flags: InterfaceFlag | None = None
    ) -> VeBusState:
        """Read the current state of the device.

        Fast changing values are read on every call, slower ones round-robin.
        With ``full`` every value is read. Raises VeBusTimeoutError if the
        essential DC and AC frames cannot be read.
        """
        state = replace(
            self.state,
            ac=dict(self.state.ac),
            ram=dict(self.state.ram),
            settings=dict(self.state.settings),
            settings_raw=dict(self.state.settings_raw),
            units=dict(self.state.units),
            mk3_version=self.mk3_version,
            firmware=self.firmware,
            phases=self.phases,
            info_frames=self.info_frames,
        )

        if self.info_frames:
            state.dc = await self.read_dc_info()
        else:
            state.dc, state.ac[1] = await self.read_info_from_ram()

        if interface_flags is not None and self.interface_flags_supported:
            try:
                state.interface_flags = await self.set_interface_flags(interface_flags)
                self._interface_flag_failures = 0
            except VeBusTimeoutError:
                # The device answered the DC request, so the interface itself
                # does not know the command (an MK2 rather than an MK3).
                self._interface_flag_failures += 1
                if self._interface_flag_failures >= MAX_FAILED_POLLS:
                    _LOGGER.info(
                        "Interface does not support the 'H' command (not an MK3?)"
                    )
                    self.interface_flags_supported = False
        if self.info_frames:
            for phase in self.phases:
                state.ac[phase] = await self.read_ac_info(phase)

        optional_reads: list[tuple[str, Callable[[], Awaitable[object]]]] = [
            ("leds", self.read_led),
            ("config", self.read_config),
            ("device_state", self.read_device_state),
        ]
        for attr, func in optional_reads:
            try:
                setattr(state, attr, await func())
            except (VeBusTimeoutError, VeBusNotSupportedError, DecodeError) as err:
                _LOGGER.debug("Reading %s failed: %s", attr, err)
                setattr(state, attr, None)

        try:
            state.ram.update(await self.read_ram_vars(FAST_RAM_VARS))
        except (VeBusTimeoutError, VeBusNotSupportedError) as err:
            _LOGGER.debug("Reading power failed: %s", err)
            for var in FAST_RAM_VARS:
                state.ram[var] = None

        if full:
            self._slow_queue.clear()
            self._refill_slow_queue()
            items = list(self._slow_queue)
            self._slow_queue.clear()
        else:
            if not self._slow_queue:
                self._refill_slow_queue()
            items = [self._slow_queue.popleft()] if self._slow_queue else []
        for kind, ids in items:
            try:
                if kind == "ram":
                    state.ram.update(await self.read_ram_vars(ids))
                elif kind == "setting_raw":
                    state.settings_raw[ids[0]] = await self.read_setting_raw(ids[0])
                else:
                    state.settings[ids[0]] = await self.read_setting(ids[0])
            except (VeBusTimeoutError, VeBusNotSupportedError) as err:
                _LOGGER.debug("Reading %s %s failed: %s", kind, ids, err)

        if len(self.units) > 1:
            if full:
                addresses = list(self.units)
            else:
                if not self._unit_queue:
                    self._unit_queue.extend(self.units)
                addresses = [self._unit_queue.popleft()]
            for address in addresses:
                try:
                    state.units[address] = await self.read_unit(address)
                except (VeBusTimeoutError, VeBusNotSupportedError, DecodeError) as err:
                    _LOGGER.debug("Reading unit %s failed: %s", address, err)
                    state.units[address] = UnitState(
                        address, firmware=self.unit_firmware.get(address)
                    )

        state.timestamp = time.monotonic()
        self.state = state
        return state

    async def refresh_config(self) -> VeBusState:
        """Re-read the MasterMultiLED frame, e.g. after changing the mode."""
        config = await self.read_config()
        self.state = replace(self.state, config=config)
        return self.state

    async def run(
        self,
        on_state: Callable[[VeBusState], None],
        on_unavailable: Callable[[Exception], None],
        *,
        interval: float = 2.0,
        interface_flags: Callable[[], InterfaceFlag | None] = lambda: None,
        initialized: bool = False,
    ) -> None:
        """Connect, poll and reconnect forever (until cancelled).

        Pass ``initialized`` if :meth:`initialize` already succeeded on the
        current connection.
        """
        delay = RECONNECT_MIN_DELAY
        while True:
            try:
                if not (initialized and self.connected):
                    await self.connect()
                    await self.initialize()
                initialized = False
                delay = RECONNECT_MIN_DELAY
                failures = 0
                while True:
                    started = time.monotonic()
                    try:
                        state = await self.poll(interface_flags=interface_flags())
                    except (VeBusTimeoutError, DecodeError) as err:
                        failures += 1
                        if failures >= MAX_FAILED_POLLS:
                            raise VeBusTimeoutError(
                                "Device stopped responding"
                                + (" (asleep?)" if self.idle else "")
                            ) from err
                    else:
                        failures = 0
                        on_state(state)
                    elapsed = time.monotonic() - started
                    await asyncio.sleep(max(0.0, interval - elapsed))
            except asyncio.CancelledError:
                await self.close()
                raise
            except (VeBusError, DecodeError) as err:
                on_unavailable(err)
                if isinstance(err, VeBusConnectionError) or self._connection_lost:
                    await self.close()
            except Exception as err:
                _LOGGER.exception("Unexpected error communicating with %s", self.url)
                on_unavailable(err)
                await self.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)


def _mk_reply(command: int, min_data: int = 0) -> Callable[[Frame], bool]:
    """Return a predicate matching an interface reply to ``command``."""

    def predicate(frame: Frame) -> bool:
        return frame.is_mk and frame.command == command and len(frame.data) >= min_data

    return predicate


async def _retry[T](
    func: Callable[[], Awaitable[T]], attempts: int = INIT_ATTEMPTS
) -> T:
    """Retry a request a few times on timeout."""
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except VeBusTimeoutError:
            if attempt == attempts:
                raise
    raise AssertionError("unreachable")


def _setting_value(reply: bytes) -> int | None:
    """Return the value of a ReadSetting reply, None if unsupported."""
    if len(reply) < 3 or reply[0] != WinmonReply.READ_SETTING_OK:
        return None
    return int.from_bytes(reply[1:3], "little")


def _frequency(period: float | None) -> float | None:
    """Convert a decoded period RAM variable (in units of 0.1 s) to Hz."""
    if not period or period <= 0:
        return None
    return round(10.0 / period, 2)
