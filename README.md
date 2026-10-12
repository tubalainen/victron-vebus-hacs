# Victron VE.Bus (MK3) for Home Assistant

A Home Assistant custom integration for Victron Energy VE.Bus inverter/chargers
(MultiPlus, MultiPlus-II, Quattro, ...). It talks to the device directly through a
**Victron MK3-USB interface**, so no GX device (Cerbo, Venus) is needed.

It is a ground-up rewrite inspired by
[j9brown/victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass). It fixes that
project's known bugs and adds new features. See [docs/ANALYSIS.md](docs/ANALYSIS.md) for
the analysis and the roadmap.

> **Status:** v0.4 beta. It is developed and tested against a MultiPlus-II using a
> protocol simulator, and has not yet been verified on real hardware. Treat the setting
> controls (v0.2), the multi-unit, Quattro and Compact support (v0.3) and the ESS and
> battery limit controls (v0.4) as beta: try
> the controls with the command line tool first (see below), and please report your
> results.

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
- **Multi-unit systems** (parallel, split-phase and three-phase).
  - All units on the VE.Bus are found at startup. Each gets its own device, linked to
    the system device, with AC and battery values, power and device state.
  - The system's AC input, AC output and battery power are the totals over all units.
  - Each phase gets its apparent power.
- **Quattro:** the second AC input's current limit and which input is active.
- **Phoenix/Compact inverters** that don't send the standard info frames: values are
  read from RAM variables instead.
- **Keep awake switch.** It stops a switched-off device from sleeping, which would cut
  the MK3's power.
- **Optional controls** (off by default):
  - mode select (on / off / charger only / inverter only)
  - AC input current limit
  - the `victron_vebus.set_remote_panel_state` action
  - buttons to force absorption, float or equalise
  - charger settings: absorption and float voltage, charge current, configured AC input
    current limit (and AC input 2 on a Quattro)
  - PowerAssist, weak AC input, UPS function and solar & wind priority switches, plus
    the solar & wind priority voltage. Settings are changed in RAM only.
  - ESS external control (needs the ESS assistant): the grid setpoint (per phase on
    three-phase systems) plus disable charge and disable feed-in switches
- **Battery operational limits** (advanced, lithium only, separate option): the Multi
  receives charge/discharge voltage and current limits, like it would from a BMS.

## Requirements

- Home Assistant 2026.8 or newer.
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
| Solar & wind priority voltage | Diagnostic. Only on firmware 506+ with the feature configured. |
| Configured AC input 2 current limit | Diagnostic. Only on devices with a second AC input (Quattro). |
| AC input / output apparent power L1–L3 | Multi-phase systems only. Voltage × current per phase, from the info frames. |
| *Unit N* AC input/output voltage, current and power; battery voltage, current and power; device state | Multi-unit systems only, one device per unit (*Victron VE.Bus unit N*). Units are read in turn, one per poll. |
| ESS active setpoint (L1–L3) | Diagnostic, when the ESS assistant is installed. The setpoint the assistant uses; positive = taken from the grid. |
| ESS external control (switch), ESS grid setpoint (L1–L3) (number), ESS disable charge / feed-in (switches) | Only with *Enable control entities* and the ESS assistant installed. See below. |
| Battery limit charge/discharge voltage/current (numbers) | Only with *Enable battery operational limits*; firmware 415+. See below. |
| Remote mode (select), Remote AC input current limit (number) | Only with *Enable control entities*. |
| Force absorption / float / equalise (buttons) | Only with *Enable control entities*; firmware 400+. |
| Absorption voltage, Float voltage, Charge current limit, Configured AC input current limit (numbers) | Only with *Enable control entities*; firmware 412+. RAM only, see below. |
| PowerAssist, Weak AC input, UPS function (switches) | Only with *Enable control entities*; firmware 412+. RAM only. |
| Solar & wind priority (switch), Solar & wind priority voltage (number) | Only with *Enable control entities*; firmware 506+ and the feature configured. RAM only. |

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

### Changing settings (beta)

The setting numbers and switches change the device's settings **in RAM only**, never
its stored (EEPROM) configuration. A change applies immediately and is lost when the
device restarts, which then goes back to the configuration made with VictronConnect
or VEConfigure. Because nothing is written to the EEPROM, automations can change these
settings as often as needed without wearing it out.

How a change is made:

- RAM-only writes need the MK3 in "long frame" mode. The integration switches the
  interface to long frames for the write and straight back to short frames afterwards.
  This is the same sequence VEConfigure uses, and polling pauses for that moment.
- Every write is read back. If the device reports a different value, the action fails
  with an error.
- Values are limited to the range the device reports for each setting. The float
  voltage can't be set above the absorption voltage, nor the absorption voltage below
  the float voltage. The solar & wind priority voltage can't be set above the
  absorption voltage.
- A switch changes only its own bit of the device's flag settings, and only bits the
  device reports as supported.

What the switches do:

- **Solar & wind priority off** makes the device charge the battery to the full
  absorption voltage again ("charge to 100%"). Switch it back on afterwards, or restart
  the device.
- **UPS function off** sets the "disable wave check" flag, so the device is more
  tolerant of a poor AC input (for example a generator) but switches over more slowly.

Requirements:

- Device firmware 412 or newer for setting changes, 400 for forcing a charge state,
  and 506 for solar & wind priority.
- The firmware version is shown in the *Firmware version* sensor. Entities whose
  feature the firmware doesn't support are not created.

### ESS external control (beta)

When the ESS assistant is installed on the Multi, Home Assistant can set the power
taken from or fed into the grid, like a GX device in "ESS mode 3". The assistant is
found at startup by walking its RAM records (the ESS wiki §3.6). Without the
assistant, no ESS entities are created.

- **ESS grid setpoint:** positive takes power from the grid, negative feeds power
  into the grid. This is the same sign as the GX device's AcPowerSetpoint; the
  integration inverts it for the MK3. On three-phase systems there is one setpoint per
  phase master (L1–L3).
- **ESS external control:** the setpoint is only sent while this switch is on. While
  it is on, the setpoint is re-sent at least every 10 seconds, because the assistant
  falls back when it receives no updates. Turning it off writes 0 W, which returns the
  assistant to its normal behaviour. Unloading the integration does the same. The
  switch and the setpoints are restored after a Home Assistant restart.
- **ESS disable charge / disable feed-in:** the assistant's flags, written to every
  phase master. With both on, the Multi goes to passthru.
- Battery protection in the Multi (sustain, low battery) always takes priority over
  the setpoint.

### Battery operational limits (advanced, beta)

> **Warning.** This is a BMS function for lithium batteries. Once the Multi has
> received battery limits, it **stops charging and inverting if it receives none for 5
> minutes**, and keeps expecting them until it is restarted. Only enable this if Home
> Assistant runs reliably, and restart the Multi after turning it off.

The option *Enable battery operational limits* (which also needs *Enable control
entities*, device firmware 415 and MK3 firmware 203) creates four numbers:

- charge voltage and charge current limit
- discharge voltage and discharge current limit (0 A stops discharging)

The limits are sent on every poll. They start from the device's absorption voltage and
charge current, 0 V and 1000 A, and keep their values across restarts. Turning the
option off asks for confirmation, because the Multi must then be restarted within
5 minutes.

## Testing on the hardware without Home Assistant

`tools/vebus_cli.py` uses the same protocol code without Home Assistant. Stop Home
Assistant first, because only one program can open the port.

```bash
pip install serialx
python tools/vebus_cli.py info    /dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_XXXX-if00-port0
python tools/vebus_cli.py monitor /dev/serial/by-id/... --interval 2
python tools/vebus_cli.py sniff   /dev/serial/by-id/...      # print raw frames

# v0.2 controls (RAM only, lost when the device restarts):
python tools/vebus_cli.py set-setting /dev/serial/by-id/... 3 27.0 --yes   # float voltage
python tools/vebus_cli.py set-flag    /dev/serial/by-id/... weak-ac on --yes
python tools/vebus_cli.py force       /dev/serial/by-id/... absorption --yes

# v0.4: hold an ESS grid setpoint for 60 s (positive = import), then back to 0 W
python tools/vebus_cli.py ess-setpoint /dev/serial/by-id/... 300 --seconds 60 --yes
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

Bump `version` in `custom_components/victron_vebus/manifest.json` (e.g. `0.2.0`) in a
pull request and merge it to `main`. That's all: the release workflow sees the new
version, creates the `v0.2.0` tag on the merge commit, publishes the GitHub release with
generated notes and attaches `victron_vebus.zip`. HACS then offers the new version as
an update. Versions with a suffix such as `0.2.0-beta1` become pre-releases.

Pushing a `vX.Y.Z` tag by hand, or publishing a release in the GitHub UI, also works.
The workflow then only checks that the tag matches the manifest version and attaches
the zip. A failed release can be retried from the Actions tab (*Release → Run
workflow*).

## Credits

- [j9brown/victron-mk3](https://github.com/j9brown/victron-mk3) and
  [victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass), for the original
  integration and for discovering the MK3 `H` (standby) command and the MultiPlus-II
  quirks.
- [xcellsior/ve-bus-programming](https://github.com/xcellsior/ve-bus-programming), for
  the VE.Bus settings research.
- Victron Energy, *Interfacing with VE.Bus products – MK2 Protocol 3.14*.

This project is not affiliated with Victron Energy.
