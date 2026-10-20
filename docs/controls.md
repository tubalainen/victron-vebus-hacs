# Controlling the Multi

[← Back to the README](../README.md) · [Documentation index](README.md)

> [!CAUTION]
> Critical controls can cut the power on AC out 1 and AC out 2, restart the Multi and
> change battery charge voltages. The authors and contributors take no responsibility
> whatsoever for any damage; see the disclaimer in the [README](../README.md). If you
> are not sure what a setting does, **do not change it**.

With *Monitor and control*, every control is an entity on the Multi's device page, so
it can be used from dashboards, automations, scripts and voice assistants.

**Routine controls** work directly: current limits, charge current, force absorption or
float, solar & wind priority and ESS.

**Critical controls** can cut the power on AC out 1 and AC out 2, restart the Multi,
change battery charge voltages or write the EEPROM. They are locked:

1. Turn on **Allow critical changes** (under *Configuration* on the device page). It
   turns itself off again after 10 minutes; its `turns_off_at` attribute says when.
2. Use the critical control: **Mode**, **Passthru**, **PowerAssist**, **Weak AC
   input**, **UPS function**, **Absorption voltage**, **Float voltage**, **Start
   equalisation**, **Restart the Multi** or **Save settings permanently**.

While the lock is off, a critical control refuses the change and says what could
happen, for example *"Mode inverter only is a critical change: the Multi disconnects
from the grid and AC out 2 loses power"*. The [table of risks](#guided-device-actions-configure--device-actions) lists them all.

**RAM first, then save.** Setting changes from entities go to the Multi's RAM only:
they apply immediately and are lost when the Multi restarts, so they are safe to try.
**Unsaved setting changes** shows how many there are. **Save settings permanently**
stores them in the EEPROM (limited to once per setting every 5 minutes).

**Passthru** sets the remote AC input current limit to 0 A. Turning it off restores the
last limit (or the configured AC input limit). Changing the mode while in passthru keeps
passthru.

For an extra confirmation on a dashboard, use a card's own confirmation, for example
a button card:

```yaml
type: button
name: Restart the Multi
entity: button.victron_ve_bus_restart_the_multi
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.victron_ve_bus_restart_the_multi
  confirmation:
    text: Restarting cuts the power on AC out 1 and AC out 2 for several seconds. Continue?
```

For automations and scripts, see [Actions](actions.md).

> Do not use the controls if a GX device or another remote panel also controls the
> Multi. They would fight over the remote switch state.

## Guided device actions (Configure → Device actions)

The same critical changes, with a full explanation: each action opens a dialog that
lists its risks, and nothing is sent until you tick *I understand the risks* and
submit. Charger settings and features can be written in RAM or permanently here too.

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

## Changing settings (beta)

The setting numbers and switches on the dashboard change the device's settings **in RAM
only**, never its stored (EEPROM) configuration, until you press *Save settings
permanently* (or use `persistent: true` or the guided device actions). A change applies immediately and is lost when the
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
- PowerAssist, weak AC input and UPS function are critical switches (behind *Allow
  critical changes*) because they affect how the Multi transfers between grid and
  inverter.

Requirements:

- Device firmware 412 or newer for setting changes, 400 for forcing a charge state,
  and 506 for solar & wind priority.
- The firmware version is shown in the *Firmware version* sensor. Entities whose
  feature the firmware doesn't support are not created.
