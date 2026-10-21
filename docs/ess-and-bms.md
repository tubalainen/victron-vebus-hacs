# ESS and battery operational limits

[← Back to the README](../README.md) · [Documentation index](README.md)

## ESS external control (experimental)

> [!WARNING]
> Not verified on a real Multi: the sign of the setpoint and what the assistant does
> when the setpoint stops being refreshed over the MK3 are untested. A wrong sign turns
> import into export. Supervise the system while using it.

When the ESS assistant is installed on the Multi and the option *Enable ESS external
control (experimental)* is on (off by default since 1.1.11, with a warning to confirm),
Home Assistant can set the power taken from or fed into the grid, like a GX device in
"ESS mode 3". The assistant is found at startup by walking its RAM records (the ESS
wiki §3.6). Without the assistant or the option, no ESS control entities are created.

- **ESS grid setpoint:** positive takes power from the grid, negative feeds power
  into the grid. This is the same sign as the GX device's AcPowerSetpoint; the
  integration inverts it for the MK3. On three-phase systems there is one setpoint per
  phase master (L1–L3).
- **ESS external control:** the setpoint is only sent while this switch is on. While
  it is on, the setpoint is re-sent at least every 10 seconds, because the assistant
  falls back when it receives no updates. Turning it off writes 0 W, which returns the
  assistant to its normal behaviour. Unloading the integration and stopping Home
  Assistant do the same. The switch always starts **off**: control is never resumed
  without someone turning it on; the setpoint numbers keep their values.
- **ESS disable charge / disable feed-in:** the assistant's flags, written to every
  phase master. With both on, the Multi goes to passthru.
- Battery protection in the Multi (sustain, low battery) always takes priority over
  the setpoint.

## Battery operational limits (advanced, experimental)

> **Warning.** This is a BMS function for lithium batteries. Once the Multi has
> received battery limits, it **stops charging and inverting if it receives none for 5
> minutes**, and keeps expecting them until it is restarted. Only enable this if Home
> Assistant runs reliably, and restart the Multi after turning it off. **A restart power
> cycles every device connected to AC out 1 and AC out 2.**

The option *Enable battery operational limits* (which also needs *Monitor and
control*, device firmware 415 and MK3 firmware 203) creates four numbers:

- charge voltage and charge current limit
- discharge voltage and discharge current limit (0 A stops discharging)

The limits are sent on every poll, before anything else and independently of the ESS
setpoint. If they can't be sent for 2 minutes, a critical repair issue appears. They
start from the device's absorption voltage and charge current, 0 V (no limit) and
1000 A, and keep their values across restarts. Changing a limit is a critical change
(behind *Allow critical changes*), and the voltages must lie within the range of the
absorption voltage setting. Not verified on a real Multi; never use it with lead
batteries.

Turning the option on asks you to confirm the risks. Turning it off, in the options
or by switching to *Monitor only* with Reconfigure, asks for confirmation, because the
Multi must then be restarted within 5 minutes, which power cycles every device on AC
out 1 and AC out 2.
