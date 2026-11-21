"""Tests for ``ds4catia.gui.main_window`` (profile/layer editor GUI).

These build real PySide6 widgets (via ``pytest-qt``'s ``qtbot`` fixture) and
exercise their actual behaviour, not just "it imports" -- see
``ds4catia/gui/main_window.py`` for the window this covers. The tests force
``QT_QPA_PLATFORM=offscreen`` themselves (before importing PySide6) so they
are reproducible regardless of how pytest is invoked.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from ds4catia.gui.main_window import (
    DEFAULT_PROFILES_DIR,
    BindingEditDialog,
    LayerActivationDialog,
    MainWindow,
    build_main_window,
    load_profiles_from_dir,
    summarize_modifiers,
    summarize_target,
)
from ds4catia.mapping.schema import (
    Binding,
    CatiaCommandTarget,
    InputRef,
    Layer,
    LayerActivation,
    Profile,
    dump_profile,
    load_profile,
)

REAL_PROFILES_DIR = Path(__file__).resolve().parent.parent / "ds4catia" / "mapping" / "profiles"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sample_profile(profile_id: str = "sample") -> Profile:
    return Profile(
        profile_id=profile_id,
        display_name="Sample Profile",
        bindings=[
            Binding(
                id="sample.cross.fit_all",
                input=InputRef(control="cross"),
                target=CatiaCommandTarget(command_id="View.FitAll"),
            ),
            Binding(
                id="sample.left_stick.orbit",
                input=InputRef(control="left_stick"),
                target=CatiaCommandTarget(command_id="View.Rotate"),
            ),
        ],
        layers=[
            Layer(
                layer_id="sample.l1-shift",
                activation=LayerActivation(trigger=InputRef(control="l1"), mode="hold"),
                overrides=[
                    Binding(
                        id="sample.l1.square.pocket",
                        input=InputRef(control="square"),
                        target=CatiaCommandTarget(command_id="PartDesign.Pocket"),
                    )
                ],
            )
        ],
    )


def _write_profile_json(path: Path, profile: Profile) -> None:
    path.write_text(json.dumps(dump_profile(profile), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# build_main_window() / loading real profiles
# ---------------------------------------------------------------------------


def test_build_main_window_loads_real_profiles(qtbot):
    window = build_main_window()
    qtbot.addWidget(window)

    assert isinstance(window, MainWindow)
    # Every shipped profile file should have loaded without error.
    real_ids = {p.stem for p in REAL_PROFILES_DIR.glob("*.json")}
    assert set(window.profiles.keys()) == real_ids
    assert window.profiles_dir == DEFAULT_PROFILES_DIR


def test_profile_selector_lists_all_loaded_profiles(qtbot, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile_a = _make_sample_profile("profile_a")
    profile_b = _make_sample_profile("profile_b")
    profile_b.display_name = "Profile B"
    _write_profile_json(profiles_dir / "a.json", profile_a)
    _write_profile_json(profiles_dir / "b.json", profile_b)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)

    assert window.profile_list.count() == 2
    shown_texts = {window.profile_list.item(i).text() for i in range(2)}
    assert shown_texts == {"Sample Profile", "Profile B"}
    shown_ids = {
        window.profile_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)
    }
    assert shown_ids == {"profile_a", "profile_b"}


def test_selecting_different_profile_updates_bindings_view(qtbot, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile_a = _make_sample_profile("profile_a")
    profile_b = _make_sample_profile("profile_b")
    profile_b.display_name = "Profile B"
    profile_b.bindings = [
        Binding(
            id="b.triangle.undo",
            input=InputRef(control="triangle"),
            target=CatiaCommandTarget(command_id="Edit.Undo"),
        )
    ]
    profile_b.layers = []
    _write_profile_json(profiles_dir / "a.json", profile_a)
    _write_profile_json(profiles_dir / "b.json", profile_b)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)

    window.select_profile("profile_a")
    assert window.current_profile_id == "profile_a"
    # base group + layer group at top level
    assert window.tree.topLevelItemCount() == 2
    base_item = window.tree.topLevelItem(0)
    assert base_item.childCount() == 2

    window.select_profile("profile_b")
    assert window.current_profile_id == "profile_b"
    assert window.tree.topLevelItemCount() == 1  # no layers in profile_b
    base_item = window.tree.topLevelItem(0)
    assert base_item.childCount() == 1
    assert base_item.child(0).text(0) == "triangle"
    assert "Edit.Undo" in base_item.child(0).text(1)


# ---------------------------------------------------------------------------
# Editing a binding through the dialog widgets
# ---------------------------------------------------------------------------


def test_edit_binding_dialog_changes_command_id(qtbot, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile = _make_sample_profile()
    _write_profile_json(profiles_dir / "sample.json", profile)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)
    window.select_profile("sample")

    # Edit the first base binding (cross -> View.FitAll) directly through
    # the dialog's widgets, as a real user editing session would.
    dialog = BindingEditDialog(binding=window.current_profile.bindings[0])
    qtbot.addWidget(dialog)
    assert dialog.type_combo.currentData() == "catia_command"
    command_page = dialog._target_pages["catia_command"]
    command_page.command_id_edit.setText("View.FitAll.Changed")
    qtbot.keyClick(command_page.command_id_edit, Qt.Key_Enter)

    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    edited = dialog.get_binding()
    assert edited.target.command_id == "View.FitAll.Changed"

    window.current_profile.bindings[0] = edited
    window._mark_dirty()

    assert window.dirty is True
    assert window.current_profile.bindings[0].target.command_id == "View.FitAll.Changed"
    # dump_profile of the in-memory profile reflects the edit.
    dumped = dump_profile(window.current_profile)
    assert dumped["bindings"][0]["target"]["command_id"] == "View.FitAll.Changed"


def test_edit_binding_dialog_changes_deadzone(qtbot):
    binding = Binding(
        id="t.left_stick.orbit",
        input=InputRef(control="left_stick"),
        target=CatiaCommandTarget(command_id="View.Rotate"),
    )
    dialog = BindingEditDialog(binding=binding)
    qtbot.addWidget(dialog)

    assert dialog.analog_box.isChecked() is False
    dialog.analog_box.setChecked(True)
    dialog.deadzone_spin.setValue(0.33)

    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    edited = dialog.get_binding()
    assert edited.analog is not None
    assert edited.analog.deadzone == pytest.approx(0.33)


def test_add_binding_via_main_window_marks_dirty(qtbot, tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile = _make_sample_profile()
    _write_profile_json(profiles_dir / "sample.json", profile)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)
    window.select_profile("sample")
    assert window.dirty is False

    new_binding = Binding(
        id="sample.r1.new_action",
        input=InputRef(control="r1"),
        target=CatiaCommandTarget(command_id="Edit.Redo"),
    )

    def fake_exec(self):
        self._result_binding = new_binding
        return QDialog.Accepted

    monkeypatch.setattr(BindingEditDialog, "exec", fake_exec)

    before_count = len(window.current_profile.bindings)
    window._on_add_binding_clicked()

    assert window.dirty is True
    assert len(window.current_profile.bindings) == before_count + 1
    assert window.current_profile.bindings[-1].id == "sample.r1.new_action"


# ---------------------------------------------------------------------------
# Layer activation dialog
# ---------------------------------------------------------------------------


def test_layer_activation_dialog_builds_valid_layer_fields(qtbot):
    dialog = LayerActivationDialog(layer=None)
    qtbot.addWidget(dialog)

    dialog.layer_id_edit.setText("new.layer")
    dialog.trigger_combo.setCurrentText("r1")
    dialog.mode_combo.setCurrentText("toggle")

    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    layer_id, activation, clears = dialog.get_result()
    assert layer_id == "new.layer"
    assert activation.trigger.control == "r1"
    assert activation.mode == "toggle"
    assert clears is True


def test_layer_activation_dialog_rejects_empty_layer_id(qtbot, monkeypatch):
    # QMessageBox.warning() is modal and would block forever under
    # offscreen/no-user-interaction test conditions; stub it out so this
    # test can assert the dialog refused to close without a real click.
    warnings: list[str] = []
    monkeypatch.setattr(
        "ds4catia.gui.main_window.QMessageBox.warning",
        lambda *args, **kwargs: warnings.append(args[-1]) or None,
    )

    dialog = LayerActivationDialog(layer=None)
    qtbot.addWidget(dialog)
    dialog.layer_id_edit.setText("   ")
    dialog.accept()
    # accept() should refuse and not close the dialog.
    assert dialog.result() != QDialog.Accepted
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Import: invalid JSON shows an error instead of crashing
# ---------------------------------------------------------------------------


def test_import_invalid_profile_shows_error_and_keeps_state(qtbot, tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile = _make_sample_profile()
    _write_profile_json(profiles_dir / "sample.json", profile)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)
    window.select_profile("sample")

    bad_path = tmp_path / "bad_profile.json"
    bad_data = json.loads(json.dumps(dump_profile(_make_sample_profile("bad"))))
    # deadzone must be within [0.0, 1.0] -- push it out of range.
    bad_data["bindings"][0]["analog"] = {"deadzone": 5.0}
    bad_path.write_text(json.dumps(bad_data), encoding="utf-8")

    shown_errors: list[str] = []
    monkeypatch.setattr(
        "ds4catia.gui.main_window.QMessageBox.critical",
        lambda *args, **kwargs: shown_errors.append(args[-1]) or None,
    )

    profiles_before = set(window.profiles.keys())
    ok = window.import_profile_from_path(bad_path)

    assert ok is False
    assert len(shown_errors) == 1
    assert "deadzone" in shown_errors[0]
    # State is unchanged: no crash, no partial/bad profile registered.
    assert set(window.profiles.keys()) == profiles_before
    assert "bad" not in window.profiles


def test_import_valid_profile_adds_and_selects_it(qtbot, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    existing = _make_sample_profile()
    _write_profile_json(profiles_dir / "sample.json", existing)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)

    other_path = tmp_path / "other.json"
    other_profile = _make_sample_profile("other")
    other_profile.display_name = "Other Profile"
    _write_profile_json(other_path, other_profile)

    ok = window.import_profile_from_path(other_path)
    assert ok is True
    assert "other" in window.profiles
    assert window.current_profile_id == "other"
    assert window.profile_list.count() == 2


# ---------------------------------------------------------------------------
# Export round-trip
# ---------------------------------------------------------------------------


def test_export_profile_round_trips_through_load_profile(qtbot, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile = _make_sample_profile()
    _write_profile_json(profiles_dir / "sample.json", profile)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)
    window.select_profile("sample")

    export_path = tmp_path / "exported.json"
    ok = window.export_profile_to_path("sample", export_path)
    assert ok is True
    assert export_path.exists()

    reloaded = load_profile(export_path)
    assert reloaded == window.profiles["sample"]


def test_save_current_profile_writes_back_to_source_file(qtbot, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    profile = _make_sample_profile()
    source_path = profiles_dir / "sample.json"
    _write_profile_json(source_path, profile)

    window = build_main_window(str(profiles_dir))
    qtbot.addWidget(window)
    window.select_profile("sample")

    window.current_profile.bindings[0].target.command_id = "View.FitAll.Edited"
    window._mark_dirty()
    assert window.dirty is True

    ok = window.save_current_profile()
    assert ok is True
    assert window.dirty is False

    reloaded = load_profile(source_path)
    assert reloaded.bindings[0].target.command_id == "View.FitAll.Edited"


# ---------------------------------------------------------------------------
# Target/modifier summaries (plain unit tests, no widgets needed)
# ---------------------------------------------------------------------------


def test_summarize_target_catia_command():
    target = CatiaCommandTarget(command_id="View.FitAll")
    assert summarize_target(target) == "CATIA Command: View.FitAll"


def test_summarize_modifiers_none():
    binding = Binding(
        id="x",
        input=InputRef(control="cross"),
        target=CatiaCommandTarget(command_id="X"),
    )
    assert summarize_modifiers(binding) == "-"


def test_load_profiles_from_dir_skips_invalid_and_reports_error(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    good = _make_sample_profile("good")
    _write_profile_json(profiles_dir / "good.json", good)
    (profiles_dir / "bad.json").write_text("{not valid json", encoding="utf-8")

    profiles, paths, errors = load_profiles_from_dir(profiles_dir)

    assert set(profiles.keys()) == {"good"}
    assert "good" in paths
    assert len(errors) == 1
    assert "bad.json" in errors[0]
