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

## Device path and Docker

A path such as `/dev/ttyUSB0` can change when USB devices are plugged in or the host
restarts. A `/dev/serial/by-id/...` path always names the same MK3. The integration
stores the by-id path when Home Assistant can see it. To change the path later, use
*Reconfigure*. The serial number of the MK3 must stay the same.

**Home Assistant in Docker (Container):** inside the container a device exists only if it
is mapped in `devices:`. There are two ways to give the MK3 a stable name:

```yaml
# docker-compose.yml
services:
  homeassistant:
    devices:
      # A: the by-id path, under the same name inside the container
      - /dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_HQ0000XXXXX-if00-port0
      # B: or the by-id path under a fixed short name
      # - /dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_HQ0000XXXXX-if00-port0:/dev/ttyMK3
```

Docker resolves the link when the container starts and creates a device node of its own
at the container path. That node is not a link, so Home Assistant can't read the MK3's
serial number from it. Since 1.1.15 the integration recognizes the by-id name
(it contains the serial number) and accepts the path in *Reconfigure*. With B, enter
`/dev/ttyMK3`. Up to 1.1.14 *Reconfigure* wrongly reported "a different MK3
interface" for a path without a readable serial number. A path that isn't mapped
doesn't exist in the container, and setup and *Reconfigure* say so.

If the same MK3 is also mapped as `/dev/ttyUSB0`, both names lead to the same device;
the integration uses only the path it is configured with.

## Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 2 s | The MK3 runs at 2400 baud and a full poll takes about one second. |
| Show alarms as repair issues | on | Raise a repair issue while an overload, low battery or temperature alarm is active. See [Alarm notifications](alarms.md). |
| Enable battery operational limits | off | Experimental. Only with *Monitor and control*. See [Battery operational limits](ess-and-bms.md#battery-operational-limits-advanced-experimental). |
| Enable ESS external control | off | Experimental. Only with *Monitor and control* and the ESS assistant. See [ESS external control](ess-and-bms.md#ess-external-control-experimental). |

Monitoring or control is not an option here: it is chosen when the integration is
added, or with *Reconfigure*.
