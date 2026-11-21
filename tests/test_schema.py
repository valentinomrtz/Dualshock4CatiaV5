"""Tests for ds4catia.mapping.schema (profile/binding/layer data model)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ds4catia.mapping.schema import (
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
    ProfileAutoActivate,
    ProfileValidationError,
    dump_profile,
    load_profile,
)


def _full_profile() -> Profile:
    """A Profile exercising every ActionTarget variant, a layer with
    overrides, and auto_activate."""

    return Profile(
        profile_id="partdesign",
        display_name="Part Design",
        auto_activate=ProfileAutoActivate(trigger="catia_workbench_id", match="PrtCfg"),
        bindings=[
            Binding(
                id="bind.rotate3d",
                input=InputRef(device="ds4", control="right_stick"),
                target=MouseMoveTarget(type="mouse_move", axis="both"),
                analog=AnalogModifiers(deadzone=0.12, sensitivity=1.4, curve="quadratic"),
            ),
            Binding(
                id="bind.zoom",
                input=InputRef(device="ds4", control="r2"),
                target=CatiaCameraTarget(type="catia_camera", operation="zoom"),
                analog=AnalogModifiers(deadzone=0.05, curve="linear"),
            ),
            Binding(
                id="bind.confirm",
                input=InputRef(device="ds4", control="cross"),
                target=KeyboardTarget(type="keyboard", keys=["Enter"]),
                digital=DigitalModifiers(tap_ms=180, hold_ms=400),
            ),
            Binding(
                id="bind.select_search",
                input=InputRef(device="ds4", control="touchpad_tap"),
                target=CatiaSelectionTarget(type="catia_selection", operation="search", query="Pad.1"),
            ),
            Binding(
                id="bind.run_macro",
                input=InputRef(device="ds4", control="triangle"),
                target=CatiaMacroTarget(type="catia_macro", macro_name="ExportStep"),
            ),
            Binding(
                id="bind.command",
                input=InputRef(device="ds4", control="square"),
                target=CatiaCommandTarget(type="catia_command", command_id="Zoom.In"),
            ),
            Binding(
                id="bind.rmb",
                input=InputRef(device="ds4", control="l3"),
                target=MouseButtonTarget(type="mouse_button", button="right"),
            ),
            Binding(
                id="bind.pause",
                input=InputRef(device="ds4", control="ps"),
                target=AppControlTarget(type="app_control", action="pause"),
            ),
            Binding(
                id="bind.enter_shift",
                input=InputRef(device="ds4", control="l1"),
                target=LayerControlTarget(type="layer_control", layer_id="l1-shift", mode="hold"),
            ),
        ],
        layers=[
            Layer(
                layer_id="l1-shift",
                activation=LayerActivation(
                    trigger=InputRef(device="ds4", control="l1"), mode="hold"
                ),
                clears_on_profile_switch=True,
                overrides=[
                    Binding(
                        id="bind.l1.sketchconstraint",
                        input=InputRef(device="ds4", control="cross"),
                        target=CatiaCommandTarget(
                            type="catia_command",
                            command_id="Sketcher.Constraint.Coincident",
                        ),
                    ),
                    Binding(
                        id="bind.l1.exitlayer",
                        input=InputRef(device="ds4", control="circle"),
                        target=LayerControlTarget(
                            type="layer_control", layer_id="l1-shift", mode="hold"
                        ),
                    ),
                ],
            )
        ],
    )


class TestRoundTrip:
    def test_full_profile_round_trips_through_dump_and_validate(self):
        profile = _full_profile()
        dumped = dump_profile(profile)
        # dump_profile must be plain-JSON-compatible.
        json.dumps(dumped)

        reloaded = Profile.model_validate(dumped)
        assert reloaded == profile

    def test_load_dump_are_inverse_via_tempfile(self, tmp_path):
        profile = _full_profile()
        path = tmp_path / "partdesign.json"
        path.write_text(json.dumps(dump_profile(profile), indent=2), encoding="utf-8")

        reloaded = load_profile(path)
        assert reloaded == profile

        # And writing it back out again produces the same dict.
        assert dump_profile(reloaded) == dump_profile(profile)


class TestActionTargetDiscriminator:
    @pytest.mark.parametrize(
        "target, expected_cls",
        [
            (CatiaCommandTarget(type="catia_command", command_id="Zoom.In"), CatiaCommandTarget),
            (
                CatiaCameraTarget(type="catia_camera", operation="rotate", axis=(0.0, 1.0, 0.0)),
                CatiaCameraTarget,
            ),
            (
                CatiaSelectionTarget(type="catia_selection", operation="clear"),
                CatiaSelectionTarget,
            ),
            (CatiaMacroTarget(type="catia_macro", macro_name="DoThing"), CatiaMacroTarget),
            (KeyboardTarget(type="keyboard", keys=["Ctrl", "S"]), KeyboardTarget),
            (MouseMoveTarget(type="mouse_move", axis="x"), MouseMoveTarget),
            (MouseButtonTarget(type="mouse_button", button="left"), MouseButtonTarget),
            (
                LayerControlTarget(type="layer_control", layer_id="shift", mode="toggle"),
                LayerControlTarget,
            ),
            (AppControlTarget(type="app_control", action="next_profile"), AppControlTarget),
        ],
    )
    def test_binding_target_parses_to_correct_variant_by_discriminator(
        self, target, expected_cls
    ):
        binding = Binding(
            id="b1",
            input=InputRef(device="ds4", control="cross"),
            target=target,
        )
        dumped = dump_profile_binding(binding)
        reloaded = Binding.model_validate(dumped)
        assert isinstance(reloaded.target, expected_cls)
        assert reloaded == binding

    def test_unknown_discriminator_value_is_rejected(self):
        with pytest.raises(ValidationError):
            Binding.model_validate(
                {
                    "id": "b1",
                    "input": {"device": "ds4", "control": "cross"},
                    "target": {"type": "not_a_real_target"},
                }
            )


def dump_profile_binding(binding: Binding) -> dict:
    return binding.model_dump(mode="json")


class TestValidationRejections:
    def test_deadzone_out_of_range_is_rejected(self):
        with pytest.raises(ValidationError):
            AnalogModifiers(deadzone=1.5)

        with pytest.raises(ValidationError):
            AnalogModifiers(deadzone=-0.1)

    def test_unknown_control_name_is_rejected(self):
        with pytest.raises(ValidationError):
            InputRef(device="ds4", control="totally_bogus_control")

    def test_valid_control_names_from_contract_are_accepted(self):
        # Every button name from DS4State.buttons, plus the non-button
        # control surfaces, must validate.
        for control in [
            "cross",
            "circle",
            "square",
            "triangle",
            "l1",
            "r1",
            "l2_digital",
            "r2_digital",
            "l3",
            "r3",
            "share",
            "options",
            "ps",
            "touchpad_click",
            "left_stick",
            "right_stick",
            "l2",
            "r2",
            "dpad",
            "touchpad_tap",
            "touchpad_swipe",
            "gyro_x",
            "gyro_y",
            "gyro_z",
        ]:
            assert InputRef(device="ds4", control=control).control == control


class TestLoadProfileErrors:
    def test_malformed_json_raises_profile_validation_error(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(ProfileValidationError) as excinfo:
            load_profile(path)

        message = str(excinfo.value)
        assert message  # non-empty, readable text
        assert "\n" not in message or len(message) > 0

    def test_invalid_field_raises_profile_validation_error_with_readable_message(
        self, tmp_path
    ):
        path = tmp_path / "invalid.json"
        bad_data = {
            "profile_id": "bad",
            "display_name": "Bad Profile",
            "bindings": [
                {
                    "id": "b1",
                    "input": {"device": "ds4", "control": "cross"},
                    "target": {"type": "catia_command", "command_id": "X"},
                    "analog": {"deadzone": 42.0},  # out of range
                }
            ],
        }
        path.write_text(json.dumps(bad_data), encoding="utf-8")

        with pytest.raises(ProfileValidationError) as excinfo:
            load_profile(path)

        message = str(excinfo.value)
        assert "deadzone" in message
        # Should mention it is about the profile, in plain text (no raw
        # pydantic repr / traceback dump).
        assert "Invalid profile" in message

    def test_missing_file_raises_profile_validation_error(self, tmp_path):
        with pytest.raises(ProfileValidationError):
            load_profile(tmp_path / "does_not_exist.json")


class TestLayerSemantics:
    def test_layer_overrides_only_list_explicit_bindings(self):
        profile = _full_profile()
        layer = profile.layers[0]
        assert layer.clears_on_profile_switch is True
        override_ids = {b.id for b in layer.overrides}
        assert override_ids == {"bind.l1.sketchconstraint", "bind.l1.exitlayer"}
        # The layer's overrides are a distinct list from the profile's
        # base bindings -- it does not implicitly include them.
        base_ids = {b.id for b in profile.bindings}
        assert override_ids.isdisjoint(base_ids)
