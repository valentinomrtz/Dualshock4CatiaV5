"""Tests for ds4catia.hid.ds4_report.

All test reports are synthetic byte arrays built by hand to match the
offsets documented in ``ds4_report.OffsetTable``/``USB_OFFSETS``/
``BT_OFFSETS`` (ultimately grounded in
research_notes/.../ds4_input_capture.md and the "El reporte HID
crudo..." section of reports/Mando DualShock4 para CATIA V5.md), not
captured from real hardware.
"""

import struct

import pytest

from ds4catia.hid.ds4_report import (
    BT_INPUT_REPORT_ID,
    BT_OFFSETS,
    DS4State,
    TouchPoint,
    USB_INPUT_REPORT_ID,
    USB_OFFSETS,
    build_output_report_usb,
    parse_bt_report,
    parse_usb_report,
    validate_report_sanity,
)

USB_REPORT_LENGTH = 64
BT_REPORT_LENGTH = 78

_DPAD_RELEASED = 0x08


def _pack_touch_point(contact_id: int, x: int, y: int, active: bool) -> bytes:
    id_byte = (contact_id & 0x7F) | (0x00 if active else 0x80)
    x_lo = x & 0xFF
    mix = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
    y_hi = (y >> 4) & 0xFF
    return bytes([id_byte, x_lo, mix, y_hi])


def _base_report(report_id: int, length: int, offsets) -> bytearray:
    """A neutral report: centered sticks, no buttons, dpad released,
    zeroed triggers/gyro/accel, both touch points inactive."""

    data = bytearray(length)
    data[0] = report_id
    data[offsets.left_stick_x] = 128
    data[offsets.left_stick_y] = 128
    data[offsets.right_stick_x] = 128
    data[offsets.right_stick_y] = 128
    data[offsets.buttons1] = _DPAD_RELEASED
    data[offsets.buttons2] = 0x00
    data[offsets.buttons3] = 0x00
    data[offsets.l2_analog] = 0
    data[offsets.r2_analog] = 0
    struct.pack_into("<3h", data, offsets.gyro_start, 0, 0, 0)
    struct.pack_into("<3h", data, offsets.accel_start, 0, 0, 0)
    data[offsets.battery] = 0x05
    p1 = _pack_touch_point(0, 0, 0, active=False)
    p2 = _pack_touch_point(0, 0, 0, active=False)
    data[offsets.touchpad_start : offsets.touchpad_start + 4] = p1
    data[offsets.touchpad_start + 4 : offsets.touchpad_start + 8] = p2
    return data


@pytest.fixture
def usb_base() -> bytearray:
    return _base_report(USB_INPUT_REPORT_ID, USB_REPORT_LENGTH, USB_OFFSETS)


@pytest.fixture
def bt_base() -> bytearray:
    return _base_report(BT_INPUT_REPORT_ID, BT_REPORT_LENGTH, BT_OFFSETS)


# ---------------------------------------------------------------------------
# Report id / length validation
# ---------------------------------------------------------------------------


def test_parse_usb_report_rejects_wrong_report_id(usb_base):
    usb_base[0] = 0x99
    with pytest.raises(ValueError):
        parse_usb_report(bytes(usb_base))


def test_parse_bt_report_rejects_wrong_report_id(bt_base):
    bt_base[0] = 0x99
    with pytest.raises(ValueError):
        parse_bt_report(bytes(bt_base))


def test_parse_usb_report_rejects_too_short_buffer():
    with pytest.raises(ValueError):
        parse_usb_report(bytes([USB_INPUT_REPORT_ID, 0x00]))


def test_parse_bt_report_rejects_too_short_buffer():
    with pytest.raises(ValueError):
        parse_bt_report(bytes([BT_INPUT_REPORT_ID, 0x00]))


def test_parse_usb_report_accepts_timestamp_override(usb_base):
    state = parse_usb_report(bytes(usb_base), timestamp=12345.0)
    assert state.timestamp == 12345.0


def test_parse_usb_report_defaults_timestamp_to_now(usb_base):
    import time

    before = time.time()
    state = parse_usb_report(bytes(usb_base))
    after = time.time()
    assert before <= state.timestamp <= after


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

_BUTTON1_BITS = {
    "square": 0x10,
    "cross": 0x20,
    "circle": 0x40,
    "triangle": 0x80,
}

_BUTTON2_BITS = {
    "l1": 0x01,
    "r1": 0x02,
    "l2_digital": 0x04,
    "r2_digital": 0x08,
    "share": 0x10,
    "options": 0x20,
    "l3": 0x40,
    "r3": 0x80,
}

_BUTTON3_BITS = {
    "ps": 0x01,
    "touchpad_click": 0x02,
}

_ALL_BUTTON_NAMES = (
    "cross",
    "circle",
    "square",
    "triangle",
    "l1",
    "r1",
    "l2_digital",
    "r2_digital",
    "l3",
    "r3",
    "share",
    "options",
    "ps",
    "touchpad_click",
)


def test_all_buttons_released_by_default(usb_base):
    state = parse_usb_report(bytes(usb_base))
    for name in _ALL_BUTTON_NAMES:
        assert state.buttons[name] is False, name


@pytest.mark.parametrize("name,bit", _BUTTON1_BITS.items())
def test_individual_face_button_pressed(usb_base, name, bit):
    usb_base[USB_OFFSETS.buttons1] = _DPAD_RELEASED | bit
    state = parse_usb_report(bytes(usb_base))
    for other in _ALL_BUTTON_NAMES:
        assert state.buttons[other] is (other == name)


@pytest.mark.parametrize("name,bit", _BUTTON2_BITS.items())
def test_individual_button2_pressed(usb_base, name, bit):
    usb_base[USB_OFFSETS.buttons2] = bit
    state = parse_usb_report(bytes(usb_base))
    for other in _ALL_BUTTON_NAMES:
        assert state.buttons[other] is (other == name)


@pytest.mark.parametrize("name,bit", _BUTTON3_BITS.items())
def test_individual_button3_pressed(usb_base, name, bit):
    usb_base[USB_OFFSETS.buttons3] = bit
    state = parse_usb_report(bytes(usb_base))
    for other in _ALL_BUTTON_NAMES:
        assert state.buttons[other] is (other == name)


# ---------------------------------------------------------------------------
# Sticks
# ---------------------------------------------------------------------------


def test_left_stick_center(usb_base):
    usb_base[USB_OFFSETS.left_stick_x] = 128
    usb_base[USB_OFFSETS.left_stick_y] = 128
    state = parse_usb_report(bytes(usb_base))
    assert abs(state.left_stick[0]) < 0.01
    assert abs(state.left_stick[1]) < 0.01


def test_left_stick_extremes(usb_base):
    usb_base[USB_OFFSETS.left_stick_x] = 0
    usb_base[USB_OFFSETS.left_stick_y] = 255
    state = parse_usb_report(bytes(usb_base))
    assert state.left_stick[0] == pytest.approx(-1.0)
    assert state.left_stick[1] == pytest.approx(1.0)


def test_right_stick_extremes(usb_base):
    usb_base[USB_OFFSETS.right_stick_x] = 255
    usb_base[USB_OFFSETS.right_stick_y] = 0
    state = parse_usb_report(bytes(usb_base))
    assert state.right_stick[0] == pytest.approx(1.0)
    assert state.right_stick[1] == pytest.approx(-1.0)


def test_right_stick_center(usb_base):
    state = parse_usb_report(bytes(usb_base))
    assert abs(state.right_stick[0]) < 0.01
    assert abs(state.right_stick[1]) < 0.01


# ---------------------------------------------------------------------------
# Analog triggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [(0, 0.0), (255, 1.0), (128, pytest.approx(128 / 255, abs=0.01))],
)
def test_l2_analog(usb_base, raw, expected):
    usb_base[USB_OFFSETS.l2_analog] = raw
    state = parse_usb_report(bytes(usb_base))
    assert state.l2_analog == expected


@pytest.mark.parametrize(
    "raw,expected",
    [(0, 0.0), (255, 1.0), (64, pytest.approx(64 / 255, abs=0.01))],
)
def test_r2_analog(usb_base, raw, expected):
    usb_base[USB_OFFSETS.r2_analog] = raw
    state = parse_usb_report(bytes(usb_base))
    assert state.r2_analog == expected


# ---------------------------------------------------------------------------
# Dpad
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nibble,expected",
    [
        (0, "n"),
        (1, "ne"),
        (2, "e"),
        (3, "se"),
        (4, "s"),
        (5, "sw"),
        (6, "w"),
        (7, "nw"),
        (8, None),
    ],
)
def test_dpad_directions(usb_base, nibble, expected):
    # keep the upper nibble (face buttons) untouched at 0
    usb_base[USB_OFFSETS.buttons1] = nibble
    state = parse_usb_report(bytes(usb_base))
    assert state.dpad == expected


def test_dpad_does_not_affect_face_buttons(usb_base):
    usb_base[USB_OFFSETS.buttons1] = 0x08 | 0x10  # released + square
    state = parse_usb_report(bytes(usb_base))
    assert state.dpad is None
    assert state.buttons["square"] is True


# ---------------------------------------------------------------------------
# Touchpad
# ---------------------------------------------------------------------------


def test_touchpad_both_points_inactive_by_default(usb_base):
    state = parse_usb_report(bytes(usb_base))
    assert len(state.touch_points) == 2
    assert all(not p.active for p in state.touch_points)


def test_touchpad_point_one_active(usb_base):
    p1 = _pack_touch_point(contact_id=5, x=1000, y=500, active=True)
    usb_base[USB_OFFSETS.touchpad_start : USB_OFFSETS.touchpad_start + 4] = p1
    state = parse_usb_report(bytes(usb_base))
    pt = state.touch_points[0]
    assert pt.active is True
    assert pt.id == 5
    assert pt.x == 1000
    assert pt.y == 500
    assert state.touch_points[1].active is False


def test_touchpad_point_two_active(usb_base):
    p2 = _pack_touch_point(contact_id=12, x=42, y=900, active=True)
    start = USB_OFFSETS.touchpad_start + 4
    usb_base[start : start + 4] = p2
    state = parse_usb_report(bytes(usb_base))
    assert state.touch_points[0].active is False
    pt = state.touch_points[1]
    assert pt.active is True
    assert pt.id == 12
    assert pt.x == 42
    assert pt.y == 900


def test_touchpad_both_points_active_simultaneously(usb_base):
    p1 = _pack_touch_point(1, 10, 20, active=True)
    p2 = _pack_touch_point(2, 1900, 940, active=True)
    usb_base[USB_OFFSETS.touchpad_start : USB_OFFSETS.touchpad_start + 4] = p1
    usb_base[USB_OFFSETS.touchpad_start + 4 : USB_OFFSETS.touchpad_start + 8] = p2
    state = parse_usb_report(bytes(usb_base))
    assert state.touch_points[0].x == 10 and state.touch_points[0].y == 20
    assert state.touch_points[1].x == 1900 and state.touch_points[1].y == 940
    assert all(p.active for p in state.touch_points)


def test_touchpad_offset_on_bluetooth_is_shifted_by_two(bt_base):
    p1 = _pack_touch_point(3, 300, 300, active=True)
    bt_base[BT_OFFSETS.touchpad_start : BT_OFFSETS.touchpad_start + 4] = p1
    state = parse_bt_report(bytes(bt_base))
    assert state.touch_points[0].active is True
    assert state.touch_points[0].x == 300
    assert state.touch_points[0].y == 300
    assert BT_OFFSETS.touchpad_start == USB_OFFSETS.touchpad_start + 2


# ---------------------------------------------------------------------------
# Gyro / accelerometer
# ---------------------------------------------------------------------------


def test_gyro_and_accel_positive_and_negative(usb_base):
    struct.pack_into("<3h", usb_base, USB_OFFSETS.gyro_start, -100, 200, -300)
    struct.pack_into("<3h", usb_base, USB_OFFSETS.accel_start, 16000, -16000, 500)
    state = parse_usb_report(bytes(usb_base))
    assert state.gyro == (-100, 200, -300)
    assert state.accel == (16000, -16000, 500)


def test_gyro_and_accel_over_bluetooth(bt_base):
    struct.pack_into("<3h", bt_base, BT_OFFSETS.gyro_start, 1, -2, 3)
    struct.pack_into("<3h", bt_base, BT_OFFSETS.accel_start, -4, 5, -6)
    state = parse_bt_report(bytes(bt_base))
    assert state.gyro == (1, -2, 3)
    assert state.accel == (-4, 5, -6)


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_level,expected_pct",
    [(0x00, 0), (0x05, 50), (0x0A, 100), (0x0F, 100)],
)
def test_battery_pct(usb_base, raw_level, expected_pct):
    usb_base[USB_OFFSETS.battery] = raw_level
    state = parse_usb_report(bytes(usb_base))
    assert state.battery_pct == expected_pct


# ---------------------------------------------------------------------------
# validate_report_sanity
# ---------------------------------------------------------------------------


def _valid_state() -> DS4State:
    return DS4State(
        buttons={name: False for name in _ALL_BUTTON_NAMES},
        left_stick=(0.0, 0.0),
        right_stick=(0.0, 0.0),
        l2_analog=0.0,
        r2_analog=0.0,
        dpad=None,
        touch_points=[
            TouchPoint(id=0, x=100, y=100, active=True),
            TouchPoint(id=1, x=0, y=0, active=False),
        ],
        gyro=(0, 0, 0),
        accel=(0, 0, 0),
        battery_pct=50,
        timestamp=0.0,
    )


def test_validate_report_sanity_no_warnings_for_valid_data():
    assert validate_report_sanity(_valid_state()) == []


def test_validate_report_sanity_accepts_none_battery():
    state = _valid_state()
    state.battery_pct = None
    assert validate_report_sanity(state) == []


def test_validate_report_sanity_flags_out_of_range_battery():
    state = _valid_state()
    state.battery_pct = 150
    warnings = validate_report_sanity(state)
    assert any("battery_pct" in w for w in warnings)


def test_validate_report_sanity_flags_out_of_range_touch_x():
    state = _valid_state()
    state.touch_points = [TouchPoint(id=0, x=5000, y=100, active=True)]
    warnings = validate_report_sanity(state)
    assert any("x out of range" in w for w in warnings)


def test_validate_report_sanity_flags_out_of_range_touch_y():
    state = _valid_state()
    state.touch_points = [TouchPoint(id=0, x=100, y=5000, active=True)]
    warnings = validate_report_sanity(state)
    assert any("y out of range" in w for w in warnings)


def test_validate_report_sanity_ignores_inactive_touch_point_out_of_range():
    state = _valid_state()
    state.touch_points = [TouchPoint(id=0, x=99999, y=99999, active=False)]
    assert validate_report_sanity(state) == []


def test_validate_report_sanity_flags_out_of_range_gyro():
    state = _valid_state()
    state.gyro = (40000, 0, 0)
    warnings = validate_report_sanity(state)
    assert any("gyro" in w for w in warnings)


def test_validate_report_sanity_flags_out_of_range_accel():
    state = _valid_state()
    state.accel = (0, 0, -40000)
    warnings = validate_report_sanity(state)
    assert any("accel" in w for w in warnings)


# ---------------------------------------------------------------------------
# build_output_report_usb
# ---------------------------------------------------------------------------


def test_build_output_report_usb_length_and_report_id():
    report = build_output_report_usb(0, 0, 0, 0, 0)
    assert isinstance(report, bytes)
    assert len(report) == 32
    assert report[0] == 0x05


def test_build_output_report_usb_roundtrips_values():
    report = build_output_report_usb(
        rumble_small=10, rumble_big=200, led_r=1, led_g=2, led_b=3
    )
    assert report[3] == 10
    assert report[4] == 200
    assert report[5] == 1
    assert report[6] == 2
    assert report[7] == 3


def test_build_output_report_usb_clamps_out_of_range_values():
    report = build_output_report_usb(
        rumble_small=-10, rumble_big=999, led_r=-1, led_g=256, led_b=0
    )
    assert report[3] == 0
    assert report[4] == 255
    assert report[5] == 0
    assert report[6] == 255
    assert report[7] == 0


def test_build_output_report_usb_flash_timing_roundtrip():
    report = build_output_report_usb(
        0, 0, 0, 0, 0, flash_on_ms=50, flash_off_ms=200
    )
    assert report[8] == 5
    assert report[9] == 20
