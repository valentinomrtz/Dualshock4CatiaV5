"""The background engine that wires everything together.

``Ds4CatiaDaemon`` is the only place in the project that knows about all
of HID input, the mapping engine, the CATIA backend, and input injection
at once -- every other module stays decoupled from the others behind the
interfaces built in earlier phases:

* ``MappingEngine`` (``ds4catia.mapping.engine``) turns a ``DS4State``
  into a list of ``ActionEvent``.
* ``CatiaBackend`` (``ds4catia.catia.interface``) executes discrete CATIA
  actions (commands, camera, selection, macros).
* ``InputInjector`` (``ds4catia.input_injection.sendinput``) executes
  continuous/fallback actions (keyboard/mouse) with a foreground-focus
  guard, for whatever the CATIA Automation API doesn't cover well.
* ``DS4Device`` (``ds4catia.hid.ds4_device``) is the optional real
  hardware source; it is intentionally optional here so the daemon can
  run in a GUI "dry-run" mode, or be unit-tested, by feeding it
  ``DS4State`` values directly via :meth:`Ds4CatiaDaemon.feed_state`.

Workbench-based profile auto-switching is also owned here (not by
``MappingEngine``): CATIA exposes no COM event for workbench changes
(per the research notes), so this polls ``CatiaBackend.get_workbench_id()``
on a timer and only switches profile once N consecutive reads agree
(``AppConfig.debounce_count``), to avoid "profile flapping" during
transient UI states.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ds4catia.app.config import AppConfig
from ds4catia.catia.interface import CatiaBackend, CatiaBackendError
from ds4catia.hid.ds4_report import DS4State
from ds4catia.input_injection.sendinput import InjectionResult, InputInjector
from ds4catia.mapping.engine import ActionEvent
from ds4catia.mapping.engine import MappingEngine
from ds4catia.mapping.schema import Profile, load_profile

# Minimal string-name -> Win32 virtual-key-code table for KeyboardTarget.keys.
# Deliberately small (just what default profiles/the GUI are expected to
# need); extending it is a matter of adding entries, not changing logic.
VK_CODES: dict[str, int] = {
    **{chr(c): c for c in range(0x30, 0x3A)},  # "0".."9"
    **{chr(c): c for c in range(0x41, 0x5B)},  # "A".."Z"
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)},  # f1..f12
}


def vk_code_for(key_name: str) -> int:
    """Resolve a profile's key name (e.g. ``"ctrl"``, ``"A"``, ``"F5"``)
    to a Win32 virtual-key code. Raises ``KeyError`` with the offending
    name for anything not in :data:`VK_CODES`, rather than silently
    sending the wrong key."""

    try:
        return VK_CODES[key_name.lower() if len(key_name) > 1 else key_name.upper()]
    except KeyError:
        raise KeyError(f"no virtual-key mapping for key name {key_name!r}") from None


def load_profiles_dir(profiles_dir: "str | Path") -> dict[str, Profile]:
    """Load every ``*.json`` profile in ``profiles_dir`` into a
    ``{profile_id: Profile}`` dict, as expected by ``MappingEngine`` and
    by :class:`Ds4CatiaDaemon` (for workbench-id -> profile_id lookup)."""

    profiles: dict[str, Profile] = {}
    for path in sorted(Path(profiles_dir).glob("*.json")):
        profile = load_profile(path)
        profiles[profile.profile_id] = profile
    return profiles


@dataclass
class DispatchResult:
    """Outcome of dispatching one ``ActionEvent``.

    ``skipped`` is ``True`` for anything that didn't actually reach CATIA
    or the injector (paused, no injector configured, a CATIA call that
    raised, an unmapped keyboard key, ...); ``reason`` is a short
    machine-readable code in that case, mirroring ``InjectionResult``.
    """

    skipped: bool
    reason: Optional[str]


# Default continuous-navigation tuning. These are starting points, not
# calibrated values -- the research explicitly flags analog camera feel
# as something to tune empirically against real CATIA (manual
# verification checklist, step 8), not something derivable from docs.
_CAMERA_ROTATE_DEG_PER_TICK = 2.0
_MOUSE_MOVE_PIXELS_PER_TICK = 12.0


class Ds4CatiaDaemon:
    """Wires HID input -> ``MappingEngine`` -> CATIA/injection dispatch."""

    def __init__(
        self,
        engine: MappingEngine,
        profiles: "dict[str, Profile]",
        catia: CatiaBackend,
        injector: Optional[InputInjector] = None,
        hid_device: object = None,
        config: Optional[AppConfig] = None,
    ) -> None:
        """
        Args:
            engine: Already constructed with the same ``profiles`` dict
                (the daemon does not construct the engine itself, so
                callers can swap engines -- e.g. for dry-run -- freely).
            profiles: Same dict passed to ``engine``'s constructor; used
                here to build the workbench-id -> profile_id lookup from
                each ``Profile.auto_activate``.
            catia: Either ``ComCatiaBackend`` (real) or ``FakeCatiaBackend``
                (dry-run/tests).
            injector: ``InputInjector``, or ``None`` if this run has no
                way to inject input (e.g. non-Windows dev/test); bindings
                that need it then resolve to ``skipped="no_injector"``.
            hid_device: A ``ds4catia.hid.ds4_device.DS4Device``-shaped
                object (duck-typed, not imported here, so this module
                stays importable without the ``hid`` package). ``None``
                means this daemon is fed externally via
                :meth:`feed_state` instead (GUI dry-run panel, tests).
            config: ``AppConfig``; defaults applied if omitted.
        """

        self.engine = engine
        self.profiles = profiles
        self.catia = catia
        self.injector = injector
        self.config = config or AppConfig()
        self._hid_device = hid_device

        self._workbench_to_profile: dict[str, str] = {
            profile.auto_activate.match: profile_id
            for profile_id, profile in profiles.items()
            if profile.auto_activate is not None
        }

        self._paused = False
        self._dry_run = False
        self._recent_workbench_reads: "list[str]" = []

        self._poll_stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._hid_stop_event = threading.Event()
        self._hid_thread: Optional[threading.Thread] = None

        # Optional observer, fired (from the HID background thread) each
        # time a *real* controller report produces dispatch results --
        # never for feed_state() calls a caller makes directly (e.g. the
        # GUI dry-run panel's own on-screen buttons already show their
        # own results inline; this hook exists so real hardware input
        # can *also* be surfaced live, by a headless --verbose print or
        # by the GUI relaying it into the same panel thread-safely).
        # Must never raise into the HID read loop -- call sites wrap it.
        self.on_dispatch: Optional[Callable[[DS4State, "list[DispatchResult]"], None]] = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Connect to CATIA, start the workbench-poll thread, and (if a
        real ``hid_device`` was given) start reading the controller."""

        self.catia.connect()

        self._poll_stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._workbench_poll_loop, daemon=True
        )
        self._poll_thread.start()

        if self._hid_device is not None:
            self._hid_device.connect()  # start_reading() requires this first
            self._hid_stop_event.clear()
            self._hid_thread = self._hid_device.start_reading(
                self._on_hid_state, stop_event=self._hid_stop_event
            )

    def stop(self) -> None:
        """Stop both background threads and disconnect from CATIA.
        Safe to call even if :meth:`start` was never called."""

        self._poll_stop_event.set()
        self._hid_stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
        if self._hid_thread is not None:
            self._hid_thread.join(timeout=2.0)
        if self._hid_device is not None:
            try:
                self._hid_device.disconnect()
            except Exception:
                pass
        try:
            self.catia.disconnect()
        except CatiaBackendError:
            pass

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused

    def set_dry_run(self, enabled: bool) -> None:
        """Toggle dry-run at runtime (so the GUI's dry-run checkbox does
        not require a restart). This only affects :meth:`dispatch`'s
        decision to actually call ``self.catia``/``self.injector`` --
        swap in a ``FakeCatiaBackend``/no injector beforehand if you want
        dry-run to also avoid touching the real backend objects."""

        self._dry_run = bool(enabled)

    def is_dry_run(self) -> bool:
        return self._dry_run

    # -- HID feed -----------------------------------------------------------

    def _on_hid_state(self, state: DS4State) -> None:
        # Runs on the DS4Device background thread; feed_state() itself
        # does no locking, but MappingEngine.process()/dispatch() only
        # touch per-daemon state, and this project runs a single DS4
        # device per daemon, so there is no concurrent writer to guard
        # against here.
        results = self.feed_state(state)
        if self.on_dispatch is not None:
            try:
                self.on_dispatch(state, results)
            except Exception:
                # A broken observer (e.g. a GUI callback racing a
                # teardown) must never take down the HID read loop.
                pass

    def feed_state(
        self, state: DS4State, now: Optional[float] = None
    ) -> "list[DispatchResult]":
        """Run one ``DS4State`` through the engine and dispatch every
        resulting ``ActionEvent``. Public (not just the HID callback) so
        the GUI's dry-run panel and tests can drive the daemon without a
        real controller."""

        now = now if now is not None else time.time()
        events = self.engine.process(state, now)
        return [self.dispatch(event) for event in events]

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, event: ActionEvent) -> DispatchResult:
        """Route one resolved ``ActionEvent`` to CATIA, to the injector,
        or to the daemon's own app-control handling, per
        ``event.target.type``. Never raises: any backend/injector error
        is caught and turned into a skipped ``DispatchResult`` so one bad
        action cannot kill the whole input loop."""

        if self._paused and event.target.type != "app_control":
            return DispatchResult(skipped=True, reason="paused")

        try:
            handler = self._DISPATCH_TABLE.get(event.target.type)
            if handler is None:
                return DispatchResult(skipped=True, reason=f"unhandled_type:{event.target.type}")
            return handler(self, event)
        except CatiaBackendError as exc:
            return DispatchResult(skipped=True, reason=f"catia_error:{exc}")
        except KeyError as exc:
            return DispatchResult(skipped=True, reason=f"bad_binding:{exc}")

    def _dispatch_catia_command(self, event: ActionEvent) -> DispatchResult:
        if event.kind not in ("tap", "hold_start"):
            return DispatchResult(skipped=True, reason=f"ignored_kind:{event.kind}")
        self.catia.start_command(event.target.command_id)
        return DispatchResult(skipped=False, reason=None)

    def _dispatch_catia_camera(self, event: ActionEvent) -> DispatchResult:
        target = event.target
        if target.operation == "rotate":
            if target.axis is not None:
                # An explicit single axis was requested (e.g. a trigger
                # or a dedicated single-axis binding): use the resolved
                # value's magnitude around exactly that axis, as before.
                magnitude = _scalar_magnitude(event.value)
                self.catia.camera_rotate(target.axis, magnitude * _CAMERA_ROTATE_DEG_PER_TICK)
            else:
                # No axis specified -- this is the common case for a
                # stick bound whole (both X and Y) to "rotate", meant as
                # a 2-axis orbit control. A single CATIA Rotate() call
                # only takes one axis, so compose the orbit from two
                # sequential single-axis rotations per tick: the stick's
                # horizontal deflection yaws around the global vertical
                # (Z) axis, its vertical deflection pitches around the
                # global horizontal (X) axis. This ignores the camera's
                # own current orientation (a "true" camera-relative
                # orbit would need the view's live up/right vectors,
                # which this backend does not currently read), but gives
                # both stick axes a visible, independent effect instead
                # of silently discarding the vertical one -- which is
                # what a single fixed-axis Rotate() call did before this
                # fix and is why "rotating" only ever spun around one
                # axis regardless of how the stick was pushed.
                x, y = _as_vector2(event.value)
                if abs(x) > 1e-6:
                    self.catia.camera_rotate((0.0, 0.0, 1.0), x * _CAMERA_ROTATE_DEG_PER_TICK)
                if abs(y) > 1e-6:
                    self.catia.camera_rotate((1.0, 0.0, 0.0), y * _CAMERA_ROTATE_DEG_PER_TICK)
        elif target.operation == "translate":
            vector = _as_vector3(event.value)
            self.catia.camera_translate(vector)
        elif target.operation == "zoom":
            magnitude = _scalar_magnitude(event.value)
            # target.axis's sign (if given) picks the zoom direction --
            # e.g. base.json binds L2 with axis (0,0,-1) for "zoom out"
            # and R2 with axis (0,0,1) for "zoom in". Both triggers
            # report a non-negative 0..1 magnitude on their own (there is
            # no such thing as a "negative" trigger pull), so without
            # this the two bindings were indistinguishable and both
            # zoomed the same direction -- a real bug this fixes, not
            # just a cosmetic one.
            direction = 1.0
            if target.axis is not None:
                dominant = max(target.axis, key=abs)
                if dominant < 0:
                    direction = -1.0
            # Treat the resolved value as a zoom delta around 1.0 rather
            # than an absolute factor, so a centered trigger/stick is a
            # no-op zoom rather than snapping to a fixed zoom level.
            self.catia.camera_zoom(1.0 + direction * magnitude)
        else:  # pragma: no cover - guarded by the schema's Literal
            return DispatchResult(skipped=True, reason=f"unknown_camera_op:{target.operation}")
        return DispatchResult(skipped=False, reason=None)

    def _dispatch_catia_selection(self, event: ActionEvent) -> DispatchResult:
        target = event.target
        if event.kind not in ("tap", "hold_start"):
            return DispatchResult(skipped=True, reason=f"ignored_kind:{event.kind}")
        if target.operation == "add":
            self.catia.selection_add(target.query or "")
        elif target.operation == "clear":
            self.catia.selection_clear()
        elif target.operation == "search":
            self.catia.selection_search(target.query or "")
        else:  # pragma: no cover - guarded by the schema's Literal
            return DispatchResult(skipped=True, reason=f"unknown_selection_op:{target.operation}")
        return DispatchResult(skipped=False, reason=None)

    def _dispatch_catia_macro(self, event: ActionEvent) -> DispatchResult:
        if event.kind not in ("tap", "hold_start"):
            return DispatchResult(skipped=True, reason=f"ignored_kind:{event.kind}")
        self.catia.run_macro(event.target.macro_name)
        return DispatchResult(skipped=False, reason=None)

    def _dispatch_keyboard(self, event: ActionEvent) -> DispatchResult:
        if self.injector is None:
            return DispatchResult(skipped=True, reason="no_injector")

        key_up = event.kind in ("release", "hold_end")
        if event.kind == "tap":
            # A tap has no separate press/release ActionEvent -- send a
            # quick down+up pair ourselves.
            for key in event.target.keys:
                self.injector.inject({"kind": "key", "vk_code": vk_code_for(key), "key_up": False})
            for key in reversed(event.target.keys):
                self.injector.inject({"kind": "key", "vk_code": vk_code_for(key), "key_up": True})
            return DispatchResult(skipped=False, reason=None)

        if event.kind not in ("hold_start", "hold_end"):
            return DispatchResult(skipped=True, reason=f"ignored_kind:{event.kind}")

        keys = event.target.keys if not key_up else list(reversed(event.target.keys))
        last_result: Optional[InjectionResult] = None
        for key in keys:
            last_result = self.injector.inject(
                {"kind": "key", "vk_code": vk_code_for(key), "key_up": key_up}
            )
        if last_result is not None and last_result.skipped:
            return DispatchResult(skipped=True, reason=last_result.reason)
        return DispatchResult(skipped=False, reason=None)

    def _dispatch_mouse_move(self, event: ActionEvent) -> DispatchResult:
        if self.injector is None:
            return DispatchResult(skipped=True, reason="no_injector")
        x, y = _as_vector2(event.value)
        axis = event.target.axis
        dx = int(x * _MOUSE_MOVE_PIXELS_PER_TICK) if axis in ("x", "both") else 0
        dy = int(y * _MOUSE_MOVE_PIXELS_PER_TICK) if axis in ("y", "both") else 0
        result = self.injector.inject({"kind": "mouse_move", "dx": dx, "dy": dy, "relative": True})
        return DispatchResult(skipped=result.skipped, reason=result.reason)

    def _dispatch_mouse_button(self, event: ActionEvent) -> DispatchResult:
        if self.injector is None:
            return DispatchResult(skipped=True, reason="no_injector")

        if event.kind == "tap":
            self.injector.inject({"kind": "mouse_button", "button": event.target.button, "down": True})
            result = self.injector.inject(
                {"kind": "mouse_button", "button": event.target.button, "down": False}
            )
            return DispatchResult(skipped=result.skipped, reason=result.reason)

        if event.kind not in ("hold_start", "hold_end"):
            return DispatchResult(skipped=True, reason=f"ignored_kind:{event.kind}")

        down = event.kind == "hold_start"
        result = self.injector.inject(
            {"kind": "mouse_button", "button": event.target.button, "down": down}
        )
        return DispatchResult(skipped=result.skipped, reason=result.reason)

    def _dispatch_layer_control(self, event: ActionEvent) -> DispatchResult:
        # The engine already applied the layer activation/deactivation
        # itself before emitting this event; nothing left to dispatch
        # (informational only, e.g. for a GUI to show the active layer).
        return DispatchResult(skipped=True, reason="informational_only")

    def _dispatch_app_control(self, event: ActionEvent) -> DispatchResult:
        if event.kind != "tap":
            return DispatchResult(skipped=True, reason=f"ignored_kind:{event.kind}")

        action = event.target.action
        if action == "pause":
            self.pause()
        elif action == "resume":
            self.resume()
        elif action in ("next_profile", "prev_profile"):
            self._cycle_profile(forward=action == "next_profile")
        else:  # pragma: no cover - guarded by the schema's Literal
            return DispatchResult(skipped=True, reason=f"unknown_app_action:{action}")
        return DispatchResult(skipped=False, reason=None)

    def _cycle_profile(self, forward: bool) -> None:
        profile_ids = sorted(self.profiles.keys())
        if not profile_ids:
            return
        current = self.engine.active_profile_id
        if current not in profile_ids:
            self.engine.set_active_profile(profile_ids[0])
            return
        idx = profile_ids.index(current)
        idx = (idx + (1 if forward else -1)) % len(profile_ids)
        self.engine.set_active_profile(profile_ids[idx])

    _DISPATCH_TABLE: "dict[str, Callable[[Ds4CatiaDaemon, ActionEvent], DispatchResult]]" = {
        "catia_command": _dispatch_catia_command,
        "catia_camera": _dispatch_catia_camera,
        "catia_selection": _dispatch_catia_selection,
        "catia_macro": _dispatch_catia_macro,
        "keyboard": _dispatch_keyboard,
        "mouse_move": _dispatch_mouse_move,
        "mouse_button": _dispatch_mouse_button,
        "layer_control": _dispatch_layer_control,
        "app_control": _dispatch_app_control,
    }

    # -- workbench auto-switch (polling + debounce) --------------------------

    def _workbench_poll_loop(self) -> None:
        interval_s = self.config.workbench_poll_interval_ms / 1000.0
        while not self._poll_stop_event.wait(interval_s):
            self.poll_workbench_once()

    def poll_workbench_once(self) -> None:
        """Read ``get_workbench_id()`` once and, if the last
        ``AppConfig.debounce_count`` reads all agree on a workbench with a
        mapped profile different from the active one, switch to it.

        Public (not just the internal poll loop) so tests -- and a future
        "refresh now" GUI action -- can drive this deterministically
        without real threading/timing."""

        if not self.catia.is_connected():
            return

        try:
            workbench_id = self.catia.get_workbench_id()
        except CatiaBackendError:
            return

        self._recent_workbench_reads.append(workbench_id)
        del self._recent_workbench_reads[: -self.config.debounce_count]

        if (
            len(self._recent_workbench_reads) == self.config.debounce_count
            and len(set(self._recent_workbench_reads)) == 1
        ):
            profile_id = self._workbench_to_profile.get(workbench_id)
            if profile_id is not None and profile_id != self.engine.active_profile_id:
                self.engine.set_active_profile(profile_id)


def _scalar_magnitude(value: "float | tuple[float, float] | None") -> float:
    if value is None:
        return 0.0
    if isinstance(value, tuple):
        return value[0]
    return float(value)


def _as_vector2(value: "float | tuple[float, float] | None") -> "tuple[float, float]":
    if value is None:
        return (0.0, 0.0)
    if isinstance(value, tuple):
        return (float(value[0]), float(value[1]))
    return (float(value), 0.0)


def _as_vector3(value: "float | tuple[float, float] | None") -> "tuple[float, float, float]":
    x, y = _as_vector2(value)
    return (x, y, 0.0)
