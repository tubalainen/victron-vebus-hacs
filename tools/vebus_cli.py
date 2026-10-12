#!/usr/bin/env python3
"""Command line tool to talk to a VE.Bus device through an MK3-USB interface.

Uses the protocol package of the integration without Home Assistant, so the
connection, the decoding and the sign conventions can be checked directly on
the hardware.

    pip install serialx
    python tools/vebus_cli.py info    /dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_...
    python tools/vebus_cli.py monitor /dev/serial/by-id/... --interval 2
    python tools/vebus_cli.py sniff   /dev/serial/by-id/...
    python tools/vebus_cli.py set-mode /dev/serial/by-id/... on --limit 16 --yes

Stop Home Assistant (or disable the integration) first: the port can only be
opened by one program at a time.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

# Import the protocol package directly, without the Home Assistant integration.
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "custom_components" / "victron_vebus"),
)

from vebus import (
    ForceChargeState,
    Mode,
    RamVar,
    Setting,
    SettingFlag,
    VeBusClient,
    VeBusError,
)
from vebus.frame import FrameDecoder
from vebus.models import VeBusState
from vebus.transport import open_serial


def _print_state(state: VeBusState) -> None:
    dc, ac, cfg = state.dc, state.ac, state.config
    ram, settings = state.ram, state.settings
    lines = []
    if dc:
        lines.append(
            f"DC   {dc.voltage:6.2f} V  charger {dc.charger_current:6.1f} A  "
            f"inverter {dc.inverter_current:6.1f} A  net {dc.battery_current:+6.1f} A  "
            f"inverter freq {dc.inverter_frequency} Hz"
        )
    for phase, info in sorted(ac.items()):
        lines.append(
            f"L{phase}   in {info.mains_voltage:6.1f} V {info.mains_current:+6.2f} A "
            f"{info.mains_frequency} Hz | out {info.inverter_voltage:6.1f} V "
            f"{info.inverter_current:+6.2f} A | state {info.device_state} "
            f"(raw {info.raw_device_state}, phases {info.phase_count})"
        )
    lines.append(
        f"Power  RAM14 (battery) {ram.get(RamVar.INVERTER_POWER_1)} W  "
        f"RAM15 (AC in, negated in HA) {ram.get(RamVar.INVERTER_POWER_2)} W  "
        f"RAM16 (AC out) {ram.get(RamVar.OUTPUT_POWER)} W"
    )
    if state.device_state:
        lines.append(
            f"State  {state.device_state.state} / {state.device_state.charge_state} "
            f"(raw {state.device_state.raw_state}/{state.device_state.raw_sub_state})"
        )
    if state.leds:
        lines.append(f"LEDs   on={state.leds.on!r} blink={state.leds.blink!r}")
    if cfg:
        lines.append(
            f"Config mode {cfg.mode} remote {cfg.remote_mode} front {cfg.front_switch_mode} "
            f"limit {cfg.actual_current_limit} A ({cfg.minimum_current_limit}-"
            f"{cfg.maximum_current_limit}) inputs {cfg.num_ac_inputs} "
            f"switch register 0x{(cfg.switch_register or 0):02x}"
        )
    lines.append(
        f"SoC {ram.get(RamVar.CHARGE_STATE)}  ripple {ram.get(RamVar.U_BAT_RMS)} V  "
        f"relay {ram.get(RamVar.MULTI_FUNCTIONAL_RELAY_STATE)}  "
        f"ignore AC {ram.get(RamVar.IGNORE_AC_INPUT_STATE)}"
    )
    lines.append(
        "Settings " + ", ".join(f"{Setting(k).name}={v}" for k, v in settings.items())
    )
    print("\n".join(lines))
    print("-" * 78)


async def _connect(port: str) -> VeBusClient:
    client = VeBusClient(port)
    await client.connect()
    await client.initialize()
    return client


async def cmd_info(args: argparse.Namespace) -> None:
    client = await _connect(args.port)
    try:
        print(f"MK3 version: {client.mk3_version}")
        print(f"Firmware:    {client.firmware}")
        print(f"Phases:      {client.phases}")
        print(f"Info frames: {client.info_frames}")
        print(f"Units:       {client.units}")
        for address, firmware in sorted(client.unit_firmware.items()):
            print(f"  unit {address}: firmware {firmware}")
        print("RAM variable info:")
        for var, info in sorted(client.ram_var_info.items()):
            print(f"  {var:3d} {RamVar(var).name:30s} {info}")
        print("Setting info:")
        for setting, info in sorted(client.setting_info.items()):
            print(f"  {setting:3d} {Setting(setting).name:30s} {info}")
        print()
        _print_state(client.state)
        if args.json:
            print(json.dumps(asdict(client.state), indent=2, default=str))
    finally:
        await client.close()


async def cmd_monitor(args: argparse.Namespace) -> None:
    client = VeBusClient(args.port)

    def on_error(err: Exception) -> None:
        print(f"Unavailable: {err}", file=sys.stderr)

    await client.run(_print_state, on_error, interval=args.interval)


async def cmd_sniff(args: argparse.Namespace) -> None:
    reader, writer = await open_serial(args.port)
    decoder = FrameDecoder()
    try:
        while True:
            data = await reader.read(256)
            if not data:
                break
            for frame in decoder.feed(data):
                print(frame.to_bytes().hex(" "))
    finally:
        writer.close()


async def cmd_set_mode(args: argparse.Namespace) -> None:
    if not args.yes:
        sys.exit("This changes how the device operates; add --yes to confirm.")
    client = await _connect(args.port)
    try:
        await client.set_remote_panel_state(Mode(args.mode), args.limit)
        config = await client.read_config()
        print(
            f"Mode now {config.mode} (remote {config.remote_mode}), "
            f"limit {config.actual_current_limit} A"
        )
    finally:
        await client.close()


async def cmd_set_setting(args: argparse.Namespace) -> None:
    if not args.yes:
        sys.exit("This changes a device setting (RAM only); add --yes to confirm.")
    client = await _connect(args.port)
    try:
        await client.complete_setting_info([args.setting])
        before = await client.read_setting(args.setting)
        after = await client.write_setting(args.setting, args.value)
        print(
            f"Setting {args.setting}: {before} -> {after} (RAM only, lost on restart)"
        )
    finally:
        await client.close()


FLAGS = {
    "power-assist": (Setting.FLAGS_0, SettingFlag.ASSIST_ENABLED, False),
    "weak-ac": (Setting.FLAGS_0, SettingFlag.WEAK_AC_INPUT, False),
    "ups": (Setting.FLAGS_0, SettingFlag.DISABLE_WAVE_CHECK, True),
    "solar-wind-priority": (Setting.FLAGS_2, SettingFlag.SOLAR_WIND_PRIORITY, False),
}


async def cmd_set_flag(args: argparse.Namespace) -> None:
    if not args.yes:
        sys.exit("This changes a device setting (RAM only); add --yes to confirm.")
    setting, flag, inverted = FLAGS[args.flag]
    enable = args.state == "on"
    client = await _connect(args.port)
    try:
        raw = await client.set_setting_flag(setting, flag, enable != inverted)
        print(f"Setting {int(setting)} is now 0x{raw:04x} (RAM only, lost on restart)")
    finally:
        await client.close()


async def cmd_force(args: argparse.Namespace) -> None:
    if not args.yes:
        sys.exit("This changes the charge state; add --yes to confirm.")
    client = await _connect(args.port)
    try:
        info = await client.force_charge_state(ForceChargeState[args.state.upper()])
        print(f"Device state {info.state} / {info.charge_state}")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    info = sub.add_parser("info", help="identify the device and read everything once")
    info.add_argument("port")
    info.add_argument("--json", action="store_true", help="also dump the state as JSON")
    info.set_defaults(func=cmd_info)
    monitor = sub.add_parser("monitor", help="poll continuously")
    monitor.add_argument("port")
    monitor.add_argument("--interval", type=float, default=2.0)
    monitor.set_defaults(func=cmd_monitor)
    sniff = sub.add_parser("sniff", help="print every frame received (no requests)")
    sniff.add_argument("port")
    sniff.set_defaults(func=cmd_sniff)
    set_mode = sub.add_parser("set-mode", help="set the remote panel mode")
    set_mode.add_argument("port")
    set_mode.add_argument("mode", choices=[m.value for m in Mode])
    set_mode.add_argument("--limit", type=float, help="AC input current limit in A")
    set_mode.add_argument("--yes", action="store_true")
    set_mode.set_defaults(func=cmd_set_mode)
    set_setting = sub.add_parser(
        "set-setting", help="change a numeric setting in RAM only, e.g. 3 27.0"
    )
    set_setting.add_argument("port")
    set_setting.add_argument("setting", type=int, help="setting id, e.g. 3 = float")
    set_setting.add_argument("value", type=float)
    set_setting.add_argument("--yes", action="store_true")
    set_setting.set_defaults(func=cmd_set_setting)
    set_flag = sub.add_parser("set-flag", help="switch a flag setting in RAM only")
    set_flag.add_argument("port")
    set_flag.add_argument("flag", choices=list(FLAGS))
    set_flag.add_argument("state", choices=["on", "off"])
    set_flag.add_argument("--yes", action="store_true")
    set_flag.set_defaults(func=cmd_set_flag)
    force = sub.add_parser("force", help="force a charge state")
    force.add_argument("port")
    force.add_argument("state", choices=["absorption", "float", "equalise"])
    force.add_argument("--yes", action="store_true")
    force.set_defaults(func=cmd_force)
    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass
    except VeBusError as err:
        sys.exit(f"Error: {err}")


if __name__ == "__main__":
    main()
