"""Coordinator-level wiring: combines the independently-built GUI pieces
(``main_window.py``, ``tray.py``, ``capture.py``, ``dry_run_panel.py``)
into one running application. This is the module ``ds4catia.main``
imports ``run_gui`` from.

None of the three GUI modules this wires together import each other --
each was built and tested standalone against the daemon/engine/schema
contracts. This module is where they actually meet:

* The editor's "Captura en vivo / Dry-run" tab (a placeholder in
  ``main_window.py`` on its own) is replaced here with a real
  :class:`~ds4catia.gui.dry_run_panel.DryRunPanel` bound to the running
  ``daemon``.
* The tray's "Abrir configuracion..." action is connected to
  showing/raising the main window.
* The tray's "Salir" action is connected to a shutdown sequence that
  stops the daemon *before* quitting the Qt event loop.
* ``ensure_app_survives_window_close`` is applied once, here, to the
  single ``QApplication`` instance -- see ``gui/tray.py``'s docstring
  for why that call has to happen at the application level and isn't
  something the tray module can do by itself.

KNOWN LIMITATION (documented, not silently glossed over): the profile
editor loads its own in-memory copy of the profiles directory,
independent of the ``Profile`` objects already loaded into the running
``daemon.engine``. Saving an edited profile writes it back to disk
correctly, but the *running* engine keeps using the profiles it was
started with until the app is restarted. Live hot-reload of edited
profiles into a running engine is not implemented in this pass (see
docs/troubleshooting.md).
"""

from __future__ import annotations

import sys


class _DispatchRelay:
    """Marshals ``Ds4CatiaDaemon.on_dispatch`` calls (fired on the HID
    background thread) onto the Qt GUI thread, via a Qt signal.

    Qt signal emission is thread-safe: emitting from a non-GUI thread to
    a slot owned by a QObject that lives on the GUI thread is
    automatically delivered as a *queued* connection, so the connected
    slot still runs on the GUI thread even though ``emit()`` itself was
    called from elsewhere. This is the standard, correct way to get
    background-thread data into Qt widgets -- touching a widget directly
    from the HID thread would be unsafe.
    """

    def __init__(self, panel) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Emitter(QObject):
            resultsReady = Signal(object, object)

        self._emitter = _Emitter()
        self._emitter.resultsReady.connect(lambda _state, results: panel.log_external_results(results))

    def __call__(self, state, results) -> None:
        self._emitter.resultsReady.emit(state, results)


def run_gui(daemon) -> int:
    """Build and run the full GUI (main window + tray) for ``daemon``.

    Blocks until the Qt application quits (from the tray's "Salir", or
    the process being killed). Returns the process exit code.
    """

    from PySide6.QtWidgets import QApplication

    from ds4catia.gui.dry_run_panel import DryRunPanel
    from ds4catia.gui.main_window import MainWindow
    from ds4catia.gui.tray import TrayController, ensure_app_survives_window_close

    app = QApplication.instance() or QApplication(sys.argv)
    ensure_app_survives_window_close(app)

    window = MainWindow(profiles_dir=daemon.config.profiles_dir)

    # Swap the editor's placeholder dry-run tab (index 1, see
    # main_window.py's _build_placeholder_tab) for the real panel, now
    # that a daemon exists to drive it. Keep the same tab position/label
    # so this reads as "filling in" the placeholder, not adding a new tab.
    dry_run_panel = DryRunPanel(daemon, parent=window)
    placeholder_label = window.tabs.tabText(1)
    window.tabs.removeTab(1)
    window.tabs.insertTab(1, dry_run_panel, placeholder_label)

    # Relay real DS4 input (read on a background thread by the daemon)
    # into the same panel's log, so it reflects a real connected
    # controller, not only its own on-screen simulated buttons.
    relay = _DispatchRelay(dry_run_panel)
    daemon.on_dispatch = relay

    tray = TrayController(
        daemon,
        on_open_config=lambda: (window.show(), window.raise_(), window.activateWindow()),
    )

    def _shutdown() -> None:
        daemon.stop()
        app.quit()

    tray.quit_requested.connect(_shutdown)
    tray.show()

    daemon.start()
    window.show()

    try:
        return app.exec()
    finally:
        # Belt-and-suspenders: make sure the background threads are
        # asked to stop even if the event loop exits some other way
        # (e.g. a test driving this function directly).
        daemon.stop()
