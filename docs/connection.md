# Connection health and keep awake

[← Back to the README](../README.md) · [Documentation index](README.md)

## Connection health

At 2400 baud the MK3 link is slow, and a reply can be lost to a bad cable, interference
or other devices busy on the VE.Bus.

- **Link quality** is the share of requests answered in the last 5 minutes. It should
  be 100% or very close to it.
- **Request timeouts** counts the requests that got no reply. **Checksum errors**
  *(disabled)* counts bytes skipped to recover from corrupted data.
- A value keeps its last good reading through up to two missed replies before it
  becomes *unknown*.
- Below 90% (with at least 100 requests) a repair issue lists what to check. It
  disappears once the link quality is back above 95%.
- These sensors stay available while the device is unreachable.

## Keep awake

When a VE.Bus device is switched off, it goes to sleep after a while. Sleeping stops
the power to the MK3, so nothing can switch the device back on remotely. With **Keep
awake** on (the default), the integration sets the MK3's *standby* flag, which keeps
the device awake. It resends the flag on every poll because the flag is lost whenever
the device loses power. A switched-off device that is kept awake uses a few watts more.

This matters most when you switch the Multi off from Home Assistant with *Mode Off*:
without *Keep awake* it can only be switched on again at the device. See
[Mode Off](controls.md#mode-off).
