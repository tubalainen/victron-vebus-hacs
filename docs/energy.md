# Energy

[← Back to the README](../README.md) · [Documentation index](README.md)

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
