"""Capture-and-confirm remap flow.

Implements the "press any control to bind it" UX pattern described in
``research_notes/.../mapping_config_ux.md`` (capture-and-confirm, with an
explicit "listening..." state), including the real bug it calls out:
Unity's input system has a documented failure mode where, during a
rebind capture, the raw controller input is *not* suppressed from the
live application -- so pressing the button you are trying to re-map also
fires whatever that button is *currently* bound to. ``MappingEngine``
already guards against exactly this class of bug: while
``set_capture_mode(True)`` is active, ``process()`` never emits
``ActionEvent``\\ s (ordinary bindings) or layer activation side effects
(see ``engine.py``'s module docstring, point 9). This module's job is
only to flip that switch at the right moments -- open -> on, and
confirm/cancel/close (including the window-manager "X") -> off -- and to
never leave it stuck on if the dialog goes away unexpectedly.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from ds4catia.mapping.engine import MappingEngine, RawInputEvent

__all__ = ["CaptureController", "CaptureDialog"]


class CaptureController:
    """Drives ``MappingEngine``'s capture mode for a remap-capture UI.

    Pure logic, no Qt dependency -- so it can be unit tested (and reused
    by a non-dialog UI) without constructing any widget.
    """

    def __init__(self, engine: MappingEngine) -> None:
        self.engine = engine
        self._active = False

    def is_active(self) -> bool:
        return self._active

    def start_capture(self) -> None:
        """Enter capture mode: live bindings/layers stop firing."""

        self.engine.set_capture_mode(True)
        self._active = True

    def poll_captured_input(self) -> Optional[RawInputEvent]:
        """Return the most recent physical input seen by the engine.

        Independent of any binding -- works even for a control that is
        not mapped to anything yet, which is the whole point of a remap
        flow. Returns ``None`` if nothing has been observed (yet).
        """

        return self.engine.last_raw_input()

    def confirm_capture(self) -> Optional[RawInputEvent]:
        """Leave capture mode and return the last captured input, if any."""

        self.engine.set_capture_mode(False)
        self._active = False
        return self.engine.last_raw_input()

    def cancel_capture(self) -> None:
        """Leave capture mode without returning anything."""

        self.engine.set_capture_mode(False)
        self._active = False


class CaptureDialog(QDialog):
    """Modal "press the control you want to assign..." dialog.

    On open, starts capture via :class:`CaptureController`; periodically
    polls for a captured input via a ``QTimer`` and, once one arrives,
    shows it and enables "Aceptar". Capture mode is always turned back
    off when the dialog goes away, however it goes away (Aceptar,
    Cancelar, or the window's own close button), via ``closeEvent`` and
    ``reject``/``done`` -- see :meth:`_deactivate_capture`.
    """

    #: Polling interval, in ms, for checking ``poll_captured_input()``.
    #: Deliberately short: this is a local in-process poll of an
    #: in-memory field, not an I/O call, and the UI should feel
    #: responsive the instant the controller input actually changes.
    POLL_INTERVAL_MS = 50

    def __init__(self, engine: MappingEngine, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Capturar entrada")
        self.controller = CaptureController(engine)
        self.captured_input: Optional[RawInputEvent] = None

        self.status_label = QLabel("Pulsa el control que quieras asignar...")
        self.result_label = QLabel("")

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.result_label)
        layout.addWidget(self.button_box)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)

        self._capture_deactivated = False

    # -- lifecycle ------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override naming)
        super().showEvent(event)
        if not self.controller.is_active():
            self.controller.start_capture()
            self._capture_deactivated = False
            self._poll_timer.start()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override naming)
        # Covers the window-manager "X" / Escape / any path that does not
        # go through accept()/reject() -- capture must never stay on
        # after the dialog is gone.
        self._deactivate_capture(confirm=False)
        super().closeEvent(event)

    def accept(self) -> None:
        self._deactivate_capture(confirm=True)
        super().accept()

    def reject(self) -> None:
        self._deactivate_capture(confirm=False)
        super().reject()

    def _deactivate_capture(self, confirm: bool) -> None:
        self._poll_timer.stop()
        if self._capture_deactivated:
            return
        self._capture_deactivated = True
        if confirm:
            self.captured_input = self.controller.confirm_capture()
        else:
            self.controller.cancel_capture()

    # -- polling ----------------------------------------------------------------

    def _poll(self) -> None:
        raw = self.controller.poll_captured_input()
        if raw is None:
            return
        self.result_label.setText(f"Detectado: {raw.control} ({raw.kind})")
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
