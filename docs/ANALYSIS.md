# Analysis of victron-mk3-hass and plan for victron-vebus-hacs

## 1. Starting point

[j9brown/victron-mk3-hass](https://github.com/j9brown/victron-mk3-hass) (domain
`victron_mk3`) and its library [j9brown/victron-mk3](https://github.com/j9brown/victron-mk3)
v0.6.0 are the reference Home Assistant integration for the MK3-USB interface.

**How it works:**

- It polls every 2 s.
- It uses Winmon mode 1 short frames.
- It reads RAM variables 14/15/16 for power using the X/Y/Z-tagged `W` commands.
- It controls the device as a remote panel using the `S` command.
- It uses the undocumented MK3 `H` command to keep a switched-off device awake.

**Why it is stale:**

- The last commit was in January 2026.
- Two PRs from September 2026 have had no review: solar/wind priority (#15) and settings
  reads (library #4).
- Issue #16 is open: the integration cannot load without internet access, because the
  library is installed from a git URL at startup.
- The maintainer has said they do not want large changes.

## 2. Bugs and limitations found

| # | Problem | Consequence | Fixed here by |
|---|---|---|---|
| 1 | A fault is latched forever (`Controller._fault` is never cleared), and the library has no reconnect logic. | After an unplug, an I/O error or any exception, a manual reload is needed. | The `VeBusClient.run()` supervisor reconnects with backoff from 1 s up to 30 s. |
| 2 | `DeviceState(msg[4])` raises for unknown state bytes, and EOF is handled as a generic exception. | The driver dies, e.g. on old firmware that sends state 0x79. | Unknown values map to `None` and the raw value is kept. |
| 3 | There is one `_w_completion` slot, waiters are matched only by class, and there is no lock. | Concurrent control and poll requests can be matched to the wrong reply (confirmed in PR #15). | One `asyncio.Lock` serialises all requests. Each request has its own predicate, and the X/Y/Z tag is matched. |
| 4 | There is no byte-wise resync after a bad checksum. | The stream can stay misaligned. | `FrameDecoder` skips byte by byte and looks ahead for a plausible frame. |
| 5 | The device identifier is the `/dev/ttyUSBx` path. The user flow sets no unique_id, and discovery never calls `_abort_if_unique_id_configured`. | Renumbered ports create duplicate devices and entities. | The unique_id is the MK3 USB serial (or the by-id path or URL). Discovery updates the path. |
| 6 | `config_flow.py:70` passes `description_placeholders` as a set. | The abort message is broken. | Rewritten. |
| 7 | The startup `S` frame sends a 10th "EEPROM flags" byte. | Risk of an unwanted EEPROM write. | The 9-byte form `S 00 00 00 01 90 00 01` is used. |
| 8 | Legacy Home Assistant patterns: `hass.data`, a coordinator without `config_entry`, a service registered per entry and never removed, hard-coded English names, no diagnostics, options or reconfigure flow. | Deprecation warnings and quality-scale gaps. | `runtime_data`, services registered in `async_setup`, translations, diagnostics, options and reconfigure flows. |
| 9 | The firmware sensor has `state_class=MEASUREMENT` and reports the MK3's firmware, not the Multi's. The LED ENUM sensors have no `options`. The phase count is hard-coded to 3. | Statistics fill up with nonsense, and the enum sensors are invalid. | The device firmware is read with Winmon 0x05/0x06. There are LED binary sensors and alarm enums with options. Phases are detected by probing F1–F4. |
| 10 | The current-limit number can't reach 0 (issue #9). | Passthru can't be selected. | Documented. The `set_remote_panel_state` action accepts 0. |
| 11 | The library lists the `asyncio` PyPI backport as a dependency and uses `assert False` for unexpected value lengths. | Dependency hygiene. | Bundled code; robust decoding. |
| 12 | `iot_class: local_push` although it polls. | Wrong metadata. | `local_polling`. |

## 3. Protocol reference used

The authoritative source is Victron's *Interfacing with VE.Bus products – MK2 Protocol
3.14*. [xcellsior FINDINGS.md](https://github.com/xcellsior/ve-bus-programming/blob/main/FINDINGS.md)
was used where it agrees with the specification.

**Errata in FINDINGS.md:**

- `A 01 00` selects address **0**, not address 1.
- `S` switch states are 1 = charger only, 2 = inverter only, 3 = on, 4 = off. They are
  not 0–3.
- `0x32` is WriteRAMVar and `0x34` is WriteData. GetSettingInfo is `0x35`.
- The interface sends idle `V` frames about every 1 s, not every 100 ms.
- The spec documents WriteViaID (`0x37`) as Winmon mode 2 only. This must be verified on
  hardware before relying on it.

**Implemented commands:**

- `V`, `A`, `L`, `H`, `S` (variant 2)
- `F` 0–5
- Winmon `0x05`/`0x06` (firmware), `0x0E` (device and charge state), `0x30` (read RAM),
  `0x31` (read setting), `0x35` (setting info) and `0x36` (RAM variable info)

**Decoding rules:**

- All scaling comes from the device's RAM-var and setting info, following the spec's
  rules: signed values, the `≥ 0x4000` reciprocal scale, and bit variables marked by
  offset `0x8000`.
- The one exception is RAM variable 3 (inverter current). It is always decoded as
  signed, because the MultiPlus-II reports it unsigned but sends negative values.

## 4. Roadmap

| Version | Scope |
|---|---|
| **0.1** | Monitoring of a single VE.Bus device. Opt-in mode and current-limit controls. Keep awake. Reconnect. Diagnostics. Protocol simulator and tests. |
| **0.2** | Buttons to force absorption, float or equalise (`0x0E` 1/2/3, firmware ≥ 400). RAM-only setting changes via `0x37` flags 0x03 in a long-frame session, with read-back and the bit masks reported by `0x35`: absorption/float voltage, charge current, configured AC input limit, PowerAssist, weak AC input, UPS function. Solar and wind priority (settings 60 and 88, j9brown issue #14 and PR #15). Passthru (limit 0) stays available through the action. |
| **0.3** | Address scan (stop after 3 misses) with one device per unit, linked to the system device. Per-unit values from RAM variables read at each unit's address, round-robin. System power totals over all units, and apparent power per phase. Quattro AC input 2 limit (setting 49, number of inputs from `F` 5). A RAM-variable fallback for devices without standard info frames, such as the Compact (j9brown issue #11). |
| **0.4** (beta) | ESS: the assistant is found from RAM 128 (ESS id 5) on every phase master. The setpoint (per phase) is written with WriteRAMVar/WriteData under an *ESS external control* switch, refreshed at least every 10 s and reset to 0 W when control stops. Disable-charge and disable-feed-in switches. Battery Operational Limits (`F` 9) behind a separate, warned option. |
| **0.5** (beta) | Safety. The user chooses *Monitor only* or *Monitor and control* when adding the integration and with Reconfigure; control needs an explicit warning to be confirmed. Everything that can cut the power on the AC outputs, restart the Multi, change charge voltages or write the EEPROM moved from dashboard entities to guided device actions in the options flow, each with a risk dialog and an "I understand" checkbox: mode, passthru, charge voltages and other settings (RAM or permanent), PowerAssist / weak AC / UPS, equalisation and restart (`F` 8). Permanent (EEPROM) writes via `0x37` flags 0x01, rate-limited to one per setting every 5 minutes. The remote panel action requires `acknowledge_risk`. Disclaimer in the README and the setup dialogs. |
| **0.6** (beta) | Energy sensors for the Energy dashboard (grid import/export, AC output, battery charged/discharged), integrated from the filtered power RAM variables with the trapezoidal rule and restored across restarts. Alarm notifications: an event entity and repair issues driven by the overload, low battery and temperature LEDs. Status sensors: virtual switch (RAM 10), inverter output voltage (setting 5), maximum absorption time (9), charge characteristic (10), low battery shutdown (11) and restart voltage (11 + 12). 0.6.0-beta2: the front panel LEDs are enum sensors (off / on / blinking) instead of binary sensors, and an `L` reply of `0x1F`/`0x1F` (LEDs unknown, spec §4) reads as unknown. |
| **0.7** (beta) | A value that cannot be read keeps its last good reading through two consecutive failures (`MAX_STALE_READS`) instead of becoming unknown at once; a re-read after a command that times out no longer fails the command. Connection health: requests are counted, and link quality (answered share over 5 minutes), request timeouts and checksum errors are diagnostic sensors, with a repair issue below 90%. Per-unit energy sensors in multi-unit systems. Per-unit alarms are not possible: the `L` LED status describes the whole VE.Bus system (spec §4). |
| **1.0** | First stable release: the features of 0.1–0.7. Monitoring and mode changes are in use on a MultiPlus-II; the items below are still verified only against the simulator. |
| **1.1** | Every control usable in Home Assistant itself. Critical changes (mode select, passthru, PowerAssist / weak AC / UPS switches, absorption and float voltage, equalise, restart, save) are entities behind an "Allow critical changes" switch that turns itself off after 10 minutes. Entity changes go to RAM; "Save settings permanently" stores the settings changed since the last restart or save (tracked by the client, cleared on restart and boot frames). Actions `set_mode`, `set_setting`, `set_feature`, `equalise`, `restart`, `save_settings` with `acknowledge_risk`. Passthru is remembered because the device may report its minimum limit instead of 0 A; a mode change in passthru keeps 0 A. |
| **1.1.1** | Texts only: every warning that mentions a restart says that it power cycles every device on AC out 1 and AC out 2, including turning off battery operational limits; the "Monitor and control" setup warning describes the 1.1 lock instead of the guided dialog only. |
| **1.1.2** | Texts only: the *Passthru* switch and dialog are renamed *Stop grid charging (0 A)*, because "passthru" was mistaken for normal operation (the device state shows Victron's normal grid-through state as passthru). Keys, actions and existing entity IDs are unchanged. |
| **1.1.3** | Every setting write is recorded (WriteViaID reply, read back, the frames of the long-frame session) in `last_write` of the diagnostics, and a failed verification names the reply. Maximum absorption time is read in minutes (setting 9 has scale 60). The charge characteristic (setting 10) is read as a bit field. |
| **1.1.4** | RAM-only writes acknowledged with 0x88 are accepted when ReadSetting reports the stored value. The value written is kept (`ram_values`) and used for polling, flag changes and *Save settings permanently* until a restart or save; the number entities show the stored value as an attribute. |
| **1.1.5** | The Multi is the only source. Setting numbers show where the value comes from (`active_value_source`) and the stored value. What ReadSetting reports (RAM or EEPROM) is learnt from writes, never kept across connections. The configured AC input limit in effect is read from the MasterMultiLED frame while it is below the remote limit (`D`). *Save settings permanently* skips values equal to the stored ones. The remote limit number shows the `D` value. |
| **1.1.6** | `D` reports the panel state last sent to the MK3, which is `04 00 00` after opening it: it counts only if its mode is the remote mode the device reports, and "off, 0 A" never counts. The remote limit number is unknown until a panel state is sent. A limit in effect above the stored setting 6 shows that ReadSetting reports EEPROM and that RAM differs; the number is then unknown with a lower bound (`active_minimum`). Mode changes keep the known remote limit. |
| **1.1.7** | Returning to Winmon mode 1 (after a long-frame session and at start-up) sends the panel state read with `D` back to the MK3 (on, charger only or inverter only; flag 0x10 as before), so the MK3 keeps it. A poll no longer undoes a setting write or a refresh made while it ran. Setting ranges missing at start-up are read up to three times. |

## 5. Things to verify on real hardware

These conventions come from j9brown's measurements and could not be verified here:

- **Power signs:** RAM 14 is battery power, positive when charging. RAM 15 is negated to
  give AC input power. RAM 16 is AC output power.
- **Front switch bits** of the MasterMultiLED switch register: up = on, down = charger
  only.
- **Remote switch bits when no remote panel was ever used:** what the "Remote mode"
  select shows in that case.
- **State of charge (RAM 13):** whether its value is meaningful when no battery
  capacity is configured.

`python tools/vebus_cli.py info <port>` prints all of these values.

v0.2 adds these, verified only against the simulator. Check them with the CLI's
`set-setting`, `set-flag` and `force` commands:

- **Long-frame session:** `D`, then `S <state from D> 01 D0 3E 06` to enter long
  frames, and `S 00 00 00 01 90 00 01` to leave them. This sequence comes from j9brown
  PR #15, which reports it working on a MultiPlus-II.
- **WriteViaID:** `37 03 <id> <lo> <hi>` should be acknowledged with `0x88`, and the
  read-back should match. The spec says this needs firmware 412 or newer.
- **Truncated replies:** whether mode 1 GetSettingInfo replies are cut short. If they
  are, the ranges are read again in long-frame mode.
- **Flag bits:** PowerAssist is bit 5 of setting 0, weak AC bit 14 and UPS bit 3 (set =
  UPS off). Solar and wind priority is bit 9 of setting 60, with its voltage in setting
  88.

v0.3 adds these, verified only against the simulator:

- **Address scan:** each address 1..31 is selected with `A 01 <addr>` and asked for its
  firmware version. The scan stops after three addresses in a row give no answer, and
  the home address is selected again afterwards. Units in a VE.Bus system must be
  identical, so the RAM variable scaling of address 0 is used for every unit.
- **Phase mapping:** which unit is on which phase is not determined. By convention,
  addresses 0, 1 and 2 are the L1, L2 and L3 masters.
- **RAM fallback for Compact and Phoenix devices:** battery current (RAM 5) is assumed
  to be positive while charging.
- **Quattro:** setting 49 is the AC input 2 limit, and bits 4–6 of the `F` 5 input
  configuration byte give the number of inputs (firmware 415 or newer).

v0.4 adds these, verified only against the simulator:

- **ESS setpoint sign over MK3:** negative takes power from the grid. Both the ESS wiki
  §3.6 and yvesf/ve-ctrl-tool say so; the wiki's line "positive for charge" contradicts
  it. The integration exposes the GX sign (positive = import) and inverts it. Check
  with `vebus_cli.py ess-setpoint`.
- **ESS refresh:** yvesf writes at least every 30 s to keep ESS from falling back. The
  integration writes every 10 s, and also immediately on every change.
- **Writing the setpoint:** `WriteRAMVar` (`0x32`, no reply) and `WriteData` (`0x34`,
  reply `0x87`) are sent with the same tag letter (X/Y/Z).
- **Battery Operational Limits:** the reply is a `0x3C` frame echoing the limits; flag
  bit 7 reports missing information.

v0.5 adds these, verified only against the simulator:

- **Restart:** `F 08 00 00 00 00` restarts the device. The Multi's relays open, so the
  AC outputs lose power; settings written in RAM are lost. The MK3 may need to be
  re-addressed afterwards, which the client does on the next request.
- **Permanent writes:** WriteViaID (`0x37`) with flags 0x01 (setting, without the
  RAM-only bit) stores the value in the EEPROM. The spec does not say whether the
  device restarts or whether every setting takes effect without one.

v0.6 adds these, verified only against the simulator:

- **Energy:** the energy sensors are only as good as the power RAM variables
  (14–16). In particular the sign of RAM 15 (AC input power, negated) decides
  import versus export. Compare with an energy meter over a day.
- **Settings units:** setting 5 is the inverter output voltage in V, setting 9 the
  maximum absorption time in hours (1 for the LiFePO4 profile), and setting 10 the
  charge characteristic (0 variable, 1 fixed, 2 fixed with storage), per
  xcellsior FINDINGS.md §7.5. Setting 12 is the restart offset above the shutdown
  voltage (setting 11).
- **Alarm LEDs:** blinking is read as a pre-alarm (warning) and lit as an alarm.
- **UPS flag and bit 7:** the spec says Flags[7] "must have the inverted value of
  flags[3]", but FINDINGS.md §7.1 shows VEConfigure changing only bit 3 when switching
  the UPS function (MultiPlus `0x81B4` ↔ `0x81BC`), and bit 7 differs by model. The
  integration therefore changes only bit 3, like VEConfigure.

1.1 adds these, verified only against the simulator:

- **Passthru:** whether the Multi reports 0 A or its minimum limit after a remote
  limit of 0 A. The integration remembers that passthru was requested either way.
- **Save settings permanently:** each setting changed in RAM is read and written back
  with WriteViaID flags 0x01 (see the permanent writes above).

Results from a real MultiPlus-II (firmware 2611556, MK3 1170216), 1.1.2:

- **RAM-only writes:** WriteViaID with flags 0x03 for settings 4 and 6 is followed by a
  ReadSetting that returns the old value ("Setting 6 reads back 60 after writing 80").
  The reply to the write was not 0x80 or 0x9B (those raise different errors), so it was
  0x87/0x88 or no reply. Either ReadSetting returns the stored (EEPROM) value, or the
  device ignores RAM-only writes. 1.1.3 records the reply to tell these apart.
- **RAM-only writes, 1.1.3:** the reply to WriteViaID (flags 0x03) for setting 4 is
  0x88 and ReadSetting keeps returning the stored 40 A. The write takes effect: with
  the charge current limit at 5 A the charger dropped to about 6 A. So on this
  firmware ReadSetting returns the EEPROM value, not the RAM value. 1.1.4 trusts the
  0x88 reply.
- **Input current limit, 1.1.4:** with the remote panel limit at 9.0 A (`D` reply
  `03 5a 00`) and setting 6 written to 7.0 A in RAM (stored 6.0 A), the MasterMultiLED
  frame reports 7.0 A in effect (min 3.6, max 32.0, "overruled by remote" off). Earlier
  diagnostics show 8.0 A in effect after writing 8 A. So the limit in effect is the
  lower of the remote and the RAM configured limit; below the remote limit it reads the
  RAM value of setting 6. 1.1.5 uses this.
- **`D` after a restart of Home Assistant, 1.1.5:** the MK3 answers `04 00 00 01 c4
  20 08 00` (off, 0 A) while the Multi runs on (remote mode on, 7.0 A in effect, RAM
  setting 6 still 7 A, no boot frame). The 1.1.3 write sessions show the same reply
  before any panel state was sent, and sending it back with flags 0xD0 (do not send
  the panel state) didn't change the Multi. So `D` is the MK3's own copy of the last
  `S`, not a value read from the Multi.
- **What clears `D`, 1.1.6:** with the remote limit set to 10 A and then two setting
  writes, the second write session starts with `D` = `04 00 00`. The only `S` sent in
  between is the return to mode 1 at the end of the first session, `00 00 00 01 90 00
  01`: an `S` with switch state 0 makes the MK3 forget its panel state. The same frame
  is sent at start-up, which explains the 1.1.5 result. 1.1.7 sends the state read with
  `D` instead of zeros.
- **Setting 9** (absorption time) reports scale 60: the value is in minutes (8 → 480).
- **Setting 10** (charge characteristic) is 3 with VictronConnect showing "Fixed +
  storage", and xcellsior saw 1 for "fixed": read as a bit field (bit 0 fixed, bit 1
  storage). 0 (adaptive) and 2 (adaptive + storage) are inferred.
- **Settings 0 and 60** report no maximum in Winmon mode 1 (truncated setting info),
  so the supported-bit mask of the flag settings is unknown on this device.
- Mode changes and the remote current limit work; link quality 100%.
