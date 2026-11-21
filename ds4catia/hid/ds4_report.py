"""Pure parsing of raw DualShock 4 (DS4) HID input/output reports.

This module does exactly one thing: turn the raw bytes a DS4 controller
sends over USB or Bluetooth into a plain ``DS4State`` snapshot, and build
the (best-effort) raw bytes of an output report (rumble + status light).
It knows nothing about hidapi, CATIA, or the GUI -- it is pure byte
parsing/packing, so it can be fully unit tested with synthetic byte
arrays and has no I/O of its own.

Everything here is grounded in the project's own research notes rather
than invented from memory -- see:

    research_notes/Mando DualShock4 para CATIA V5/ds4_input_capture.md
        (Question 4: "Raw HID report format")
    reports/Mando DualShock4 para CATIA V5.md
        (section "El reporte HID crudo es la unica via hacia
        touchpad, giroscopio y luz de estado")

Confirmed by that research (cited from a Linux kernel driver patch
review comment and the Wine project's DS4 Bluetooth analysis):

    * USB input report ID:                 0x01
    * Bluetooth extended input report ID:  0x11
    * Calibration feature report ID:       0x05
    * The multi-touch touchpad block starts at byte offset 33 on USB
      and byte offset 35 on Bluetooth (Bluetooth adds a constant +2
      byte offset relative to USB for the fields that follow the
      report ID, due to extra BT HID framing).
    * Gyroscope and accelerometer are each three consecutive signed
      16-bit little-endian fields (confirmed independently by
      Chromium's ``dualshock4_controller.cc`` device struct).

NOT independently confirmed by the research (the two most
authoritative community references, psdevwiki.com and
eleccelerator.com, could not be fetched in that research pass -- see
the "Gaps" sections of ds4_input_capture.md, Question 4): the exact
bit-level layout of buttons/sticks/triggers/dpad/battery, and the
*output* report (rumble/light bar) layout. Those are filled in here
from the widely-replicated community DS4 report layout (the same one
used by the Linux ``hid-sony``/``hid-playstation`` drivers and
countless independent reimplementations), but -- per the research's
own caution that "firmware/report-format stability... is not 100%
guaranteed" -- they should be treated as best-effort until checked
against real hardware. ``validate_report_sanity`` exists precisely so
that a wrong offset produces a warning instead of a silent bad value,
and the output-report builder is explicitly flagged with
``# VERIFY-ON-HARDWARE`` comments throughout.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

__all__ = [
    "USB_INPUT_REPORT_ID",
    "BT_INPUT_REPORT_ID",
    "FEATURE_REPORT_CALIBRATION",
    "DpadDirection",
    "TouchPoint",
    "DS4State",
    "OffsetTable",
    "USB_OFFSETS",
    "BT_OFFSETS",
    "parse_usb_report",
    "parse_bt_report",
    "validate_report_sanity",
    "build_output_report_usb",
]


# ---------------------------------------------------------------------------
# Report IDs (confirmed by research_notes/.../ds4_input_capture.md, Q4)
# ---------------------------------------------------------------------------

USB_INPUT_REPORT_ID: int = 0x01
BT_INPUT_REPORT_ID: int = 0x11
FEATURE_REPORT_CALIBRATION: int = 0x05


# ---------------------------------------------------------------------------
# Public data model (shared contract -- do not rename these fields)
# ---------------------------------------------------------------------------

DpadDirection = Literal["n", "ne", "e", "se", "s", "sw", "w", "nw", None]


@dataclass
class TouchPoint:
    """One finger on the DS4 touchpad.

    ``id`` is the controller-assigned tracking id for this contact (it is
    stable for as long as the same finger stays down, and changes on the
    next new touch-down -- it is not a fixed slot number).
    """

    id: int
    x: int
    y: int
    active: bool


@dataclass
class DS4State:
    """A single parsed snapshot of a DS4 input report."""

    buttons: dict
    left_stick: tuple
    right_stick: tuple
    l2_analog: float
    r2_analog: float
    dpad: DpadDirection
    touch_points: list = field(default_factory=list)
    gyro: tuple = (0, 0, 0)
    accel: tuple = (0, 0, 0)
    battery_pct: Optional[int] = None
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Offset table
#
# Byte offsets are 0-indexed from the start of the raw report buffer,
# where byte 0 is the report ID itself (0x01 for USB, 0x11 for BT), i.e.
# the same convention hidapi uses when the device has report IDs: the
# report ID is included as the first byte of the buffer returned by a
# read.
#
# The USB layout below is the standard community DS4 report layout
# (matching the Linux hid-sony/hid-playstation driver's field order).
# The BT layout is the same fields shifted by the confirmed +2 byte
# offset (see module docstring), which is also why BT's touchpad_start
# (35) and USB's (33) differ by exactly 2, matching the research
# finding directly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OffsetTable:
    """Named byte offsets for one transport's DS4 input report."""

    left_stick_x: int
    left_stick_y: int
    right_stick_x: int
    right_stick_y: int
    buttons1: int  # dpad nibble (low 4 bits) + square/cross/circle/triangle
    buttons2: int  # l1/r1/l2_digital/r2_digital/share/options/l3/r3
    buttons3: int  # ps/touchpad_click (+ unused report counter bits)
    l2_analog: int
    r2_analog: int
    gyro_start: int  # 3x signed int16 LE: gyro_x, gyro_y, gyro_z
    accel_start: int  # 3x signed int16 LE: accel_x, accel_y, accel_z
    battery: int
    touchpad_start: int  # start of touch point 1 (4 bytes); point 2 follows


# Bit masks within buttons1/buttons2/buttons3 -- VERIFY-ON-HARDWARE for the
# exact bit *positions* (the research did not confirm these), though this
# bit layout is the one consistently reproduced across independent DS4
# community implementations.
_DPAD_MASK = 0x0F
_BTN1_SQUARE = 0x10
_BTN1_CROSS = 0x20
_BTN1_CIRCLE = 0x40
_BTN1_TRIANGLE = 0x80

_BTN2_L1 = 0x01
_BTN2_R1 = 0x02
_BTN2_L2_DIGITAL = 0x04
_BTN2_R2_DIGITAL = 0x08
_BTN2_SHARE = 0x10
_BTN2_OPTIONS = 0x20
_BTN2_L3 = 0x40
_BTN2_R3 = 0x80

_BTN3_PS = 0x01
_BTN3_TOUCHPAD_CLICK = 0x02

_DPAD_TABLE: dict = {
    0: "n",
    1: "ne",
    2: "e",
    3: "se",
    4: "s",
    5: "sw",
    6: "w",
    7: "nw",
    8: None,  # released
}

USB_OFFSETS = OffsetTable(
    left_stick_x=1,
    left_stick_y=2,
    right_stick_x=3,
    right_stick_y=4,
    buttons1=5,
    buttons2=6,
    buttons3=7,
    l2_analog=8,
    r2_analog=9,
    gyro_start=13,
    accel_start=19,
    battery=30,
    touchpad_start=33,  # confirmed by research_notes/.../ds4_input_capture.md
)

BT_OFFSETS = OffsetTable(
    left_stick_x=USB_OFFSETS.left_stick_x + 2,
    left_stick_y=USB_OFFSETS.left_stick_y + 2,
    right_stick_x=USB_OFFSETS.right_stick_x + 2,
    right_stick_y=USB_OFFSETS.right_stick_y + 2,
    buttons1=USB_OFFSETS.buttons1 + 2,
    buttons2=USB_OFFSETS.buttons2 + 2,
    buttons3=USB_OFFSETS.buttons3 + 2,
    l2_analog=USB_OFFSETS.l2_analog + 2,
    r2_analog=USB_OFFSETS.r2_analog + 2,
    gyro_start=USB_OFFSETS.gyro_start + 2,
    accel_start=USB_OFFSETS.accel_start + 2,
    battery=USB_OFFSETS.battery + 2,
    touchpad_start=USB_OFFSETS.touchpad_start + 2,  # 35, confirmed by research
)

# Each touch point occupies 4 bytes (1 id/active byte + 3 packed X/Y bytes);
# the DS4 reports up to 2 simultaneous touch points.
_TOUCH_POINT_SIZE = 4
_TOUCH_POINT_COUNT = 2

# Minimum buffer length needed to read both touch points, per transport.
_USB_MIN_LENGTH = USB_OFFSETS.touchpad_start + _TOUCH_POINT_SIZE * _TOUCH_POINT_COUNT
_BT_MIN_LENGTH = BT_OFFSETS.touchpad_start + _TOUCH_POINT_SIZE * _TOUCH_POINT_COUNT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_stick(raw: int) -> float:
    """Map a raw 0..255 stick axis byte to -1.0..1.0."""

    value = (raw - 127.5) / 127.5
    return max(-1.0, min(1.0, value))


def _normalize_trigger(raw: int) -> float:
    """Map a raw 0..255 analog trigger byte to 0.0..1.0."""

    return max(0.0, min(1.0, raw / 255.0))


def _parse_dpad(buttons1: int) -> DpadDirection:
    return _DPAD_TABLE.get(buttons1 & _DPAD_MASK)


def _parse_signed_int16_triplet(data: bytes, offset: int) -> tuple:
    """Unpack three consecutive signed int16 LE values starting at offset."""

    return struct.unpack_from("<3h", data, offset)


def unpack_touch_point(data: bytes, offset: int) -> TouchPoint:
    """Unpack one DS4 touchpad contact (4 bytes: id/active + 12-bit X/Y pair).

    Byte layout (standard DS4 multi-touch packing, consistent with the
    "12-bit X + 12-bit Y" packing the research notes describe):

        byte 0: bit 7 = NOT touching (1 = finger up), bits 0-6 = contact
                tracking id (increments each new touch-down)
        byte 1: X bits 0-7 (low 8 bits of a 12-bit value)
        byte 2: bits 0-3 = X bits 8-11 (high nibble of X),
                bits 4-7 = Y bits 0-3 (low nibble of Y)
        byte 3: Y bits 4-11 (high 8 bits of a 12-bit value)
    """

    id_byte, x_lo, mix, y_hi = data[offset], data[offset + 1], data[offset + 2], data[offset + 3]
    active = not (id_byte & 0x80)
    contact_id = id_byte & 0x7F
    x = x_lo | ((mix & 0x0F) << 8)
    y = (mix >> 4) | (y_hi << 4)
    return TouchPoint(id=contact_id, x=x, y=y, active=active)


def _unpack_touchpad(data: bytes, start_offset: int) -> list:
    points = []
    for i in range(_TOUCH_POINT_COUNT):
        points.append(unpack_touch_point(data, start_offset + i * _TOUCH_POINT_SIZE))
    return points


def _parse_battery_pct(raw_byte: int) -> Optional[int]:
    """Best-effort battery-level byte -> percentage.

    VERIFY-ON-HARDWARE: the low nibble is the widely-cited "battery level"
    field (0..~10/11 depending on charging state); this maps it linearly
    onto 0..100. The high nibble (cable/charging flags) is not currently
    exposed as a separate field.
    """

    level = raw_byte & 0x0F
    pct = min(level, 10) * 10
    return max(0, min(100, pct))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_common(data: bytes, offsets: OffsetTable, timestamp: float) -> DS4State:
    buttons1 = data[offsets.buttons1]
    buttons2 = data[offsets.buttons2]
    buttons3 = data[offsets.buttons3]

    buttons = {
        "square": bool(buttons1 & _BTN1_SQUARE),
        "cross": bool(buttons1 & _BTN1_CROSS),
        "circle": bool(buttons1 & _BTN1_CIRCLE),
        "triangle": bool(buttons1 & _BTN1_TRIANGLE),
        "l1": bool(buttons2 & _BTN2_L1),
        "r1": bool(buttons2 & _BTN2_R1),
        "l2_digital": bool(buttons2 & _BTN2_L2_DIGITAL),
        "r2_digital": bool(buttons2 & _BTN2_R2_DIGITAL),
        "share": bool(buttons2 & _BTN2_SHARE),
        "options": bool(buttons2 & _BTN2_OPTIONS),
        "l3": bool(buttons2 & _BTN2_L3),
        "r3": bool(buttons2 & _BTN2_R3),
        "ps": bool(buttons3 & _BTN3_PS),
        "touchpad_click": bool(buttons3 & _BTN3_TOUCHPAD_CLICK),
    }

    left_stick = (
        _normalize_stick(data[offsets.left_stick_x]),
        _normalize_stick(data[offsets.left_stick_y]),
    )
    right_stick = (
        _normalize_stick(data[offsets.right_stick_x]),
        _normalize_stick(data[offsets.right_stick_y]),
    )

    l2_analog = _normalize_trigger(data[offsets.l2_analog])
    r2_analog = _normalize_trigger(data[offsets.r2_analog])

    dpad = _parse_dpad(buttons1)

    gyro = _parse_signed_int16_triplet(data, offsets.gyro_start)
    accel = _parse_signed_int16_triplet(data, offsets.accel_start)

    battery_pct = _parse_battery_pct(data[offsets.battery])

    touch_points = _unpack_touchpad(data, offsets.touchpad_start)

    return DS4State(
        buttons=buttons,
        left_stick=left_stick,
        right_stick=right_stick,
        l2_analog=l2_analog,
        r2_analog=r2_analog,
        dpad=dpad,
        touch_points=touch_points,
        gyro=gyro,
        accel=accel,
        battery_pct=battery_pct,
        timestamp=timestamp,
    )


def parse_usb_report(data: bytes, timestamp: Optional[float] = None) -> DS4State:
    """Parse a raw USB DS4 input report (report ID 0x01) into a ``DS4State``.

    ``data`` is expected to include the report ID as its first byte (as
    hidapi returns it for devices that use report IDs). ``timestamp``
    defaults to ``time.time()`` and can be overridden by tests for
    deterministic snapshots.
    """

    if len(data) < _USB_MIN_LENGTH:
        raise ValueError(
            f"USB DS4 report too short: got {len(data)} bytes, need at least "
            f"{_USB_MIN_LENGTH}"
        )
    if data[0] != USB_INPUT_REPORT_ID:
        raise ValueError(
            f"unexpected USB report id 0x{data[0]:02x}, expected "
            f"0x{USB_INPUT_REPORT_ID:02x}"
        )
    return _parse_common(data, USB_OFFSETS, time.time() if timestamp is None else timestamp)


def parse_bt_report(data: bytes, timestamp: Optional[float] = None) -> DS4State:
    """Parse a raw Bluetooth DS4 extended input report (report ID 0x11).

    Same contract as ``parse_usb_report``, but for the Bluetooth-extended
    report whose fields are offset by +2 bytes relative to USB (see the
    module docstring and ``BT_OFFSETS``).
    """

    if len(data) < _BT_MIN_LENGTH:
        raise ValueError(
            f"Bluetooth DS4 report too short: got {len(data)} bytes, need at "
            f"least {_BT_MIN_LENGTH}"
        )
    if data[0] != BT_INPUT_REPORT_ID:
        raise ValueError(
            f"unexpected Bluetooth report id 0x{data[0]:02x}, expected "
            f"0x{BT_INPUT_REPORT_ID:02x}"
        )
    return _parse_common(data, BT_OFFSETS, time.time() if timestamp is None else timestamp)


# ---------------------------------------------------------------------------
# Sanity checking
# ---------------------------------------------------------------------------

_TOUCHPAD_MAX_X = 1920
_TOUCHPAD_MAX_Y = 942
_INT16_MIN = -32768
_INT16_MAX = 32767


def validate_report_sanity(state: DS4State) -> list:
    """Defensive range checks on a parsed ``DS4State``.

    The exact byte offsets used by ``parse_usb_report``/``parse_bt_report``
    are not 100% guaranteed by the project's research (see module
    docstring), so this never raises -- it returns a list of plain-text
    warning strings (empty if nothing looks wrong) that calling code can
    log, surface in a UI, or ignore.
    """

    warnings: list = []

    if state.battery_pct is not None and not (0 <= state.battery_pct <= 100):
        warnings.append(
            f"battery_pct out of range: {state.battery_pct} (expected 0-100 or None)"
        )

    for point in state.touch_points:
        if not point.active:
            continue
        if not (0 <= point.x <= _TOUCHPAD_MAX_X):
            warnings.append(
                f"touch point {point.id} x out of range: {point.x} "
                f"(expected 0-{_TOUCHPAD_MAX_X})"
            )
        if not (0 <= point.y <= _TOUCHPAD_MAX_Y):
            warnings.append(
                f"touch point {point.id} y out of range: {point.y} "
                f"(expected 0-{_TOUCHPAD_MAX_Y})"
            )

    for axis_name, triplet in (("gyro", state.gyro), ("accel", state.accel)):
        for i, component in enumerate(triplet):
            if not (_INT16_MIN <= component <= _INT16_MAX):
                warnings.append(
                    f"{axis_name}[{i}] out of int16 range: {component}"
                )

    return warnings


# ---------------------------------------------------------------------------
# Output report (rumble + status light)
#
# VERIFY-ON-HARDWARE: the research explicitly could not confirm the exact
# byte layout of the DS4's output report (the community reference pages
# that document it, psdevwiki.com/eleccelerator.com, were unreachable in
# the research session -- see ds4_input_capture.md, Question 4, "Output
# reports (rumble + light bar)"). The layout below is a best-effort
# reconstruction from widely-known community conventions and must be
# checked against real hardware before being relied on.
# ---------------------------------------------------------------------------

_OUTPUT_REPORT_ID_USB = 0x05  # VERIFY-ON-HARDWARE
_OUTPUT_REPORT_LENGTH_USB = 32  # VERIFY-ON-HARDWARE


def build_output_report_usb(
    rumble_small: int,
    rumble_big: int,
    led_r: int,
    led_g: int,
    led_b: int,
    flash_on_ms: int = 0,
    flash_off_ms: int = 0,
) -> bytes:
    """Build a best-effort raw USB output report (rumble + status light).

    # VERIFY-ON-HARDWARE: this report's exact layout (report id, the
    # "which features are enabled" flags byte, motor ordering, and total
    # length) is NOT confirmed by this project's research and must be
    # checked against a real DS4 before being trusted. It is modeled on
    # the commonly-reproduced community layout:
    #
    #   byte 0: report id (0x05)
    #   byte 1: feature flags (which of rumble/led/flash are active)
    #   byte 2: reserved
    #   byte 3: rumble_small (right/weak motor), 0-255
    #   byte 4: rumble_big (left/strong motor), 0-255
    #   byte 5: led_r, 0-255
    #   byte 6: led_g, 0-255
    #   byte 7: led_b, 0-255
    #   byte 8: flash_on duration (0-255, approx. units of 10ms)
    #   byte 9: flash_off duration (0-255, approx. units of 10ms)
    #   remaining bytes: zero padding to the report's full length

    All of ``rumble_small``, ``rumble_big``, ``led_r``, ``led_g``,
    ``led_b`` are clamped to 0-255. ``flash_on_ms``/``flash_off_ms`` are
    converted to a rough 0-255 "tens of milliseconds" unit and clamped;
    callers wanting no flashing should leave them at the default 0.
    """

    def clamp_byte(value: int) -> int:
        return max(0, min(255, int(value)))

    def clamp_flash(value_ms: int) -> int:
        return max(0, min(255, int(value_ms) // 10))

    report = bytearray(_OUTPUT_REPORT_LENGTH_USB)
    report[0] = _OUTPUT_REPORT_ID_USB
    report[1] = 0xF7  # VERIFY-ON-HARDWARE: "enable rumble + led + flash" flags
    report[2] = 0x00
    report[3] = clamp_byte(rumble_small)
    report[4] = clamp_byte(rumble_big)
    report[5] = clamp_byte(led_r)
    report[6] = clamp_byte(led_g)
    report[7] = clamp_byte(led_b)
    report[8] = clamp_flash(flash_on_ms)
    report[9] = clamp_flash(flash_off_ms)
    return bytes(report)
