"""Dry-run test panel: simulate a DS4 controller without real hardware.

``DryRunPanel`` is a self-contained widget with a handful of buttons
(cross/circle/square/triangle) and stick sliders (left stick X/Y) that
build synthetic ``DS4State`` snapshots and feed them straight into a
``Ds4CatiaDaemon`` via :meth:`Ds4CatiaDaemon.feed_state`. It then shows,
in a scrolling log, the ``DispatchResult`` list that call returns (one
line per resolved ``ActionEvent``, with its skip/reason), so a user can
see exactly what the current profile *would* do for a given input --
and, when ``daemon.catia`` is a ``FakeCatiaBackend`` (the normal case
for a dry run; a real ``ComCatiaBackend`` dry run would have nothing
useful in ``call_log``), what it *would have sent to CATIA*, via a
second log fed from ``daemon.catia.call_log``.

This widget is deliberately independent of ``gui/main_window.py`` --
construct it with any ``Ds4CatiaDaemon`` (including one built in a test
with ``FakeCatiaBackend`` + a minimal in-memory ``Profile``) and it
works standalone; the coordinator decides later where it lives inside
the full application window.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ds4catia.hid.ds4_report import DS4State

__all__ = ["DryRunPanel"]

# Buttons exposed in the panel's quick-tap row. (A full button list would
# cover every DS4_BUTTON_CONTROLS entry; these four are the task's
# explicit minimum and keep the widget small -- extending the row is a
# matter of adding more entries here.)
_TAP_BUTTONS: "list[tuple[str, str]]" = [
    ("cross", "Cross (X)"),
    ("circle", "Circle"),
    ("square", "Square"),
    ("triangle", "Triangle"),
]

_RELEASED_BUTTONS: "dict[str, bool]" = {
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

# A real tap is "pressed, then released a little while later", not
# "pressed and released at the exact same instant" -- the engine's
# tap/hold classification is duration-based, so the press/release pair
# fed here must carry two distinct timestamps for a sub-``tap_ms``
# duration to even be measurable as >= 0.
_TAP_RELEASE_DELAY_S = 0.05


class DryRunPanel(QWidget):
    """Simulate DS4 input and drive ``daemon.feed_state()`` with it."""

    def __init__(self, daemon, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.daemon = daemon

        # Running synthetic clock, monotonically increasing -- the engine
        # cares about elapsed time between frames (tap/hold duration,
        # analog-change tracking), not wall-clock time, so a simple
        # incrementing counter is both deterministic (handy for tests)
        # and physically meaningful.
        self._clock: float = 1.0

        self._buttons: "dict[str, bool]" = dict(_RELEASED_BUTTONS)
        self._left_stick: "tuple[float, float]" = (0.0, 0.0)
        self._right_stick: "tuple[float, float]" = (0.0, 0.0)

        self.button_widgets: "dict[str, QPushButton]" = {}

        layout = QVBoxLayout(self)

        buttons_box = QGroupBox("Botones (tap)")
        buttons_row = QHBoxLayout(buttons_box)
        for control, label in _TAP_BUTTONS:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, c=control: self._on_button_tap(c))
            buttons_row.addWidget(btn)
            self.button_widgets[control] = btn
        layout.addWidget(buttons_box)

        sticks_box = QGroupBox("Stick izquierdo")
        sticks_layout = QHBoxLayout(sticks_box)

        sticks_layout.addWidget(QLabel("X"))
        self.left_stick_x_slider = _make_axis_slider()
        self.left_stick_x_slider.valueChanged.connect(self._on_left_stick_changed)
        sticks_layout.addWidget(self.left_stick_x_slider)

        sticks_layout.addWidget(QLabel("Y"))
        self.left_stick_y_slider = _make_axis_slider()
        self.left_stick_y_slider.valueChanged.connect(self._on_left_stick_changed)
        sticks_layout.addWidget(self.left_stick_y_slider)

        layout.addWidget(sticks_box)

        layout.addWidget(QLabel("Resultados de dispatch:"))
        self.result_log = QListWidget()
        layout.addWidget(self.result_log)

        layout.addWidget(QLabel("Llamadas a CATIA (FakeCatiaBackend.call_log):"))
        self.catia_log = QListWidget()
        layout.addWidget(self.catia_log)

        self.refresh_catia_log_button = QPushButton("Actualizar log de CATIA")
        self.refresh_catia_log_button.clicked.connect(self.refresh_catia_log)
        layout.addWidget(self.refresh_catia_log_button)

        self.refresh_catia_log()

    # -- synthetic input construction ------------------------------------------

    def _next_time(self) -> float:
        self._clock += _TAP_RELEASE_DELAY_S
        return self._clock

    def _build_state(self, timestamp: float) -> DS4State:
        return DS4State(
            buttons=dict(self._buttons),
            left_stick=self._left_stick,
            right_stick=self._right_stick,
            l2_analog=0.0,
            r2_analog=0.0,
            dpad=None,
            touch_points=[],
            gyro=(0, 0, 0),
            accel=(0, 0, 0),
            battery_pct=100,
            timestamp=timestamp,
        )

    def _feed(self) -> "list":
        now = self._next_time()
        state = self._build_state(now)
        results = self.daemon.feed_state(state, now=now)
        for result in results:
            self.result_log.addItem(f"skipped={result.skipped} reason={result.reason}")
        self.refresh_catia_log()
        return results

    # -- button row -------------------------------------------------------------

    def _on_button_tap(self, control: str) -> None:
        """Simulate a quick tap: press, then release shortly after."""

        self._buttons[control] = True
        self._feed()
        self._buttons[control] = False
        self._feed()

    # -- stick sliders ------------------------------------------------------------

    def _on_left_stick_changed(self, _value: int) -> None:
        self._left_stick = (
            _slider_to_axis(self.left_stick_x_slider),
            _slider_to_axis(self.left_stick_y_slider),
        )
        self._feed()

    # -- CATIA call log -----------------------------------------------------------

    def refresh_catia_log(self) -> None:
        """Re-populate the CATIA call log from ``daemon.catia.call_log``.

        A no-op (empty log) if ``daemon.catia`` has no ``call_log``
        attribute at all -- only ``FakeCatiaBackend`` exposes one; a
        real ``ComCatiaBackend`` simply shows nothing here.
        """

        self.catia_log.clear()
        call_log = getattr(self.daemon.catia, "call_log", None)
        if not call_log:
            return
        for record in call_log:
            as_dict = record.as_dict() if hasattr(record, "as_dict") else record
            self.catia_log.addItem(str(as_dict))


def _make_axis_slider() -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setMinimum(-100)
    slider.setMaximum(100)
    slider.setValue(0)
    return slider


def _slider_to_axis(slider: QSlider) -> float:
    return slider.value() / 100.0
