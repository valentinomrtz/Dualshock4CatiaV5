"""Real HID I/O against a physical DualShock 4 controller.

This module is the I/O boundary: it owns everything that actually talks
to hardware (opening the HID device, reading raw reports, writing output
reports, the Bluetooth extended-mode handshake). It deliberately knows as
little as possible about *what the bytes mean* -- that is entirely
delegated to the pure parser in ``ds4catia.hid.ds4_report``
(``parse_usb_report``/``parse_bt_report``/``build_output_report_usb``),
which this module imports and uses but never reimplements.

Library: ``hidapi`` via its Python bindings, PyPI package ``hid``
(https://pypi.org/project/hid/, the libusb/hidapi project's own bindings
-- this is the exact package pinned in this project's ``pyproject.toml``
as ``hid>=1.0.5; sys_platform == 'win32'``). Its documented shape (per the
project's own README/examples) is:

    hid.enumerate(vendor_id=0, product_id=0) -> list[dict]
        Each dict carries (at least) 'path', 'vendor_id', 'product_id',
        'serial_number', 'release_number', 'manufacturer_string',
        'product_string', 'usage_page', 'usage', 'interface_number', and
        'bus_type' (an int matching hidapi's own ``hid_bus_type`` C enum:
        0=unknown, 1=USB, 2=Bluetooth, 3=I2C, 4=SPI).

    hid.Device(vid=None, pid=None, serial=None, path=None)
        Opens a device (by path, or by vid/pid/serial). Exposes
        ``.read(size, timeout=None)`` (returns b"" on timeout/no data),
        ``.write(data)``, ``.send_feature_report(data)``,
        ``.get_feature_report(report_id, size)``, and ``.close()``.

Linux-friendly import story
----------------------------
This module is developed and unit-tested (for its own logic, with a fake
``hid`` module injected) on Linux, with no physical DS4 attached and
*without* the ``hid`` package installed at all (it is a Windows-only
dependency per ``pyproject.toml``). Importing ``hid`` is therefore
performed lazily/defensively at module load time, exactly like
``ds4catia.input_injection.sendinput`` does for its Windows-only imports
and ``ds4catia.catia.interface``/the real CATIA COM backend are expected
to: ``import hid`` is wrapped in a ``try/except`` and falls back to
``hid = None``, so simply *importing* this module never raises here.
Only the methods that actually need a working HID handle
(``discover_devices``, ``DS4Device.connect``/``read_raw``/
``write_output_report``/``perform_bt_extended_handshake``) raise a clear,
actionable error (``RuntimeError`` for "hid is not installed at all",
``DS4ConnectionError`` for every other hidapi-level failure) instead of a
cryptic ``AttributeError`` on ``None``.

Transport (USB vs Bluetooth) and the Bluetooth handshake
----------------------------------------------------------
Per ``research_notes/Mando DualShock4 para CATIA V5/ds4_input_capture.md``
(Question 3): USB is the recommended default transport because it needs
no special handshake and always sends the full extended report (ID
``0x11`` is Bluetooth-only; USB's extended report is ``0x01``). Over
Bluetooth, the DS4 boots into a basic, touchpad/gyro-free report mode,
and -- unlike Linux's kernel driver -- Windows does **not** automatically
send the feature report that switches it into extended mode; the
application itself must request/send the calibration feature report
(``FEATURE_REPORT_CALIBRATION``, ``0x05``, the same id ``ds4_report.py``
defines) to trigger that switch. The exact handshake byte content was
not confirmed against a primary source in that research pass (it is
flagged there as a gap), so :meth:`DS4Device.perform_bt_extended_handshake`
is explicitly marked ``# VERIFY-ON-HARDWARE`` and should be checked
against a real Bluetooth-paired controller.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from ds4catia.hid.ds4_report import (
    BT_INPUT_REPORT_ID,
    FEATURE_REPORT_CALIBRATION,
    USB_INPUT_REPORT_ID,
    DS4State,
    build_output_report_usb,
    parse_bt_report,
    parse_usb_report,
)

_HID_IMPORT_ERROR: Optional[BaseException] = None
try:
    import hid  # type: ignore[import-not-found]
except Exception as _exc:  # pragma: no cover - exercised only when `hid` is missing
    hid = None  # type: ignore[assignment]
    _HID_IMPORT_ERROR = _exc


__all__ = [
    "DS4_VENDOR_ID",
    "DS4_PRODUCT_IDS",
    "DeviceInfo",
    "discover_devices",
    "DS4ConnectionError",
    "DS4Device",
]


# ---------------------------------------------------------------------------
# Identifiers (confirmed by research_notes/.../ds4_input_capture.md, Q5:
# "054C:05C4 for v1 DS4 / 054C:09CC for v2 DS4 -- extremely well-known
# industry-standard identifiers").
# ---------------------------------------------------------------------------

DS4_VENDOR_ID: int = 0x054C
DS4_PRODUCT_IDS: tuple = (0x05C4, 0x09CC)

# hidapi's own bus-type enum (`hid_bus_type` in hidapi.h), mirrored here as
# a defensive fallback: we cannot import the real `hid` package in this dev
# environment to confirm whether the installed version re-exports these as
# module-level constants (`hid.BUS_USB` / `hid.BUS_BLUETOOTH`, as later
# releases of the libusb/hidapi python bindings do). Code below prefers
# `getattr(hid, "BUS_BLUETOOTH", ...)` and falls back to this literal only
# if the module does not define it.
_HID_BUS_USB = 0x01
_HID_BUS_BLUETOOTH = 0x02

_TRANSPORT_USB = "usb"
_TRANSPORT_BLUETOOTH = "bluetooth"

# Raw USB input reports are 64 bytes on the wire for the DS4 (matches the
# buffer size used throughout tests/test_ds4_report.py's USB fixtures);
# used as the default read-buffer size.
_DEFAULT_READ_SIZE = 64

# VERIFY-ON-HARDWARE: cited by the research notes as "37 bytes USB vs. 41
# bytes Bluetooth" for the calibration feature report (0x05); not
# independently confirmed against a primary source.
_FEATURE_REPORT_CALIBRATION_SIZE_BT = 41


@dataclass
class DeviceInfo:
    """One DS4 HID device, as reported by ``hid.enumerate()``.

    ``path`` is the only field actually required to open the device
    (``hid.Device(path=...)``); the rest are carried along for
    diagnostics/display and transport detection.

    ``bus_type`` lets :meth:`DS4Device.connect` infer USB vs Bluetooth
    automatically when the installed ``hid`` build reports it. Not every
    ``hid``/hidapi build does (it is a relatively recent addition to the
    hidapi C library), so it is ``Optional`` here; when it is ``None``,
    :class:`DS4Device` defaults to treating the connection as USB (the
    project's recommended default transport) unless the caller passes an
    explicit ``transport`` hint to the constructor.
    """

    path: Optional[bytes]
    vendor_id: int
    product_id: int
    serial_number: Optional[str] = None
    interface_number: Optional[int] = None
    bus_type: Optional[int] = None
    manufacturer_string: Optional[str] = None
    product_string: Optional[str] = None


def _require_hid() -> None:
    """Raise a clear, actionable error if the ``hid`` package is missing.

    Without this guard, every call below would instead fail with a bare
    ``AttributeError: 'NoneType' object has no attribute 'enumerate'`` (or
    similar) -- correct, but unhelpful to whoever hits it.
    """
    if hid is None:
        if _HID_IMPORT_ERROR is not None:
            # `hid` is installed but failed to import for some other
            # reason (missing native dependency, ABI mismatch, ...) --
            # surface that real error instead of implying it's simply
            # not installed, which sends people down the wrong path.
            raise RuntimeError(
                "The 'hid' module is installed but failed to import: "
                f"{type(_HID_IMPORT_ERROR).__name__}: {_HID_IMPORT_ERROR}. "
                "This usually means a native dependency the compiled "
                "'hidapi' package needs isn't present on this machine "
                "(e.g. the Microsoft Visual C++ Redistributable runtime "
                "on Windows) -- see docs/troubleshooting.md."
            ) from _HID_IMPORT_ERROR
        raise RuntimeError(
            "The 'hid' module (hidapi Python bindings; PyPI package "
            "'hidapi', pinned as hidapi>=0.14 for Windows in this "
            "project's pyproject.toml) is not installed in this "
            "environment. Raw HID access to a DS4 controller requires "
            "it -- install it with `pip install hidapi` on a machine "
            "where the controller will actually be read (this dev "
            "environment is Linux with no controller attached, so this "
            "is expected here). Do NOT install PyPI's separate 'hid' "
            "package instead -- it imports under the same name but does "
            "not bundle the compiled hidapi library, and fails with an "
            "'Unable to load any of the following libraries' ImportError "
            "unless that library is already present on the system (see "
            "docs/troubleshooting.md)."
        )


def discover_devices() -> list:
    """Enumerate connected DS4 controllers (USB or Bluetooth) via hidapi.

    Returns:
        A list of :class:`DeviceInfo`, one per HID top-level collection
        matching ``DS4_VENDOR_ID``/``DS4_PRODUCT_IDS`` that
        ``hid.enumerate()`` reports (a single physical controller can
        legitimately show up more than once if it exposes more than one
        HID interface; callers that care should disambiguate via
        ``interface_number``).

    Raises:
        RuntimeError: if the ``hid`` package is not installed, with a
        clear, actionable message (see :func:`_require_hid`) instead of
        an ``AttributeError`` on ``hid`` being ``None``.
    """
    _require_hid()
    devices = []
    for info in hid.enumerate(DS4_VENDOR_ID, 0):
        product_id = info.get("product_id")
        if product_id not in DS4_PRODUCT_IDS:
            continue
        devices.append(
            DeviceInfo(
                path=info.get("path"),
                vendor_id=info.get("vendor_id", DS4_VENDOR_ID),
                product_id=product_id,
                serial_number=info.get("serial_number"),
                interface_number=info.get("interface_number"),
                bus_type=info.get("bus_type"),
                manufacturer_string=info.get("manufacturer_string"),
                product_string=info.get("product_string"),
            )
        )
    return devices


class DS4ConnectionError(Exception):
    """Raised for any HID-level failure talking to a physical DS4.

    Every exception the ``hid``/hidapi library itself can raise (its
    exact exception types vary by platform/backend and are not part of
    its documented public contract) is caught at the boundary and
    re-raised as this single, project-owned type with a clear message --
    callers of :class:`DS4Device` never need to know or catch anything
    ``hid``-specific. It is also raised for this module's own guard
    clauses (e.g. calling a device method before :meth:`DS4Device.connect`).
    """


class DS4Device:
    """A single physical DS4's HID handle, plus the logic around it.

    Responsibilities:

    * Open/close the underlying ``hid.Device`` handle
      (:meth:`connect`/:meth:`disconnect`), translating any failure into
      :class:`DS4ConnectionError`.
    * Detect USB vs Bluetooth, used to pick ``parse_usb_report`` vs
      ``parse_bt_report`` and to decide whether the Bluetooth
      extended-mode handshake applies.
    * Run a background read loop that parses incoming reports and invokes
      a caller-supplied callback (:meth:`start_reading`).
    * Send output reports, including a convenience helper that builds one
      from rumble/light-bar parameters (:meth:`set_rumble_and_light`).

    Transport detection (see module docstring): if the ``DeviceInfo``
    passed to the constructor carries a non-``None`` ``bus_type``, that
    is used. Otherwise, an explicit ``transport`` argument
    (``"usb"``/``"bluetooth"``) is used if given. Otherwise, this
    defaults to ``"usb"`` -- the project's recommended default transport,
    and the safe choice when the transport genuinely cannot be
    determined (USB needs no handshake, so defaulting to it never makes
    :meth:`perform_bt_extended_handshake` fire spuriously).
    """

    def __init__(
        self,
        device_info: Optional[DeviceInfo] = None,
        transport: Optional[str] = None,
    ) -> None:
        """Create a (not yet connected) wrapper for one DS4.

        Args:
            device_info: Which device to open, as returned by
                :func:`discover_devices`. If ``None``, :meth:`connect`
                opens the first DS4 hidapi finds by vendor id alone
                (``hid.Device(vid=DS4_VENDOR_ID)``) -- convenient for the
                common single-controller case, but callers with more
                than one DS4 attached should always pass an explicit
                ``device_info`` (from :func:`discover_devices`) instead.
            transport: Optional explicit ``"usb"``/``"bluetooth"`` hint,
                taking precedence over ``device_info.bus_type`` when
                given. Use this when the installed ``hid`` build does not
                report ``bus_type` at all.
        """
        self._device_info = device_info
        self._requested_transport = transport
        self._handle = None  # the underlying hid.Device, once connected
        self._transport: Optional[str] = None

    def is_connected(self) -> bool:
        """Return whether this wrapper currently holds an open HID handle."""
        return self._handle is not None

    def connect(self) -> None:
        """Open the underlying HID device handle.

        Idempotent: calling this while already connected is a no-op.

        Raises:
            RuntimeError: if the ``hid`` package is not installed.
            DS4ConnectionError: if ``hid`` is installed but opening the
                device fails (not found, permission denied, already
                exclusively opened by something else, etc.) -- whatever
                exception ``hid.Device(...)`` itself raised is wrapped
                here, never propagated directly.
        """
        _require_hid()
        if self._handle is not None:
            return

        try:
            if self._device_info is not None and self._device_info.path is not None:
                handle = hid.Device(path=self._device_info.path)
            elif self._device_info is not None:
                handle = hid.Device(
                    vid=self._device_info.vendor_id,
                    pid=self._device_info.product_id,
                )
            else:
                handle = hid.Device(vid=DS4_VENDOR_ID)
        except Exception as exc:
            raise DS4ConnectionError(f"Failed to open DS4 HID device: {exc}") from exc

        self._handle = handle
        self._transport = self._detect_transport()

    def _detect_transport(self) -> str:
        if self._requested_transport in (_TRANSPORT_USB, _TRANSPORT_BLUETOOTH):
            return self._requested_transport

        bus_type = self._device_info.bus_type if self._device_info is not None else None
        if bus_type is None:
            # Some hid/hidapi builds surface bus_type on the open handle
            # itself rather than (or in addition to) the enumerate() dict.
            bus_type = getattr(self._handle, "bus_type", None)

        bluetooth_value = getattr(hid, "BUS_BLUETOOTH", _HID_BUS_BLUETOOTH)
        if bus_type == bluetooth_value:
            return _TRANSPORT_BLUETOOTH

        # Default: USB. This is both the project's recommended default
        # transport (see module docstring) and the safe fallback when the
        # transport cannot be determined at all, since it never triggers
        # the Bluetooth-only handshake spuriously.
        return _TRANSPORT_USB

    def disconnect(self) -> None:
        """Close the underlying HID device handle, if open.

        Safe to call even if never connected (no-op). Raises
        :class:`DS4ConnectionError` if the underlying ``close()`` call
        itself fails; the handle is cleared either way so a subsequent
        :meth:`connect` can still be attempted.
        """
        if self._handle is None:
            return
        try:
            self._handle.close()
        except Exception as exc:
            raise DS4ConnectionError(f"Failed to close DS4 HID device: {exc}") from exc
        finally:
            self._handle = None
            self._transport = None

    def _ensure_connected(self) -> None:
        if self._handle is None:
            raise DS4ConnectionError(
                "DS4Device is not connected; call connect() first."
            )

    def perform_bt_extended_handshake(self) -> None:
        """Force a Bluetooth-connected DS4 into extended (0x11) report mode.

        # VERIFY-ON-HARDWARE: per the module docstring, the research could
        # not confirm the exact handshake byte content against a primary
        # source -- only that *requesting the calibration feature report
        # (id 0x05)* is the documented trigger (this is what Linux's
        # kernel driver does, which is what causes Windows-side tools that
        # replicate it to start receiving 0x11 reports). This issues a
        # `get_feature_report` for that id and must be verified against a
        # real Bluetooth-paired DS4 before being trusted.

        No-op if the connection is USB: the handshake is Bluetooth-only
        and meaningless (there is nothing to "force" -- USB already
        always sends the extended report).

        Raises:
            DS4ConnectionError: if not connected, or if the underlying
                ``get_feature_report`` call fails.
        """
        self._ensure_connected()
        if self._transport != _TRANSPORT_BLUETOOTH:
            return

        try:
            self._handle.get_feature_report(
                FEATURE_REPORT_CALIBRATION, _FEATURE_REPORT_CALIBRATION_SIZE_BT
            )
        except Exception as exc:
            raise DS4ConnectionError(
                "Bluetooth extended-mode handshake (feature report "
                f"0x{FEATURE_REPORT_CALIBRATION:02x}) failed: {exc}"
            ) from exc

    def read_raw(self, timeout_ms: Optional[int] = None) -> Optional[bytes]:
        """Read one raw report from the device.

        Args:
            timeout_ms: Maximum time to block waiting for a report, in
                milliseconds. ``None`` defers to the ``hid`` library's own
                default (platform-dependent; may block indefinitely).

        Returns:
            The raw report bytes (including the leading report-id byte),
            or ``None`` if the read timed out / no data was available.

        Raises:
            DS4ConnectionError: if not connected, or if the underlying
                read fails.
        """
        self._ensure_connected()
        try:
            data = self._handle.read(_DEFAULT_READ_SIZE, timeout_ms)
        except Exception as exc:
            raise DS4ConnectionError(f"Failed to read from DS4: {exc}") from exc
        if not data:
            return None
        return bytes(data)

    def write_output_report(self, data: bytes) -> None:
        """Write a pre-built raw output report to the device.

        Use :func:`ds4catia.hid.ds4_report.build_output_report_usb` (or
        :meth:`set_rumble_and_light` as a shortcut) to build ``data``; this
        method itself does no interpretation of the bytes, only I/O.

        Raises:
            DS4ConnectionError: if not connected, or if the underlying
                write fails.
        """
        self._ensure_connected()
        try:
            self._handle.write(bytes(data))
        except Exception as exc:
            raise DS4ConnectionError(f"Failed to write to DS4: {exc}") from exc

    def set_rumble_and_light(
        self,
        rumble_small: int = 0,
        rumble_big: int = 0,
        led_r: int = 0,
        led_g: int = 0,
        led_b: int = 0,
        flash_on_ms: int = 0,
        flash_off_ms: int = 0,
    ) -> None:
        """Convenience wrapper: build a USB output report and send it.

        Internally calls ``build_output_report_usb`` (from
        ``ds4_report.py`` -- see its own ``# VERIFY-ON-HARDWARE`` notes on
        the output report's byte layout) and then
        :meth:`write_output_report`. Only confirmed meaningful over USB;
        the Bluetooth output report layout is a separate, unconfirmed
        question the research flagged as a gap.
        """
        report = build_output_report_usb(
            rumble_small=rumble_small,
            rumble_big=rumble_big,
            led_r=led_r,
            led_g=led_g,
            led_b=led_b,
            flash_on_ms=flash_on_ms,
            flash_off_ms=flash_off_ms,
        )
        self.write_output_report(report)

    def start_reading(
        self,
        callback: Callable[[DS4State], None],
        stop_event: Optional[threading.Event] = None,
    ) -> threading.Thread:
        """Start a background thread that reads, parses, and dispatches reports.

        The loop: ``read_raw()`` -> look at the first byte to decide
        ``parse_usb_report`` vs ``parse_bt_report`` (by comparing against
        ``USB_INPUT_REPORT_ID``/``BT_INPUT_REPORT_ID``, imported from
        ``ds4_report.py``) -> ``callback(state)``. A report with neither
        id, or one too short for the parser to accept (it raises
        ``ValueError``), is silently skipped rather than killing the
        thread -- a single malformed report should not stop the whole
        reading loop.

        A bounded ``read_raw`` timeout (100 ms) is used internally so the
        loop polls ``stop_event`` promptly instead of potentially
        blocking forever on a controller that stops sending reports.

        Args:
            callback: Called with each successfully parsed ``DS4State``.
                Runs on the background thread -- callers needing to touch
                UI state, etc. from it are responsible for their own
                thread-safety/marshalling.
            stop_event: Signals the loop to stop when set. If omitted, a
                fresh ``threading.Event`` is created (callers who do not
                keep a reference to it have no way to stop the thread
                short of disconnecting the device, which makes the next
                ``read_raw`` raise and also ends the loop).

        Returns:
            The already-started (non-blocking) ``Thread``; call
            ``.join()`` on it to block until it stops.

        Raises:
            DS4ConnectionError: if not connected yet.
        """
        self._ensure_connected()
        if stop_event is None:
            stop_event = threading.Event()

        def _loop() -> None:
            while not stop_event.is_set():
                try:
                    raw = self.read_raw(timeout_ms=100)
                except DS4ConnectionError:
                    return  # device gone / I/O error: stop the loop
                if not raw:
                    continue

                report_id = raw[0]
                try:
                    if report_id == USB_INPUT_REPORT_ID:
                        state = parse_usb_report(raw)
                    elif report_id == BT_INPUT_REPORT_ID:
                        state = parse_bt_report(raw)
                    else:
                        continue  # unknown report id (e.g. basic BT mode)
                except ValueError:
                    continue  # malformed/too-short report; skip it

                callback(state)

        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()
        return thread
