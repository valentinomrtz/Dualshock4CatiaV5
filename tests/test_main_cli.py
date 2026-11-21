"""Smoke tests for the CLI wiring in ds4catia.main.

Only --dry-run / --headless paths are exercised here (no real hardware,
no blocking loop): building the daemon and checking it is wired with a
FakeCatiaBackend and the expected default profile."""

from __future__ import annotations

from ds4catia.app.daemon import Ds4CatiaDaemon
from ds4catia.catia.fake_backend import FakeCatiaBackend
from ds4catia.main import build_parser, _build_daemon


def test_build_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.dry_run is False
    assert args.headless is False
    assert args.connection is None


def test_build_daemon_dry_run_uses_fake_backend():
    parser = build_parser()
    args = parser.parse_args(["--dry-run", "--headless"])
    daemon = _build_daemon(args)
    assert isinstance(daemon, Ds4CatiaDaemon)
    assert isinstance(daemon.catia, FakeCatiaBackend)
    assert daemon.is_dry_run() is True
    assert daemon.engine.active_profile_id == "base"


def test_build_daemon_rejects_unknown_default_profile():
    import pytest

    parser = build_parser()
    args = parser.parse_args(["--dry-run", "--default-profile", "does_not_exist"])
    with pytest.raises(SystemExit):
        _build_daemon(args)
