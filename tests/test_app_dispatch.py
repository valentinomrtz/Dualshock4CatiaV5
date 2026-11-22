"""Tests for ds4catia.app.daemon: dispatch routing, workbench auto-switch
debounce, and the feed_state() end-to-end path -- all against
FakeCatiaBackend and a fake injector, never real Windows/CATIA."""

from __future__ import annotations

import pytest

from ds4catia.app.config import AppConfig
from ds4catia.app.daemon import Ds4CatiaDaemon, vk_code_for
from ds4catia.catia.fake_backend import FakeCatiaBackend
from ds4catia.hid.ds4_report import DS4State
from ds4catia.input_injection.sendinput import InjectionResult
from ds4catia.mapping.engine import ActionEvent, MappingEngine
from ds4catia.mapping.schema import (
    AppControlTarget,
    Binding,
    CatiaCameraTarget,
    CatiaCommandTarget,
    CatiaMacroTarget,
    CatiaSelectionTarget,
    InputRef,
    KeyboardTarget,
    LayerControlTarget,
    MouseButtonTarget,
    MouseMoveTarget,
    Profile,
    ProfileAutoActivate,
)


class FakeInjector:
    """Records every inject() call; never touches real Win32 APIs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.skip_next = False

    def inject(self, action) -> InjectionResult:
        self.calls.append(dict(action))
        if self.skip_next:
            self.skip_next = False
            return InjectionResult(skipped=True, reason="not_foreground")
        return InjectionResult(skipped=False, reason=None)


def _empty_profiles() -> dict:
    return {"base": Profile(profile_id="base", display_name="Base")}


def _make_daemon(injector=None, config=None) -> Ds4CatiaDaemon:
    profiles = _empty_profiles()
    engine = MappingEngine(profiles)
    engine.set_active_profile("base")
    catia = FakeCatiaBackend()
    catia.connect()
    catia.call_log.clear()  # drop the connect() call so tests start from a clean log
    return Ds4CatiaDaemon(
        engine=engine,
        profiles=profiles,
        catia=catia,
        injector=injector,
        config=config,
    )


def _event(target, kind="tap", value=None, now=1.0) -> ActionEvent:
    return ActionEvent(binding_id="b1", target=target, kind=kind, value=value, timestamp=now)


# -- routing: CATIA targets ---------------------------------------------------


def test_dispatch_catia_command_tap():
    daemon = _make_daemon()
    result = daemon.dispatch(_event(CatiaCommandTarget(command_id="View.FitAll"), kind="tap"))
    assert result.skipped is False
    assert daemon.catia.call_log[-1].method == "start_command"
    assert daemon.catia.call_log[-1].args == ("View.FitAll",)


def test_dispatch_catia_command_ignores_release():
    daemon = _make_daemon()
    result = daemon.dispatch(_event(CatiaCommandTarget(command_id="X"), kind="release"))
    assert result.skipped is True
    assert result.reason.startswith("ignored_kind")
    assert daemon.catia.call_log == []


def test_dispatch_catia_camera_rotate():
    daemon = _make_daemon()
    target = CatiaCameraTarget(operation="rotate", axis=(0.0, 1.0, 0.0))
    daemon.dispatch(_event(target, kind="analog_value", value=0.5))
    call = daemon.catia.call_log[-1]
    assert call.method == "camera_rotate"
    assert call.args[0] == (0.0, 1.0, 0.0)
    assert call.args[1] == pytest.approx(0.5 * 2.0)


def test_dispatch_catia_camera_rotate_without_axis_drives_both_stick_axes():
    # base.json's left_stick binding has no explicit `axis` -- both the
    # stick's X (yaw) and Y (pitch) must each produce their own Rotate()
    # call, not just whichever component _scalar_magnitude used to pick.
    daemon = _make_daemon()
    target = CatiaCameraTarget(operation="rotate")
    daemon.dispatch(_event(target, kind="analog_value", value=(0.5, -0.3)))
    rotate_calls = [c for c in daemon.catia.call_log if c.method == "camera_rotate"]
    assert len(rotate_calls) == 2
    assert rotate_calls[0].args[0] == (0.0, 0.0, 1.0)
    assert rotate_calls[0].args[1] == pytest.approx(0.5 * 2.0)
    assert rotate_calls[1].args[0] == (1.0, 0.0, 0.0)
    assert rotate_calls[1].args[1] == pytest.approx(-0.3 * 2.0)


def test_dispatch_catia_camera_rotate_without_axis_skips_zero_components():
    daemon = _make_daemon()
    target = CatiaCameraTarget(operation="rotate")
    daemon.dispatch(_event(target, kind="analog_value", value=(0.0, -0.3)))
    rotate_calls = [c for c in daemon.catia.call_log if c.method == "camera_rotate"]
    assert len(rotate_calls) == 1
    assert rotate_calls[0].args[0] == (1.0, 0.0, 0.0)


def test_dispatch_catia_camera_zoom_direction_follows_axis_sign():
    daemon = _make_daemon()
    zoom_out = CatiaCameraTarget(operation="zoom", axis=(0.0, 0.0, -1.0))
    daemon.dispatch(_event(zoom_out, kind="analog_value", value=0.4))
    assert daemon.catia.call_log[-1].args[0] == pytest.approx(1.0 - 0.4)

    zoom_in = CatiaCameraTarget(operation="zoom", axis=(0.0, 0.0, 1.0))
    daemon.dispatch(_event(zoom_in, kind="analog_value", value=0.4))
    assert daemon.catia.call_log[-1].args[0] == pytest.approx(1.0 + 0.4)


def test_dispatch_catia_camera_translate_uses_vector():
    daemon = _make_daemon()
    target = CatiaCameraTarget(operation="translate")
    daemon.dispatch(_event(target, kind="analog_value", value=(0.3, -0.4)))
    call = daemon.catia.call_log[-1]
    assert call.method == "camera_translate"
    assert call.args[0] == (0.3, -0.4, 0.0)


def test_dispatch_catia_camera_zoom_centers_on_one():
    daemon = _make_daemon()
    target = CatiaCameraTarget(operation="zoom")
    daemon.dispatch(_event(target, kind="analog_value", value=0.0))
    call = daemon.catia.call_log[-1]
    assert call.method == "camera_zoom"
    assert call.args[0] == pytest.approx(1.0)


def test_dispatch_catia_selection_add():
    daemon = _make_daemon()
    target = CatiaSelectionTarget(operation="add", query="Body.1")
    daemon.dispatch(_event(target, kind="tap"))
    call = daemon.catia.call_log[-1]
    assert call.method == "selection_add"
    assert call.args == ("Body.1",)


def test_dispatch_catia_macro():
    daemon = _make_daemon()
    daemon.dispatch(_event(CatiaMacroTarget(macro_name="MyMacro"), kind="tap"))
    call = daemon.catia.call_log[-1]
    assert call.method == "run_macro"
    assert call.args == ("MyMacro",)


def test_dispatch_catia_error_is_caught():
    from ds4catia.catia.interface import CatiaCommandError

    daemon = _make_daemon()
    daemon.catia.fail_next_call(CatiaCommandError("boom"))
    result = daemon.dispatch(_event(CatiaCommandTarget(command_id="X"), kind="tap"))
    assert result.skipped is True
    assert result.reason.startswith("catia_error")


# -- routing: injection targets -----------------------------------------------


def test_dispatch_keyboard_tap_sends_down_then_up():
    injector = FakeInjector()
    daemon = _make_daemon(injector=injector)
    daemon.dispatch(_event(KeyboardTarget(keys=["ctrl", "A"]), kind="tap"))
    kinds = [(c["kind"], c["vk_code"], c["key_up"]) for c in injector.calls]
    assert kinds == [
        ("key", vk_code_for("ctrl"), False),
        ("key", vk_code_for("A"), False),
        ("key", vk_code_for("A"), True),
        ("key", vk_code_for("ctrl"), True),
    ]


def test_dispatch_keyboard_hold_start_and_end():
    injector = FakeInjector()
    daemon = _make_daemon(injector=injector)
    daemon.dispatch(_event(KeyboardTarget(keys=["shift"]), kind="hold_start"))
    assert injector.calls[-1]["key_up"] is False
    daemon.dispatch(_event(KeyboardTarget(keys=["shift"]), kind="hold_end"))
    assert injector.calls[-1]["key_up"] is True


def test_dispatch_keyboard_without_injector_is_skipped():
    daemon = _make_daemon(injector=None)
    result = daemon.dispatch(_event(KeyboardTarget(keys=["A"]), kind="tap"))
    assert result.skipped is True
    assert result.reason == "no_injector"


def test_dispatch_keyboard_unknown_key_is_bad_binding():
    injector = FakeInjector()
    daemon = _make_daemon(injector=injector)
    result = daemon.dispatch(_event(KeyboardTarget(keys=["not_a_real_key"]), kind="tap"))
    assert result.skipped is True
    assert result.reason.startswith("bad_binding")


def test_dispatch_mouse_move_scales_and_respects_axis():
    injector = FakeInjector()
    daemon = _make_daemon(injector=injector)
    daemon.dispatch(_event(MouseMoveTarget(axis="x"), kind="analog_value", value=(1.0, 1.0)))
    call = injector.calls[-1]
    assert call["kind"] == "mouse_move"
    assert call["dx"] != 0
    assert call["dy"] == 0


def test_dispatch_mouse_move_reports_skip_from_injector():
    injector = FakeInjector()
    injector.skip_next = True
    daemon = _make_daemon(injector=injector)
    result = daemon.dispatch(_event(MouseMoveTarget(), kind="analog_value", value=(1.0, 0.0)))
    assert result.skipped is True
    assert result.reason == "not_foreground"


def test_dispatch_mouse_button_tap():
    injector = FakeInjector()
    daemon = _make_daemon(injector=injector)
    daemon.dispatch(_event(MouseButtonTarget(button="left"), kind="tap"))
    assert [c["down"] for c in injector.calls] == [True, False]


# -- layer_control / app_control ----------------------------------------------


def test_dispatch_layer_control_is_informational_only():
    daemon = _make_daemon()
    result = daemon.dispatch(
        _event(LayerControlTarget(layer_id="shift", mode="hold"), kind="press")
    )
    assert result.skipped is True
    assert result.reason == "informational_only"
    assert daemon.catia.call_log == []


def test_dispatch_app_control_pause_resume():
    daemon = _make_daemon()
    daemon.dispatch(_event(AppControlTarget(action="pause"), kind="tap"))
    assert daemon.is_paused() is True
    # While paused, app_control itself must still get through (otherwise
    # resume could never be dispatched again).
    daemon.dispatch(_event(AppControlTarget(action="resume"), kind="tap"))
    assert daemon.is_paused() is False


def test_pause_blocks_other_dispatch():
    daemon = _make_daemon()
    daemon.pause()
    result = daemon.dispatch(_event(CatiaCommandTarget(command_id="X"), kind="tap"))
    assert result.skipped is True
    assert result.reason == "paused"
    assert daemon.catia.call_log == []


def test_app_control_next_profile_cycles():
    profiles = {
        "base": Profile(profile_id="base", display_name="Base"),
        "part_design": Profile(profile_id="part_design", display_name="PD"),
    }
    engine = MappingEngine(profiles)
    engine.set_active_profile("base")
    catia = FakeCatiaBackend()
    catia.connect()
    daemon = Ds4CatiaDaemon(engine=engine, profiles=profiles, catia=catia)
    daemon.dispatch(_event(AppControlTarget(action="next_profile"), kind="tap"))
    assert engine.active_profile_id == "part_design"
    daemon.dispatch(_event(AppControlTarget(action="next_profile"), kind="tap"))
    assert engine.active_profile_id == "base"


# -- workbench auto-switch debounce -------------------------------------------


def test_workbench_autoswitch_requires_consecutive_agreement():
    profiles = {
        "base": Profile(profile_id="base", display_name="Base"),
        "part_design": Profile(
            profile_id="part_design",
            display_name="PD",
            auto_activate=ProfileAutoActivate(trigger="catia_workbench_id", match="PrtCfg"),
        ),
    }
    engine = MappingEngine(profiles)
    engine.set_active_profile("base")
    catia = FakeCatiaBackend()
    catia.connect()
    config = AppConfig(debounce_count=3)
    daemon = Ds4CatiaDaemon(engine=engine, profiles=profiles, catia=catia, config=config)

    # A single transient read must NOT switch the profile.
    catia.set_workbench_sequence(["PrtCfg", "SktCfg", "PrtCfg", "PrtCfg", "PrtCfg"])
    daemon.poll_workbench_once()  # PrtCfg
    daemon.poll_workbench_once()  # SktCfg (breaks the streak)
    assert engine.active_profile_id == "base"

    daemon.poll_workbench_once()  # PrtCfg (1/3)
    daemon.poll_workbench_once()  # PrtCfg (2/3)
    assert engine.active_profile_id == "base"
    daemon.poll_workbench_once()  # PrtCfg (3/3) -> switch
    assert engine.active_profile_id == "part_design"


def test_workbench_autoswitch_ignores_catia_errors():
    from ds4catia.catia.interface import CatiaConnectionError

    daemon = _make_daemon(config=AppConfig(debounce_count=1))
    daemon.catia.fail_next_call(CatiaConnectionError("com dropped"))
    # Must not raise.
    daemon.poll_workbench_once()


def test_workbench_autoswitch_noop_when_disconnected():
    daemon = _make_daemon(config=AppConfig(debounce_count=1))
    daemon.catia.disconnect()
    daemon.poll_workbench_once()  # must not raise despite being disconnected


# -- feed_state end-to-end ------------------------------------------------------


def _state(buttons=None, **kwargs) -> DS4State:
    base_buttons = {
        "cross": False, "circle": False, "square": False, "triangle": False,
        "l1": False, "r1": False, "l2_digital": False, "r2_digital": False,
        "l3": False, "r3": False, "share": False, "options": False,
        "ps": False, "touchpad_click": False,
    }
    if buttons:
        base_buttons.update(buttons)
    defaults = dict(
        buttons=base_buttons,
        left_stick=(0.0, 0.0),
        right_stick=(0.0, 0.0),
        l2_analog=0.0,
        r2_analog=0.0,
        dpad=None,
        touch_points=[],
        gyro=(0, 0, 0),
        accel=(0, 0, 0),
        battery_pct=100,
        timestamp=0.0,
    )
    defaults.update(kwargs)
    return DS4State(**defaults)


def test_feed_state_dispatches_a_tap_to_catia():
    profile = Profile(
        profile_id="base",
        display_name="Base",
        bindings=[
            Binding(
                id="fit-all",
                input=InputRef(control="cross"),
                target=CatiaCommandTarget(command_id="View.FitAll"),
            )
        ],
    )
    profiles = {"base": profile}
    engine = MappingEngine(profiles)
    engine.set_active_profile("base")
    catia = FakeCatiaBackend()
    catia.connect()
    daemon = Ds4CatiaDaemon(engine=engine, profiles=profiles, catia=catia)

    daemon.feed_state(_state(buttons={"cross": True}), now=0.0)
    daemon.feed_state(_state(buttons={"cross": False}), now=0.05)

    assert daemon.catia.call_log[-1].method == "start_command"
    assert daemon.catia.call_log[-1].args == ("View.FitAll",)


# -- start()/stop() lifecycle with a (fake) real hid_device -------------------


class FakeHidDevice:
    """Stands in for ds4catia.hid.ds4_device.DS4Device: records the call
    order of connect/start_reading/disconnect without touching hardware."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started_with = None

    def connect(self) -> None:
        self.calls.append("connect")

    def start_reading(self, callback, stop_event=None):
        if "connect" not in self.calls:
            raise AssertionError("start_reading() called before connect()")
        self.calls.append("start_reading")
        self.started_with = callback

        class _DummyThread:
            def join(self, timeout=None):
                pass

        return _DummyThread()

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def test_start_connects_hid_device_before_reading_and_stop_disconnects_it():
    daemon = _make_daemon()
    hid_device = FakeHidDevice()
    daemon._hid_device = hid_device

    daemon.start()
    assert hid_device.calls == ["connect", "start_reading"]
    assert hid_device.started_with == daemon._on_hid_state  # bound methods compare by ==, not `is`

    daemon.stop()
    assert hid_device.calls == ["connect", "start_reading", "disconnect"]


# -- on_dispatch observer (real-hardware-only notification) -------------------


def test_on_hid_state_invokes_on_dispatch_with_state_and_results():
    profile = Profile(
        profile_id="base",
        display_name="Base",
        bindings=[
            Binding(
                id="fit-all",
                input=InputRef(control="cross"),
                target=CatiaCommandTarget(command_id="View.FitAll"),
            )
        ],
    )
    profiles = {"base": profile}
    engine = MappingEngine(profiles)
    engine.set_active_profile("base")
    catia = FakeCatiaBackend()
    catia.connect()
    daemon = Ds4CatiaDaemon(engine=engine, profiles=profiles, catia=catia)

    seen: list = []
    daemon.on_dispatch = lambda state, results: seen.append((state, results))

    # Directly exercising the real-hardware-only path (_on_hid_state),
    # not feed_state(), since on_dispatch must NOT fire for a caller
    # (e.g. the GUI dry-run panel's own buttons) that calls feed_state()
    # itself -- only for input that arrived via the HID background
    # thread's callback.
    daemon._on_hid_state(_state(buttons={"cross": True}, timestamp=0.0))
    daemon._on_hid_state(_state(buttons={"cross": False}, timestamp=0.05))

    assert len(seen) == 2
    assert any(r.skipped is False for _state_arg, results in seen for r in results)


def test_feed_state_does_not_trigger_on_dispatch():
    daemon = _make_daemon()
    seen: list = []
    daemon.on_dispatch = lambda state, results: seen.append((state, results))

    daemon.feed_state(_state(buttons={"cross": True}), now=0.0)

    assert seen == []


def test_on_dispatch_exception_does_not_break_hid_callback():
    daemon = _make_daemon()
    daemon.on_dispatch = lambda state, results: (_ for _ in ()).throw(RuntimeError("boom"))

    # Must not raise -- a broken observer can't be allowed to kill the
    # real HID read loop.
    daemon._on_hid_state(_state(buttons={"cross": True}))
