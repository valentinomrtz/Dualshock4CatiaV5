"""System tray icon for the DS4->CATIA daemon.

``build_tray_icon(daemon)`` builds a :class:`QSystemTrayIcon` with a
context menu to pause/resume the daemon, see the active mapping profile,
request the main configuration window to open, and quit the application.
This module owns only the tray icon and its menu -- it knows nothing
about ``gui/main_window.py`` (the profile/layer editor another agent is
building) beyond an injectable callback/signal the coordinator wires up
later.

IMPORTANT -- keeping the app alive with the main window closed
================================================================
A tray-resident Qt application must be able to survive the user closing
its main window with the title-bar "X" (that is the whole point of a
tray icon: the app keeps running in the background, reachable from the
tray, even with no window open). By default PySide6/Qt quits the
*entire application* as soon as the last visible top-level window is
closed, independently of anything this module does.

**This module's tray icon has no control over that behaviour on its
own.** The fix is an *application-level* setting that must be applied
once, before (or right after) the ``QApplication`` is constructed:

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

:func:`ensure_app_survives_window_close` below is a tiny convenience
wrapper around exactly that one call -- call it (or set the flag
yourself) on whatever ``QApplication`` instance ends up hosting this
tray icon and the (separately-built) main window. **The coordinator
integrating this tray with ``gui/main_window.py`` must not forget this
step**, or closing the main window will silently kill the whole
daemon, tray icon included, defeating the purpose of having a tray at
all.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

__all__ = [
    "TrayController",
    "build_tray_icon",
    "ensure_app_survives_window_close",
]


def ensure_app_survives_window_close(app: QApplication) -> None:
    """Make ``app`` keep running after its last visible window closes.

    This is the application-level counterpart a tray icon needs in order
    to be useful: without it, closing the main configuration window (via
    the title-bar X, Alt+F4, etc.) quits the whole process, tray icon and
    background daemon included. See the module docstring for details.
    """

    app.setQuitOnLastWindowClosed(False)


class TrayController(QObject):
    """Owns the :class:`QSystemTrayIcon` + menu wired to a ``Ds4CatiaDaemon``.

    Exposed as a small controller object (rather than a bare function
    returning only the icon) so the coordinator can connect to
    ``open_config_requested`` and ``quit_requested``, and so tests can
    poke at ``self.tray``/``self.menu`` directly.
    """

    #: Emitted when the user picks "Abrir configuracion..." from the menu.
    #: The coordinator connects this to whatever shows/raises the main
    #: window (``gui/main_window.py``); this module never imports it.
    open_config_requested = Signal()

    #: Emitted when the user picks "Salir". Connect this to
    #: ``QApplication.quit`` (or your own shutdown sequence, e.g. one that
    #: also calls ``daemon.stop()`` first) -- this controller does not call
    #: ``daemon.stop()`` or quit the application itself, so a caller that
    #: wants a clean shutdown must handle both.
    quit_requested = Signal()

    def __init__(
        self,
        daemon,
        parent: Optional[QObject] = None,
        on_open_config: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.daemon = daemon

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(_default_icon())
        self.tray.setToolTip("DS4 -> CATIA V5")

        self.menu = QMenu()

        self.pause_resume_action = QAction(self.menu)
        self.pause_resume_action.triggered.connect(self._on_pause_resume_triggered)
        self.menu.addAction(self.pause_resume_action)

        self.profile_action = QAction(self.menu)
        self.profile_action.setEnabled(False)  # informational only, see docstring
        self.menu.addAction(self.profile_action)

        self.menu.addSeparator()

        self.open_config_action = QAction("Abrir configuracion...", self.menu)
        self.open_config_action.triggered.connect(self._on_open_config_triggered)
        self.menu.addAction(self.open_config_action)

        self.menu.addSeparator()

        self.quit_action = QAction("Salir", self.menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.menu)

        if on_open_config is not None:
            self.open_config_requested.connect(on_open_config)

        self.refresh()

    # -- menu actions -------------------------------------------------------

    def _on_pause_resume_triggered(self) -> None:
        if self.daemon.is_paused():
            self.daemon.resume()
        else:
            self.daemon.pause()
        self.refresh()

    def _on_open_config_triggered(self) -> None:
        self.open_config_requested.emit()

    # -- state sync -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-sync the menu's labels with the daemon's current state.

        Called automatically after pause/resume; call it yourself if the
        active profile or pause state can change from elsewhere (e.g. the
        workbench auto-switch poll, or the main window's profile editor)
        and the tray menu needs to reflect that the next time it opens.
        """

        if self.daemon.is_paused():
            self.pause_resume_action.setText("Reanudar")
        else:
            self.pause_resume_action.setText("Pausar")

        profile_id = self.daemon.engine.active_profile_id
        label = profile_id if profile_id is not None else "(ninguno)"
        self.profile_action.setText(f"Perfil activo: {label}")

    def show(self) -> None:
        self.tray.show()


def _default_icon() -> QIcon:
    # A real deployment should ship a proper .ico/.png resource; this
    # standard-icon fallback keeps the tray icon constructible (and
    # testable headlessly) with zero bundled assets. The coordinator can
    # call ``tray.setIcon(...)`` with a real icon after construction.
    style = QApplication.style()
    if style is not None:
        return style.standardIcon(style.StandardPixmap.SP_ComputerIcon)
    return QIcon()


def build_tray_icon(daemon, parent: Optional[QObject] = None) -> QSystemTrayIcon:
    """Build and return the :class:`QSystemTrayIcon` for ``daemon``.

    This is the simple functional entry point the task brief asks for;
    for access to the signals (``open_config_requested``,
    ``quit_requested``) or to force a menu refresh later, keep a
    reference to the :class:`TrayController` instead via
    ``TrayController(daemon, parent=...)`` and read its ``.tray``.
    The returned ``QSystemTrayIcon`` keeps the controller alive as its
    Qt-parented child, so it is safe to drop the controller reference.
    """

    controller = TrayController(daemon, parent=parent)
    # Keep the controller reachable from the icon object itself (Qt does
    # not do this for us), so callers who only kept `tray` around do not
    # lose access to the menu/signals.
    controller.tray._ds4catia_controller = controller  # type: ignore[attr-defined]
    return controller.tray
