"""Configuration data model (profiles/bindings/layers) for ds4catia.

See ``ds4catia.mapping.schema`` for the full model. The names below are
re-exported here for convenience; ``schema`` remains the canonical module
other code should import from.
"""

from ds4catia.mapping.schema import (
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
    ProfileAutoActivate,
    ProfileValidationError,
    dump_profile,
    load_profile,
)

__all__ = [
    "ActionTarget",
    "AnalogModifiers",
    "AppControlTarget",
    "Binding",
    "CatiaCameraTarget",
    "CatiaCommandTarget",
    "CatiaMacroTarget",
    "CatiaSelectionTarget",
    "DigitalModifiers",
    "InputRef",
    "KeyboardTarget",
    "Layer",
    "LayerActivation",
    "LayerControlTarget",
    "MouseButtonTarget",
    "MouseMoveTarget",
    "Profile",
    "ProfileAutoActivate",
    "ProfileValidationError",
    "dump_profile",
    "load_profile",
]
