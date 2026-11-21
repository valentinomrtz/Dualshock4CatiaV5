"""Tests for ds4catia.mapping.engine (real-time binding resolution)."""

from __future__ import annotations

import pytest

from ds4catia.hid.ds4_report import DS4State, TouchPoint
from ds4catia.mapping.engine import (
    ActionEvent,
    MappingEngine,
    apply_curve,
    apply_deadzone,
    classify_digital,
)
from ds4catia.mapping.schema import (
    AnalogModifiers,
    AppControlTarget,
    Binding,
    CatiaCommandTarget,
    DigitalModifiers,
    InputRef,
    Layer,
    LayerActivation,
    LayerControlTarget,
    MouseButtonTarget,
    MouseMoveTarget,
    Profile,
)


# ---------------------------------------------------------------------------
# Helpers to build synthetic DS4State snapshots without going through the
# HID byte parser.
# ---------------------------------------------------------------------------

_RELEASED_BUTTONS = {
    "square": False,
    "cross": False,
    "circle": False,
    "triangle": False,
    "l1": False,
    "r1": False,
    "l2_digital": False,
    "r2_digital": False,
    "share": False,
    "options": False,
    "l3": False,
    "r3": False,
    "ps": False,
    "touchpad_click": False,
}


def make_state(timestamp: float, **button_overrides) -> DS4State:
    buttons = dict(_RELEASED_BUTTONS)
    buttons.update(button_overrides)
    return DS4State(
        buttons=buttons,
        left_stick=(0.0, 0.0),
        right_stick=(0.0, 0.0),
        l2_analog=0.0,
        r2_analog=0.0,
        dpad=None,
        touch_points=[
            TouchPoint(id=0, x=0, y=0, active=False),
            TouchPoint(id=0, x=0, y=0, active=False),
        ],
        gyro=(0, 0, 0),
        accel=(0, 0, 0),
        battery_pct=100,
        timestamp=timestamp,
    )


def press(state: DS4State, **button_overrides) -> DS4State:
    """Return a copy of ``state`` with the given buttons overridden."""

    buttons = dict(state.buttons)
    buttons.update(button_overrides)
    return DS4State(
        buttons=buttons,
        left_stick=state.left_stick,
        right_stick=state.right_stick,
        l2_analog=state.l2_analog,
        r2_analog=state.r2_analog,
        dpad=state.dpad,
        touch_points=state.touch_points,
        gyro=state.gyro,
        accel=state.accel,
        battery_pct=state.battery_pct,
        timestamp=state.timestamp,
    )


def with_stick(state: DS4State, left_stick=None, right_stick=None, timestamp=None) -> DS4State:
    return DS4State(
        buttons=dict(state.buttons),
        left_stick=left_stick if left_stick is not None else state.left_stick,
        right_stick=right_stick if right_stick is not None else state.right_stick,
        l2_analog=state.l2_analog,
        r2_analog=state.r2_analog,
        dpad=state.dpad,
        touch_points=state.touch_points,
        gyro=state.gyro,
        accel=state.accel,
        battery_pct=state.battery_pct,
        timestamp=timestamp if timestamp is not None else state.timestamp,
    )


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


class TestApplyDeadzone:
    def test_value_within_deadzone_is_zeroed(self):
        assert apply_deadzone(0.05, deadzone=0.1) == 0.0
        assert apply_deadzone(-0.05, deadzone=0.1) == 0.0

    def test_value_exactly_at_deadzone_boundary_is_zeroed(self):
        assert apply_deadzone(0.1, deadzone=0.1) == 0.0

    def test_value_above_deadzone_is_rescaled_preserving_sign(self):
        # deadzone=0.1: value 0.1 maps to 0.0, value 1.0 maps to 1.0, linearly between.
        result = apply_deadzone(0.55, deadzone=0.1)
        assert result == pytest.approx(0.5, abs=1e-6)

        result_neg = apply_deadzone(-0.55, deadzone=0.1)
        assert result_neg == pytest.approx(-0.5, abs=1e-6)

    def test_anti_deadzone_sets_a_floor_just_past_the_threshold(self):
        result = apply_deadzone(0.11, deadzone=0.1, anti_deadzone=0.2)
        assert result > 0.19  # jumps close to the anti-deadzone floor immediately

    def test_full_scale_value_maps_to_one(self):
        assert apply_deadzone(1.0, deadzone=0.1) == pytest.approx(1.0)

    def test_deadzone_of_one_zeroes_everything(self):
        assert apply_deadzone(1.0, deadzone=1.0) == 0.0


class TestApplyCurve:
    def test_linear_is_identity(self):
        assert apply_curve(0.5, "linear") == pytest.approx(0.5)
        assert apply_curve(-0.5, "linear") == pytest.approx(-0.5)

    def test_quadratic_shrinks_midrange_and_preserves_sign(self):
        assert apply_curve(0.5, "quadratic") == pytest.approx(0.25)
        assert apply_curve(-0.5, "quadratic") == pytest.approx(-0.25)

    def test_cubic_shrinks_more_than_quadratic(self):
        assert apply_curve(0.5, "cubic") == pytest.approx(0.125)

    def test_ease_out_quad_grows_faster_than_linear_at_midrange(self):
        assert apply_curve(0.5, "ease_out_quad") == pytest.approx(0.75)

    def test_ease_out_cubic_grows_even_faster(self):
        assert apply_curve(0.5, "ease_out_cubic") == pytest.approx(0.875)

    def test_endpoints_are_fixed_for_every_curve(self):
        for curve in ("linear", "quadratic", "cubic", "ease_out_quad", "ease_out_cubic"):
            assert apply_curve(0.0, curve) == pytest.approx(0.0)
            assert apply_curve(1.0, curve) == pytest.approx(1.0)

    def test_custom_bezier_without_params_falls_back_to_linear(self):
        assert apply_curve(0.5, "custom_bezier", None) == pytest.approx(0.5)
        assert apply_curve(0.5, "custom_bezier", {}) == pytest.approx(0.5)

    def test_custom_bezier_with_params_shapes_the_curve(self):
        # A bezier close to ease-out-quad-ish shape: should end up above the
        # linear midpoint for a curve that front-loads motion.
        params = {"x1": 0.0, "y1": 0.8, "x2": 0.2, "y2": 1.0}
        result = apply_curve(0.5, "custom_bezier", params)
        assert result > 0.5

    def test_custom_bezier_endpoints_are_zero_and_one(self):
        params = {"x1": 0.3, "y1": 0.1, "x2": 0.7, "y2": 0.9}
        assert apply_curve(0.0, "custom_bezier", params) == pytest.approx(0.0, abs=1e-3)
        assert apply_curve(1.0, "custom_bezier", params) == pytest.approx(1.0, abs=1e-3)

    def test_unknown_curve_raises(self):
        with pytest.raises(ValueError):
            apply_curve(0.5, "not_a_real_curve")


class TestClassifyDigital:
    def test_short_press_is_tap(self):
        assert classify_digital(50.0, tap_ms=180) == "tap"

    def test_press_right_below_tap_threshold_is_tap(self):
        assert classify_digital(179.9, tap_ms=180) == "tap"

    def test_press_at_or_above_tap_threshold_is_hold(self):
        assert classify_digital(180.0, tap_ms=180) == "hold"
        assert classify_digital(500.0, tap_ms=180) == "hold"


# ---------------------------------------------------------------------------
# Fixtures: a base profile + a shift layer
# ---------------------------------------------------------------------------


def make_base_profile() -> Profile:
    return Profile(
        profile_id="base",
        display_name="Base",
        bindings=[
            Binding(
                id="bind.cross",
                input=InputRef(device="ds4", control="cross"),
                target=CatiaCommandTarget(type="catia_command", command_id="Base.Cross"),
            ),
            Binding(
                id="bind.circle",
                input=InputRef(device="ds4", control="circle"),
                target=CatiaCommandTarget(type="catia_command", command_id="Base.Circle"),
            ),
            Binding(
                id="bind.l1.shift",
                input=InputRef(device="ds4", control="l1"),
                target=LayerControlTarget(type="layer_control", layer_id="shift", mode="hold"),
            ),
            Binding(
                id="bind.r1.toggle",
                input=InputRef(device="ds4", control="r1"),
                target=LayerControlTarget(type="layer_control", layer_id="toggled", mode="toggle"),
            ),
            Binding(
                id="bind.triangle.tap",
                input=InputRef(device="ds4", control="triangle"),
                target=MouseButtonTarget(type="mouse_button", button="left"),
                digital=DigitalModifiers(tap_ms=150, hold_ms=350),
            ),
            Binding(
                id="bind.rstick",
                input=InputRef(device="ds4", control="right_stick"),
                target=MouseMoveTarget(type="mouse_move", axis="both"),
                analog=AnalogModifiers(deadzone=0.1, curve="linear"),
            ),
        ],
        layers=[
            Layer(
                layer_id="shift",
                activation=LayerActivation(
                    trigger=InputRef(device="ds4", control="l1"), mode="hold"
                ),
                overrides=[
                    Binding(
                        id="bind.cross.shifted",
                        input=InputRef(device="ds4", control="cross"),
                        target=CatiaCommandTarget(type="catia_command", command_id="Shift.Cross"),
                    ),
                ],
            ),
            Layer(
                layer_id="toggled",
                activation=LayerActivation(
                    trigger=InputRef(device="ds4", control="r1"), mode="toggle"
                ),
                overrides=[
                    Binding(
                        id="bind.circle.toggled",
                        input=InputRef(device="ds4", control="circle"),
                        target=CatiaCommandTarget(type="catia_command", command_id="Toggled.Circle"),
                    ),
                ],
            ),
        ],
    )


def make_other_profile() -> Profile:
    return Profile(
        profile_id="other",
        display_name="Other",
        bindings=[
            Binding(
                id="bind.other.cross",
                input=InputRef(device="ds4", control="cross"),
                target=AppControlTarget(type="app_control", action="pause"),
            ),
        ],
    )


@pytest.fixture
def engine() -> MappingEngine:
    eng = MappingEngine({"base": make_base_profile(), "other": make_other_profile()})
    eng.set_active_profile("base")
    return eng


# ---------------------------------------------------------------------------
# Base binding resolution (no layers)
# ---------------------------------------------------------------------------


class TestBaseResolution:
    def test_simple_tap_resolves_to_base_binding(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.05
        s1 = press(s0, cross=True)
        events = engine.process(s1, t)
        assert events == []  # press edge alone emits nothing yet (tap/hold unresolved)

        t = 0.10
        s2 = press(s0, cross=False)
        events = engine.process(s2, t)
        assert len(events) == 1
        assert events[0].kind == "tap"
        assert events[0].binding_id == "bind.cross"
        assert events[0].target.command_id == "Base.Cross"
        assert events[0].value is None


# ---------------------------------------------------------------------------
# Layer activation: hold mode
# ---------------------------------------------------------------------------


class TestHoldLayer:
    def test_hold_layer_overrides_listed_control_but_not_others(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        # Press and hold L1 -> activates "shift" layer.
        t = 0.01
        s1 = press(s0, l1=True)
        events = engine.process(s1, t)
        assert any(e.kind == "press" and e.binding_id == "bind.l1.shift" for e in events)
        assert engine.active_layers == ["shift"]

        # While L1 is held, tap "cross": should resolve to the layer's override.
        t = 0.02
        s2 = press(s1, cross=True)
        engine.process(s2, t)
        t = 0.03
        s3 = press(s1, cross=False)
        events = engine.process(s3, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.cross.shifted"
        assert tap_events[0].target.command_id == "Shift.Cross"

        # "circle" is NOT listed in the shift layer's overrides -> falls back
        # to the base binding.
        t = 0.04
        s4 = press(s1, circle=True)
        engine.process(s4, t)
        t = 0.05
        s5 = press(s1, circle=False)
        events = engine.process(s5, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.circle"
        assert tap_events[0].target.command_id == "Base.Circle"

        # Release L1 -> deactivates the layer.
        t = 0.06
        s6 = press(s0, l1=False)
        events = engine.process(s6, t)
        assert any(e.kind == "release" and e.binding_id == "bind.l1.shift" for e in events)
        assert engine.active_layers == []


# ---------------------------------------------------------------------------
# Layer activation: toggle mode
# ---------------------------------------------------------------------------


class TestToggleLayer:
    def test_toggle_layer_switches_on_then_off_on_press_edges(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        # Press R1 -> toggles "toggled" layer ON.
        t = 0.01
        s1 = press(s0, r1=True)
        events = engine.process(s1, t)
        assert any(e.kind == "press" and e.binding_id == "bind.r1.toggle" for e in events)
        assert engine.active_layers == ["toggled"]

        # Releasing R1 does nothing (toggle only reacts to press edges).
        t = 0.02
        s2 = press(s0, r1=False)
        events = engine.process(s2, t)
        assert events == []
        assert engine.active_layers == ["toggled"]

        # Pressing R1 again toggles it back OFF.
        t = 0.03
        s3 = press(s0, r1=True)
        events = engine.process(s3, t)
        assert any(e.kind == "release" and e.binding_id == "bind.r1.toggle" for e in events)
        assert engine.active_layers == []

        t = 0.04
        s4 = press(s0, r1=False)
        engine.process(s4, t)


# ---------------------------------------------------------------------------
# Two layers active simultaneously, same control overridden by both
# ---------------------------------------------------------------------------


class TestLayerTieBreak:
    def test_last_activated_layer_wins_on_shared_override(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        # Activate "shift" (hold, via L1) first.
        t = 0.01
        s1 = press(s0, l1=True)
        engine.process(s1, t)
        assert engine.active_layers == ["shift"]

        # Then activate "toggled" (toggle, via R1) second -- both layers
        # happen to override "circle" in this test's profile variant below.
        t = 0.02
        s2 = press(s1, r1=True)
        engine.process(s2, t)
        assert engine.active_layers == ["shift", "toggled"]

        # "circle" is overridden only by "toggled" in make_base_profile();
        # confirm it resolves to the toggled layer's override (not base).
        t = 0.03
        s3 = press(s2, circle=True)
        engine.process(s3, t)
        t = 0.04
        s4 = press(s2, circle=False)
        events = engine.process(s4, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.circle.toggled"

    def test_two_layers_overriding_the_same_control_last_activated_wins(self):
        profile = Profile(
            profile_id="dual",
            display_name="Dual",
            bindings=[
                Binding(
                    id="bind.base.cross",
                    input=InputRef(device="ds4", control="cross"),
                    target=CatiaCommandTarget(type="catia_command", command_id="Base"),
                ),
                Binding(
                    id="bind.l1",
                    input=InputRef(device="ds4", control="l1"),
                    target=LayerControlTarget(type="layer_control", layer_id="layer_a", mode="hold"),
                ),
                Binding(
                    id="bind.r1",
                    input=InputRef(device="ds4", control="r1"),
                    target=LayerControlTarget(type="layer_control", layer_id="layer_b", mode="hold"),
                ),
            ],
            layers=[
                Layer(
                    layer_id="layer_a",
                    activation=LayerActivation(trigger=InputRef(device="ds4", control="l1")),
                    overrides=[
                        Binding(
                            id="bind.a.cross",
                            input=InputRef(device="ds4", control="cross"),
                            target=CatiaCommandTarget(type="catia_command", command_id="A"),
                        ),
                    ],
                ),
                Layer(
                    layer_id="layer_b",
                    activation=LayerActivation(trigger=InputRef(device="ds4", control="r1")),
                    overrides=[
                        Binding(
                            id="bind.b.cross",
                            input=InputRef(device="ds4", control="cross"),
                            target=CatiaCommandTarget(type="catia_command", command_id="B"),
                        ),
                    ],
                ),
            ],
        )
        eng = MappingEngine({"dual": profile})
        eng.set_active_profile("dual")

        t = 0.0
        s0 = make_state(t)
        eng.process(s0, t)

        # Activate layer_a (via L1) first, then layer_b (via R1) second.
        t = 0.01
        s1 = press(s0, l1=True)
        eng.process(s1, t)
        t = 0.02
        s2 = press(s1, r1=True)
        eng.process(s2, t)
        assert eng.active_layers == ["layer_a", "layer_b"]

        t = 0.03
        s3 = press(s2, cross=True)
        eng.process(s3, t)
        t = 0.04
        s4 = press(s2, cross=False)
        events = eng.process(s4, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.b.cross", "layer_b activated last, must win the tie"

        # Now release R1 (deactivating layer_b) -- layer_a's override should
        # take back over for "cross".
        t = 0.05
        s5 = press(s2, r1=False)
        eng.process(s5, t)
        assert eng.active_layers == ["layer_a"]

        t = 0.06
        s6 = press(s5, cross=True)
        eng.process(s6, t)
        t = 0.07
        s7 = press(s5, cross=False)
        events = eng.process(s7, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.a.cross"


# ---------------------------------------------------------------------------
# set_active_profile clears layers
# ---------------------------------------------------------------------------


class TestProfileSwitchClearsLayers:
    def test_switching_base_profile_clears_all_active_layers(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = press(s0, l1=True)
        engine.process(s1, t)
        t = 0.02
        s2 = press(s1, r1=True)
        engine.process(s2, t)
        assert set(engine.active_layers) == {"shift", "toggled"}

        engine.set_active_profile("other")
        assert engine.active_layers == []
        assert engine.active_profile_id == "other"

        # And the new profile's own binding for "cross" is now in effect.
        t = 0.03
        s3 = make_state(t)
        engine.process(s3, t)
        t = 0.04
        s4 = press(s3, cross=True)
        engine.process(s4, t)
        t = 0.05
        s5 = press(s3, cross=False)
        events = engine.process(s5, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.other.cross"


# ---------------------------------------------------------------------------
# Tap vs hold
# ---------------------------------------------------------------------------


class TestTapVsHold:
    def test_short_press_emits_tap_only(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = press(s0, triangle=True)
        events = engine.process(s1, t)
        assert events == []

        t = 0.05  # 40ms held, well under tap_ms=150
        s2 = press(s0, triangle=False)
        events = engine.process(s2, t)
        kinds = [e.kind for e in events]
        assert kinds == ["tap"]
        assert events[0].binding_id == "bind.triangle.tap"

    def test_long_press_emits_hold_start_then_eventually_hold_end(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = press(s0, triangle=True)
        events = engine.process(s1, t)
        assert events == []

        # Still held, but not yet past hold_ms=350ms.
        t = 0.2
        events = engine.process(s1, t)
        assert events == []

        # Now past hold_ms=350ms while still held -> hold_start fires.
        t = 0.4
        events = engine.process(s1, t)
        assert [e.kind for e in events] == ["hold_start"]
        assert events[0].binding_id == "bind.triangle.tap"

        # Holding further still produces no duplicate hold_start.
        t = 0.6
        events = engine.process(s1, t)
        assert events == []

        # Release -> hold_end (hold_start already fired, so only hold_end now).
        t = 0.8
        s2 = press(s0, triangle=False)
        events = engine.process(s2, t)
        assert [e.kind for e in events] == ["hold_end"]
        assert events[0].binding_id == "bind.triangle.tap"

    def test_release_between_tap_ms_and_hold_ms_emits_full_hold_pair(self, engine: MappingEngine):
        # tap_ms=150, hold_ms=350 for "bind.triangle.tap". Release at 200ms:
        # classify_digital says "hold" (>=150) but hold_start never
        # proactively fired (< 350ms held) -> full hold_start+hold_end pair.
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = press(s0, triangle=True)
        engine.process(s1, t)

        t = 0.01 + 0.2  # 190ms later
        s2 = press(s0, triangle=False)
        events = engine.process(s2, t)
        assert [e.kind for e in events] == ["hold_start", "hold_end"]


# ---------------------------------------------------------------------------
# Analog value resolution
# ---------------------------------------------------------------------------


class TestAnalogResolution:
    def test_stick_within_deadzone_emits_nothing(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = with_stick(s0, right_stick=(0.05, 0.0), timestamp=t)
        events = engine.process(s1, t)
        assert events == []

    def test_stick_past_deadzone_emits_analog_value_with_curve_applied(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = with_stick(s0, right_stick=(1.0, 0.0), timestamp=t)
        events = engine.process(s1, t)
        assert len(events) == 1
        assert events[0].kind == "analog_value"
        assert events[0].binding_id == "bind.rstick"
        x, y = events[0].value
        assert x == pytest.approx(1.0, abs=1e-6)
        assert y == pytest.approx(0.0, abs=1e-6)

    def test_analog_value_only_re_emits_on_appreciable_change(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        t = 0.01
        s1 = with_stick(s0, right_stick=(0.5, 0.0), timestamp=t)
        events = engine.process(s1, t)
        assert len(events) == 1

        # Same value again next frame -> no re-emission.
        t = 0.02
        events = engine.process(s1, t)
        assert events == []

        # Releasing the stick back to center is an appreciable change again.
        t = 0.03
        s2 = with_stick(s0, right_stick=(0.0, 0.0), timestamp=t)
        events = engine.process(s2, t)
        assert len(events) == 1
        assert events[0].value == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Capture mode
# ---------------------------------------------------------------------------


class TestCaptureMode:
    def test_capture_mode_suppresses_action_events_but_updates_last_raw_input(
        self, engine: MappingEngine
    ):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        engine.set_capture_mode(True)

        t = 0.01
        s1 = press(s0, cross=True)
        events = engine.process(s1, t)
        assert events == []
        raw = engine.last_raw_input()
        assert raw is not None
        assert raw.control == "cross"
        assert raw.kind == "press"

        t = 0.02
        s2 = press(s0, cross=False)
        events = engine.process(s2, t)
        assert events == []  # even the tap classification is suppressed
        raw = engine.last_raw_input()
        assert raw.control == "cross"
        assert raw.kind == "release"

    def test_capture_mode_does_not_trigger_layer_side_effects(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        engine.set_capture_mode(True)

        t = 0.01
        s1 = press(s0, l1=True)
        events = engine.process(s1, t)
        assert events == []
        assert engine.active_layers == []  # no layer activation while capturing

    def test_capture_mode_off_resumes_normal_emission(self, engine: MappingEngine):
        t = 0.0
        s0 = make_state(t)
        engine.process(s0, t)

        engine.set_capture_mode(True)
        t = 0.01
        s1 = press(s0, cross=True)
        engine.process(s1, t)
        t = 0.02
        s2 = press(s0, cross=False)
        engine.process(s2, t)

        engine.set_capture_mode(False)

        t = 0.03
        s3 = press(s0, cross=True)
        engine.process(s3, t)
        t = 0.04
        s4 = press(s0, cross=False)
        events = engine.process(s4, t)
        tap_events = [e for e in events if e.kind == "tap"]
        assert len(tap_events) == 1
        assert tap_events[0].binding_id == "bind.cross"
