"""Real-time binding resolution engine.

This module is the "brain" that, given the current DS4 controller state and
the set of profiles/layers configured by the user (``ds4catia.mapping.schema``),
decides which ``ActionEvent``\\ s should fire. It knows nothing about HID
transport, real CATIA automation, or the GUI -- those are the coordinator's
job, built on top of this engine. It is pure Python + the schema models, so
it is fully unit-testable with synthetic ``DS4State`` snapshots.

Design decisions worth calling out explicitly (several of these resolve
ambiguities the task brief left open; they are documented here rather than
buried in code so a future maintainer -- human or agent -- can find them):

1. **Layer override resolution & tie-break rule.** For every physical
   control, the *effective* binding is: the base profile's binding for that
   control, then, for each currently active layer **in the order it was
   activated (oldest first)**, that layer's ``overrides`` entry for the
   control if it has one, overwriting whatever came before. In other words:
   **if two active layers both override the same control, the layer that was
   activated most recently wins.** This mirrors how most "shift" systems
   behave (the most recently engaged modifier is the most specific one) and
   is simple to reason about: ``active_layers`` is just an activation-order
   list, and later entries are applied later, so they win ties.

   A layer (de)activated during one ``process()`` call takes effect starting
   from the *next* call -- within a single call every control is resolved
   against the active-layer set as it stood at the start of that call. This
   keeps a single pass over a precomputed "effective bindings" dict simple
   and avoids order-of-iteration dependent behaviour within one frame.

2. **Who drives layer activation.** ``Layer.activation`` (the ``trigger`` +
   ``mode`` on the ``Layer`` itself) is treated as *declarative metadata*
   (useful for a GUI to show "hold L1 to enter this layer"). The actual
   runtime trigger the engine reacts to is whichever ``Binding`` -- in the
   base profile or in a currently active layer's overrides -- has a
   ``target`` of type ``LayerControlTarget``. This is what the task brief's
   step 4 describes, and it means layer activation is resolved through the
   exact same per-control, layer-aware pipeline as every other control
   (including a layer-control binding appearing inside another layer's
   overrides, for nested shifts), rather than a separate parallel path.

3. **LayerControlTarget bindings *do* emit an informational ActionEvent.**
   ``kind="press"`` when the layer becomes active (hold mode: button down;
   toggle mode: the toggle-on edge) and ``kind="release"`` when it becomes
   inactive (hold mode: button up; toggle mode: the toggle-off edge), with
   ``value=None`` and ``target`` set to the ``LayerControlTarget`` itself.
   This lets a coordinator/GUI show "layer X is now active" feedback without
   needing to poll ``active_layers`` every tick. Ordinary (non-layer)
   digital bindings **never** emit ``"press"``/``"release"`` -- see point 4.

4. **Ordinary digital bindings only ever emit ``tap``, or the
   ``hold_start``/``hold_end`` pair -- never raw ``press``/``release``.**
   A raw press/release is an intermediate detection step, not something a
   destination action can usually do anything with on its own (it needs the
   tap-vs-hold decision). ``hold_start`` fires as soon as the button has been
   held for >= its ``DigitalModifiers.hold_ms`` (while still physically
   held, enabling e.g. continuous camera rotation while a hold is in
   progress), and the matching ``hold_end`` fires on release.
   ``classify_digital(duration_ms, tap_ms)`` only distinguishes tap
   (``duration_ms < tap_ms``) from hold (``>= tap_ms``); ``hold_ms`` is a
   *separate*, strictly-larger-in-practice threshold used only for the
   proactive ``hold_start``. If a button is released in the gap between
   ``tap_ms`` and ``hold_ms`` (classified as "hold" by duration, but never
   long enough to proactively fire ``hold_start``), the engine fires the
   full ``hold_start`` then ``hold_end`` pair at release time, so a "hold"
   classification always yields a complete hold_start/hold_end pair.

5. **The binding in effect at press-time is "armed" for the whole press.**
   Each physical control's digital state is tracked by control name (not by
   binding identity), and the resolved binding at the moment a press edge is
   detected is kept until the matching release, even if which layer governs
   that control, or the active profile, changes while the button is held.
   This avoids firing an action for a binding that was not actually in
   effect when the user pressed the button, and avoids "losing" the tap/hold
   classification of a press if a layer flips mid-hold. If a control
   becomes newly mapped while it is *already* physically held down (no press
   edge was ever observed under that binding), no event fires for that
   ongoing press; the user effectively "missed" the window.

6. **Analog resolution pipeline**, applied in this fixed order:
   ``apply_deadzone`` -> ``apply_curve`` -> multiply by ``sensitivity`` ->
   clamp to [-1, 1] (or [0, 1] component-wise is not special-cased; triggers
   are naturally non-negative inputs so deadzone/curve on them behave the
   same as for the positive half of a stick axis). ``left_stick``/
   ``right_stick`` are resolved as a vector: a *radial* deadzone/curve is
   applied to the stick's magnitude (clamped to the unit circle), and the
   resulting scale factor is applied to both components, preserving
   direction. An ``analog_value`` event fires only when the resolved value
   changes from the last emitted value by more than a small epsilon
   (``_ANALOG_CHANGE_EPSILON``), to avoid flooding consumers with
   unchanged-value events every frame.

7. **Gyro axes are read from raw signed int16 fields** (``DS4State.gyro``
   is *not* pre-normalized, unlike the sticks/triggers -- see
   ``ds4_report.py``). This module normalizes them to -1.0..1.0 by dividing
   by 32768.0. This is an engine-level approximation (it is not a
   physically-calibrated deg/s scale); like several things in
   ``ds4_report.py`` itself, it is called out here as best-effort rather
   than invented silently.

8. **Touchpad pseudo-controls** (not covered by the task brief's required
   test list, but part of the schema's ``VALID_DS4_CONTROLS``, so handled
   for completeness): ``touchpad_tap`` is treated as a simple digital
   control (`pressed` = any touch point currently active) and goes through
   the same tap/hold pipeline as a button. ``touchpad_swipe`` is
   inherently a *relative* motion (there is no persistent "swipe position"),
   so unlike the other analog controls it emits ``kind="analog_delta"``
   (frame-to-frame normalized (dx, dy)) rather than ``analog_value``, and
   only when a touch point with a stable id is active in both the previous
   and current frame.

9. **Capture mode** (``set_capture_mode(True)``) suppresses every outward
   ``ActionEvent`` *and* every layer activation/deactivation side effect --
   nothing the physical controller does while capturing is allowed to leak
   into the live mapping, which is exactly the double-trigger bug class the
   task brief calls out. Internal edge/press-duration tracking (so capture
   mode can resume cleanly afterwards) keeps running regardless. Raw
   physical input tracking for ``last_raw_input()`` is independent of the
   active profile/bindings entirely -- it diffs the raw ``DS4State`` itself,
   so capture UI can see "what was just pressed" even for a control that
   has no binding at all yet (which is the whole point of a remap flow).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Union

from ds4catia.hid.ds4_report import DS4State
from ds4catia.mapping.schema import (
    ActionTarget,
    AnalogModifiers,
    Binding,
    DigitalModifiers,
    LayerControlTarget,
    Profile,
    VALID_DS4_CONTROLS,
)

__all__ = [
    "ActionEvent",
    "RawInputEvent",
    "MappingEngine",
    "apply_deadzone",
    "apply_curve",
    "classify_digital",
]


# ---------------------------------------------------------------------------
# Control categorization
# ---------------------------------------------------------------------------

_DIGITAL_BUTTON_CONTROLS: frozenset[str] = frozenset(
    {
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
    }
)
_DIGITAL_OTHER_CONTROLS: frozenset[str] = frozenset({"dpad", "touchpad_tap"})
_DIGITAL_CONTROLS: frozenset[str] = _DIGITAL_BUTTON_CONTROLS | _DIGITAL_OTHER_CONTROLS

_ANALOG_SCALAR_CONTROLS: frozenset[str] = frozenset({"l2", "r2", "gyro_x", "gyro_y", "gyro_z"})
_ANALOG_VECTOR_CONTROLS: frozenset[str] = frozenset({"left_stick", "right_stick"})
_ANALOG_DELTA_CONTROLS: frozenset[str] = frozenset({"touchpad_swipe"})

# Defensive sanity check: catch this module silently going stale if
# schema.py ever grows/shrinks the set of valid DS4 controls.
assert (
    _DIGITAL_CONTROLS | _ANALOG_SCALAR_CONTROLS | _ANALOG_VECTOR_CONTROLS | _ANALOG_DELTA_CONTROLS
    == VALID_DS4_CONTROLS
), "engine.py's control categorization is out of sync with schema.VALID_DS4_CONTROLS"

_GYRO_FULL_SCALE = 32768.0  # see module docstring point 7
_TOUCHPAD_WIDTH = 1920.0  # mirrors ds4_report.py's touchpad sanity-check bounds
_TOUCHPAD_HEIGHT = 942.0

_ANALOG_CHANGE_EPSILON = 1e-3


# ---------------------------------------------------------------------------
# ActionEvent / RawInputEvent
# ---------------------------------------------------------------------------

ActionEventKind = Literal[
    "press", "release", "tap", "hold_start", "hold_end", "analog_delta", "analog_value"
]


@dataclass
class ActionEvent:
    """One resolved, ready-to-execute action, emitted by ``MappingEngine.process``.

    ``value`` is ``None`` for every digital kind (``press``, ``release``,
    ``tap``, ``hold_start``, ``hold_end``) and a float (scalar controls) or
    ``(float, float)`` tuple (stick/touchpad-swipe controls) for the analog
    kinds (``analog_value``, ``analog_delta``).
    """

    binding_id: str
    target: ActionTarget
    kind: ActionEventKind
    value: Union[float, "tuple[float, float]", None]
    timestamp: float


RawInputKind = Literal["press", "release", "analog_change"]


@dataclass
class RawInputEvent:
    """The most recent *physical* change observed on the pad, independent of
    any binding -- what the remap-capture UI needs to know "what was just
    pressed/moved", even for a control that isn't bound to anything yet.
    """

    control: str
    kind: RawInputKind
    value: Union[float, "tuple[float, float]", None]
    timestamp: float


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def apply_deadzone(value: float, deadzone: float, anti_deadzone: float = 0.0) -> float:
    """Apply a magnitude deadzone (with optional anti-deadzone floor) to ``value``.

    ``value`` may be negative (a stick axis) or non-negative (a trigger); the
    sign is preserved. Values whose magnitude is at or below ``deadzone``
    become exactly ``0.0``. Values above it are rescaled so the output jumps
    immediately to ``anti_deadzone`` right past the deadzone threshold (so
    there is no "dead" feel right at the edge) and then increases linearly
    up to ``1.0`` at ``abs(value) == 1.0``. The result is clamped to
    ``[-1.0, 1.0]``.
    """

    sign = -1.0 if value < 0 else 1.0
    magnitude = min(abs(value), 1.0)

    if magnitude <= deadzone:
        return 0.0

    if deadzone >= 1.0:
        # Degenerate configuration: nothing above the deadzone can exist.
        return 0.0

    span = 1.0 - deadzone
    scaled = anti_deadzone + (magnitude - deadzone) / span * (1.0 - anti_deadzone)
    return sign * max(0.0, min(1.0, scaled))


def _cubic_bezier_point(t: float, p1: float, p2: float) -> float:
    """Evaluate one component of a cubic bezier with endpoints 0 and 1 and
    control points ``p1``/``p2``, at parameter ``t`` (CSS ``cubic-bezier``
    convention: the curve always starts at (0, 0) and ends at (1, 1))."""

    mt = 1.0 - t
    # B(t) = 3*mt^2*t*p1 + 3*mt*t^2*p2 + t^3  (the mt^3*0 and t^3*1 endpoint
    # terms simplify away since the curve is anchored at 0 and 1).
    return 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t


def _cubic_bezier_ease(x: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Evaluate a CSS-style ``cubic-bezier(x1, y1, x2, y2)`` easing at ``x``.

    Solves for the parameter ``t`` whose bezier-X equals ``x`` via bisection
    (robust for the monotonic-X control point ranges a sane easing curve
    uses), then returns the bezier-Y at that ``t``.
    """

    x = max(0.0, min(1.0, x))
    lo, hi = 0.0, 1.0
    t = x
    for _ in range(30):
        t = (lo + hi) / 2.0
        bx = _cubic_bezier_point(t, x1, x2)
        if bx < x:
            lo = t
        else:
            hi = t
    return max(0.0, min(1.0, _cubic_bezier_point(t, y1, y2)))


def apply_curve(value: float, curve: str, curve_params: Optional[dict] = None) -> float:
    """Apply a response curve to ``value`` (sign-preserving, shapes magnitude).

    Supported ``curve`` names: ``"linear"``, ``"quadratic"``, ``"cubic"``,
    ``"ease_out_quad"``, ``"ease_out_cubic"``, ``"custom_bezier"``.

    For ``"custom_bezier"``, ``curve_params`` must supply numeric ``"x1"``,
    ``"y1"``, ``"x2"``, ``"y2"`` keys (CSS ``cubic-bezier`` control points);
    if ``curve_params`` is missing, ``None``, or does not contain all four
    numeric keys, this falls back to linear. Design decision: a malformed or
    absent custom curve should never raise or silently produce a broken
    (e.g. NaN) response -- degrading gracefully to the identity curve is
    safer for a live input pipeline than refusing to resolve the control at
    all.
    """

    sign = -1.0 if value < 0 else 1.0
    magnitude = min(abs(value), 1.0)

    if curve == "linear":
        shaped = magnitude
    elif curve == "quadratic":
        shaped = magnitude**2
    elif curve == "cubic":
        shaped = magnitude**3
    elif curve == "ease_out_quad":
        shaped = 1.0 - (1.0 - magnitude) ** 2
    elif curve == "ease_out_cubic":
        shaped = 1.0 - (1.0 - magnitude) ** 3
    elif curve == "custom_bezier":
        shaped = _shape_custom_bezier(magnitude, curve_params)
    else:
        raise ValueError(f"unknown response curve {curve!r}")

    return sign * shaped


def _shape_custom_bezier(magnitude: float, curve_params: Optional[dict]) -> float:
    if not curve_params:
        return magnitude  # fall back to linear, per apply_curve's docstring
    try:
        x1 = float(curve_params["x1"])
        y1 = float(curve_params["y1"])
        x2 = float(curve_params["x2"])
        y2 = float(curve_params["y2"])
    except (KeyError, TypeError, ValueError):
        return magnitude  # malformed params -> fall back to linear
    return _cubic_bezier_ease(magnitude, x1, y1, x2, y2)


def classify_digital(press_duration_ms: float, tap_ms: int) -> Literal["tap", "hold"]:
    """Classify a completed button press as a ``"tap"`` or a ``"hold"``.

    A press shorter than ``tap_ms`` is a tap; ``press_duration_ms >= tap_ms``
    is a hold. See the module docstring (point 4) for how this relates to
    the separate, larger ``hold_ms`` threshold used to proactively fire
    ``hold_start`` while a button is still held down.
    """

    return "tap" if press_duration_ms < tap_ms else "hold"


# ---------------------------------------------------------------------------
# Internal per-control tracking state
# ---------------------------------------------------------------------------


@dataclass
class _DigitalTrack:
    pressed: bool
    press_time: float
    binding: Optional[Binding]
    hold_fired: bool


# ---------------------------------------------------------------------------
# Raw control readers (physical value, independent of any binding)
# ---------------------------------------------------------------------------


def _read_digital_raw(state: DS4State, control: str) -> bool:
    if control == "dpad":
        return state.dpad is not None
    if control == "touchpad_tap":
        return any(tp.active for tp in state.touch_points)
    return bool(state.buttons.get(control, False))


def _normalize_gyro(raw: int) -> float:
    return max(-1.0, min(1.0, raw / _GYRO_FULL_SCALE))


def _read_scalar_raw(state: DS4State, control: str) -> float:
    if control == "l2":
        return state.l2_analog
    if control == "r2":
        return state.r2_analog
    if control == "gyro_x":
        return _normalize_gyro(state.gyro[0])
    if control == "gyro_y":
        return _normalize_gyro(state.gyro[1])
    if control == "gyro_z":
        return _normalize_gyro(state.gyro[2])
    raise ValueError(f"not a scalar analog control: {control!r}")


def _read_vector_raw(state: DS4State, control: str) -> "tuple[float, float]":
    if control == "left_stick":
        return state.left_stick
    if control == "right_stick":
        return state.right_stick
    raise ValueError(f"not a vector analog control: {control!r}")


def _read_touchpad_swipe_delta(
    state: DS4State, prev: Optional[DS4State]
) -> Optional["tuple[float, float]"]:
    if prev is None:
        return None
    for cur_point, prev_point in zip(state.touch_points, prev.touch_points):
        if cur_point.active and prev_point.active and cur_point.id == prev_point.id:
            dx = (cur_point.x - prev_point.x) / _TOUCHPAD_WIDTH
            dy = (cur_point.y - prev_point.y) / _TOUCHPAD_HEIGHT
            return (dx, dy)
    return None


# ---------------------------------------------------------------------------
# Analog resolution (deadzone -> curve -> sensitivity -> clamp)
# ---------------------------------------------------------------------------


def _resolve_scalar(raw: float, mods: AnalogModifiers) -> float:
    shaped = apply_deadzone(raw, mods.deadzone, mods.anti_deadzone)
    shaped = apply_curve(shaped, mods.curve, mods.curve_params)
    shaped *= mods.sensitivity
    return max(-1.0, min(1.0, shaped))


def _resolve_vector(raw: "tuple[float, float]", mods: AnalogModifiers) -> "tuple[float, float]":
    x, y = raw
    magnitude = math.hypot(x, y)
    if magnitude <= 0.0:
        return (0.0, 0.0)
    clamped_magnitude = min(magnitude, 1.0)
    shaped_magnitude = _resolve_scalar(clamped_magnitude, mods)
    ratio = shaped_magnitude / magnitude
    return (x * ratio, y * ratio)


def _values_differ(
    a: Union[float, "tuple[float, float]", None],
    b: Union[float, "tuple[float, float]", None],
    epsilon: float = _ANALOG_CHANGE_EPSILON,
) -> bool:
    if isinstance(a, tuple) or isinstance(b, tuple):
        a = a if isinstance(a, tuple) else (0.0, 0.0)
        b = b if isinstance(b, tuple) else (0.0, 0.0)
        return abs(a[0] - b[0]) > epsilon or abs(a[1] - b[1]) > epsilon
    a = a if a is not None else 0.0
    b = b if b is not None else 0.0
    return abs(a - b) > epsilon


# ---------------------------------------------------------------------------
# MappingEngine
# ---------------------------------------------------------------------------


class MappingEngine:
    """Resolves a ``DS4State`` against the active profile/layers into ``ActionEvent``\\ s.

    See the module docstring for the design decisions this class embodies
    (layer tie-break rule, layer-control handling, tap/hold semantics,
    capture mode, ...).
    """

    def __init__(self, profiles: "dict[str, Profile]") -> None:
        self._profiles: "dict[str, Profile]" = profiles
        self._active_profile_id: Optional[str] = None
        self._active_layers: "list[str]" = []
        self._capture_mode: bool = False
        self._prev_state: Optional[DS4State] = None
        self._digital_tracks: "dict[str, _DigitalTrack]" = {}
        self._analog_last_emitted: "dict[str, Union[float, tuple[float, float]]]" = {}
        self._last_raw_input: Optional[RawInputEvent] = None

    # -- profile / layer state -------------------------------------------------

    def set_active_profile(self, profile_id: str) -> None:
        """Switch the active base profile, clearing ALL active layers.

        Mirrors Steam Input's action-set-layer rule: activating a new base
        action set always clears every active layer, regardless of each
        layer's own ``clears_on_profile_switch`` flag (that flag is, per the
        schema's own docstring, about whether a layer would survive a
        *different* kind of transition than a base-profile switch; a base
        profile switch always wins).
        """

        if profile_id not in self._profiles:
            raise KeyError(f"unknown profile_id: {profile_id!r}")
        self._active_profile_id = profile_id
        self._active_layers = []

    @property
    def active_profile_id(self) -> Optional[str]:
        return self._active_profile_id

    @property
    def active_layers(self) -> "list[str]":
        """Currently active layer ids, oldest-activated first (see point 1)."""

        return list(self._active_layers)

    def set_capture_mode(self, active: bool) -> None:
        self._capture_mode = bool(active)

    def last_raw_input(self) -> Optional[RawInputEvent]:
        return self._last_raw_input

    # -- effective binding resolution ------------------------------------------

    def _resolve_effective_bindings(self) -> "dict[str, Binding]":
        if self._active_profile_id is None:
            return {}
        profile = self._profiles[self._active_profile_id]
        effective: "dict[str, Binding]" = {b.input.control: b for b in profile.bindings}

        layers_by_id = {layer.layer_id: layer for layer in profile.layers}
        for layer_id in self._active_layers:  # oldest -> newest; last one wins ties
            layer = layers_by_id.get(layer_id)
            if layer is None:
                continue
            for override in layer.overrides:
                effective[override.input.control] = override

        return effective

    # -- main entry point -------------------------------------------------------

    def process(self, state: DS4State, now: float) -> "list[ActionEvent]":
        events: "list[ActionEvent]" = []

        self._update_raw_input_tracking(state, self._prev_state, now)

        effective = self._resolve_effective_bindings()

        digital_controls = {c for c in effective if c in _DIGITAL_CONTROLS}
        digital_controls |= self._digital_tracks.keys()
        for control in digital_controls:
            self._process_digital(control, effective.get(control), state, now, events)

        for control in effective:
            if control in _ANALOG_SCALAR_CONTROLS or control in _ANALOG_VECTOR_CONTROLS:
                self._process_analog(control, effective[control], state, now, events)
            elif control in _ANALOG_DELTA_CONTROLS:
                self._process_analog_delta(control, effective[control], state, self._prev_state, now, events)

        self._prev_state = state
        return events

    # -- digital pipeline ---------------------------------------------------

    def _process_digital(
        self,
        control: str,
        binding: Optional[Binding],
        state: DS4State,
        now: float,
        events: "list[ActionEvent]",
    ) -> None:
        raw_pressed = _read_digital_raw(state, control)
        track = self._digital_tracks.get(control)
        was_pressed = track.pressed if track is not None else False

        if raw_pressed and not was_pressed:
            new_track = _DigitalTrack(pressed=True, press_time=now, binding=binding, hold_fired=False)
            self._digital_tracks[control] = new_track
            if binding is not None and not self._capture_mode:
                if isinstance(binding.target, LayerControlTarget):
                    self._apply_layer_control(binding, pressed=True, now=now, events=events)
            return

        if raw_pressed and was_pressed:
            assert track is not None
            if (
                track.binding is not None
                and not track.hold_fired
                and not isinstance(track.binding.target, LayerControlTarget)
            ):
                mods = track.binding.digital or DigitalModifiers()
                held_ms = (now - track.press_time) * 1000.0
                if held_ms >= mods.hold_ms:
                    track.hold_fired = True
                    if not self._capture_mode:
                        events.append(self._make_event(track.binding, "hold_start", None, now))
            return

        if (not raw_pressed) and was_pressed:
            assert track is not None
            held_ms = (now - track.press_time) * 1000.0
            bound = track.binding
            if bound is not None and not self._capture_mode:
                if isinstance(bound.target, LayerControlTarget):
                    self._apply_layer_control(bound, pressed=False, now=now, events=events)
                else:
                    mods = bound.digital or DigitalModifiers()
                    classification = classify_digital(held_ms, mods.tap_ms)
                    if classification == "tap":
                        events.append(self._make_event(bound, "tap", None, now))
                    else:
                        if not track.hold_fired:
                            events.append(self._make_event(bound, "hold_start", None, now))
                        events.append(self._make_event(bound, "hold_end", None, now))
            self._digital_tracks[control] = _DigitalTrack(
                pressed=False, press_time=now, binding=None, hold_fired=False
            )
            return

        # not pressed, wasn't pressed: nothing to do.
        if control not in self._digital_tracks:
            self._digital_tracks[control] = _DigitalTrack(
                pressed=False, press_time=now, binding=None, hold_fired=False
            )

    def _apply_layer_control(
        self, binding: Binding, pressed: bool, now: float, events: "list[ActionEvent]"
    ) -> None:
        target = binding.target
        assert isinstance(target, LayerControlTarget)
        layer_id = target.layer_id

        if target.mode == "hold":
            if pressed:
                if layer_id not in self._active_layers:
                    self._active_layers.append(layer_id)
                events.append(self._make_event(binding, "press", None, now))
            else:
                if layer_id in self._active_layers:
                    self._active_layers.remove(layer_id)
                events.append(self._make_event(binding, "release", None, now))
        else:  # "toggle"
            if pressed:
                if layer_id in self._active_layers:
                    self._active_layers.remove(layer_id)
                    events.append(self._make_event(binding, "release", None, now))
                else:
                    self._active_layers.append(layer_id)
                    events.append(self._make_event(binding, "press", None, now))
            # toggle only reacts to the press edge; release is a no-op.

    # -- analog pipeline ------------------------------------------------------

    def _process_analog(
        self, control: str, binding: Binding, state: DS4State, now: float, events: "list[ActionEvent]"
    ) -> None:
        if isinstance(binding.target, LayerControlTarget):
            return  # see module docstring: not meaningfully supported.

        mods = binding.analog or AnalogModifiers()
        if control in _ANALOG_VECTOR_CONTROLS:
            raw = _read_vector_raw(state, control)
            resolved: Union[float, "tuple[float, float]"] = _resolve_vector(raw, mods)
        else:
            raw_scalar = _read_scalar_raw(state, control)
            resolved = _resolve_scalar(raw_scalar, mods)

        last = self._analog_last_emitted.get(control)
        if _values_differ(resolved, last):
            if not self._capture_mode:
                events.append(self._make_event(binding, "analog_value", resolved, now))
            self._analog_last_emitted[control] = resolved

    def _process_analog_delta(
        self,
        control: str,
        binding: Binding,
        state: DS4State,
        prev: Optional[DS4State],
        now: float,
        events: "list[ActionEvent]",
    ) -> None:
        if isinstance(binding.target, LayerControlTarget):
            return

        delta = _read_touchpad_swipe_delta(state, prev)
        if delta is None:
            return

        mods = binding.analog or AnalogModifiers()
        resolved = _resolve_vector(delta, mods)
        if resolved != (0.0, 0.0) and not self._capture_mode:
            events.append(self._make_event(binding, "analog_delta", resolved, now))

    # -- raw physical input tracking (for capture-mode UI) ---------------------

    def _update_raw_input_tracking(
        self, state: DS4State, prev: Optional[DS4State], now: float
    ) -> None:
        if prev is None:
            return

        for control in _DIGITAL_CONTROLS:
            cur = _read_digital_raw(state, control)
            was = _read_digital_raw(prev, control)
            if cur and not was:
                self._last_raw_input = RawInputEvent(control, "press", None, now)
            elif was and not cur:
                self._last_raw_input = RawInputEvent(control, "release", None, now)

        for control in _ANALOG_SCALAR_CONTROLS:
            cur_v = _read_scalar_raw(state, control)
            prev_v = _read_scalar_raw(prev, control)
            if abs(cur_v - prev_v) > _ANALOG_CHANGE_EPSILON:
                self._last_raw_input = RawInputEvent(control, "analog_change", cur_v, now)

        for control in _ANALOG_VECTOR_CONTROLS:
            cur_v = _read_vector_raw(state, control)
            prev_v = _read_vector_raw(prev, control)
            if _values_differ(cur_v, prev_v):
                self._last_raw_input = RawInputEvent(control, "analog_change", cur_v, now)

    # -- event construction -----------------------------------------------------

    @staticmethod
    def _make_event(
        binding: Binding,
        kind: ActionEventKind,
        value: Union[float, "tuple[float, float]", None],
        now: float,
    ) -> ActionEvent:
        return ActionEvent(
            binding_id=binding.id, target=binding.target, kind=kind, value=value, timestamp=now
        )
