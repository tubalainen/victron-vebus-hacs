# Entities

[← Back to the README](../README.md) · [Documentation index](README.md)

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
| Alarm (event) | Fires when an alarm starts, escalates or clears. See [Alarm notifications](alarms.md). |
| AC input energy imported / exported, AC output energy, Battery energy charged / discharged | kWh for the Energy dashboard. See [Energy](energy.md). |
| AC input connected | Mains LED lit, i.e. the AC input relay is closed. |
| Relay, Virtual switch, Ignore AC input | Diagnostic. |
| AC input current limit (+ min/max) | Diagnostic; min/max are *(disabled)*. |
| Absorption / float voltage, charge current limit, battery capacity | Diagnostic, read from the device settings. |
| Inverter output voltage, Low battery shutdown / restart voltage, Charge characteristic, Maximum absorption time | Diagnostic, read from the device settings. The restart voltage is the shutdown voltage plus its hysteresis. |
| Firmware version, MK3 interface version | Diagnostic. |
| Mains / Absorption / Bulk / Float / Inverter / Overload / Low battery / Temperature LED | Diagnostic: the front panel LEDs, each off, on or blinking. |
| Keep awake (switch) | See [Keep awake](connection.md#keep-awake). |
| Solar & wind priority voltage | Diagnostic. Only on firmware 506+ with the feature configured. |
| Configured AC input 2 current limit | Diagnostic. Only on devices with a second AC input (Quattro). |
| AC input / output apparent power L1–L3 | Multi-phase systems only. Voltage × current per phase, from the info frames. |
| *Unit N* AC input/output voltage, current and power; battery voltage, current and power; device state; energy (as for the system) | Multi-unit systems only, one device per unit (*Victron VE.Bus unit N*). Units are read in turn, one per poll. |
| Link quality, Request timeouts, Checksum errors | Diagnostic. See [Connection health](connection.md). Checksum errors is *(disabled)*. |
| ESS active setpoint (L1–L3) | Diagnostic, when the ESS assistant is installed. The setpoint the assistant uses; positive = taken from the grid. |
| PowerAssist, Weak AC input, UPS function (binary sensors) | Diagnostic, read-only. With *Monitor and control* there are also switches. |
| ESS external control (switch), ESS grid setpoint (L1–L3) (number), ESS disable charge / feed-in (switches) | *Monitor and control* with the ESS assistant installed. See [ESS external control](ess-and-bms.md#ess-external-control-beta). |
| Battery limit charge/discharge voltage/current (numbers) | Only with *Enable battery operational limits*; firmware 415+. See [Battery operational limits](ess-and-bms.md#battery-operational-limits-advanced-beta). |
| Remote AC input current limit (number) | *Monitor and control*. The limit last sent through the remote panel; unknown until one is set if the MK3 holds none. |
| Force absorption / float (buttons) | *Monitor and control*; firmware 400+. |
| Charge current limit, Configured AC input (2) current limit (numbers) | *Monitor and control*; firmware 412+. RAM only. Attributes *Active value source* and *Stored value*, see [RAM and EEPROM](controls.md#ram-and-eeprom-the-multi-is-the-source). |
| Allow critical changes (switch) | *Monitor and control*. Off by default; turns itself off after 10 minutes. See [Controlling the Multi](controls.md). |
| Mode (select), Stop grid charging (0 A), PowerAssist, Weak AC input, UPS function (switches), Absorption / Float voltage (numbers) | *Monitor and control*; critical. Settings need firmware 412+ and change RAM only. See [Modes and Stop grid charging compared](controls.md#modes-and-stop-grid-charging-compared). |
| Start equalisation, Restart the Multi, Save settings permanently (buttons) | *Monitor and control*; critical. Equalisation needs firmware 400+, saving 412+. |
| Unsaved setting changes | Diagnostic, *Monitor and control*. The number of settings changed in RAM only, listed in the `settings` attribute. |
| Solar & wind priority (switch), Solar & wind priority voltage (number) | *Monitor and control*; firmware 506+ and the feature configured. RAM only. |

## Multi-unit, Quattro and Compact devices

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
