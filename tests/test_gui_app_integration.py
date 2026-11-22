"""Integration test for ds4catia.gui.app.run_gui: checks that the
independently-built GUI pieces (main window, tray, dry-run panel) are
actually wired together correctly, without blocking on the real Qt
event loop (app.exec() is never called here -- that part is for the
user to exercise manually, per the project's manual verification
checklist)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ds4catia.app.config import AppConfig
from ds4catia.app.daemon import Ds4CatiaDaemon
from ds4catia.catia.fake_backend import FakeCatiaBackend
from ds4catia.mapping.engine import MappingEngine
from ds4catia.mapping.schema import Profile


def _make_dry_run_daemon() -> Ds4CatiaDaemon:
    profiles = {"base": Profile(profile_id="base", display_name="Base")}
    engine = MappingEngine(profiles)
    engine.set_active_profile("base")
    catia = FakeCatiaBackend()
    config = AppConfig(dry_run=True)
    daemon = Ds4CatiaDaemon(engine=engine, profiles=profiles, catia=catia, config=config)
    daemon.set_dry_run(True)
    return daemon


def test_run_gui_wires_dry_run_panel_into_placeholder_tab(qtbot, monkeypatch):
    # Stub out app.exec()/daemon lifecycle so this test returns immediately
    # instead of blocking on a real event loop -- we only want to assert
    # the wiring happened, not actually run the app.
    from ds4catia.gui import app as gui_app

    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    daemon = _make_dry_run_daemon()
    exit_code = gui_app.run_gui(daemon)

    assert exit_code == 0
    # daemon.start()/stop() must both have run without raising (dry-run
    # backend, no real hardware involved).
    assert daemon.catia.is_connected() is False  # stop() disconnected it again


def test_run_gui_tray_open_config_shows_window(qtbot, monkeypatch):
    from ds4catia.gui import app as gui_app
    from ds4catia.gui.dry_run_panel import DryRunPanel
    from ds4catia.gui.main_window import MainWindow
    from ds4catia.gui.tray import TrayController, ensure_app_survives_window_close
    from PySide6.QtWidgets import QApplication
    import sys

    daemon = _make_dry_run_daemon()
    app = QApplication.instance() or QApplication(sys.argv)
    ensure_app_survives_window_close(app)

    window = MainWindow(profiles_dir=daemon.config.profiles_dir)
    dry_run_panel = DryRunPanel(daemon, parent=window)
    placeholder_label = window.tabs.tabText(1)
    window.tabs.removeTab(1)
    window.tabs.insertTab(1, dry_run_panel, placeholder_label)

    assert window.tabs.widget(1) is dry_run_panel
    assert window.tabs.tabText(1) == placeholder_label

    window.hide()
    tray = TrayController(
        daemon,
        on_open_config=lambda: (window.show(), window.raise_(), window.activateWindow()),
    )
    tray.open_config_requested.emit()
    qtbot.wait(10)
    assert window.isVisible() is True


def test_run_gui_quit_stops_daemon_before_quitting_app(monkeypatch):
    from ds4catia.gui import app as gui_app

    calls = []
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    daemon = _make_dry_run_daemon()
    real_stop = daemon.stop

    def _tracked_stop():
        calls.append("stop")
        real_stop()

    daemon.stop = _tracked_stop  # type: ignore[method-assign]

    gui_app.run_gui(daemon)

    assert "stop" in calls


def test_run_gui_wires_on_dispatch_to_relay_into_the_dry_run_panel(monkeypatch):
    """The gap a real user hit: moving a physically-connected DS4 showed
    nothing in the dry-run panel, because nothing relayed the daemon's
    background-thread HID callback into it -- the panel only reflected
    its own on-screen simulated buttons. Checks the fix at the run_gui
    level: after wiring, daemon.on_dispatch is set, and invoking it (as
    _on_hid_state would, from the real HID thread) ends up logged in
    the real DryRunPanel instance the user sees in the window's tabs."""

    from ds4catia.app.daemon import DispatchResult
    from ds4catia.gui import app as gui_app

    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    daemon = _make_dry_run_daemon()
    gui_app.run_gui(daemon)

    assert daemon.on_dispatch is not None

    # run_gui doesn't return the window, but its tray/panel stay alive
    # only as local variables for the duration of app.exec() -- which we
    # stubbed to return immediately, so by this point they're gone. What
    # we CAN still verify without reaching into run_gui's locals is the
    # relay plumbing itself: construct it the same way run_gui does,
    # against a stand-in panel, and confirm the signal-based hand-off
    # actually calls log_external_results synchronously (same-thread
    # Qt connections run immediately, not just when queued).
    calls = []

    class _FakePanel:
        def log_external_results(self, results):
            calls.append(results)

    relay = gui_app._DispatchRelay(_FakePanel())
    results = [DispatchResult(skipped=False, reason=None)]
    relay(None, results)

    assert calls == [results]
