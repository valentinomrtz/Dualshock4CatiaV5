"""Tests for ds4catia.hid.ds4_device.

ds4_device.py is the real HID I/O layer against a physical DualShock 4
controller. This development environment is Linux, has the `hid` package
(hidapi's Python bindings -- a Windows-only dependency per this project's
pyproject.toml) *not installed at all*, and has no physical controller
attached. None of the following can therefore be exercised here, and are
left to the user's own manual verification checklist on a Windows machine
with a real DS4 and the `hid` package installed:

  * That `hid.enumerate()` actually finds a real DS4 (USB or Bluetooth)
    and that its reported fields (path, bus_type, etc.) match what this
    module assumes.
  * That `hid.Device(...)` actually opens the real device without error,
    and that reading from it returns real report bytes.
  * That bytes read from a real controller parse into sensible
    `DS4State` values end-to-end through this module (the pure parser
    itself is already covered against synthetic bytes by
    tests/test_ds4_report.py; what's untested here is the real hidapi
    round-trip).
  * That `perform_bt_extended_handshake` actually switches a real,
    Bluetooth-paired DS4 into extended (0x11) report mode -- the
    project's research notes could not confirm the exact handshake byte
    content against a primary source, so this is explicitly flagged
    `# VERIFY-ON-HARDWARE` in ds4_device.py and must be checked by hand.
  * That `write_output_report`/`set_rumble_and_light` actually produce a
    visible/felt rumble or light-bar effect on a real controller.

What *is* tested here, using a fake `hid` module injected via
monkeypatching (never the real library), is this module's own logic:
that it imports cleanly without `hid` installed, the guard clauses before
`connect()`, wrapping of library exceptions into `DS4ConnectionError`,
transport (USB/Bluetooth) handling, and the `start_reading` background
thread correctly parsing a synthetic USB report and invoking its
callback, plus stopping cleanly via `stop_event`.
"""

import importlib
import threading

import pytest

import ds4catia.hid.ds4_device as ds4_device
from ds4catia.hid.ds4_report import USB_INPUT_REPORT_ID, DS4State

USB_REPORT_LENGTH = 64


def _valid_usb_report() -> bytes:
    """A minimal, well-formed USB DS4 report: centered sticks, nothing
    pressed, dpad released -- built the same way
    tests/test_ds4_report.py's fixtures are, just inlined here since this
    module only needs one simple report."""
    data = bytearray(USB_REPORT_LENGTH)
    data[0] = USB_INPUT_REPORT_ID
    data[1] = 128  # left_stick_x, centered
    data[2] = 128  # left_stick_y, centered
    data[3] = 128  # right_stick_x, centered
    data[4] = 128  # right_stick_y, centered
    data[5] = 0x08  # buttons1: dpad released, no face buttons
    return bytes(data)


# ---------------------------------------------------------------------------
# Import story / missing-library guard clauses
# ---------------------------------------------------------------------------


def test_module_imports_without_hid_installed():
    """Importing the module must succeed even though `hid` is not
    installed in this environment -- reload to re-prove it rather than
    relying only on this file's own top-level import having worked."""
    reloaded = importlib.reload(ds4_device)
    assert reloaded.hid is None


def test_discover_devices_raises_runtime_error_without_hid(monkeypatch):
    monkeypatch.setattr(ds4_device, "hid", None)
    with pytest.raises(RuntimeError):
        ds4_device.discover_devices()


# ---------------------------------------------------------------------------
# Guard clauses: calling device methods before connect()
# ---------------------------------------------------------------------------


def test_is_connected_false_before_connect():
    device = ds4_device.DS4Device()
    assert device.is_connected() is False


def test_read_raw_before_connect_raises():
    device = ds4_device.DS4Device()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.read_raw()


def test_write_output_report_before_connect_raises():
    device = ds4_device.DS4Device()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.write_output_report(b"\x00" * 32)


def test_bt_handshake_before_connect_raises():
    device = ds4_device.DS4Device()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.perform_bt_extended_handshake()


def test_start_reading_before_connect_raises():
    device = ds4_device.DS4Device()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.start_reading(lambda state: None)


# ---------------------------------------------------------------------------
# Fakes standing in for the `hid` package (never the real library).
# ---------------------------------------------------------------------------


class _FakeHidHandle:
    """Stand-in for a real `hid.Device` handle."""

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.written = []
        self.feature_report_requests = []
        self._queue = []
        self.bus_type = None

    def queue_report(self, data: bytes) -> None:
        self._queue.append(data)

    def read(self, size, timeout=None):
        if self._queue:
            return self._queue.pop(0)
        return b""

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def send_feature_report(self, data):
        return len(data)

    def get_feature_report(self, report_id, size):
        self.feature_report_requests.append((report_id, size))
        return bytes([report_id]) + bytes(size - 1)

    def close(self):
        self.closed = True


class _FakeHidModule:
    """Stand-in for the top-level `hid` module."""

    def __init__(self, handle):
        self._handle = handle
        self.open_calls = []

    def Device(self, *args, **kwargs):
        self.open_calls.append(kwargs or args)
        return self._handle

    def enumerate(self, vendor_id=0, product_id=0):
        return []


class _ExplodingHidModule:
    """A fake `hid` module whose Device() always fails to open."""

    def Device(self, *args, **kwargs):
        raise OSError("no such device")


# ---------------------------------------------------------------------------
# connect() / disconnect()
# ---------------------------------------------------------------------------


def test_connect_wraps_library_exception(monkeypatch):
    monkeypatch.setattr(ds4_device, "hid", _ExplodingHidModule())
    device = ds4_device.DS4Device()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.connect()


def test_connect_disconnect_roundtrip(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    assert device.is_connected() is True

    device.disconnect()
    assert device.is_connected() is False
    assert handle.closed is True


def test_connect_is_idempotent(monkeypatch):
    handle = _FakeHidHandle()
    fake_hid = _FakeHidModule(handle)
    monkeypatch.setattr(ds4_device, "hid", fake_hid)

    device = ds4_device.DS4Device()
    device.connect()
    device.connect()  # second call must be a no-op, not re-open
    assert len(fake_hid.open_calls) == 1


def test_disconnect_without_connect_is_a_noop():
    device = ds4_device.DS4Device()
    device.disconnect()  # must not raise
    assert device.is_connected() is False


# ---------------------------------------------------------------------------
# Transport detection + Bluetooth handshake
# ---------------------------------------------------------------------------


def test_bt_handshake_is_noop_on_usb_transport(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()  # no transport hint -> defaults to USB
    device.connect()
    device.perform_bt_extended_handshake()

    assert handle.feature_report_requests == []


def test_bt_handshake_requests_calibration_feature_report_on_bluetooth(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device(transport="bluetooth")
    device.connect()
    device.perform_bt_extended_handshake()

    assert len(handle.feature_report_requests) == 1
    report_id, _size = handle.feature_report_requests[0]
    from ds4catia.hid.ds4_report import FEATURE_REPORT_CALIBRATION

    assert report_id == FEATURE_REPORT_CALIBRATION


def test_device_info_bus_type_selects_bluetooth_transport(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))
    monkeypatch.setattr(ds4_device, "_HID_BUS_BLUETOOTH", 2, raising=False)

    info = ds4_device.DeviceInfo(
        path=b"fake-path",
        vendor_id=ds4_device.DS4_VENDOR_ID,
        product_id=ds4_device.DS4_PRODUCT_IDS[0],
        bus_type=2,  # matches the hidapi HID_API_BUS_BLUETOOTH value
    )
    device = ds4_device.DS4Device(device_info=info)
    device.connect()
    device.perform_bt_extended_handshake()

    assert len(handle.feature_report_requests) == 1


# ---------------------------------------------------------------------------
# read_raw() / write_output_report()
# ---------------------------------------------------------------------------


def test_read_raw_returns_none_on_empty_read(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    assert device.read_raw(timeout_ms=10) is None


def test_read_raw_returns_bytes_when_available(monkeypatch):
    handle = _FakeHidHandle()
    handle.queue_report(_valid_usb_report())
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    raw = device.read_raw()
    assert raw == _valid_usb_report()


def test_read_raw_wraps_library_exception(monkeypatch):
    handle = _FakeHidHandle()

    def _boom(size, timeout=None):
        raise OSError("device disconnected")

    handle.read = _boom
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.read_raw()


def test_write_output_report_sends_raw_bytes(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    payload = b"\x05" + b"\x00" * 31
    device.write_output_report(payload)

    assert handle.written == [payload]


def test_write_output_report_wraps_library_exception(monkeypatch):
    handle = _FakeHidHandle()

    def _boom(data):
        raise OSError("write failed")

    handle.write = _boom
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    with pytest.raises(ds4_device.DS4ConnectionError):
        device.write_output_report(b"\x00" * 32)


def test_set_rumble_and_light_builds_and_sends_report(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()
    device.set_rumble_and_light(rumble_small=10, rumble_big=20, led_r=1, led_g=2, led_b=3)

    assert len(handle.written) == 1
    report = handle.written[0]
    assert report[0] == 0x05
    assert report[3] == 10
    assert report[4] == 20
    assert report[5] == 1
    assert report[6] == 2
    assert report[7] == 3


# ---------------------------------------------------------------------------
# start_reading(): the background thread loop
# ---------------------------------------------------------------------------


def test_start_reading_parses_usb_report_and_invokes_callback(monkeypatch):
    handle = _FakeHidHandle()
    handle.queue_report(_valid_usb_report())
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()

    received = []
    stop_event = threading.Event()

    def callback(state):
        received.append(state)
        stop_event.set()

    thread = device.start_reading(callback, stop_event=stop_event)
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert len(received) == 1
    state = received[0]
    assert isinstance(state, DS4State)
    assert abs(state.left_stick[0]) < 0.01
    assert abs(state.right_stick[1]) < 0.01
    assert state.dpad is None


def test_start_reading_returns_started_nonblocking_thread(monkeypatch):
    handle = _FakeHidHandle()
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()

    stop_event = threading.Event()
    thread = device.start_reading(lambda state: None, stop_event=stop_event)

    # start_reading must not block the calling thread: control must have
    # returned here already, with the loop still running in the background.
    assert isinstance(thread, threading.Thread)
    assert thread.is_alive()

    stop_event.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_start_reading_skips_malformed_report_without_crashing(monkeypatch):
    handle = _FakeHidHandle()
    # Too short to be a valid USB report -- parse_usb_report raises
    # ValueError internally, which start_reading must swallow and move on
    # rather than letting it kill the background thread.
    handle.queue_report(bytes([USB_INPUT_REPORT_ID, 0x00]))
    handle.queue_report(_valid_usb_report())
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()

    received = []
    stop_event = threading.Event()

    def callback(state):
        received.append(state)
        stop_event.set()

    thread = device.start_reading(callback, stop_event=stop_event)
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert len(received) == 1


def test_start_reading_ignores_unknown_report_id(monkeypatch):
    handle = _FakeHidHandle()
    bad = bytearray(_valid_usb_report())
    bad[0] = 0x99  # neither USB_INPUT_REPORT_ID nor BT_INPUT_REPORT_ID
    handle.queue_report(bytes(bad))
    handle.queue_report(_valid_usb_report())
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()

    received = []
    stop_event = threading.Event()

    def callback(state):
        received.append(state)
        stop_event.set()

    thread = device.start_reading(callback, stop_event=stop_event)
    thread.join(timeout=5.0)

    assert len(received) == 1


def test_start_reading_stops_cleanly_without_ever_calling_callback(monkeypatch):
    handle = _FakeHidHandle()  # no queued reports: read() always returns b""
    monkeypatch.setattr(ds4_device, "hid", _FakeHidModule(handle))

    device = ds4_device.DS4Device()
    device.connect()

    received = []
    stop_event = threading.Event()
    thread = device.start_reading(received.append, stop_event=stop_event)

    stop_event.set()
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert received == []
