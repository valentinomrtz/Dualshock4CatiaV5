# Dualshock4CatiaV5 (ds4catia)

Control CATIA V5 using a PlayStation 4 (DualShock 4) controller. A project
aimed at mapping controller inputs to CAD operations, navigation and
shortcuts to make 3D modelling more intuitive, accessible and efficient.

ds4catia reads the DS4's full raw HID report (buttons, both analog sticks,
analog triggers, touchpad, gyroscope/accelerometer, light bar, rumble — not
just the subset an XInput-style gamepad exposes) and drives CATIA V5 through
a hybrid of its COM Automation API (for discrete actions: commands, camera,
selection, macros) and synthetic keyboard/mouse input (for continuous
navigation the API doesn't cover well). Every mapping is user-configurable
through JSON profiles, editable either by hand or through a PySide6 GUI.

## Status

Fully implemented, with 293 automated tests passing (`pytest -q`). **Not
yet validated against real Windows/CATIA V5/DS4 hardware** — this was built
in a Linux development environment with no Windows, no CATIA V5, and no
physical controller available. See
[`docs/manual_verification_checklist.md`](docs/manual_verification_checklist.md)
for what still needs confirming on a real setup before relying on this in
production.

## Documentation

- [`docs/installation.md`](docs/installation.md) — requirements, installing,
  running the app, building a Windows `.exe`.
- [`docs/profiles_and_layers.md`](docs/profiles_and_layers.md) — the
  Binding → Profile → Layer configuration model, with real examples.
- [`docs/remapping_gui.md`](docs/remapping_gui.md) — using the profile
  editor, the dry-run panel, and the system tray.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — known risks
  (antivirus/EDR false positives, VM/VDI/Citrix, UIPI blocking injection,
  Bluetooth caveats, unverified workbench IDs) with concrete mitigations.
- [`docs/manual_verification_checklist.md`](docs/manual_verification_checklist.md)
  — the ordered checklist to run once on real Windows + CATIA V5 + DS4
  hardware.
- [`ds4catia/mapping/profiles/README.md`](ds4catia/mapping/profiles/README.md)
  — which default profile fields are research-confirmed vs. unverified
  placeholders, and how to fix them.
- [`reports/`](reports/) and [`research_notes/`](research_notes/) — the
  architecture research this project's design decisions are based on.

## Quick start

```bash
pip install -e .[dev]
pytest -q          # 293 tests, no Windows/hardware needed
ds4catia --dry-run  # try it without CATIA or a real controller
```

See [`docs/installation.md`](docs/installation.md) for the full setup,
including running against a real CATIA V5 instance on Windows.
