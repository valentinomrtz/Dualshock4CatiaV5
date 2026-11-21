"""Tests for ds4catia.gui.tray, ds4catia.gui.capture and
ds4catia.gui.dry_run_panel -- the system tray, the capture-and-confirm
remap flow, and the dry-run simulation panel.

Runs headlessly via pytest-qt + PySide6's offscreen platform plugin; see
the ``QT_QPA_PLATFORM`` line below, set before any Qt import happens.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from ds4catia.app.daemon import Ds4CatiaDaemon
from ds4catia.catia.fake_backend import FakeCatiaBackend
from ds4catia.gui.capture import CaptureController, CaptureDialog
from ds4catia.gui.dry_run_panel import DryRunPanel
from ds4catia.gui.tray import TrayController, build_tray_icon, ensure_app_survives_window_close
from ds4catia.hid.ds4_report import DS4State, TouchPoint
from ds4catia.mapping.engine import MappingEngine
from ds4catia.mapping.schema import (
    Binding,
    CatiaCommandTarget,
    InputRef,
    Profile,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
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


def _minimal_profile() -> Profile:
    """A profile with one known binding: cross -> a CATIA command."""

    binding = Binding(
        id="b-cross",
        input=InputRef(control="cross"),
        target=CatiaCommandTarget(command_id="View.FitAll"),
    )
    return Profile(profile_id="test", display_name="Test profile", bindings=[binding])


@pytest.fixture
def profiles() -> dict:
    profile = _minimal_profile()
    return {profile.profile_id: profile}


@pytest.fixture
def engine(profiles) -> MappingEngine:
    eng = MappingEngine(profiles)
    eng.set_active_profile("test")
    return eng


@pytest.fixture
def catia() -> FakeCatiaBackend:
    backend = FakeCatiaBackend()
    backend.connect()
    backend.call_log.clear()  # drop the connect() call for a clean starting log
    return backend


@pytest.fixture
def daemon(engine, profiles, catia) -> Ds4CatiaDaemon:
    return Ds4CatiaDaemon(engine=engine, profiles=profiles, catia=catia)


# ---------------------------------------------------------------------------
# Tray
# ---------------------------------------------------------------------------


def test_build_tray_icon_constructs_without_error(qapp, daemon):
    tray = build_tray_icon(daemon)
    assert tray is not None
    menu = tray.contextMenu()
    assert menu is not None


def test_tray_menu_has_expected_actions(qapp, daemon):
    controller = TrayController(daemon)
    action_texts = [a.text() for a in controller.menu.actions() if a.text()]

    assert any("ausar" in t for t in action_texts)  # "Pausar"
    assert any("Perfil activo" in t for t in action_texts)
    assert any("configuraci" in t.lower() for t in action_texts)
    assert any(t == "Salir" for t in action_texts)


def test_tray_pause_action_triggers_daemon_pause(qapp, daemon):
    controller = TrayController(daemon)
    assert daemon.is_paused() is False
    assert controller.pause_resume_action.text() == "Pausar"

    controller.pause_resume_action.trigger()

    assert daemon.is_paused() is True
    assert controller.pause_resume_action.text() == "Reanudar"

    controller.pause_resume_action.trigger()

    assert daemon.is_paused() is False
    assert controller.pause_resume_action.text() == "Pausar"


def test_tray_profile_action_reflects_active_profile(qapp, daemon):
    controller = TrayController(daemon)
    assert "test" in controller.profile_action.text()


def test_tray_open_config_action_emits_signal(qapp, daemon, qtbot):
    controller = TrayController(daemon)
    with qtbot.waitSignal(controller.open_config_requested, timeout=1000):
        controller.open_config_action.trigger()


def test_tray_open_config_callback_injection(qapp, daemon):
    calls = []
    controller = TrayController(daemon, on_open_config=lambda: calls.append(True))
    controller.open_config_action.trigger()
    assert calls == [True]


def test_tray_quit_action_emits_signal(qapp, daemon, qtbot):
    controller = TrayController(daemon)
    with qtbot.waitSignal(controller.quit_requested, timeout=1000):
        controller.quit_action.trigger()


def test_ensure_app_survives_window_close_sets_flag(qapp):
    qapp.setQuitOnLastWindowClosed(True)
    ensure_app_survives_window_close(qapp)
    assert qapp.quitOnLastWindowClosed() is False


# ---------------------------------------------------------------------------
# CaptureController
# ---------------------------------------------------------------------------


def test_start_capture_sets_engine_capture_mode(engine):
    controller = CaptureController(engine)
    assert controller.is_active() is False

    controller.start_capture()

    assert controller.is_active() is True
    assert engine._capture_mode is True  # noqa: SLF001 (whitebox check, documented as ok)


def test_capture_mode_suppresses_action_events_then_cancel_restores(engine):
    controller = CaptureController(engine)
    controller.start_capture()

    now = 1.0
    state = make_state(now, cross=True)
    events = engine.process(state, now)
    assert events == []  # suppressed: cross is bound, but capture mode is on

    now = 1.05  # release, still short press -> would be a "tap" if not suppressed
    state = make_state(now, cross=False)
    events = engine.process(state, now)
    assert events == []

    controller.cancel_capture()
    assert controller.is_active() is False

    # Now that capture mode is off, the same cross tap fires for real.
    now = 2.0
    state = make_state(now, cross=True)
    events = engine.process(state, now)
    assert events == []  # press alone never emits; tap fires on release

    now = 2.05
    state = make_state(now, cross=False)
    events = engine.process(state, now)
    assert len(events) == 1
    assert events[0].kind == "tap"
    assert events[0].target.command_id == "View.FitAll"


def test_confirm_capture_returns_last_raw_input(engine):
    controller = CaptureController(engine)
    controller.start_capture()

    # last_raw_input() needs a previous state to diff against.
    engine.process(make_state(1.0), 1.0)
    engine.process(make_state(1.5, triangle=True), 1.5)

    assert controller.poll_captured_input() is not None
    assert controller.poll_captured_input().control == "triangle"
    assert controller.poll_captured_input().kind == "press"

    result = controller.confirm_capture()

    assert result is not None
    assert result.control == "triangle"
    assert result.kind == "press"
    assert controller.is_active() is False


def test_cancel_capture_returns_nothing(engine):
    controller = CaptureController(engine)
    controller.start_capture()
    controller.cancel_capture()
    assert controller.is_active() is False
    # set_capture_mode(False) was actually called:
    assert engine._capture_mode is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# CaptureDialog
# ---------------------------------------------------------------------------


def test_capture_dialog_activates_capture_on_show(qapp, qtbot, engine):
    dialog = CaptureDialog(engine)
    qtbot.addWidget(dialog)

    assert engine._capture_mode is False  # noqa: SLF001

    dialog.show()
    qtbot.waitExposed(dialog)

    assert engine._capture_mode is True  # noqa: SLF001
    assert dialog.controller.is_active() is True

    dialog.close()


def test_capture_dialog_deactivates_capture_on_close_via_x(qapp, qtbot, engine):
    """Closing via the window's own close() (the "X" path), not a button."""

    dialog = CaptureDialog(engine)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert engine._capture_mode is True  # noqa: SLF001

    dialog.close()

    assert engine._capture_mode is False  # noqa: SLF001
    assert dialog.controller.is_active() is False


def test_capture_dialog_shows_and_confirms_captured_input(qapp, qtbot, engine):
    dialog = CaptureDialog(engine)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    ok_button = dialog.button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert ok_button.isEnabled() is False

    # Feed the engine a synthetic press while the dialog is capturing,
    # exactly like a real controller press would (through some other
    # feed path the daemon would normally own) -- then let the dialog's
    # own poll timer pick it up.
    engine.process(make_state(1.0), 1.0)
    engine.process(make_state(1.5, square=True), 1.5)

    dialog._poll()  # drive the poll synchronously instead of waiting on the QTimer

    assert ok_button.isEnabled() is True
    assert "square" in dialog.result_label.text()

    dialog.accept()

    assert dialog.captured_input is not None
    assert dialog.captured_input.control == "square"
    assert engine._capture_mode is False  # noqa: SLF001


def test_capture_dialog_cancel_discards_captured_input(qapp, qtbot, engine):
    dialog = CaptureDialog(engine)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    engine.process(make_state(1.0), 1.0)
    engine.process(make_state(1.5, circle=True), 1.5)
    dialog._poll()

    dialog.reject()

    assert dialog.captured_input is None
    assert engine._capture_mode is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# DryRunPanel
# ---------------------------------------------------------------------------


def test_dry_run_panel_button_tap_feeds_daemon_and_logs_result(qapp, qtbot, daemon):
    panel = DryRunPanel(daemon)
    qtbot.addWidget(panel)

    feed_calls = []
    original_feed_state = daemon.feed_state

    def spy_feed_state(state, now=None):
        feed_calls.append(state)
        return original_feed_state(state, now=now)

    daemon.feed_state = spy_feed_state

    cross_button = panel.button_widgets["cross"]
    qtbot.mouseClick(cross_button, Qt.MouseButton.LeftButton)

    # One press frame + one release frame were fed.
    assert len(feed_calls) == 2
    assert feed_calls[0].buttons["cross"] is True
    assert feed_calls[1].buttons["cross"] is False

    # The cross -> catia_command("View.FitAll") binding should have fired
    # a real (non-skipped) dispatch on the release (tap) frame.
    assert daemon.catia.call_log[-1].method == "start_command"
    assert daemon.catia.call_log[-1].args == ("View.FitAll",)

    # The result log shows at least one non-skipped entry.
    log_texts = [panel.result_log.item(i).text() for i in range(panel.result_log.count())]
    assert any("skipped=False" in t for t in log_texts)


def test_dry_run_panel_stick_slider_feeds_daemon(qapp, qtbot, daemon):
    panel = DryRunPanel(daemon)
    qtbot.addWidget(panel)

    feed_calls = []
    original_feed_state = daemon.feed_state

    def spy_feed_state(state, now=None):
        feed_calls.append(state)
        return original_feed_state(state, now=now)

    daemon.feed_state = spy_feed_state

    panel.left_stick_x_slider.setValue(50)

    assert len(feed_calls) >= 1
    assert feed_calls[-1].left_stick[0] == pytest.approx(0.5)


def test_dry_run_panel_shows_fake_catia_call_log(qapp, qtbot, daemon):
    panel = DryRunPanel(daemon)
    qtbot.addWidget(panel)

    panel.button_widgets["cross"].click()

    panel.refresh_catia_log()
    catia_texts = [panel.catia_log.item(i).text() for i in range(panel.catia_log.count())]
    assert any("start_command" in t for t in catia_texts)
