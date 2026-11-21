"""CLI entrypoint for ds4catia.

Picks a dry-run (``FakeCatiaBackend``, no injector) or real
(``ComCatiaBackend`` + ``InputInjector`` + ``DS4Device``) stack
depending on the platform and flags, loads profiles, and runs the
daemon either headless or with the PySide6 GUI.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ds4catia.app.config import AppConfig
from ds4catia.app.daemon import Ds4CatiaDaemon, load_profiles_dir
from ds4catia.catia.fake_backend import FakeCatiaBackend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ds4catia",
        description="Control CATIA V5 with a DualShock 4 controller.",
    )
    parser.add_argument(
        "--profile",
        dest="profiles_dir",
        default=None,
        help="Directory of profile *.json files (default: bundled defaults).",
    )
    parser.add_argument(
        "--default-profile",
        dest="default_profile_id",
        default=None,
        help="profile_id to activate on startup (default: AppConfig's).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a FakeCatiaBackend and no input injection, logging actions instead.",
    )
    parser.add_argument(
        "--no-gui",
        "--headless",
        dest="headless",
        action="store_true",
        help="Run the background daemon without the PySide6 GUI/tray.",
    )
    parser.add_argument(
        "--connection",
        choices=["usb", "bt", "auto"],
        default=None,
        help="DS4 connection mode (default: AppConfig's, normally 'auto').",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List detected DS4 HID devices and exit.",
    )
    return parser


def _build_daemon(args: argparse.Namespace) -> Ds4CatiaDaemon:
    config = AppConfig()
    if args.profiles_dir:
        config.profiles_dir = args.profiles_dir
    if args.default_profile_id:
        config.default_profile_id = args.default_profile_id
    if args.connection:
        config.connection = args.connection
    if args.dry_run:
        config.dry_run = True

    profiles = load_profiles_dir(config.profiles_dir)
    if config.default_profile_id not in profiles:
        raise SystemExit(
            f"default profile {config.default_profile_id!r} not found in "
            f"{config.profiles_dir!r} (available: {sorted(profiles)})"
        )

    from ds4catia.mapping.engine import MappingEngine

    engine = MappingEngine(profiles)
    engine.set_active_profile(config.default_profile_id)

    injector = None
    hid_device = None
    if config.dry_run or sys.platform != "win32":
        catia = FakeCatiaBackend()
    else:  # pragma: no cover - exercised only on real Windows
        from ds4catia.catia.com_backend import ComCatiaBackend
        from ds4catia.hid.ds4_device import DS4Device
        from ds4catia.input_injection.sendinput import InputInjector, get_foreground_window

        catia = ComCatiaBackend()
        hid_device = DS4Device(transport=None if config.connection == "auto" else config.connection)

        def _catia_hwnd() -> int:
            # Best-effort: real CATIA HWND lookup is part of the manual
            # verification checklist, not something testable here. A
            # future pass can replace this with a proper FindWindow by
            # class name once that is confirmed against real CATIA.
            return get_foreground_window()

        injector = InputInjector(target_hwnd_getter=_catia_hwnd)

    daemon = Ds4CatiaDaemon(
        engine=engine,
        profiles=profiles,
        catia=catia,
        injector=injector,
        hid_device=hid_device,
        config=config,
    )
    daemon.set_dry_run(config.dry_run)
    return daemon


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_devices:
        from ds4catia.hid.ds4_device import discover_devices

        for device in discover_devices():
            print(device)
        return 0

    daemon = _build_daemon(args)

    if not args.headless:
        try:
            from ds4catia.gui.main_window import run_gui
        except ImportError as exc:
            print(f"GUI unavailable ({exc}); falling back to --headless.", file=sys.stderr)
        else:
            return run_gui(daemon)

    daemon.start()
    print(
        f"ds4catia running headless (dry_run={daemon.is_dry_run()}), "
        f"active profile: {daemon.engine.active_profile_id!r}. Ctrl+C to stop."
    )
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
