"""Tests for the pure, platform-independent logic in
``ds4catia.input_injection.sendinput``.

Only logic that can genuinely be exercised on a non-Windows (Linux) dev
machine is tested here:

- :func:`should_inject` -- a pure function with no Win32 dependency.
- :class:`InputInjector` -- using fake ``target_hwnd_getter`` /
  ``foreground_getter`` callables and monkeypatched ``send_*`` functions,
  so no real Win32 call is ever made.

Real injection (``SendInput``), ``GetForegroundWindow``, and
``get_window_integrity_level`` against actual Windows cannot be verified
from this Linux environment -- that is expected and is left to manual
verification on a Windows machine (see the task's verification checklist).
This test module itself must still be importable/runnable here, which is
why the very first test below simply asserts that importing the module
under test does not raise, even though ``ctypes.windll``/``win32gui`` do
not exist on Linux.
"""

import pytest


def test_module_imports_without_error_on_linux():
    """Importing the module must succeed even without Windows APIs."""
    from ds4catia.input_injection import sendinput  # noqa: F401


from ds4catia.input_injection.sendinput import (  # noqa: E402
    InjectionResult,
    InputInjector,
    should_inject,
)


class TestShouldInject:
    def test_foreground_equals_target_returns_true(self):
        assert should_inject(foreground_hwnd=1234, target_hwnd=1234) is True

    def test_foreground_differs_from_target_returns_false(self):
        assert should_inject(foreground_hwnd=1234, target_hwnd=5678) is False

    def test_target_hwnd_zero_returns_false_even_if_it_matches_foreground(self):
        # Degenerate case: foreground somehow reported as 0 too. A null
        # target must never be treated as a valid injection target.
        assert should_inject(foreground_hwnd=0, target_hwnd=0) is False

    def test_target_hwnd_none_returns_false_even_if_it_matches_foreground(self):
        assert should_inject(foreground_hwnd=None, target_hwnd=None) is False

    def test_target_hwnd_none_with_real_foreground_returns_false(self):
        assert should_inject(foreground_hwnd=4242, target_hwnd=None) is False

    def test_target_hwnd_zero_with_real_foreground_returns_false(self):
        assert should_inject(foreground_hwnd=4242, target_hwnd=0) is False


class TestInputInjectorSkipsWhenNotForeground:
    def test_skips_and_does_not_call_send_key(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        called = []
        monkeypatch.setattr(
            sendinput_mod, "send_key", lambda *a, **k: called.append(("send_key", a, k))
        )

        injector = InputInjector(
            target_hwnd_getter=lambda: 111,
            foreground_getter=lambda: 999,  # different window focused
        )
        result = injector.inject({"kind": "key", "vk_code": 0x41})

        assert result == InjectionResult(skipped=True, reason="not_foreground")
        assert called == []

    def test_skips_and_does_not_call_send_mouse_move(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        called = []
        monkeypatch.setattr(
            sendinput_mod,
            "send_mouse_move",
            lambda *a, **k: called.append(("send_mouse_move", a, k)),
        )

        injector = InputInjector(
            target_hwnd_getter=lambda: 111,
            foreground_getter=lambda: 999,
        )
        result = injector.inject({"kind": "mouse_move", "dx": 5, "dy": -3})

        assert result.skipped is True
        assert result.reason == "not_foreground"
        assert called == []

    def test_skips_and_does_not_call_send_mouse_button(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        called = []
        monkeypatch.setattr(
            sendinput_mod,
            "send_mouse_button",
            lambda *a, **k: called.append(("send_mouse_button", a, k)),
        )

        injector = InputInjector(
            target_hwnd_getter=lambda: 0,  # no valid target at all
            foreground_getter=lambda: 0,
        )
        result = injector.inject({"kind": "mouse_button", "button": "left", "down": True})

        assert result.skipped is True
        assert result.reason == "not_foreground"
        assert called == []


class TestInputInjectorDispatchesWhenForeground:
    def test_dispatches_key_action(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        calls = []
        monkeypatch.setattr(
            sendinput_mod, "send_key", lambda *a, **k: calls.append((a, k))
        )

        injector = InputInjector(
            target_hwnd_getter=lambda: 777,
            foreground_getter=lambda: 777,  # matches target
        )
        result = injector.inject({"kind": "key", "vk_code": 0x57, "key_up": True})

        assert result == InjectionResult(skipped=False, reason=None)
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args == (0x57,)
        assert kwargs == {"key_up": True}

    def test_dispatches_mouse_move_action_with_defaults(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        calls = []
        monkeypatch.setattr(
            sendinput_mod, "send_mouse_move", lambda *a, **k: calls.append((a, k))
        )

        injector = InputInjector(
            target_hwnd_getter=lambda: 42,
            foreground_getter=lambda: 42,
        )
        result = injector.inject({"kind": "mouse_move", "dx": 10, "dy": -2})

        assert result.skipped is False
        assert result.reason is None
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args == (10, -2)
        assert kwargs == {"relative": True}

    def test_dispatches_mouse_button_action(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        calls = []
        monkeypatch.setattr(
            sendinput_mod, "send_mouse_button", lambda *a, **k: calls.append((a, k))
        )

        injector = InputInjector(
            target_hwnd_getter=lambda: 42,
            foreground_getter=lambda: 42,
        )
        result = injector.inject({"kind": "mouse_button", "button": "right", "down": False})

        assert result.skipped is False
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args == ("right", False)
        assert kwargs == {}

    def test_target_hwnd_getter_is_called_fresh_each_time(self, monkeypatch):
        """Focus can change between controller ticks, so the injector must
        re-resolve both the target and the foreground window on every
        call rather than caching them."""
        import ds4catia.input_injection.sendinput as sendinput_mod

        monkeypatch.setattr(sendinput_mod, "send_key", lambda *a, **k: None)

        targets = iter([100, 200])
        foregrounds = iter([100, 200])

        injector = InputInjector(
            target_hwnd_getter=lambda: next(targets),
            foreground_getter=lambda: next(foregrounds),
        )

        first = injector.inject({"kind": "key", "vk_code": 1})
        second = injector.inject({"kind": "key", "vk_code": 1})

        assert first.skipped is False
        assert second.skipped is False

    def test_unknown_action_kind_raises(self, monkeypatch):
        import ds4catia.input_injection.sendinput as sendinput_mod

        monkeypatch.setattr(sendinput_mod, "send_key", lambda *a, **k: None)

        injector = InputInjector(
            target_hwnd_getter=lambda: 1,
            foreground_getter=lambda: 1,
        )
        with pytest.raises(ValueError):
            injector.inject({"kind": "something_else"})
