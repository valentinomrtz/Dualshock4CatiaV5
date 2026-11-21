"""Tests for the default profiles shipped in ds4catia/mapping/profiles/.

These are the profiles installed by default with the application (one
base/global profile plus one per CATIA workbench). Every ``*.json`` file in
that directory must be a valid ``Profile`` per ``ds4catia.mapping.schema``,
every ``profile_id`` must be unique across the set, and every binding's
``InputRef.control`` must be one of the DS4 controls that ``schema.py``
already validates against (``load_profile`` raising no error is sufficient
evidence of that; this module does not duplicate schema.py's own validation
logic).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ds4catia.mapping.schema import Profile, ProfileValidationError, load_profile

PROFILES_DIR = Path(__file__).resolve().parent.parent / "ds4catia" / "mapping" / "profiles"


def _profile_paths() -> list[Path]:
    paths = sorted(PROFILES_DIR.glob("*.json"))
    assert paths, f"expected at least one profile JSON file in {PROFILES_DIR}"
    return paths


@pytest.fixture(scope="module")
def profile_paths() -> list[Path]:
    return _profile_paths()


@pytest.fixture(scope="module")
def loaded_profiles(profile_paths: list[Path]) -> dict[Path, Profile]:
    """Load every default profile once, failing with a clear message on error."""

    loaded: dict[Path, Profile] = {}
    for path in profile_paths:
        try:
            loaded[path] = load_profile(path)
        except ProfileValidationError as exc:  # pragma: no cover - failure path
            pytest.fail(f"{path.name} failed to load as a valid Profile:\n{exc}")
    return loaded


class TestDefaultProfilesLoad:
    def test_at_least_one_profile_file_exists(self, profile_paths: list[Path]) -> None:
        assert len(profile_paths) >= 1

    @pytest.mark.parametrize("path", _profile_paths(), ids=lambda p: p.name)
    def test_each_profile_loads_without_error(self, path: Path) -> None:
        # load_profile() raises ProfileValidationError (which wraps pydantic's
        # ValidationError / JSON errors) on anything invalid -- successfully
        # returning a Profile is exactly the contract we need to check here.
        profile = load_profile(path)
        assert isinstance(profile, Profile)

    def test_every_profile_is_a_profile_instance(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        for path, profile in loaded_profiles.items():
            assert isinstance(profile, Profile), f"{path.name} did not load as a Profile"


class TestDefaultProfilesAreConsistent:
    def test_profile_ids_are_unique_across_all_default_profiles(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        ids = [profile.profile_id for profile in loaded_profiles.values()]
        duplicates = {pid for pid in ids if ids.count(pid) > 1}
        assert not duplicates, f"duplicate profile_id(s) across default profiles: {duplicates}"
        assert len(ids) == len(set(ids))

    def test_binding_ids_are_unique_within_each_profile(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        # Bindings ids must be unique *within* a profile (base bindings plus
        # every layer's overrides); nothing requires uniqueness *across*
        # profiles since only one profile is active at a time.
        for path, profile in loaded_profiles.items():
            all_ids = [b.id for b in profile.bindings]
            for layer in profile.layers:
                all_ids.extend(b.id for b in layer.overrides)
            duplicates = {bid for bid in all_ids if all_ids.count(bid) > 1}
            assert not duplicates, f"{path.name} has duplicate binding id(s): {duplicates}"

    def test_layer_ids_referenced_by_layer_control_targets_exist_in_profile(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        # Sanity check for the layer pattern: any binding whose target is
        # layer_control should point at a layer_id actually defined on that
        # same profile (base bindings activate layers; layer overrides may
        # also reference a layer, e.g. to exit it).
        for path, profile in loaded_profiles.items():
            defined_layer_ids = {layer.layer_id for layer in profile.layers}
            all_bindings = list(profile.bindings)
            for layer in profile.layers:
                all_bindings.extend(layer.overrides)
            for binding in all_bindings:
                if binding.target.type == "layer_control":
                    assert binding.target.layer_id in defined_layer_ids, (
                        f"{path.name}: binding {binding.id!r} references "
                        f"undefined layer_id {binding.target.layer_id!r}"
                    )

    def test_profiles_with_auto_activate_use_catia_workbench_id_trigger(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        for path, profile in loaded_profiles.items():
            if profile.auto_activate is not None:
                assert profile.auto_activate.trigger == "catia_workbench_id"
                assert profile.auto_activate.match, f"{path.name}: empty auto_activate.match"

    def test_base_profile_has_no_auto_activate(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        base_profiles = [
            profile for profile in loaded_profiles.values() if profile.profile_id == "base"
        ]
        assert len(base_profiles) == 1, "expected exactly one profile with profile_id='base'"
        assert base_profiles[0].auto_activate is None

    def test_confirmed_part_design_workbench_id(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        # PrtCfg (Part Design) is the one workbench id confirmed by project
        # research; pin it down explicitly so a future edit doesn't silently
        # drift away from the confirmed value.
        part_design = next(
            profile
            for profile in loaded_profiles.values()
            if profile.profile_id == "part_design"
        )
        assert part_design.auto_activate is not None
        assert part_design.auto_activate.match == "PrtCfg"

    def test_round_trip_dump_and_reload_is_stable(
        self, loaded_profiles: dict[Path, Profile]
    ) -> None:
        from ds4catia.mapping.schema import dump_profile

        for path, profile in loaded_profiles.items():
            dumped = dump_profile(profile)
            reloaded = Profile.model_validate(dumped)
            assert reloaded == profile, f"{path.name} did not round-trip through dump/validate"
