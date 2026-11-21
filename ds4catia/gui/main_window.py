"""Profile / layer editor GUI for ds4catia.

This module implements the "configuration" half of the application's GUI: a
window that lets a user browse the mapping profiles shipped in
``ds4catia/mapping/profiles/`` (or any other directory), inspect and edit the
``Binding``/``Layer`` objects of a selected ``Profile`` (see
``ds4catia.mapping.schema``), and import/export/save profiles as JSON.

It intentionally does NOT implement:

- the system tray integration (``ds4catia/gui/tray.py``),
- the live capture-and-confirm-against-the-controller flow, or
- the dry-run/live-preview panel

(``ds4catia/gui/capture.py``) -- those are owned by a different module/agent.
This window exposes an empty placeholder tab for that functionality so the
overall window layout reads as complete, without implementing any of its
behaviour.

The module's only required public entry point is :func:`build_main_window`,
which constructs (but does not show or exec) a ``QMainWindow`` -- this lets
both tests and the application's future ``run_gui()`` entry point drive the
window without depending on a ``main()``/``exec()`` call living here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from pydantic import ValidationError

from ds4catia.mapping.schema import (
    VALID_DS4_CONTROLS,
    ActionTarget,
    AnalogModifiers,
    AppControlTarget,
    Binding,
    CatiaCameraTarget,
    CatiaCommandTarget,
    CatiaMacroTarget,
    CatiaSelectionTarget,
    DigitalModifiers,
    InputRef,
    KeyboardTarget,
    Layer,
    LayerActivation,
    LayerControlTarget,
    MouseButtonTarget,
    MouseMoveTarget,
    Profile,
    ProfileValidationError,
    dump_profile,
    load_profile,
)

__all__ = ["MainWindow", "build_main_window", "DEFAULT_PROFILES_DIR"]


DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent.parent / "mapping" / "profiles"

#: The 9 ``ActionTarget`` variants, in the order offered in the editor's
#: "target type" combo box, paired with a human-readable label.
TARGET_TYPE_LABELS: list[tuple[str, str]] = [
    ("catia_command", "CATIA Command"),
    ("catia_camera", "CATIA Camera"),
    ("catia_selection", "CATIA Selection"),
    ("catia_macro", "CATIA Macro"),
    ("keyboard", "Keyboard"),
    ("mouse_move", "Mouse Move"),
    ("mouse_button", "Mouse Button"),
    ("layer_control", "Layer Control"),
    ("app_control", "App Control"),
]

SORTED_CONTROLS: list[str] = sorted(VALID_DS4_CONTROLS)

CURVE_OPTIONS: list[str] = [
    "linear",
    "quadratic",
    "cubic",
    "ease_out_quad",
    "ease_out_cubic",
    "custom_bezier",
]


# ---------------------------------------------------------------------------
# Human-readable summaries (used by the bindings tree)
# ---------------------------------------------------------------------------


def summarize_target(target: ActionTarget) -> str:
    """Return a short, human-readable description of an ``ActionTarget``."""

    kind = target.type
    if kind == "catia_command":
        return f"CATIA Command: {target.command_id}"
    if kind == "catia_camera":
        axis = f" axis={tuple(target.axis)}" if target.axis is not None else ""
        return f"CATIA Camera: {target.operation}{axis}"
    if kind == "catia_selection":
        query = f" ({target.query})" if target.query else ""
        return f"CATIA Selection: {target.operation}{query}"
    if kind == "catia_macro":
        return f"CATIA Macro: {target.macro_name}"
    if kind == "keyboard":
        return "Keyboard: " + "+".join(target.keys)
    if kind == "mouse_move":
        return f"Mouse Move ({target.axis})"
    if kind == "mouse_button":
        return f"Mouse Button: {target.button}"
    if kind == "layer_control":
        return f"Layer Control: {target.layer_id} ({target.mode})"
    if kind == "app_control":
        return f"App Control: {target.action}"
    return kind  # pragma: no cover - exhaustive over the 9 known variants


def summarize_modifiers(binding: Binding) -> str:
    """Return a short, human-readable description of a binding's modifiers."""

    parts: list[str] = []
    if binding.analog is not None:
        a = binding.analog
        parts.append(
            f"analog(deadzone={a.deadzone:g}, sensitivity={a.sensitivity:g}, curve={a.curve})"
        )
    if binding.digital is not None:
        d = binding.digital
        parts.append(f"digital(tap_ms={d.tap_ms}, hold_ms={d.hold_ms})")
    return "; ".join(parts) if parts else "-"


# ---------------------------------------------------------------------------
# Profile directory loading
# ---------------------------------------------------------------------------


def load_profiles_from_dir(
    profiles_dir: str | os.PathLike[str],
) -> tuple[dict[str, Profile], dict[str, Path], list[str]]:
    """Load every ``*.json`` profile file in ``profiles_dir``.

    Returns a tuple ``(profiles, paths, errors)``:

    - ``profiles``: ``profile_id -> Profile`` for every file that parsed
      and validated successfully.
    - ``paths``: ``profile_id -> Path`` of the file it was loaded from.
    - ``errors``: human-readable ``"<filename>: <message>"`` strings for
      every file that failed to load, so the caller can surface them
      without the whole directory scan failing.
    """

    profiles_dir = Path(profiles_dir)
    profiles: dict[str, Profile] = {}
    paths: dict[str, Path] = {}
    errors: list[str] = []

    if not profiles_dir.is_dir():
        return profiles, paths, errors

    for path in sorted(profiles_dir.glob("*.json")):
        try:
            profile = load_profile(path)
        except ProfileValidationError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        profiles[profile.profile_id] = profile
        paths[profile.profile_id] = path

    return profiles, paths, errors


# ---------------------------------------------------------------------------
# Binding edit dialog
# ---------------------------------------------------------------------------


class BindingEditDialog(QDialog):
    """Dialog to create/edit one ``Binding``: input control, target, modifiers."""

    def __init__(self, binding: Binding | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit binding" if binding is not None else "Add binding")
        self._result_binding: Binding | None = None

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.id_edit = QLineEdit(binding.id if binding is not None else "")
        form.addRow("Binding id:", self.id_edit)

        self.control_combo = QComboBox()
        self.control_combo.addItems(SORTED_CONTROLS)
        if binding is not None and binding.input.control in SORTED_CONTROLS:
            self.control_combo.setCurrentText(binding.input.control)
        form.addRow("Input control:", self.control_combo)
        layout.addLayout(form)

        # --- target type + per-type pages -----------------------------
        self.type_combo = QComboBox()
        for type_id, label in TARGET_TYPE_LABELS:
            self.type_combo.addItem(label, userData=type_id)
        target_form = QFormLayout()
        target_form.addRow("Target type:", self.type_combo)
        layout.addLayout(target_form)

        self.target_stack = QStackedWidget()
        self._target_pages: dict[str, QWidget] = {}
        self._build_target_pages()
        layout.addWidget(self.target_stack)

        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

        initial_type = binding.target.type if binding is not None else "catia_command"
        index = self.type_combo.findData(initial_type)
        self.type_combo.setCurrentIndex(max(index, 0))
        self._on_type_changed(self.type_combo.currentIndex())
        if binding is not None:
            self._load_target_into_page(binding.target)

        # --- modifiers ---------------------------------------------------
        self.analog_box = QGroupBox("Analog modifiers")
        self.analog_box.setCheckable(True)
        self.analog_box.setChecked(binding is not None and binding.analog is not None)
        analog_form = QFormLayout(self.analog_box)
        self.deadzone_spin = self._double_spin(0.0, 1.0, 0.01, 2)
        self.anti_deadzone_spin = self._double_spin(0.0, 1.0, 0.01, 2)
        self.sensitivity_spin = self._double_spin(0.01, 10.0, 0.05, 2)
        self.sensitivity_spin.setValue(1.0)
        self.curve_combo = QComboBox()
        self.curve_combo.addItems(CURVE_OPTIONS)
        self.curve_params_edit = QLineEdit()
        self.curve_params_edit.setPlaceholderText('optional JSON, e.g. {"k": 1.0}')
        if binding is not None and binding.analog is not None:
            a = binding.analog
            self.deadzone_spin.setValue(a.deadzone)
            self.anti_deadzone_spin.setValue(a.anti_deadzone)
            self.sensitivity_spin.setValue(a.sensitivity)
            self.curve_combo.setCurrentText(a.curve)
            if a.curve_params is not None:
                self.curve_params_edit.setText(json.dumps(a.curve_params))
        analog_form.addRow("Deadzone:", self.deadzone_spin)
        analog_form.addRow("Anti-deadzone:", self.anti_deadzone_spin)
        analog_form.addRow("Sensitivity:", self.sensitivity_spin)
        analog_form.addRow("Curve:", self.curve_combo)
        analog_form.addRow("Curve params:", self.curve_params_edit)
        layout.addWidget(self.analog_box)

        self.digital_box = QGroupBox("Digital modifiers")
        self.digital_box.setCheckable(True)
        self.digital_box.setChecked(binding is not None and binding.digital is not None)
        digital_form = QFormLayout(self.digital_box)
        self.tap_ms_spin = QSpinBox()
        self.tap_ms_spin.setRange(1, 60_000)
        self.tap_ms_spin.setValue(180)
        self.hold_ms_spin = QSpinBox()
        self.hold_ms_spin.setRange(1, 60_000)
        self.hold_ms_spin.setValue(400)
        if binding is not None and binding.digital is not None:
            self.tap_ms_spin.setValue(binding.digital.tap_ms)
            self.hold_ms_spin.setValue(binding.digital.hold_ms)
        digital_form.addRow("Tap (ms):", self.tap_ms_spin)
        digital_form.addRow("Hold (ms):", self.hold_ms_spin)
        layout.addWidget(self.digital_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _double_spin(minimum: float, maximum: float, step: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        return spin

    # -- per-target-type pages -----------------------------------------

    def _build_target_pages(self) -> None:
        # catia_command
        page = QWidget()
        form = QFormLayout(page)
        edit = QLineEdit()
        form.addRow("Command id:", edit)
        self._target_pages["catia_command"] = page
        page.command_id_edit = edit  # type: ignore[attr-defined]
        self.target_stack.addWidget(page)

        # catia_camera
        page = QWidget()
        form = QFormLayout(page)
        op_combo = QComboBox()
        op_combo.addItems(["rotate", "translate", "zoom"])
        form.addRow("Operation:", op_combo)
        axis_check = QCheckBox("Set axis vector")
        form.addRow(axis_check)
        axis_x = self._double_spin(-1.0, 1.0, 0.1, 2)
        axis_y = self._double_spin(-1.0, 1.0, 0.1, 2)
        axis_z = self._double_spin(-1.0, 1.0, 0.1, 2)
        form.addRow("Axis X:", axis_x)
        form.addRow("Axis Y:", axis_y)
        form.addRow("Axis Z:", axis_z)
        page.operation_combo = op_combo  # type: ignore[attr-defined]
        page.axis_check = axis_check  # type: ignore[attr-defined]
        page.axis_x = axis_x  # type: ignore[attr-defined]
        page.axis_y = axis_y  # type: ignore[attr-defined]
        page.axis_z = axis_z  # type: ignore[attr-defined]
        self._target_pages["catia_camera"] = page
        self.target_stack.addWidget(page)

        # catia_selection
        page = QWidget()
        form = QFormLayout(page)
        op_combo = QComboBox()
        op_combo.addItems(["add", "clear", "search"])
        query_edit = QLineEdit()
        query_edit.setPlaceholderText("optional query (used by 'search')")
        form.addRow("Operation:", op_combo)
        form.addRow("Query:", query_edit)
        page.operation_combo = op_combo  # type: ignore[attr-defined]
        page.query_edit = query_edit  # type: ignore[attr-defined]
        self._target_pages["catia_selection"] = page
        self.target_stack.addWidget(page)

        # catia_macro
        page = QWidget()
        form = QFormLayout(page)
        edit = QLineEdit()
        form.addRow("Macro name:", edit)
        page.macro_name_edit = edit  # type: ignore[attr-defined]
        self._target_pages["catia_macro"] = page
        self.target_stack.addWidget(page)

        # keyboard
        page = QWidget()
        form = QFormLayout(page)
        edit = QLineEdit()
        edit.setPlaceholderText("e.g. Ctrl+A  (keys separated by '+')")
        form.addRow("Keys:", edit)
        page.keys_edit = edit  # type: ignore[attr-defined]
        self._target_pages["keyboard"] = page
        self.target_stack.addWidget(page)

        # mouse_move
        page = QWidget()
        form = QFormLayout(page)
        axis_combo = QComboBox()
        axis_combo.addItems(["x", "y", "both"])
        form.addRow("Axis:", axis_combo)
        page.axis_combo = axis_combo  # type: ignore[attr-defined]
        self._target_pages["mouse_move"] = page
        self.target_stack.addWidget(page)

        # mouse_button
        page = QWidget()
        form = QFormLayout(page)
        button_combo = QComboBox()
        button_combo.addItems(["left", "right", "middle"])
        form.addRow("Button:", button_combo)
        page.button_combo = button_combo  # type: ignore[attr-defined]
        self._target_pages["mouse_button"] = page
        self.target_stack.addWidget(page)

        # layer_control
        page = QWidget()
        form = QFormLayout(page)
        layer_id_edit = QLineEdit()
        mode_combo = QComboBox()
        mode_combo.addItems(["hold", "toggle"])
        form.addRow("Layer id:", layer_id_edit)
        form.addRow("Mode:", mode_combo)
        page.layer_id_edit = layer_id_edit  # type: ignore[attr-defined]
        page.mode_combo = mode_combo  # type: ignore[attr-defined]
        self._target_pages["layer_control"] = page
        self.target_stack.addWidget(page)

        # app_control
        page = QWidget()
        form = QFormLayout(page)
        action_combo = QComboBox()
        action_combo.addItems(["pause", "resume", "next_profile", "prev_profile"])
        form.addRow("Action:", action_combo)
        page.action_combo = action_combo  # type: ignore[attr-defined]
        self._target_pages["app_control"] = page
        self.target_stack.addWidget(page)

    def _on_type_changed(self, _index: int) -> None:
        type_id = self.type_combo.currentData()
        page = self._target_pages[type_id]
        self.target_stack.setCurrentWidget(page)

    def _load_target_into_page(self, target: ActionTarget) -> None:
        page = self._target_pages[target.type]
        if target.type == "catia_command":
            page.command_id_edit.setText(target.command_id)  # type: ignore[attr-defined]
        elif target.type == "catia_camera":
            page.operation_combo.setCurrentText(target.operation)  # type: ignore[attr-defined]
            if target.axis is not None:
                page.axis_check.setChecked(True)  # type: ignore[attr-defined]
                page.axis_x.setValue(target.axis[0])  # type: ignore[attr-defined]
                page.axis_y.setValue(target.axis[1])  # type: ignore[attr-defined]
                page.axis_z.setValue(target.axis[2])  # type: ignore[attr-defined]
        elif target.type == "catia_selection":
            page.operation_combo.setCurrentText(target.operation)  # type: ignore[attr-defined]
            page.query_edit.setText(target.query or "")  # type: ignore[attr-defined]
        elif target.type == "catia_macro":
            page.macro_name_edit.setText(target.macro_name)  # type: ignore[attr-defined]
        elif target.type == "keyboard":
            page.keys_edit.setText("+".join(target.keys))  # type: ignore[attr-defined]
        elif target.type == "mouse_move":
            page.axis_combo.setCurrentText(target.axis)  # type: ignore[attr-defined]
        elif target.type == "mouse_button":
            page.button_combo.setCurrentText(target.button)  # type: ignore[attr-defined]
        elif target.type == "layer_control":
            page.layer_id_edit.setText(target.layer_id)  # type: ignore[attr-defined]
            page.mode_combo.setCurrentText(target.mode)  # type: ignore[attr-defined]
        elif target.type == "app_control":
            page.action_combo.setCurrentText(target.action)  # type: ignore[attr-defined]

    def _build_target(self) -> ActionTarget:
        type_id = self.type_combo.currentData()
        page = self._target_pages[type_id]
        if type_id == "catia_command":
            return CatiaCommandTarget(command_id=page.command_id_edit.text().strip())  # type: ignore[attr-defined]
        if type_id == "catia_camera":
            axis = None
            if page.axis_check.isChecked():  # type: ignore[attr-defined]
                axis = (
                    page.axis_x.value(),  # type: ignore[attr-defined]
                    page.axis_y.value(),  # type: ignore[attr-defined]
                    page.axis_z.value(),  # type: ignore[attr-defined]
                )
            return CatiaCameraTarget(operation=page.operation_combo.currentText(), axis=axis)  # type: ignore[attr-defined]
        if type_id == "catia_selection":
            query = page.query_edit.text().strip() or None  # type: ignore[attr-defined]
            return CatiaSelectionTarget(operation=page.operation_combo.currentText(), query=query)  # type: ignore[attr-defined]
        if type_id == "catia_macro":
            return CatiaMacroTarget(macro_name=page.macro_name_edit.text().strip())  # type: ignore[attr-defined]
        if type_id == "keyboard":
            raw = page.keys_edit.text().strip()  # type: ignore[attr-defined]
            keys = [part.strip() for part in raw.split("+") if part.strip()]
            return KeyboardTarget(keys=keys)
        if type_id == "mouse_move":
            return MouseMoveTarget(axis=page.axis_combo.currentText())  # type: ignore[attr-defined]
        if type_id == "mouse_button":
            return MouseButtonTarget(button=page.button_combo.currentText())  # type: ignore[attr-defined]
        if type_id == "layer_control":
            return LayerControlTarget(
                layer_id=page.layer_id_edit.text().strip(),  # type: ignore[attr-defined]
                mode=page.mode_combo.currentText(),  # type: ignore[attr-defined]
            )
        if type_id == "app_control":
            return AppControlTarget(action=page.action_combo.currentText())  # type: ignore[attr-defined]
        raise ValueError(f"unknown target type {type_id!r}")  # pragma: no cover

    def _build_analog(self) -> AnalogModifiers | None:
        if not self.analog_box.isChecked():
            return None
        curve_params: dict[str, Any] | None = None
        raw = self.curve_params_edit.text().strip()
        if raw:
            curve_params = json.loads(raw)  # may raise json.JSONDecodeError
        return AnalogModifiers(
            deadzone=self.deadzone_spin.value(),
            anti_deadzone=self.anti_deadzone_spin.value(),
            sensitivity=self.sensitivity_spin.value(),
            curve=self.curve_combo.currentText(),
            curve_params=curve_params,
        )

    def _build_digital(self) -> DigitalModifiers | None:
        if not self.digital_box.isChecked():
            return None
        return DigitalModifiers(
            tap_ms=self.tap_ms_spin.value(),
            hold_ms=self.hold_ms_spin.value(),
        )

    def accept(self) -> None:  # noqa: D102 - Qt override
        binding_id = self.id_edit.text().strip()
        if not binding_id:
            QMessageBox.warning(self, "Invalid binding", "Binding id must not be empty.")
            return
        try:
            target = self._build_target()
            analog = self._build_analog()
            digital = self._build_digital()
            binding = Binding(
                id=binding_id,
                input=InputRef(control=self.control_combo.currentText()),
                target=target,
                analog=analog,
                digital=digital,
            )
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Invalid curve params", f"Not valid JSON: {exc}")
            return
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid binding", str(ProfileValidationError(exc)))
            return
        self._result_binding = binding
        super().accept()

    def get_binding(self) -> Binding:
        """Return the validated ``Binding`` built from the dialog's fields.

        Only meaningful after the dialog was accepted (``exec() ==
        QDialog.Accepted``).
        """

        assert self._result_binding is not None
        return self._result_binding


# ---------------------------------------------------------------------------
# Layer activation dialog (used for both "add layer" and "edit activation")
# ---------------------------------------------------------------------------


class LayerActivationDialog(QDialog):
    """Dialog to create a new ``Layer`` or edit an existing one's activation."""

    def __init__(self, layer: Layer | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit layer" if layer is not None else "Add layer")
        self._result: tuple[str, LayerActivation, bool] | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.layer_id_edit = QLineEdit(layer.layer_id if layer is not None else "")
        form.addRow("Layer id:", self.layer_id_edit)

        self.trigger_combo = QComboBox()
        self.trigger_combo.addItems(SORTED_CONTROLS)
        if layer is not None and layer.activation.trigger.control in SORTED_CONTROLS:
            self.trigger_combo.setCurrentText(layer.activation.trigger.control)
        form.addRow("Trigger control:", self.trigger_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["hold", "toggle"])
        if layer is not None:
            self.mode_combo.setCurrentText(layer.activation.mode)
        form.addRow("Mode:", self.mode_combo)

        self.clears_check = QCheckBox("Clears on profile switch")
        self.clears_check.setChecked(layer is None or layer.clears_on_profile_switch)
        form.addRow(self.clears_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # noqa: D102 - Qt override
        layer_id = self.layer_id_edit.text().strip()
        if not layer_id:
            QMessageBox.warning(self, "Invalid layer", "Layer id must not be empty.")
            return
        try:
            activation = LayerActivation(
                trigger=InputRef(control=self.trigger_combo.currentText()),
                mode=self.mode_combo.currentText(),
            )
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid layer", str(ProfileValidationError(exc)))
            return
        self._result = (layer_id, activation, self.clears_check.isChecked())
        super().accept()

    def get_result(self) -> tuple[str, LayerActivation, bool]:
        """Return ``(layer_id, activation, clears_on_profile_switch)``."""

        assert self._result is not None
        return self._result


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """The profile/layer editor's main window.

    Holds every loaded ``Profile`` in memory (``self.profiles``), tracks the
    file each was loaded from (``self.profile_paths``), and tracks whether
    the currently selected profile has unsaved edits (``self.dirty``).
    """

    def __init__(
        self,
        profiles_dir: str | os.PathLike[str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("ds4catia - Profile & layer editor")

        self.profiles_dir = Path(profiles_dir) if profiles_dir is not None else DEFAULT_PROFILES_DIR
        self.profiles: dict[str, Profile] = {}
        self.profile_paths: dict[str, Path] = {}
        self.current_profile_id: str | None = None
        self.dirty: bool = False

        self._build_ui()
        self.reload_profiles()

    # -- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, stretch=1)

        # Left: profile list + import/export/save
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Profiles"))
        self.profile_list = QListWidget()
        self.profile_list.currentItemChanged.connect(self._on_profile_selected)
        left_layout.addWidget(self.profile_list, stretch=1)

        self.import_button = QPushButton("Importar perfil...")
        self.import_button.clicked.connect(self._on_import_clicked)
        left_layout.addWidget(self.import_button)

        self.export_button = QPushButton("Exportar perfil...")
        self.export_button.clicked.connect(self._on_export_clicked)
        left_layout.addWidget(self.export_button)

        self.save_button = QPushButton("Guardar")
        self.save_button.clicked.connect(self._on_save_clicked)
        left_layout.addWidget(self.save_button)

        self.status_label = QLabel("")
        left_layout.addWidget(self.status_label)

        splitter.addWidget(left)

        # Right: tabs (Bindings/Layers editor + placeholders)
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)

        self.tabs.addTab(self._build_bindings_tab(), "Bindings y capas")
        self.tabs.addTab(self._build_placeholder_tab(), "Captura en vivo / Dry-run")

    def _build_bindings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Control", "Target", "Modificadores"])
        self.tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        layout.addWidget(self.tree, stretch=1)

        button_row = QHBoxLayout()

        self.add_binding_button = QPushButton("Añadir binding")
        self.add_binding_button.clicked.connect(self._on_add_binding_clicked)
        button_row.addWidget(self.add_binding_button)

        self.edit_binding_button = QPushButton("Editar binding")
        self.edit_binding_button.clicked.connect(self._on_edit_binding_clicked)
        button_row.addWidget(self.edit_binding_button)

        self.remove_binding_button = QPushButton("Eliminar binding")
        self.remove_binding_button.clicked.connect(self._on_remove_binding_clicked)
        button_row.addWidget(self.remove_binding_button)

        layout.addLayout(button_row)

        layer_button_row = QHBoxLayout()

        self.add_layer_button = QPushButton("Añadir capa")
        self.add_layer_button.clicked.connect(self._on_add_layer_clicked)
        layer_button_row.addWidget(self.add_layer_button)

        self.edit_layer_button = QPushButton("Editar activación de capa")
        self.edit_layer_button.clicked.connect(self._on_edit_layer_clicked)
        layer_button_row.addWidget(self.edit_layer_button)

        self.remove_layer_button = QPushButton("Eliminar capa")
        self.remove_layer_button.clicked.connect(self._on_remove_layer_clicked)
        layer_button_row.addWidget(self.remove_layer_button)

        layout.addLayout(layer_button_row)

        return tab

    def _build_placeholder_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        label = QLabel(
            "La captura en vivo contra el mando y el panel de dry-run se "
            "implementan en otro módulo (ds4catia/gui/capture.py y "
            "ds4catia/gui/tray.py)."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return tab

    # -- profile loading / selection --------------------------------------

    def reload_profiles(self) -> None:
        """(Re)load every profile JSON file from ``self.profiles_dir``."""

        profiles, paths, errors = load_profiles_from_dir(self.profiles_dir)
        self.profiles = profiles
        self.profile_paths = paths
        self.current_profile_id = None
        self.dirty = False

        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile_id, profile in sorted(
            self.profiles.items(), key=lambda kv: kv[1].display_name.lower()
        ):
            item = QListWidgetItem(profile.display_name)
            item.setData(Qt.ItemDataRole.UserRole, profile_id)
            self.profile_list.addItem(item)
        self.profile_list.blockSignals(False)

        if errors:
            self.status_label.setText(f"{len(errors)} perfil(es) no cargados (ver detalle).")
        else:
            self.status_label.setText("")

        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)
        else:
            self._populate_tree()

    @property
    def current_profile(self) -> Profile | None:
        if self.current_profile_id is None:
            return None
        return self.profiles.get(self.current_profile_id)

    def _on_profile_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self.current_profile_id = None
        else:
            self.current_profile_id = current.data(Qt.ItemDataRole.UserRole)
        self.dirty = False
        self._populate_tree()
        self._update_status()

    def select_profile(self, profile_id: str) -> None:
        """Select the profile ``profile_id`` in the profile list, if present."""

        for row in range(self.profile_list.count()):
            item = self.profile_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == profile_id:
                self.profile_list.setCurrentRow(row)
                return

    # -- bindings/layers tree ---------------------------------------------

    def _populate_tree(self) -> None:
        self.tree.clear()
        profile = self.current_profile
        if profile is None:
            return

        base_item = QTreeWidgetItem(["Bindings base", "", ""])
        base_item.setData(0, Qt.ItemDataRole.UserRole, {"scope": "base"})
        self.tree.addTopLevelItem(base_item)
        for index, binding in enumerate(profile.bindings):
            self._add_binding_item(base_item, binding, {"scope": "base", "index": index})

        for layer_index, layer in enumerate(profile.layers):
            label = (
                f"Capa: {layer.layer_id}  "
                f"(trigger={layer.activation.trigger.control}, mode={layer.activation.mode})"
            )
            layer_item = QTreeWidgetItem([label, "", ""])
            layer_item.setData(
                0, Qt.ItemDataRole.UserRole, {"scope": "layer", "layer_index": layer_index}
            )
            self.tree.addTopLevelItem(layer_item)
            for index, binding in enumerate(layer.overrides):
                self._add_binding_item(
                    layer_item,
                    binding,
                    {"scope": "layer", "layer_index": layer_index, "index": index},
                )

        self.tree.expandAll()
        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)

    def _add_binding_item(
        self, parent_item: QTreeWidgetItem, binding: Binding, data: dict[str, Any]
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [binding.input.control, summarize_target(binding.target), summarize_modifiers(binding)]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        parent_item.addChild(item)
        return item

    def _selected_item_data(self) -> dict[str, Any] | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _binding_list_for_scope(self, data: dict[str, Any]) -> list[Binding] | None:
        profile = self.current_profile
        if profile is None:
            return None
        if data["scope"] == "base":
            return profile.bindings
        if data["scope"] == "layer":
            return profile.layers[data["layer_index"]].overrides
        return None  # pragma: no cover - only two scopes exist

    def _mark_dirty(self) -> None:
        self.dirty = True
        self._update_status()

    def _update_status(self) -> None:
        if self.current_profile_id is None:
            self.status_label.setText("")
            return
        if self.dirty:
            self.status_label.setText(f"Cambios sin guardar ({self.current_profile_id})")
        else:
            self.status_label.setText(f"Sin cambios pendientes ({self.current_profile_id})")

    # -- binding add/edit/remove -------------------------------------------

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is not None and "index" in data:
            self._edit_binding_at(data)

    def _on_add_binding_clicked(self) -> None:
        profile = self.current_profile
        if profile is None:
            return
        data = self._selected_item_data()
        scope: dict[str, Any] = {"scope": "base"}
        if data is not None:
            if data["scope"] == "layer":
                scope = {"scope": "layer", "layer_index": data["layer_index"]}
        binding_list = self._binding_list_for_scope(scope)
        if binding_list is None:
            return

        dialog = BindingEditDialog(binding=None, parent=self)
        if dialog.exec() == QDialog.Accepted:
            binding_list.append(dialog.get_binding())
            self._mark_dirty()
            self._populate_tree()

    def _on_edit_binding_clicked(self) -> None:
        data = self._selected_item_data()
        if data is None or "index" not in data:
            QMessageBox.information(self, "Editar binding", "Selecciona primero un binding.")
            return
        self._edit_binding_at(data)

    def _edit_binding_at(self, data: dict[str, Any]) -> None:
        binding_list = self._binding_list_for_scope(data)
        if binding_list is None:
            return
        index = data["index"]
        dialog = BindingEditDialog(binding=binding_list[index], parent=self)
        if dialog.exec() == QDialog.Accepted:
            binding_list[index] = dialog.get_binding()
            self._mark_dirty()
            self._populate_tree()

    def _on_remove_binding_clicked(self) -> None:
        data = self._selected_item_data()
        if data is None or "index" not in data:
            QMessageBox.information(self, "Eliminar binding", "Selecciona primero un binding.")
            return
        binding_list = self._binding_list_for_scope(data)
        if binding_list is None:
            return
        del binding_list[data["index"]]
        self._mark_dirty()
        self._populate_tree()

    # -- layer add/edit/remove ---------------------------------------------

    def _on_add_layer_clicked(self) -> None:
        profile = self.current_profile
        if profile is None:
            return
        dialog = LayerActivationDialog(layer=None, parent=self)
        if dialog.exec() == QDialog.Accepted:
            layer_id, activation, clears = dialog.get_result()
            if any(layer.layer_id == layer_id for layer in profile.layers):
                QMessageBox.warning(
                    self, "Capa duplicada", f"Ya existe una capa con id '{layer_id}'."
                )
                return
            profile.layers.append(
                Layer(
                    layer_id=layer_id,
                    activation=activation,
                    clears_on_profile_switch=clears,
                    overrides=[],
                )
            )
            self._mark_dirty()
            self._populate_tree()

    def _selected_layer_index(self) -> int | None:
        data = self._selected_item_data()
        if data is None or data["scope"] != "layer":
            return None
        return data["layer_index"]

    def _on_edit_layer_clicked(self) -> None:
        profile = self.current_profile
        layer_index = self._selected_layer_index()
        if profile is None or layer_index is None:
            QMessageBox.information(
                self, "Editar capa", "Selecciona primero el encabezado de una capa."
            )
            return
        layer = profile.layers[layer_index]
        dialog = LayerActivationDialog(layer=layer, parent=self)
        if dialog.exec() == QDialog.Accepted:
            layer_id, activation, clears = dialog.get_result()
            layer.layer_id = layer_id
            layer.activation = activation
            layer.clears_on_profile_switch = clears
            self._mark_dirty()
            self._populate_tree()

    def _on_remove_layer_clicked(self) -> None:
        profile = self.current_profile
        layer_index = self._selected_layer_index()
        if profile is None or layer_index is None:
            QMessageBox.information(
                self, "Eliminar capa", "Selecciona primero el encabezado de una capa."
            )
            return
        layer = profile.layers[layer_index]
        confirm = QMessageBox.question(
            self,
            "Eliminar capa",
            f"¿Eliminar la capa '{layer.layer_id}' y todos sus bindings?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            del profile.layers[layer_index]
            self._mark_dirty()
            self._populate_tree()

    # -- import / export / save --------------------------------------------

    def import_profile_from_path(self, path: str | os.PathLike[str]) -> bool:
        """Load a profile JSON file at ``path`` and add/replace it in memory.

        On success, the profile is selected and ``True`` is returned. On
        failure (invalid JSON / schema), a readable error dialog is shown
        (using ``ProfileValidationError``'s message) and ``False`` is
        returned -- the previously loaded profiles are left untouched.
        """

        try:
            profile = load_profile(path)
        except ProfileValidationError as exc:
            QMessageBox.critical(self, "Error al importar perfil", str(exc))
            return False

        self.profiles[profile.profile_id] = profile
        self.profile_paths[profile.profile_id] = Path(path)

        existing_row = None
        for row in range(self.profile_list.count()):
            if self.profile_list.item(row).data(Qt.ItemDataRole.UserRole) == profile.profile_id:
                existing_row = row
                break
        if existing_row is not None:
            self.profile_list.item(existing_row).setText(profile.display_name)
        else:
            item = QListWidgetItem(profile.display_name)
            item.setData(Qt.ItemDataRole.UserRole, profile.profile_id)
            self.profile_list.addItem(item)

        self.select_profile(profile.profile_id)
        self.dirty = False
        self._update_status()
        return True

    def _on_import_clicked(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Importar perfil", str(self.profiles_dir), "Perfiles JSON (*.json)"
        )
        if path:
            self.import_profile_from_path(path)

    def export_profile_to_path(
        self, profile_id: str, path: str | os.PathLike[str]
    ) -> bool:
        """Write ``self.profiles[profile_id]`` to ``path`` as JSON.

        Returns ``True`` on success, ``False`` if ``profile_id`` is unknown
        or writing failed (in which case an error dialog is shown).
        """

        profile = self.profiles.get(profile_id)
        if profile is None:
            return False
        try:
            data = dump_profile(profile)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.write("\n")
        except OSError as exc:
            QMessageBox.critical(self, "Error al exportar perfil", str(exc))
            return False
        self.status_label.setText(f"Exportado a {path}")
        return True

    def _on_export_clicked(self) -> None:
        if self.current_profile_id is None:
            QMessageBox.information(self, "Exportar perfil", "No hay ningún perfil seleccionado.")
            return
        default_name = f"{self.current_profile_id}.json"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Exportar perfil", default_name, "Perfiles JSON (*.json)"
        )
        if path:
            self.export_profile_to_path(self.current_profile_id, path)

    def save_current_profile(self) -> bool:
        """Write the current profile back to the file it was loaded from.

        Returns ``True`` on success, ``False`` if there is no current
        profile or no known file path for it (an error dialog is shown in
        that case).
        """

        if self.current_profile_id is None:
            return False
        path = self.profile_paths.get(self.current_profile_id)
        if path is None:
            QMessageBox.warning(
                self,
                "Guardar perfil",
                "Este perfil no tiene un archivo asociado; usa 'Exportar perfil...'.",
            )
            return False
        if not self.export_profile_to_path(self.current_profile_id, path):
            return False
        self.dirty = False
        self.status_label.setText(f"Guardado: {path}")
        return True

    def _on_save_clicked(self) -> None:
        self.save_current_profile()


def build_main_window(profiles_dir: str | None = None) -> QMainWindow:
    """Construct (without showing or ``exec()``-ing) the editor's main window."""

    return MainWindow(profiles_dir=profiles_dir)
