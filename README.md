# Victron VE.Bus (MK3) for Home Assistant

> [!CAUTION]
> **Disclaimer — use at your own risk.** This is an unofficial community project. It is
> not made, endorsed or supported by Victron Energy. It is provided "as is", without any
> warranty of any kind (see the [MIT license](LICENSE)). **The authors and contributors
> take no responsibility whatsoever for any damage** to inverters, chargers, batteries,
> other equipment or property, for injury, or for any other loss caused by using this
> integration, its command line tool or this documentation. Controlling an
> inverter/charger can cut the power to your loads and can damage batteries. If you are
> not sure what a setting does, **do not change it**.

A Home Assistant custom integration for Victron Energy VE.Bus inverter/chargers
(MultiPlus, MultiPlus-II, Quattro, ...). It talks to the device directly through a
**Victron MK3-USB interface**, so no GX device (Cerbo, Venus) is needed.

It is a ground-up rewrite inspired by
[j9brown/victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass). It fixes that
project's known bugs and adds new features. See [docs/ANALYSIS.md](docs/ANALYSIS.md) for
the analysis and the roadmap.

> **Status:** v0.7 beta. It is developed and tested against a MultiPlus-II using a
> protocol simulator, and has not yet been verified on real hardware. Treat everything
> beyond monitoring as beta: try the controls with the command line tool first (see
> below), and please report your results.

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
- **Energy** for the Home Assistant Energy dashboard: energy taken from and fed into
  the grid, energy delivered to the loads, and battery energy charged and discharged
  (kWh, integrated from the power readings).
- **States.**
  - Device state (inverting, passthru, charging, ...) and charge state (bulk,
    absorption, float, ...).
  - Effective mode and front switch position.
  - Overload, low battery and temperature alarms (ok / warning / alarm).
  - **Alarm notifications:** an *Alarm* event entity for automations, and a repair
    issue while an alarm is active.
  - AC input connected, the multi-functional relay, the virtual switch and the "ignore
    AC input" state.
- **Diagnostics.**
  - Firmware version, input current limit and its range.
  - Charger settings: absorption and float voltage, charge current, battery capacity,
    charge characteristic and maximum absorption time.
  - Inverter output voltage, low battery shutdown and restart voltage.
  - The eight front panel LEDs, each off, on or blinking.
  - A diagnostics download with communication statistics and the last raw frames.
  - **Connection health:** link quality (share of answered requests), request
    timeouts and checksum errors, plus a repair issue while the connection is poor.
  - A single missed or garbled reply no longer makes values flicker to "unknown":
    the last good reading is kept through up to two missed replies.
- **Multi-unit systems** (parallel, split-phase and three-phase).
  - All units on the VE.Bus are found at startup. Each gets its own device, linked to
    the system device, with AC and battery values, power and device state.
  - The system's AC input, AC output and battery power are the totals over all units.
  - Energy sensors for every unit, besides the system totals.
  - Alarms are system-wide: the MK3 protocol reports the alarm LEDs for the whole
    VE.Bus system, not per unit.
  - Each phase gets its apparent power.
- **Quattro:** the second AC input's current limit and which input is active.
- **Phoenix/Compact inverters** that don't send the standard info frames: values are
  read from RAM variables instead.
- **Keep awake switch.** It stops a switched-off device from sleeping, which would cut
  the MK3's power.
- **Monitor only, or monitor and control.** You choose this when you add the
  integration and can change it with *Reconfigure*. *Monitor only* never changes how
  the Multi operates.
- **Controls** (only with *Monitor and control*):
  - On the dashboard, for routine use and automations: the remote AC input current
    limit, charge current limit, configured AC input limit(s), buttons to force
    absorption or float, solar & wind priority, and ESS external control (grid
    setpoint per phase, disable charge / feed-in).
  - **Guided device actions** under *Configure → Device actions*, for everything that
    can cut the power on the AC outputs, restart the Multi, change charge voltages or
    write the EEPROM: mode, passthru, charge voltages and other settings (in RAM or
    permanently), PowerAssist / weak AC input / UPS function, equalisation and restart.
    Each action shows its risks in a dialog and must be confirmed.
  - The `victron_vebus.set_remote_panel_state` action for automations, which requires
    `acknowledge_risk: true`.
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
4. Choose **Monitor only** (recommended to start with) or **Monitor and control**.
   *Monitor and control* shows a warning you must confirm: **do not change any setting
   if you are not sure what it does.** You can switch between the two later with
   *Settings → Devices & services → Victron VE.Bus → ⋮ → Reconfigure*.

### Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 2 s | The MK3 runs at 2400 baud and a full poll takes about one second. |
| Show alarms as repair issues | on | Raise a repair issue while an overload, low battery or temperature alarm is active. See [Alarm notifications](#alarm-notifications). |
| Enable battery operational limits | off | Only with *Monitor and control*. See [Battery operational limits](#battery-operational-limits-advanced-beta). |

Monitoring or control is not an option here: it is chosen when the integration is
added, or with *Reconfigure*.

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
| Alarm (event) | Fires when an alarm starts, escalates or clears. See [Alarm notifications](#alarm-notifications). |
| AC input energy imported / exported, AC output energy, Battery energy charged / discharged | kWh for the Energy dashboard. See [Energy](#energy). |
| AC input connected | Mains LED lit, i.e. the AC input relay is closed. |
| Relay, Virtual switch, Ignore AC input | Diagnostic. |
| AC input current limit (+ min/max) | Diagnostic; min/max are *(disabled)*. |
| Absorption / float voltage, charge current limit, battery capacity | Diagnostic, read from the device settings. |
| Inverter output voltage, Low battery shutdown / restart voltage, Charge characteristic, Maximum absorption time | Diagnostic, read from the device settings. The restart voltage is the shutdown voltage plus its hysteresis. |
| Firmware version, MK3 interface version | Diagnostic. |
| Mains / Absorption / Bulk / Float / Inverter / Overload / Low battery / Temperature LED | Diagnostic: the front panel LEDs, each off, on or blinking. |
| Keep awake (switch) | See below. |
| Solar & wind priority voltage | Diagnostic. Only on firmware 506+ with the feature configured. |
| Configured AC input 2 current limit | Diagnostic. Only on devices with a second AC input (Quattro). |
| AC input / output apparent power L1–L3 | Multi-phase systems only. Voltage × current per phase, from the info frames. |
| *Unit N* AC input/output voltage, current and power; battery voltage, current and power; device state; energy (as for the system) | Multi-unit systems only, one device per unit (*Victron VE.Bus unit N*). Units are read in turn, one per poll. |
| Link quality, Request timeouts, Checksum errors | Diagnostic. See [Connection health](#connection-health). Checksum errors is *(disabled)*. |
| ESS active setpoint (L1–L3) | Diagnostic, when the ESS assistant is installed. The setpoint the assistant uses; positive = taken from the grid. |
| PowerAssist, Weak AC input, UPS function (binary sensors) | Diagnostic, read-only. Change them with the guided device actions. |
| ESS external control (switch), ESS grid setpoint (L1–L3) (number), ESS disable charge / feed-in (switches) | *Monitor and control* with the ESS assistant installed. See below. |
| Battery limit charge/discharge voltage/current (numbers) | Only with *Enable battery operational limits*; firmware 415+. See below. |
| Remote AC input current limit (number) | *Monitor and control*. The mode is changed with the guided device actions. |
| Force absorption / float (buttons) | *Monitor and control*; firmware 400+. Equalisation is a guided device action. |
| Charge current limit, Configured AC input (2) current limit (numbers) | *Monitor and control*; firmware 412+. RAM only. Charge voltages are guided device actions. |
| Solar & wind priority (switch), Solar & wind priority voltage (number) | *Monitor and control*; firmware 506+ and the feature configured. RAM only. |

### Energy

VE.Bus devices have no energy counters that can be read through the MK3 (a GX device
calculates them the same way), so the integration integrates the power readings over
time. The totals are kept across restarts.

| Energy dashboard | Entity |
|---|---|
| Grid consumption | AC input energy imported |
| Return to grid | AC input energy exported |
| Battery: energy going in / out | Battery energy charged / discharged |
| Individual device | AC output energy |

- The values are estimates. They are as accurate as the device's power readings and
  are sampled at the polling interval (2 s by default); short peaks between polls are
  missed. Expect a few percent difference from a calibrated energy meter.
- Nothing is counted while the device is unreachable or Home Assistant is stopped.
- Battery energy is measured on the DC side of the Multi. It does not include solar
  chargers or DC loads connected directly to the battery.
- AC output energy counts power going to the loads; power fed back from the AC output
  (e.g. an AC-coupled PV inverter) is not counted.
- In multi-unit systems the system energy is the total of all units, and every unit
  has its own energy sensors too.

### Alarm notifications

The Multi signals three alarms with its LEDs: **overload**, **low battery** and
**temperature**. A blinking LED is a *warning* (pre-alarm); a lit LED is an *alarm*,
and the Multi usually switches off the inverter, so the AC outputs can lose power.

- The **Alarm** event entity fires `<alarm>_warning`, `<alarm>_alarm` and
  `<alarm>_cleared` (for example `low_battery_warning`), with the attributes `alarm`
  and `level`. An alarm that is already active when Home Assistant starts is reported
  too.
- While an alarm is active, a **repair issue** explains what it means and what to
  check. It disappears when the alarm clears. Turn off *Show alarms as repair issues*
  in the integration settings if you don't want them.

Example: a notification on your phone for every alarm and warning.

```yaml
triggers:
  - trigger: state
    entity_id: event.victron_ve_bus_alarm
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Victron alarm
      message: >
        {{ state_attr('event.victron_ve_bus_alarm', 'alarm') }}:
        {{ state_attr('event.victron_ve_bus_alarm', 'level') }}
```

### Connection health

At 2400 baud the MK3 link is slow, and a reply can be lost to a bad cable, interference
or other devices busy on the VE.Bus.

- **Link quality** is the share of requests answered in the last 5 minutes. It should
  be 100% or very close to it.
- **Request timeouts** counts the requests that got no reply. **Checksum errors**
  *(disabled)* counts bytes skipped to recover from corrupted data.
- A value keeps its last good reading through up to two missed replies before it
  becomes *unknown*.
- Below 90% (with at least 100 requests) a repair issue lists what to check. It
  disappears once the link quality is back above 95%.
- These sensors stay available while the device is unreachable.

### Keep awake

When a VE.Bus device is switched off, it goes to sleep after a while. Sleeping stops
the power to the MK3, so nothing can switch the device back on remotely. With **Keep
awake** on (the default), the integration sets the MK3's *standby* flag, which keeps
the device awake. It resends the flag on every poll because the flag is lost whenever
the device loses power. A switched-off device that is kept awake uses a few watts more.

### Guided device actions (Configure → Device actions)

Everything that can cut the power on AC out 1 and AC out 2, restart the Multi, change
battery charge voltages or write the EEPROM is only available here. Each action opens a
dialog that lists its risks; nothing is sent until you tick *I understand the risks*
and submit.

| Action | What can happen |
|---|---|
| **Restart the Multi** | The Multi restarts and its relays open: **AC out 1 and AC out 2 lose power** for several seconds. Settings changed in RAM are lost. |
| **Mode: off** | **AC out 1 and AC out 2 lose power** until the mode is changed back. |
| **Mode: charger only** | No inverter: the AC outputs **lose power on a grid failure**. |
| **Mode: inverter only** | The Multi disconnects from the grid (relay switches); **AC out 2 loses power**; the battery discharges. |
| **Mode: on** | Normal operation; if the Multi was off it starts and its relays click. |
| **Passthru** (limit 0 A) | With PowerAssist off the charger stops and the grid is passed through; with PowerAssist on the Multi inverts from the battery instead. |
| **Charger settings** | Wrong charge voltages or currents can damage the battery; too high an AC input limit can trip the grid breaker. |
| **PowerAssist / weak AC / UPS** | PowerAssist off can trip the grid breaker under load; UPS off makes transfers slower, which **can interrupt the AC outputs** on a grid failure. |
| **Equalisation** | Charges above the absorption voltage — never for lithium, gel or AGM batteries unless allowed by the manufacturer. |

Charger settings and features can be changed **in RAM** (lost when the Multi restarts)
or **permanently** in the EEPROM. Permanent writes replace the value set with
VictronConnect or VEConfigure, are limited to one per setting every 5 minutes to protect
the EEPROM, and should not be automated. Victron documents no restart for these writes
and the integration does not restart the Multi, but some settings may only take full
effect after the next restart.

### The remote panel action (automations)

`victron_vebus.set_remote_panel_state` sets the mode and optionally the AC input current
limit, like a Digital Multi Control panel. Because modes other than *on* and a 0 A limit
can cut the power on the AC outputs, the action fails unless `acknowledge_risk: true`,
and it only works on an entry set up for *Monitor and control*.
Changing the mode keeps the current limit when none is given.

```yaml
action: victron_vebus.set_remote_panel_state
data:
  config_entry_id: 01J...   # pick the Victron VE.Bus entry in the UI editor
  mode: "on"
  current_limit: 10
  acknowledge_risk: true
```

These commands only change RAM in the device, not its EEPROM. They are lost when the
device restarts.

> Do not use the controls if a GX device or another remote panel also controls the
> Multi. They would fight over the remote switch state.

### Changing settings (beta)

The setting numbers and switches on the dashboard change the device's settings **in RAM
only**, never its stored (EEPROM) configuration (only the guided device actions can
write permanently). A change applies immediately and is lost when the
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
- PowerAssist, weak AC input and UPS function are shown as read-only sensors; they are
  changed with the guided device actions because they affect how the Multi transfers
  between grid and inverter.

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

The option *Enable battery operational limits* (which also needs *Monitor and
control*, device firmware 415 and MK3 firmware 203) creates four numbers:

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
