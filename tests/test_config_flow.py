"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USB, SOURCE_USER
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.victron_vebus.config_flow import _same_node
from custom_components.victron_vebus.const import (
    CONF_ALARM_ISSUES,
    CONF_ENABLE_BATTERY_LIMITS,
    CONF_ENABLE_CONTROLS,
    CONF_SCAN_INTERVAL,
    CONF_USAGE,
    DOMAIN,
    USAGE_CONTROL,
    USAGE_MONITOR,
)

from .conftest import DEVICE_PATH
from .vebus.simulator import FakeMK3

USB_INFO = UsbServiceInfo(
    device="/dev/ttyUSB0",
    vid="0403",
    pid="6015",
    serial_number="HQ123",
    manufacturer="Victron Energy BV",
    description="MK3-USB Interface",
)


def _patch_usb(by_id: str = DEVICE_PATH, serial: str | None = "HQ123"):
    usb_device = None
    if serial:
        usb_device = type("Dev", (), {"serial_number": serial})()
    return (
        patch(
            "custom_components.victron_vebus.config_flow.usb.get_serial_by_id",
            return_value=by_id,
        ),
        patch(
            "custom_components.victron_vebus.config_flow.usb.usb_device_from_path",
            return_value=usb_device,
        ),
    )


async def _usage(hass: HomeAssistant, result, usage: str = USAGE_MONITOR):
    """Answer the monitor/control question."""
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "usage"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USAGE: usage}
    )


async def test_user_serial_flow(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Selecting a serial port creates a monitoring entry keyed by the USB serial."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "serial"}
    )
    assert result["type"] is FlowResultType.FORM
    by_id, from_path = _patch_usb()
    with by_id, from_path:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB0"}
        )
    result = await _usage(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: DEVICE_PATH}
    assert result["options"] == {CONF_ENABLE_CONTROLS: False}
    assert result["result"].unique_id == "HQ123"


async def test_control_needs_warning_confirmed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """Choosing control shows a warning that must be acknowledged."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "socket://10.0.0.5:4001"}
    )
    result = await _usage(hass, result, USAGE_CONTROL)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "control_warning"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"acknowledge": False}
    )
    assert result["errors"] == {"base": "acknowledge_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"acknowledge": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_ENABLE_CONTROLS: True}


async def test_manual_flow_socket_url(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A ser2net URL is accepted as is."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: " socket://10.0.0.5:4001 "}
    )
    result = await _usage(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: "socket://10.0.0.5:4001"}
    assert result["result"].unique_id == "socket://10.0.0.5:4001"


async def test_user_flow_errors(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Errors are shown and the flow can recover."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    fake_mk3.fail_open = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "socket://10.0.0.5:4001"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    fake_mk3.fail_open = False
    fake_mk3.device.asleep = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "socket://10.0.0.5:4001"}
    )
    assert result["errors"] == {"base": "no_response"}

    fake_mk3.device.asleep = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "socket://10.0.0.5:4001"}
    )
    result = await _usage(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """The same interface cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "serial"}
    )
    by_id, from_path = _patch_usb()
    with by_id, from_path:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB0"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_usb_discovery(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """A discovered MK3 is confirmed and set up."""
    by_id, _ = _patch_usb()
    with by_id:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USB}, data=USB_INFO
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "usb_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _usage(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: DEVICE_PATH}
    assert result["result"].unique_id == "HQ123"


async def test_usb_discovery_updates_path(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Rediscovery of a configured interface updates its path."""
    config_entry.add_to_hass(hass)
    new_path = "/dev/serial/by-id/new"
    by_id, _ = _patch_usb(by_id=new_path)
    with (
        by_id,
        patch("custom_components.victron_vebus.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USB}, data=USB_INFO
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_DEVICE] == new_path


async def test_reconfigure(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """The device path can be changed, but only to the same interface."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    by_id, from_path = _patch_usb(by_id="/dev/serial/by-id/other", serial="OTHER")
    with by_id, from_path:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB1", CONF_USAGE: USAGE_MONITOR}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"

    result = await config_entry.start_reconfigure_flow(hass)
    by_id, from_path = _patch_usb(by_id="/dev/serial/by-id/moved")
    with (
        by_id,
        from_path,
        patch("custom_components.victron_vebus.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB1", CONF_USAGE: USAGE_MONITOR}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_DEVICE] == "/dev/serial/by-id/moved"


def _entry_on(device: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Victron VE.Bus",
        unique_id="HQ123",
        data={CONF_DEVICE: device},
        options={},
    )


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, device: str, serial: str | None
):
    """Reconfigure to a device path whose USB serial number is given (or None)."""
    by_id, from_path = _patch_usb(by_id=device, serial=serial)
    result = await entry.start_reconfigure_flow(hass)
    with (
        by_id,
        from_path,
        patch("custom_components.victron_vebus.async_setup_entry", return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: device, CONF_USAGE: USAGE_MONITOR}
        )


async def test_reconfigure_to_by_id_without_serial_number(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """As in Docker: the serial number can't be read, the by-id name has it."""
    entry = _entry_on("/dev/ttyUSB0")
    entry.add_to_hass(hass)
    result = await _reconfigure(hass, entry, DEVICE_PATH, serial=None)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE] == DEVICE_PATH
    assert entry.unique_id == "HQ123"


async def test_reconfigure_without_serial_number_is_probed(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """An unknown serial number is no proof of another interface."""
    entry = _entry_on("/dev/ttyUSB0")
    entry.add_to_hass(hass)
    with patch("custom_components.victron_vebus.config_flow.async_probe") as probe:
        result = await _reconfigure(hass, entry, "/dev/ttyACM0", serial=None)
    assert result["reason"] == "reconfigure_successful"
    probe.assert_awaited_once_with("/dev/ttyACM0")
    assert entry.unique_id == "HQ123"


async def test_reconfigure_to_another_serial_number_is_refused(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A serial number that is known and different is another interface."""
    entry = _entry_on("/dev/ttyUSB0")
    entry.add_to_hass(hass)
    other = "/dev/serial/by-id/usb-VictronEnergy_MK3-USB_Interface_HQ999-if00-port0"
    result = await _reconfigure(hass, entry, other, serial="HQ999")
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_DEVICE] == "/dev/ttyUSB0"


async def test_reconfigure_to_a_missing_path(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """A path that doesn't exist (not mapped into the container) says so."""
    entry = _entry_on("/dev/ttyUSB0")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.victron_vebus.config_flow._path_exists", return_value=False
    ):
        result = await _reconfigure(hass, entry, DEVICE_PATH, serial=None)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_not_found"}
    assert entry.data[CONF_DEVICE] == "/dev/ttyUSB0"


async def test_reconfigure_to_another_name_of_the_same_port(
    hass: HomeAssistant, fake_mk3: FakeMK3
) -> None:
    """The port in use isn't opened again under another name (Docker node)."""
    entry = _entry_on("/dev/ttyUSB0")
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.victron_vebus.config_flow._same_node",
            return_value=True,
        ),
        patch("custom_components.victron_vebus.config_flow.async_probe") as probe,
    ):
        result = await _reconfigure(hass, entry, DEVICE_PATH, serial="HQ123")
    assert result["reason"] == "reconfigure_successful"
    probe.assert_not_awaited()
    assert entry.data[CONF_DEVICE] == DEVICE_PATH


async def test_manual_path_missing(hass: HomeAssistant, fake_mk3: FakeMK3) -> None:
    """Setting up with a path that doesn't exist explains why."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with patch(
        "custom_components.victron_vebus.config_flow._path_exists", return_value=False
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB7"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_not_found"}


async def test_reconfigure_to_control_and_back(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Switching to control needs the warning; monitor only also drops BMS."""
    config_entry.add_to_hass(hass)
    by_id, from_path = _patch_usb(by_id=DEVICE_PATH)
    with (
        by_id,
        from_path,
        patch("custom_components.victron_vebus.async_setup_entry", return_value=True),
    ):
        result = await config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: DEVICE_PATH, CONF_USAGE: USAGE_CONTROL}
        )
        assert result["step_id"] == "control_warning"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"acknowledge": True}
        )
        assert result["reason"] == "reconfigure_successful"
        assert config_entry.options[CONF_ENABLE_CONTROLS] is True

        hass.config_entries.async_update_entry(
            config_entry,
            options={**config_entry.options, CONF_ENABLE_BATTERY_LIMITS: True},
        )
        result = await config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: DEVICE_PATH, CONF_USAGE: USAGE_MONITOR}
        )
        # Monitoring only stops the battery limits: confirmed first (1.1.11).
        assert result["step_id"] == "disable_battery_limits"
        assert config_entry.options[CONF_ENABLE_BATTERY_LIMITS] is True
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["reason"] == "reconfigure_successful"
    assert config_entry.options[CONF_ENABLE_CONTROLS] is False
    assert config_entry.options[CONF_ENABLE_BATTERY_LIMITS] is False


async def test_options_flow(
    hass: HomeAssistant, fake_mk3: FakeMK3, config_entry: MockConfigEntry
) -> None:
    """Monitoring only goes straight to the settings; no battery limit option."""
    config_entry.add_to_hass(hass)
    with patch("custom_components.victron_vebus.async_setup_entry", return_value=True):
        await hass.config_entries.async_setup(config_entry.entry_id)
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "settings"
        assert CONF_ENABLE_BATTERY_LIMITS not in result["data_schema"].schema
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: 5, CONF_ALARM_ISSUES: False}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 5, CONF_ALARM_ISSUES: False}


def test_same_node_by_device_number(tmp_path) -> None:
    """Two device nodes with the same numbers are the same device."""
    first, second, other = (tmp_path / name for name in ("a", "b", "c"))
    for path in (first, second, other):
        path.touch()
    stats = {
        str(first): (0o20660, 0x1880),
        str(second): (0o20660, 0x1880),
        str(other): (0o20660, 0x1881),
    }

    def fake_stat(path):
        mode, rdev = stats[str(path)]
        return type("S", (), {"st_mode": mode, "st_rdev": rdev})()

    with patch("custom_components.victron_vebus.config_flow.os.stat", fake_stat):
        assert _same_node(str(first), str(second))
        assert not _same_node(str(first), str(other))
    assert not _same_node(str(first), str(tmp_path / "missing"))
