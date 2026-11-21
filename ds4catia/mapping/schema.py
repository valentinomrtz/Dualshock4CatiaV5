"""Configuration data model for DS4 -> CATIA V5 mappings.

This module defines the three-level configuration model agreed for the
project (see research_notes/.../mapping_config_ux.md and the "Pila
recomendada..." section of reports/Mando DualShock4 para CATIA V5.md):

    Binding  -- one physical DS4 input (+ optional analog/digital
                modifiers) mapped to one destination ``ActionTarget``.
    Profile  -- a complete set of Bindings for a "mode", typically one
                per CATIA workbench plus one base/global profile.
    Layer    -- a momentary "shift" overlay (held or toggled) that only
                overrides the bindings it explicitly lists, and is
                cleared automatically on profile switch -- mirroring
                Steam Input's action set layer semantics.

This module is intentionally limited to the *data model*: (de)serializing
and validating profiles. It does not parse HID reports, talk to CATIA, or
resolve bindings/layers at runtime -- those live in other modules.

The public names exported here (``AnalogModifiers``, ``DigitalModifiers``,
``InputRef``, ``ActionTarget`` and its member classes, ``Binding``,
``LayerActivation``, ``Layer``, ``ProfileAutoActivate``, ``Profile``,
``load_profile``, ``dump_profile``, ``ProfileValidationError``) are a
shared contract with other modules (GUI, resolution engine) and must not
be renamed.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

__all__ = [
    "AnalogModifiers",
    "DigitalModifiers",
    "InputRef",
    "ActionTarget",
    "CatiaCommandTarget",
    "CatiaCameraTarget",
    "CatiaSelectionTarget",
    "CatiaMacroTarget",
    "KeyboardTarget",
    "MouseMoveTarget",
    "MouseButtonTarget",
    "LayerControlTarget",
    "AppControlTarget",
    "Binding",
    "LayerActivation",
    "Layer",
    "ProfileAutoActivate",
    "Profile",
    "load_profile",
    "dump_profile",
    "ProfileValidationError",
]


# ---------------------------------------------------------------------------
# Valid physical input control names.
#
# These are the DS4 buttons exposed by DS4State.buttons (owned by the HID
# parsing module, which this module intentionally does not depend on --
# the set of names is duplicated here as the fixed contract), plus the
# non-button control surfaces of the pad (sticks, analog triggers, dpad,
# touchpad and gyro axes).
# ---------------------------------------------------------------------------

DS4_BUTTON_CONTROLS: frozenset[str] = frozenset(
    {
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
    }
)

DS4_OTHER_CONTROLS: frozenset[str] = frozenset(
    {
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
    }
)

VALID_DS4_CONTROLS: frozenset[str] = DS4_BUTTON_CONTROLS | DS4_OTHER_CONTROLS


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------


class AnalogModifiers(BaseModel):
    """Response-shaping options for an analog input (stick/trigger/gyro axis)."""

    deadzone: float = Field(default=0.1, ge=0.0, le=1.0)
    anti_deadzone: float = Field(default=0.0, ge=0.0, le=1.0)
    sensitivity: float = Field(default=1.0, gt=0.0)
    curve: Literal[
        "linear",
        "quadratic",
        "cubic",
        "ease_out_quad",
        "ease_out_cubic",
        "custom_bezier",
    ] = "linear"
    curve_params: dict[str, Any] | None = None

    model_config = {"extra": "forbid"}


class DigitalModifiers(BaseModel):
    """Tap/hold timing options for a digital (button-like) input."""

    tap_ms: int = Field(default=180, gt=0)
    hold_ms: int = Field(default=400, gt=0)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Input reference
# ---------------------------------------------------------------------------


class InputRef(BaseModel):
    """A reference to one physical control on a device."""

    device: Literal["ds4"] = "ds4"
    control: str

    model_config = {"extra": "forbid"}

    @field_validator("control")
    @classmethod
    def _validate_control(cls, value: str) -> str:
        if value not in VALID_DS4_CONTROLS:
            valid = ", ".join(sorted(VALID_DS4_CONTROLS))
            raise ValueError(
                f"unknown DS4 control {value!r}; must be one of: {valid}"
            )
        return value


# ---------------------------------------------------------------------------
# Action targets (discriminated union on `type`)
# ---------------------------------------------------------------------------


class CatiaCommandTarget(BaseModel):
    """Invoke a CATIA command/menu action by its identifier."""

    type: Literal["catia_command"] = "catia_command"
    command_id: str

    model_config = {"extra": "forbid"}


class CatiaCameraTarget(BaseModel):
    """Drive the CATIA 3D viewer camera (rotate/translate/zoom)."""

    type: Literal["catia_camera"] = "catia_camera"
    operation: Literal["rotate", "translate", "zoom"]
    axis: tuple[float, float, float] | None = None

    model_config = {"extra": "forbid"}


class CatiaSelectionTarget(BaseModel):
    """Manipulate the CATIA selection set."""

    type: Literal["catia_selection"] = "catia_selection"
    operation: Literal["add", "clear", "search"]
    query: str | None = None

    model_config = {"extra": "forbid"}


class CatiaMacroTarget(BaseModel):
    """Run a named CATIA macro."""

    type: Literal["catia_macro"] = "catia_macro"
    macro_name: str

    model_config = {"extra": "forbid"}


class KeyboardTarget(BaseModel):
    """Send a keyboard chord/sequence."""

    type: Literal["keyboard"] = "keyboard"
    keys: list[str]

    model_config = {"extra": "forbid"}


class MouseMoveTarget(BaseModel):
    """Move the mouse cursor (relative), optionally restricted to one axis."""

    type: Literal["mouse_move"] = "mouse_move"
    axis: Literal["x", "y", "both"] = "both"

    model_config = {"extra": "forbid"}


class MouseButtonTarget(BaseModel):
    """Press/release a mouse button."""

    type: Literal["mouse_button"] = "mouse_button"
    button: Literal["left", "right", "middle"]

    model_config = {"extra": "forbid"}


class LayerControlTarget(BaseModel):
    """Activate/deactivate a Layer (shift overlay)."""

    type: Literal["layer_control"] = "layer_control"
    layer_id: str
    mode: Literal["hold", "toggle"]

    model_config = {"extra": "forbid"}


class AppControlTarget(BaseModel):
    """Control the ds4catia application itself (pause, profile switching)."""

    type: Literal["app_control"] = "app_control"
    action: Literal["pause", "resume", "next_profile", "prev_profile"]

    model_config = {"extra": "forbid"}


ActionTarget = Annotated[
    Union[
        CatiaCommandTarget,
        CatiaCameraTarget,
        CatiaSelectionTarget,
        CatiaMacroTarget,
        KeyboardTarget,
        MouseMoveTarget,
        MouseButtonTarget,
        LayerControlTarget,
        AppControlTarget,
    ],
    Field(discriminator="type"),
]

_ActionTargetAdapter: TypeAdapter[Any] = TypeAdapter(ActionTarget)


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


class Binding(BaseModel):
    """One physical input (+ optional modifiers) mapped to a destination action."""

    id: str
    input: InputRef
    target: ActionTarget
    analog: AnalogModifiers | None = None
    digital: DigitalModifiers | None = None

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------


class LayerActivation(BaseModel):
    """How a Layer is activated: which control triggers it, and hold vs toggle."""

    trigger: InputRef
    mode: Literal["hold", "toggle"] = "hold"

    model_config = {"extra": "forbid"}


class Layer(BaseModel):
    """A "shift" overlay that only overrides the bindings it explicitly lists.

    Cleared automatically whenever the active Profile changes, mirroring
    Steam Input's rule that activating a new base action set clears all
    active action set layers.
    """

    layer_id: str
    activation: LayerActivation
    clears_on_profile_switch: bool = True
    overrides: list[Binding] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileAutoActivate(BaseModel):
    """Automatic profile-switching trigger, e.g. based on the active CATIA workbench."""

    trigger: Literal["catia_workbench_id"]
    match: str

    model_config = {"extra": "forbid"}


class Profile(BaseModel):
    """A complete set of Bindings (+ Layers) for one "mode".

    Typically one Profile per CATIA workbench, plus one base/global Profile.
    """

    profile_id: str
    display_name: str
    auto_activate: ProfileAutoActivate | None = None
    bindings: list[Binding] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProfileValidationError(Exception):
    """A user/GUI-friendly wrapper around a pydantic ValidationError.

    ``str(error)`` is plain text describing, one line per problem, which
    field failed and why -- suitable for display directly in a GUI dialog
    or an error log, without the caller needing to know anything about
    pydantic's own error representation.
    """

    def __init__(self, source: ValidationError | Exception, *, context: str | None = None):
        self.source = source
        self.context = context
        message = self._format_message(source, context)
        super().__init__(message)

    @staticmethod
    def _format_message(source: Exception, context: str | None) -> str:
        header = f"Invalid profile{f' ({context})' if context else ''}:"
        if isinstance(source, ValidationError):
            lines = [header]
            for err in source.errors():
                loc = ".".join(str(part) for part in err["loc"]) or "<root>"
                lines.append(f"  - {loc}: {err['msg']}")
            return "\n".join(lines)
        return f"{header} {source}"


# ---------------------------------------------------------------------------
# Load / dump
# ---------------------------------------------------------------------------


def load_profile(path: str | os.PathLike[str]) -> Profile:
    """Load and validate a Profile from a JSON file on disk.

    Raises:
        ProfileValidationError: if the file is not valid JSON, or the JSON
            does not conform to the Profile schema. The error message is
            plain text suitable for a GUI dialog.
    """

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw_text = fh.read()
    except OSError as exc:
        raise ProfileValidationError(exc, context=str(path)) from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProfileValidationError(exc, context=str(path)) from exc

    try:
        return Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileValidationError(exc, context=str(path)) from exc


def dump_profile(profile: Profile) -> dict[str, Any]:
    """Serialize a Profile to a plain JSON-compatible dict.

    This is the inverse of ``load_profile`` (modulo the file I/O): writing
    ``json.dumps(dump_profile(profile))`` to a file and then calling
    ``load_profile`` on it reproduces an equal ``Profile``.
    """

    return profile.model_dump(mode="json")
