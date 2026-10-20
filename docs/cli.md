# Command line tool

[← Back to the README](../README.md) · [Documentation index](README.md)

Testing on the hardware without Home Assistant.

`tools/vebus_cli.py` uses the same protocol code without Home Assistant. Stop Home
Assistant first, because only one program can open the port.

```bash
pip install serialx
python tools/vebus_cli.py info    /dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_XXXX-if00-port0
python tools/vebus_cli.py monitor /dev/serial/by-id/... --interval 2
python tools/vebus_cli.py sniff   /dev/serial/by-id/...      # print raw frames

# v0.2 controls (RAM only, lost when the device restarts):
python tools/vebus_cli.py set-setting /dev/serial/by-id/... 3 27.0 --yes   # float voltage
python tools/vebus_cli.py set-flag    /dev/serial/by-id/... weak-ac on --yes
python tools/vebus_cli.py force       /dev/serial/by-id/... absorption --yes

# v0.4: hold an ESS grid setpoint for 60 s (positive = import), then back to 0 W
python tools/vebus_cli.py ess-setpoint /dev/serial/by-id/... 300 --seconds 60 --yes
```

Please include the `info` output when reporting issues for devices other than the
MultiPlus-II.
