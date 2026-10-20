# Installation and setup

[← Back to the README](../README.md) · [Documentation index](README.md)

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

## Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 2 s | The MK3 runs at 2400 baud and a full poll takes about one second. |
| Show alarms as repair issues | on | Raise a repair issue while an overload, low battery or temperature alarm is active. See [Alarm notifications](alarms.md). |
| Enable battery operational limits | off | Only with *Monitor and control*. See [Battery operational limits](ess-and-bms.md#battery-operational-limits-advanced-beta). |

Monitoring or control is not an option here: it is chosen when the integration is
added, or with *Reconfigure*.
