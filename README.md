# Victron VE.Bus (MK3) for Home Assistant

A Home Assistant custom integration for Victron Energy VE.Bus inverter/chargers
(MultiPlus, MultiPlus-II, Quattro, ...). It talks to the device directly through a
**Victron MK3-USB interface**, so no GX device (Cerbo, Venus) is needed.

It is a ground-up rewrite inspired by
[j9brown/victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass). It fixes that
project's known bugs and adds new features. See [docs/ANALYSIS.md](docs/ANALYSIS.md) for
the analysis and the roadmap.

> **Status:** v0.1, monitoring first. It is developed and tested against a single
> MultiPlus-II using a protocol simulator, and has not yet been verified on real
> hardware. Multi-unit systems (parallel or three-phase) are partially supported: phases
> are detected, but every value comes from the L1 master. Please report your results.

## Features

- **Bundled protocol driver.**
  - Pure Python, no extra PyPI package. Installation does not need internet access and
    does not break on Home Assistant upgrades.
  - Survives unplugging, sleeping devices and line noise. It reconnects automatically
    with backoff and resynchronises the byte stream after corrupted frames.
  - All requests are serialised, so control commands never get crossed with polling
    replies.
- **Setup.**
  - The MK3 is auto-discovered over USB, or you can pick a serial port.
  - Remote connections work too, e.g. ser2net via `socket://host:port`.
  - The device is identified by the MK3 serial number, so its entities survive
    `/dev/ttyUSBx` renumbering.
- **Measurements.**
  - AC input and output voltage, current and frequency, per detected phase.
  - AC input and output power.
  - Battery voltage, current and power; charger and inverter DC current; battery ripple.
  - State of charge, when the built-in battery monitor is configured.
- **States.**
  - Device state (inverting, passthru, charging, ...) and charge state (bulk,
    absorption, float, ...).
  - Effective mode and front switch position.
  - Overload, low battery and temperature alarms (ok / warning / alarm).
  - AC input connected, the multi-functional relay and the "ignore AC input" state.
- **Diagnostics.**
  - Firmware version, input current limit and its range.
  - Charger settings: absorption and float voltage, charge current, battery capacity.
  - Raw LED states.
  - A diagnostics download with communication statistics and the last raw frames.
- **Keep awake switch.** It stops a switched-off device from sleeping, which would cut
  the MK3's power.
- **Optional controls** (off by default):
  - mode select (on / off / charger only / inverter only)
  - AC input current limit
  - the `victron_vebus.set_remote_panel_state` action

## Requirements

- Home Assistant 2026.5 or newer.
- A Victron **MK3-USB** interface. The older MK2-USB also works, but without the keep
  awake function.
- Exactly one program may use the MK3 at a time. Close VictronConnect and VEConfigure
  while Home Assistant is running.

## Installation

1. In HACS, open *Integrations* → ⋮ → *Custom repositories*. Add
   `https://github.com/tubalainen/victron-vebus-hacs` with category *Integration*.
2. Install **Victron VE.Bus (MK3)** and restart Home Assistant.
3. Plug in the MK3. Home Assistant should discover it. Otherwise go to *Settings →
   Devices & services → Add integration → Victron VE.Bus (MK3)*, then choose the serial
   port or enter a path or URL.

### Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 2 s | The MK3 runs at 2400 baud and a full poll takes about one second. |
| Enable control entities | off | Adds the mode select, the AC input current limit number and the `set_remote_panel_state` action. |

## Entities

Entities marked *(disabled)* are created disabled and can be enabled in the entity
settings.

| Entity | Notes |
|---|---|
| AC input voltage / current / frequency | One set per phase. Named "... L1/L2/L3" on multi-phase systems. |
| AC output voltage / current | One set per phase. |
| AC input power, AC output power | Positive input power means power taken from the grid. |
| AC output frequency | |
| Battery voltage, current, power | Positive means charging. |
| Charger current, Inverter DC current | DC side, from the DC info frame. |
| State of charge | Only created if a battery capacity is configured in the device. |
| Device state | down, startup, off, slave, invert_full/half/aes, power_assist, bypass (passthru), charge |
| Charge state | not_charging, init, bulk, absorption, float, storage, repeated/forced absorption, equalise, bulk_stopped |
| Mode, Front switch | The mode in effect and the physical switch position. |
| Overload / Low battery / Temperature alarm | ok, warning (LED blinking), alarm (LED on) |
| AC input connected | Mains LED lit, i.e. the AC input relay is closed. |
| Relay, Ignore AC input | Diagnostic. |
| AC input current limit (+ min/max) | Diagnostic; min/max are *(disabled)*. |
| Absorption / float voltage, charge current limit, battery capacity | Diagnostic, read from the device settings. |
| Firmware version, MK3 interface version | Diagnostic. |
| LED binary sensors (8) | *(disabled)*, with a `blinking` attribute. |
| Keep awake (switch) | See below. |
| Remote mode (select), Remote AC input current limit (number) | Only with *Enable control entities*. |

### Keep awake

When a VE.Bus device is switched off, it goes to sleep after a while. Sleeping stops
the power to the MK3, so nothing can switch the device back on remotely. With **Keep
awake** on (the default), the integration sets the MK3's *standby* flag, which keeps
the device awake. It resends the flag on every poll because the flag is lost whenever
the device loses power. A switched-off device that is kept awake uses a few watts more.

### Controls and the remote panel action

The controls act like a Digital Multi Control remote panel:

- Changing the mode keeps the current input limit.
- Changing the limit keeps the mode that is currently in effect.
- The device clamps the limit to the min/max it reports.
- Per the Victron documentation, a limit of 0 makes the device pass through (with
  PowerAssist off) or invert (with PowerAssist on). The number entity respects the
  device minimum; use the action to send 0.

```yaml
action: victron_vebus.set_remote_panel_state
target:
  entity_id: select.victron_ve_bus_remote_mode
data:
  mode: "on"
  current_limit: 10
```

These commands only change RAM in the device, not its EEPROM. They are lost when the
device restarts.

> Do not use the controls if a GX device or another remote panel also controls the
> Multi. They would fight over the remote switch state.

## Testing on the hardware without Home Assistant

`tools/vebus_cli.py` uses the same protocol code without Home Assistant. Stop Home
Assistant first, because only one program can open the port.

```bash
pip install serialx
python tools/vebus_cli.py info    /dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_XXXX-if00-port0
python tools/vebus_cli.py monitor /dev/serial/by-id/... --interval 2
python tools/vebus_cli.py sniff   /dev/serial/by-id/...      # print raw frames
```

Please include the `info` output when reporting issues for devices other than the
MultiPlus-II.

## Development

```bash
pip install -r requirements_test.txt -r requirements_lint.txt
pytest --cov
ruff check . && ruff format --check . && mypy custom_components/victron_vebus
```

How the code is organised:

- **`custom_components/victron_vebus/vebus/`:** the protocol package. It has no Home
  Assistant imports and is type checked in strict mode.
- **`tests/vebus/simulator.py`:** a scripted MK3 with a MultiPlus-II behind it. Both the
  protocol tests and the Home Assistant tests use it.

### Releasing

1. Bump `version` in `custom_components/victron_vebus/manifest.json` (e.g. `0.2.0`) and
   merge that to `main`.
2. Tag the merge commit on `main` and push the tag:

   ```bash
   git checkout main && git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```

The release workflow checks that the tag matches the manifest version, creates the
GitHub release with generated notes and attaches `victron_vebus.zip`. HACS then offers
the new version as an update. Tags with a suffix such as `v0.2.0-beta1` become
pre-releases.

## Credits

- [j9brown/victron-mk3](https://github.com/j9brown/victron-mk3) and
  [victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass), for the original
  integration and for discovering the MK3 `H` (standby) command and the MultiPlus-II
  quirks.
- [xcellsior/ve-bus-programming](https://github.com/xcellsior/ve-bus-programming), for
  the VE.Bus settings research.
- Victron Energy, *Interfacing with VE.Bus products – MK2 Protocol 3.14*.

This project is not affiliated with Victron Energy.
