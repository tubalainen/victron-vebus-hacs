# Controlling the Multi

[← Back to the README](../README.md) · [Documentation index](README.md)

> [!CAUTION]
> Critical controls can cut the power on AC out 1 and AC out 2, restart the Multi and
> change battery charge voltages. The authors and contributors take no responsibility
> whatsoever for any damage; see the disclaimer in the [README](../README.md). If you
> are not sure what a setting does, **do not change it**.

With *Monitor and control*, every control is an entity on the Multi's device page, so
it can be used from dashboards, automations, scripts and voice assistants.

**Routine controls** work directly: the remote AC input current limit (above 0 A),
force absorption or float, and solar & wind priority.

**Critical controls** can cut the power on AC out 1 and AC out 2, restart the Multi,
change battery charge voltages or write the EEPROM. They are locked:

1. Turn on **Allow critical changes** (under *Configuration* on the device page). It
   turns itself off again after 10 minutes; its `turns_off_at` attribute says when.
2. Use the critical control: **Mode**, **Stop grid charging (0 A)** (or a remote limit
   of 0 A), **PowerAssist**, **Weak AC input**, **UPS function**, **Absorption
   voltage**, **Float voltage**, **Charge current limit**, **Configured AC input
   current limit(s)**, the **battery limits**, **Start equalisation**, **Restart the
   Multi** or **Save settings permanently**. Since 1.1.11 the charge current and the
   configured AC input limits are critical too: a wrong value can damage the battery or
   trip a breaker.
3. Turn **Allow critical changes** off again when you are done. While it is on, *every*
   critical change is allowed, by anyone using the dashboard.

While the lock is off, a critical control refuses the change and says what could
happen, for example *"Mode inverter only is a critical change: the Multi disconnects
from the grid and AC out 2 loses power"*. The [table of risks](#guided-device-actions-configure--device-actions) lists them all.

**RAM first, then save.** Setting changes from entities go to the Multi's RAM only:
they apply immediately and are lost when the Multi restarts, so they are safe to try.
**Unsaved setting changes** shows how many there are. **Save settings permanently**
stores them in the EEPROM (limited to once per setting every 5 minutes).

**Stop grid charging (0 A)** sets the remote AC input current limit to 0 A: the charger
stops and the grid is passed straight through to the AC outputs (Victron calls this
*passthru*). Use it to stop charging from the grid for a while, for example during
expensive hours or on a weak generator. Normal operation (mode *On*) does **not** need
it: then the Multi passes the grid through and charges the battery at the same time, and
the *Device state* sensor shows *Passthru* whenever the battery needs no charging.
Turning the switch off restores the last remote limit above 0 A, else the last limit
in effect, else the configured limit of the active AC input. There is no fixed default
(it could be above the breaker): if no limit is known, give one in the guided dialog.
The switch follows the remote panel state the MK3 reports, and turns off when the
Multi restarts. Changing the mode while grid charging is stopped keeps the 0 A limit,
and a mode change or a new limit keeps the mode requested through the remote panel
(not the mode in effect, which also depends on the Multi's front switch). See
[Modes and Stop grid charging compared](#modes-and-stop-grid-charging-compared).

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
    text: Restarting power cycles every device on AC out 1 and AC out 2. Continue?
```

For automations and scripts, see [Actions](actions.md).

> Do not use the controls if a GX device or another remote panel also controls the
> Multi. They would fight over the remote switch state.

## Modes and Stop grid charging compared

*Stop grid charging (0 A)* and *Mode: Inverter only* are easy to mix up, but they do
nearly opposite things with the grid:

| | Grid connected | Loads powered by | Battery | AC out 1 | AC out 2 | Typical use |
|---|---|---|---|---|---|---|
| **Mode: On** (normal) | Yes | Grid | Charged | Powered | Powered | Everyday operation |
| **Stop grid charging (0 A)** | **Yes** | Grid, passed straight through | Not charged, not discharged (PowerAssist off) | Powered | Powered | "Use the grid, but don't charge the battery right now" |
| **Mode: Inverter only** | **No**, the input relay opens | Battery, through the inverter | **Discharges** | Powered, after a short switch-over | **No power** | "Run the house from the battery, ignore the grid" |
| **Mode: Charger only** | Yes | Grid | Charged | Powered, but **not on a grid failure** | Powered | Charging without inverter backup |
| **Mode: Off** | No | Nothing | Not charged | **No power** | **No power** | Switching the Multi off |

Picture the Multi as a box between the grid and your house:

- **On:** grid → house and grid → battery; on a grid failure, battery → house.
- **Stop grid charging:** grid → house only; the charger pauses and the battery is left
  alone.
- **Inverter only:** the grid is disconnected; battery → house, as if there were no grid.

**Effect on the AC outputs:**

- **Stop grid charging** has no effect on AC out 1 and AC out 2: they stay powered from
  the grid without any interruption.
- **Inverter only** switches AC out 1 over to the inverter. With the *UPS function* on
  this is very fast (under 20 ms) and most devices don't notice; with it off the
  switch-over is slower and can reset computers and other sensitive equipment. The power
  is then limited to what the inverter can deliver, so large loads can cause an overload.
  **AC out 2 loses power**, because it is only live while the Multi is on the grid.
  Switching back to *On* makes the Multi synchronise with the grid and reconnect, which
  is normally seamless.

**No restart:** changing the mode and *Stop grid charging* are remote panel commands
that take effect immediately (MK2 protocol §5), like the knob and switch of a Digital
Multi Control panel. Neither restarts the Multi. Only *Restart the Multi* does, and that
power cycles every device on AC out 1 and AC out 2. This follows the Victron
documentation and has been tested against the protocol simulator, not yet on every
device.

**PowerAssist:** with PowerAssist on, a 0 A limit does not just pass the grid through:
the Multi stays on the grid but supplies the loads from the battery. To only pause
charging, keep PowerAssist off.

### Mode Off

**What it does:** the inverter and the charger switch off, so **AC out 1 and AC out 2
lose power** and the battery is not charged. It is not a restart: the Multi stays off
until another mode is selected.

**How it is protected:**

- The *Mode* select on the dashboard refuses it while *Allow critical changes* is off,
  with *"Mode off is a critical change: AC out 1 and AC out 2 lose power"*.
- The guided dialog (*Configure → Device actions → Change the mode*) shows the full
  warning and only acts after you tick *I understand the risks*.
- The `set_mode` and `set_remote_panel_state` actions refuse it without
  `acknowledge_risk: true`.
- With *Monitor only*, none of these controls exist.

> [!CAUTION]
> A switched-off Multi can go to **sleep**. Asleep, it also stops powering the MK3, so
> Home Assistant can no longer switch it back on: only the switch on the device can.
> Keep the **[Keep awake](connection.md#keep-awake)** switch on (the default) whenever
> you use *Mode Off*.

**Switching back on:** selecting *On* is a critical change too, so unlock again first.
The Multi then starts, and the AC outputs come back once it is running.

## Guided device actions (Configure → Device actions)

The same critical changes, with a full explanation: each action opens a dialog that
lists its risks, and nothing is sent until you tick *I understand the risks* and
submit. Charger settings and features can be written in RAM or permanently here too.

| Action | What can happen |
|---|---|
| **Restart the Multi** | The Multi restarts and its relays open: **AC out 1 and AC out 2 lose power** for several seconds, so **every device connected to them is power cycled** (switched off and on again). Settings changed in RAM are lost. |
| **Mode: off** | **AC out 1 and AC out 2 lose power** until the mode is changed back. Not a restart. Keep *Keep awake* on, or the Multi can go to sleep and can then only be switched on at the device. See [Mode Off](#mode-off). |
| **Mode: charger only** | No inverter: the AC outputs **lose power on a grid failure**. |
| **Mode: inverter only** | The Multi disconnects from the grid (relay switches): AC out 1 switches over to the inverter (fast with the UPS function on, slower without) and is limited to the inverter's power; **AC out 2 loses power**; the battery discharges. |
| **Mode: on** | Normal operation; if the Multi was off it starts and its relays click. |
| **Stop grid charging** (passthru, limit 0 A) | No restart, and AC out 1 and AC out 2 stay powered. With PowerAssist off the charger stops and the grid is passed through; with PowerAssist on the Multi supplies the loads from the battery instead. |
| **Charger settings** | Wrong charge voltages or currents can damage the battery; too high an AC input limit can trip the grid breaker. |
| **PowerAssist / weak AC / UPS** | PowerAssist off can trip the grid breaker under load; UPS off makes transfers slower, which **can interrupt the AC outputs** on a grid failure. |
| **Equalisation** | Charges above the absorption voltage — never for lithium, gel or AGM batteries unless allowed by the manufacturer. |

Charger settings and features can be changed **in RAM** (lost when the Multi restarts)
or **permanently** in the EEPROM. Permanent writes replace the value set with
VictronConnect or VEConfigure, are limited to one per setting every 5 minutes to protect
the EEPROM, and should not be automated. Victron documents no restart for these writes
and the integration does not restart the Multi, but some settings may only take full
effect after the next restart. **A restart power cycles every device on AC out 1 and AC
out 2**, so choose a convenient moment for it.

## Changing settings (beta)

The setting numbers and switches on the dashboard change the device's settings **in RAM
only**, never its stored (EEPROM) configuration, until you press *Save settings
permanently* (or use `persistent: true` or the guided device actions). A change applies immediately and is lost when the
device restarts, which then goes back to the configuration made with VictronConnect
or VEConfigure. Because nothing is written to the EEPROM, automations can change these
settings as often as needed without wearing it out.

### RAM and EEPROM: the Multi is the source

The Multi keeps every setting twice: in **RAM** (the value in effect) and in the
**EEPROM** (the stored value it returns to after a restart). The integration shows only
what the Multi reports and keeps nothing of its own: after a restart of Home Assistant
everything is read from the Multi again.

Not every Multi reports both. A MultiPlus-II with firmware 556 reports the **stored**
value when a setting is read, while a RAM-only change does apply. Each setting number
therefore has two attributes:

| Attribute | Meaning |
|---|---|
| **Active value source** | *Read from the Multi (RAM)*: the Multi reports the value in effect. *Change acknowledged by the Multi*: the Multi confirmed a RAM-only change (reply 0x88) that it doesn't show when read; the value it confirmed is shown. *Stored value (RAM can't be read)*: no change was made since Home Assistant started, so the stored value is shown. *Read from the Multi (RAM or stored not known yet)*: nothing has shown yet which of the two the Multi reports. |
| **Stored value** | The EEPROM value, when the Multi is known to report it. |

The **configured AC input current limit** follows the same rules as the other
settings. 1.1.5 to 1.1.9 also read it from the limit in effect, assuming that is the
lower of the remote and the configured limit. That held in several readings on a
MultiPlus-II and not in others, so since 1.1.10 the limit in effect is shown only by
the *AC input current limit* sensor.

If the range of a setting can't be read when the integration starts, its number (and
for solar & wind priority, the switch) appears as soon as the range has been read: the
integration tries again every few minutes, without a reload.

The **Remote AC input current limit** number shows the limit last sent through the
remote panel, not the limit in effect (that is the *AC input current limit* sensor).
The Multi doesn't report it; only the MK3 does, from the last panel state sent to it.
The integration keeps that state in the MK3 when it changes settings and when Home
Assistant starts. If the MK3 has none (after it was unplugged, or with 1.1.6 and
earlier), the number is **unknown** until a limit or mode is set. Setting it once
makes the configured limit readable again. A mode
change keeps the remote limit if it is known, otherwise it sends the limit in effect,
which leaves the limit unchanged.

*Save settings permanently* writes only values the Multi acknowledged and that differ
from the stored value, so the EEPROM isn't written for nothing. A permanent write
(WriteViaID, one setting at a time) doesn't restart the Multi. VEConfigure restarts it
because it sends the whole configuration.

How a change is made:

- RAM-only writes need the MK3 in "long frame" mode. The integration switches the
  interface to long frames for the write and straight back to short frames afterwards.
  This is the same sequence VEConfigure uses, and polling pauses for that moment.
- Every write is read back. If the device reports a different value, the action fails
  with an error, unless the Multi acknowledged a RAM-only write (see above). The
  integration can't tell such a device from one that acknowledges a change and ignores
  it; none of the latter is known. The diagnostics download shows the last write in
  `last_write`, what ReadSetting reports in `readsetting_reports`, and the acknowledged
  RAM values in `ram_values`.
- Values are limited to the range the device reports for each setting. The float
  voltage can't be set above the absorption voltage, nor the absorption voltage below
  the float voltage. The solar & wind priority voltage can't be set above the
  absorption voltage.
- A switch changes only its own bit of the device's flag settings, and only bits the
  device reports as supported. Other bits keep their value; bits the device doesn't
  list as supported are taken from the stored value when it is known. Up to 1.1.8,
  switching a feature *off* (or the UPS function *on*) also cleared bit 15 of
  setting 0; see the [1.1.9 release notes](releases/v1.1.9.md).
- A flag change in RAM takes effect at once, without a restart. This was verified
  with PowerAssist on a MultiPlus-II: switched off, the minimum input current limit
  dropped from 3.6 A to 0 A and the Multi stopped assisting. Solar & wind priority
  didn't stop a charge that was already running.

What the switches do:

- **Solar & wind priority off** makes the device charge the battery to the full
  absorption voltage again ("charge to 100%"). Switch it back on afterwards, or restart
  the device.
- PowerAssist, weak AC input and UPS function are critical switches (behind *Allow
  critical changes*) because they affect how the Multi transfers between grid and
  inverter.
- **UPS function** is experimental and disabled by default (enable the entity to use
  it). Victron's protocol says flag 7 must always be the inverse of the UPS bit
  (flag 3); the integration changes only the UPS bit, as VEConfigure was seen to do.
  This hasn't been verified on hardware.

Requirements:

- Device firmware 412 or newer for setting changes, 400 for forcing a charge state,
  and 506 for solar & wind priority.
- The firmware version is shown in the *Firmware version* sensor. Entities whose
  feature the firmware doesn't support are not created.
