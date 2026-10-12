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
| **0.3** (beta) | Address scan (stop after 3 misses) with one device per unit, linked to the system device. Per-unit values from RAM variables read at each unit's address, round-robin. System power totals over all units, and apparent power per phase. Quattro AC input 2 limit (setting 49, number of inputs from `F` 5). A RAM-variable fallback for devices without standard info frames, such as the Compact (j9brown issue #11). |
| 0.4 | ESS: scan for the assistant starting at RAM 128 (ESS has id 5). A setpoint number with periodic refresh, disable-charge and disable-feed-in switches. Optionally, Battery Operational Limits (`F` 9). |

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
