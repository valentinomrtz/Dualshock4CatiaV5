"""Application-level settings for the DS4->CATIA daemon.

Pure Python, no Windows/CATIA/hidapi dependency at all -- fully
unit-testable on any platform.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ConnectionMode = Literal["usb", "bt", "auto"]

# ds4catia/app/config.py -> ds4catia/mapping/profiles
DEFAULT_PROFILES_DIR = str(
    Path(__file__).resolve().parent.parent / "mapping" / "profiles"
)


@dataclass
class AppConfig:
    """User-editable settings, persisted as a small JSON file.

    Not to be confused with a DS4 *profile* (``ds4catia.mapping.schema.Profile``):
    this is app-wide configuration (which profiles directory to load, which
    profile to start on, connection mode, poll/debounce tuning), not a set
    of control bindings.
    """

    profiles_dir: str = DEFAULT_PROFILES_DIR
    default_profile_id: str = "base"
    connection: ConnectionMode = "auto"
    dry_run: bool = False
    workbench_poll_interval_ms: int = 500
    debounce_count: int = 3

    @classmethod
    def load(cls, path: "str | os.PathLike[str]") -> "AppConfig":
        """Load settings from ``path``, falling back to defaults for any
        field missing from the file. Returns pure defaults if the file
        does not exist (there is no error for "first run, no config yet")."""

        path = Path(path)
        if not path.exists():
            return cls()

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        defaults = asdict(cls())
        defaults.update({k: v for k, v in data.items() if k in defaults})
        return cls(**defaults)

    def save(self, path: "str | os.PathLike[str]") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, sort_keys=True)
