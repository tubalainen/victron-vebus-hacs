# Notes for Claude Code

Project: Home Assistant integration for Victron VE.Bus inverter/chargers through an
MK3-USB interface. See `README.md` and `docs/` (the protocol notes and roadmap are in
`docs/ANALYSIS.md`).

## Backlog: VEConfigure feature gap (not started)

Recorded after 1.1.2. **Do not start this work unless the user asks.** Setting and
flag numbers refer to the MK2 protocol 3.14 (§7.3.13). The VEConfigure side is from
general knowledge and was not checked against the current VEConfigure release.

Planned for 1.2 (agreed earlier): absorption time (setting 9), low battery shutdown /
restart (11, 12), battery capacity (64), charge curve (10), dynamic current limiter
(flag 28), accept wide input frequency (flag 27), AES (flag 8).

Further gaps (writable in VEConfigure, missing here):

- General: "AC input overruled by remote" (flags 15, 30); battery monitor SoC when
  bulk finished and charge efficiency (65, 72).
- Grid: AC input voltage range low/high and hysteresis (44–47).
- Inverter: assist current boost factor (48); inverter output voltage (5, read only
  today); low battery pre-alarm offset (63); AES current levels (50, 51; the spec says
  they only take effect after a restart).
- Charger: repeated absorption time / interval (7, 8); disable charger (flag 6); stop
  after 10 h bulk (flag 4); reduced float (flag 11); tubular-plate curve (flag 29).
  Lithium / VE.Bus BMS / VSense options are not mapped to documented setting IDs.
- Virtual switch: its conditions and timers (settings 15–43, 52–59, 62 and flags).
- A read-only "export all settings" in diagnostics.

Out of scope (not possible over the MK3, or dangerous): installing or configuring
assistants, system configuration (parallel / three-phase / number of units), firmware
updates, configuration files and factory reset, settings behind a higher access level,
50/60 Hz, grid codes / loss-of-mains detection, ground relay, "not promoted" flags.

Already better than VEConfigure: monitoring, energy, alarms, automations, ESS external
control, battery operational limits, and RAM-only (temporary) changes with an explicit
"Save settings permanently".
