"""Synthetic input injection wrapper around the Win32 ``SendInput`` API.

Why this module exists
-----------------------
CATIA V5's COM Automation API does not cover smooth, continuous navigation
gestures (orbit/pan/zoom-by-drag). For that kind of input, this project
falls back to injecting low-level keyboard/mouse events the same way a
human would produce them, via the Win32 ``SendInput`` function.

``SendInput`` has two properties that make "fire and forget" injection
dangerous (see ``research_notes/.../constraints_limitations.md``, Key
Question 3, and the corresponding section of the research report):

1. **It always targets whatever window currently has the real, system-wide
   keyboard focus.** It is *not* possible to pass an arbitrary background
   HWND to ``SendInput`` and have the event delivered there. If CATIA is
   not the foreground/focused window at the moment of the call, the
   injected keys/mouse events go to whatever *is* focused instead
   (another application, the desktop, a dialog, ...). This is why this
   module centers everything around :func:`should_inject`: a pure,
   side-effect-free guard that must return ``True`` before any injection
   is attempted.

2. **It is subject to UIPI (User Interface Privilege Isolation).** Windows
   silently drops synthetic input sent from a process running at a lower
   integrity level than the target process (the textbook case: CATIA
   launched "as Administrator" while this app runs as a normal user).
   Crucially, per Microsoft's own documentation, this failure is
   *silent*: neither the ``SendInput`` return value nor ``GetLastError``
   will indicate that UIPI was the cause. A caller that only checks
   "did SendInput return without error" can be fooled into believing
   input landed when it did not. :func:`get_window_integrity_level` exists
   purely as a best-effort diagnostic so callers/operators can notice this
   situation and surface it to the user, rather than as something this
   module relies on to decide whether to inject.

Because of (1) and (2), the design rule this module follows is: **never
inject blind**. Every call goes through :class:`InputInjector`, which
checks :func:`should_inject` first and skips (reporting why, via
:class:`InjectionResult`) rather than injecting into the wrong window or
silently doing nothing with no record of it.

``PostMessage``/``SendMessage`` are deliberately *not* used as the
injection mechanism. Per Microsoft/Raymond Chen ("The Old New Thing"),
``PostMessage`` is explicitly documented as unreliable for simulating
real keyboard/mouse input (it does not update global keyboard state such
as ``GetAsyncKeyState``, and many applications simply ignore it), so it
is not a suitable primary mechanism here — only ``SendInput`` is.

Linux-friendly import story
----------------------------
This module is developed and unit-tested (for its pure logic) on Linux,
where none of the Windows-only modules it ultimately depends on
(``ctypes.wintypes``, ``win32gui``/``win32api`` from ``pywin32``, etc.)
are importable. Every Windows-only import below is therefore performed
lazily/defensively at module load time, falling back to ``None`` on
failure, so that simply ``import``-ing this module never raises on a
non-Windows system. Only the functions that actually *call* into Win32
will raise (``OSError``/``AttributeError``) if invoked on a platform
where those symbols are unavailable -- which is expected and intentional:
this module cannot be meaningfully exercised end-to-end outside Windows.
"""

from __future__ import annotations

import ctypes
import platform
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Lazy / defensive imports of Windows-only modules.
#
# On a non-Windows system (this dev environment, Linux) these will fail to
# import. We catch that and fall back to None so that importing *this*
# module never raises. Every function below that needs one of these must
# check for None and raise a clear, actionable error at call time instead
# of at import time.
# ---------------------------------------------------------------------------

try:
    import ctypes.wintypes as wintypes  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only on non-Windows
    wintypes = None  # type: ignore[assignment]

try:
    import win32gui  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only on non-Windows
    win32gui = None  # type: ignore[assignment]

try:
    import win32api  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only on non-Windows
    win32api = None  # type: ignore[assignment]

try:
    import win32process  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only on non-Windows
    win32process = None  # type: ignore[assignment]


def _is_windows() -> bool:
    return platform.system() == "Windows" or sys.platform == "win32"


def _user32():
    """Return ``ctypes.windll.user32``, raising a clear error off-Windows.

    Accessing ``ctypes.windll`` itself raises ``AttributeError`` on
    non-Windows platforms (it simply does not exist in the ``ctypes``
    module there), so this is wrapped to produce a clearer message.
    """
    try:
        return ctypes.windll.user32  # type: ignore[attr-defined]
    except AttributeError as exc:  # pragma: no cover - exercised only on non-Windows
        raise OSError(
            "ds4catia.input_injection.sendinput requires Windows "
            "(ctypes.windll.user32 is unavailable on this platform)."
        ) from exc


# ---------------------------------------------------------------------------
# Virtual-key / button constants (subset actually used by this module).
# ---------------------------------------------------------------------------

# INPUT.type values (winuser.h)
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# KEYBDINPUT.dwFlags (winuser.h)
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001

# MOUSEINPUT.dwFlags (winuser.h)
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

_MOUSE_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


# ---------------------------------------------------------------------------
# ctypes structures matching winuser.h exactly (field order/size/alignment
# matter: these are passed by pointer straight into the Win32 SendInput
# ABI, so any mismatch here corrupts the call on the Windows side).
# ---------------------------------------------------------------------------

if _is_windows() or wintypes is not None:
    ULONG_PTR = ctypes.c_size_t  # matches winnt.h's ULONG_PTR on both 32/64-bit

    class KEYBDINPUT(ctypes.Structure):
        """Mirrors ``KEYBDINPUT`` from ``winuser.h``."""

        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        """Mirrors ``MOUSEINPUT`` from ``winuser.h``."""

        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        """Mirrors ``HARDWAREINPUT`` from ``winuser.h`` (unused but part of
        the union; included for a faithful, complete layout)."""

        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_ushort),
            ("wParamH", ctypes.c_ushort),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        """Mirrors ``INPUT`` from ``winuser.h``.

        Note the real ``winuser.h`` struct is ``{ DWORD type; union {...} }``
        with the union anonymous/unnamed in C. ctypes does not support
        anonymous struct members pre-3.11 in a fully transparent way across
        versions, so the union is exposed as a named field (``u``) instead;
        callers in this module always go through the helper constructors
        below rather than poking the struct directly.
        """

        _anonymous_ = ("u",)
        _fields_ = [
            ("type", ctypes.c_ulong),
            ("u", _INPUT_UNION),
        ]

else:  # pragma: no cover - should not happen; defensive fallback
    KEYBDINPUT = None  # type: ignore[assignment]
    MOUSEINPUT = None  # type: ignore[assignment]
    HARDWAREINPUT = None  # type: ignore[assignment]
    INPUT = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Pure logic: the shared contract with the rest of the project.
# ---------------------------------------------------------------------------

def should_inject(foreground_hwnd: int, target_hwnd: int) -> bool:
    """Decide whether it is safe to call ``SendInput`` right now.

    ``SendInput`` injects into whatever window currently holds the real,
    system-wide keyboard focus -- it has no concept of "send this to
    HWND X" the way ``PostMessage`` does. That means injecting is *only*
    meaningful, and only safe, when the window we believe is in the
    foreground (``foreground_hwnd``, typically obtained via
    :func:`get_foreground_window`) is in fact the window we intend to
    control (``target_hwnd``, e.g. CATIA's main window).

    If we skipped this check, any mismatch (the user alt-tabbed away,
    a modal dialog from another app grabbed focus, CATIA has not started
    yet, ...) would silently send keystrokes/mouse movement into whatever
    unrelated application happens to be focused -- which is far worse
    than doing nothing, and would do so without any error being raised
    (SendInput itself does not fail just because the "wrong" window is
    focused; it happily injects into it).

    ``target_hwnd`` of ``0`` or ``None`` is explicitly treated as "no
    valid target" and never matches, even if the foreground window
    also happens to report a falsy handle (which should not normally
    happen for a real foreground window, but we do not want to rely on
    that and accidentally authorize injection against a null target).

    Args:
        foreground_hwnd: The HWND currently reported as having system
            keyboard focus (see :func:`get_foreground_window`).
        target_hwnd: The HWND of the window we intend to control (e.g.
            CATIA's main application window).

    Returns:
        ``True`` only if ``target_hwnd`` is a non-null handle and it is
        exactly the window currently in the foreground.
    """
    if target_hwnd in (0, None):
        return False
    return foreground_hwnd == target_hwnd


# ---------------------------------------------------------------------------
# Low-level senders. These touch real Win32 APIs and cannot be exercised
# on Linux; they are still written to the documented ABI so they are
# correct once run on Windows.
# ---------------------------------------------------------------------------

def _send_input(*inputs: "INPUT") -> int:
    """Call ``SendInput`` with the given array of ``INPUT`` structures.

    Returns the number of events successfully injected, as reported by
    ``SendInput`` itself. Note (see module docstring) that a return value
    equal to ``len(inputs)`` does *not* guarantee the input reached the
    intended application if UIPI silently blocked it against a
    higher-integrity-level target -- callers are expected to have already
    gone through :func:`should_inject` / :class:`InputInjector` and to use
    :func:`get_window_integrity_level` for diagnostics, not as a
    post-hoc success check.
    """
    user32 = _user32()
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, ctypes.pointer(arr), ctypes.sizeof(INPUT))
    return int(sent)


def _make_keybd_input(vk_code: int, key_up: bool) -> "INPUT":
    flags = KEYEVENTF_KEYUP if key_up else 0
    ki = KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = ki
    return inp


def _make_mouse_move_input(dx: int, dy: int, relative: bool) -> "INPUT":
    flags = MOUSEEVENTF_MOVE
    if not relative:
        flags |= MOUSEEVENTF_ABSOLUTE
    mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0)
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = mi
    return inp


def _make_mouse_button_input(button: str, down: bool) -> "INPUT":
    try:
        down_flag, up_flag = _MOUSE_BUTTON_FLAGS[button]
    except KeyError as exc:
        raise ValueError(
            f"Unknown mouse button {button!r}; expected one of "
            f"{sorted(_MOUSE_BUTTON_FLAGS)!r}"
        ) from exc
    flags = down_flag if down else up_flag
    mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0)
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = mi
    return inp


def send_key(vk_code: int, key_up: bool = False) -> None:
    """Inject a single keyboard event for the given virtual-key code.

    This always lands on whichever window currently has real system
    keyboard focus (see module docstring) -- callers must have already
    verified that is the intended target, normally via
    :func:`should_inject` or :class:`InputInjector`.

    Args:
        vk_code: A Win32 virtual-key code (``VK_*`` constant).
        key_up: If ``False`` (default) sends a key-down event; if
            ``True`` sends a key-up event.
    """
    _send_input(_make_keybd_input(vk_code, key_up))


def send_key_combo(vk_codes: list) -> None:
    """Press a combination of keys together, then release it.

    All keys in ``vk_codes`` are pressed down in the given order first,
    then released in the *reverse* order (last pressed, first released),
    mirroring how a human holding e.g. Ctrl+Shift+S would naturally
    release the keys and matching the conventional modifier-key release
    order used by most input-simulation libraries.

    Args:
        vk_codes: Virtual-key codes to press, in press order.
    """
    for vk in vk_codes:
        send_key(vk, key_up=False)
    for vk in reversed(vk_codes):
        send_key(vk, key_up=True)


def send_mouse_move(dx: int, dy: int, relative: bool = True) -> None:
    """Inject a mouse-move event.

    Args:
        dx: Horizontal movement. Relative pixel delta if ``relative`` is
            ``True`` (the default, used for continuous orbit/pan-style
            navigation); otherwise an absolute coordinate normalized to
            the 0..65535 range per ``MOUSEEVENTF_ABSOLUTE`` semantics
            (callers are responsible for that normalization).
        dy: Vertical movement, same semantics as ``dx``.
        relative: Whether ``dx``/``dy`` are relative deltas (``True``) or
            absolute normalized coordinates (``False``).
    """
    _send_input(_make_mouse_move_input(dx, dy, relative))


def send_mouse_button(button: str, down: bool) -> None:
    """Inject a mouse button press or release.

    Args:
        button: One of ``"left"``, ``"right"``, ``"middle"``.
        down: ``True`` for button-down, ``False`` for button-up.
    """
    _send_input(_make_mouse_button_input(button, down))


def get_foreground_window() -> int:
    """Return the HWND of the window that currently has system keyboard
    focus (``GetForegroundWindow``).

    This is the authoritative value :func:`should_inject` must be checked
    against before any injection: ``SendInput`` always lands here,
    regardless of what HWND the caller "meant" to target.
    """
    if win32gui is not None:
        return int(win32gui.GetForegroundWindow())
    return int(_user32().GetForegroundWindow())


# Integrity level RIDs (winnt.h, SECURITY_MANDATORY_*_RID), used to turn a
# raw SID RID into something comparable/human-readable for diagnostics.
_INTEGRITY_UNTRUSTED_RID = 0x00000000
_INTEGRITY_LOW_RID = 0x00001000
_INTEGRITY_MEDIUM_RID = 0x00002000
_INTEGRITY_MEDIUM_PLUS_RID = 0x00002100
_INTEGRITY_HIGH_RID = 0x00003000
_INTEGRITY_SYSTEM_RID = 0x00004000


def get_window_integrity_level(hwnd: int) -> Optional[int]:
    """Best-effort lookup of the Windows integrity level (RID) of the
    process owning ``hwnd``.

    This exists purely as a *diagnostic* aid: per Microsoft's own
    ``SendInput`` documentation, UIPI blocks synthetic input from a
    lower-integrity-level process into a higher-integrity-level one
    *silently* -- no error is raised and ``GetLastError`` gives no
    indication this happened. So if orbit/pan input stops reaching CATIA
    for no apparent reason, comparing this process's integrity level
    against CATIA's (via this function) is the way to confirm "CATIA is
    probably running elevated / at a higher integrity level" as the root
    cause, rather than chasing a phantom bug elsewhere.

    This is deliberately forgiving: any failure along the way (window
    gone, access denied to open the process/token, API unavailable,
    running on a non-Windows platform, etc.) results in ``None`` rather
    than an exception, since this is a "nice to have" diagnostic, not a
    gate that :class:`InputInjector` depends on to decide whether to
    inject (that decision is made solely by :func:`should_inject`).

    Returns:
        The integrity level as a raw RID integer (comparable against the
        ``_INTEGRITY_*_RID`` constants in this module; higher means more
        privileged), or ``None`` if it could not be determined.
    """
    try:
        user32 = _user32()
        advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]

        # 1. Window -> owning process id.
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        # 2. Open the process so we can get at its primary token.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hproc = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not hproc:
            return None

        try:
            TOKEN_QUERY = 0x0008
            htoken = ctypes.c_void_p()
            if not advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(htoken)):
                return None

            try:
                # 3. TokenIntegrityLevel == 25 (TOKEN_INFORMATION_CLASS).
                TokenIntegrityLevel = 25
                size = ctypes.c_ulong(0)
                advapi32.GetTokenInformation(
                    htoken, TokenIntegrityLevel, None, 0, ctypes.byref(size)
                )
                if size.value == 0:
                    return None
                buf = ctypes.create_string_buffer(size.value)
                if not advapi32.GetTokenInformation(
                    htoken, TokenIntegrityLevel, buf, size.value, ctypes.byref(size)
                ):
                    return None

                # buf is a TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label }.
                # The first pointer-sized field is PSID; the integrity RID
                # is the SID's last sub-authority.
                sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                if not sid_ptr:
                    return None
                get_sub_authority_count = advapi32.GetSidSubAuthorityCount
                get_sub_authority_count.restype = ctypes.POINTER(ctypes.c_ubyte)
                count_ptr = get_sub_authority_count(sid_ptr)
                if not count_ptr:
                    return None
                count = count_ptr[0]
                if count == 0:
                    return None
                get_sub_authority = advapi32.GetSidSubAuthority
                get_sub_authority.restype = ctypes.POINTER(ctypes.c_ulong)
                rid_ptr = get_sub_authority(sid_ptr, count - 1)
                if not rid_ptr:
                    return None
                return int(rid_ptr[0])
            finally:
                ctypes.windll.kernel32.CloseHandle(htoken)  # type: ignore[attr-defined]
        finally:
            ctypes.windll.kernel32.CloseHandle(hproc)  # type: ignore[attr-defined]
    except Exception:
        # Deliberately broad: this is a best-effort diagnostic that must
        # never raise and never block real injection logic.
        return None


# ---------------------------------------------------------------------------
# High-level injector: the only thing the rest of the project is expected
# to call directly.
# ---------------------------------------------------------------------------

@dataclass
class InjectionResult:
    """Outcome of an :meth:`InputInjector.inject` call.

    Attributes:
        skipped: ``True`` if no Win32 injection call was made at all
            (because :func:`should_inject` returned ``False``).
        reason: ``None`` when ``skipped`` is ``False``; a short machine
            -readable code (e.g. ``"not_foreground"``) explaining why the
            call was skipped otherwise.
    """

    skipped: bool
    reason: Optional[str]


class InputInjector:
    """Guards and dispatches ``SendInput``-based injection for a single
    target window.

    This class intentionally knows nothing about *how* the target window
    (CATIA) is located -- that responsibility belongs to the CATIA/COM
    integration layer elsewhere in the project. Instead it is handed a
    ``target_hwnd_getter`` callable, so the caller decides how the current
    CATIA HWND is obtained (e.g. re-resolved on every call, cached, chosen
    among several CATIA windows, etc.) and this class stays decoupled from
    that logic.

    Every call to :meth:`inject` first re-checks :func:`should_inject`
    against a freshly read foreground window -- never relying on a cached
    "we were focused a moment ago" assumption, since focus can change
    between controller input ticks.
    """

    def __init__(
        self,
        target_hwnd_getter: Callable[[], int],
        foreground_getter: Optional[Callable[[], int]] = None,
    ) -> None:
        """Create an injector bound to a particular target window.

        Args:
            target_hwnd_getter: Zero-argument callable returning the
                current HWND of the window this injector should control
                (e.g. CATIA's main window). Called fresh on every
                :meth:`inject` call, so it can reflect CATIA
                starting/restarting/changing windows over time.
            foreground_getter: Zero-argument callable returning the
                current system foreground HWND. Defaults to
                :func:`get_foreground_window` (the real Win32 call).
                Overriding this is primarily meant for unit tests on
                platforms where the real Win32 call is unavailable.
        """
        self._target_hwnd_getter = target_hwnd_getter
        self._foreground_getter = foreground_getter or get_foreground_window

    def inject(self, action: Any) -> InjectionResult:
        """Inject a single input action if, and only if, the target
        window is currently the real system foreground window.

        Args:
            action: A simple mapping (dict) or object exposing at least a
                ``"kind"`` entry/attribute, one of ``"key"``,
                ``"mouse_move"``, ``"mouse_button"``, plus the fields that
                kind needs:

                - ``"key"``: ``vk_code`` (int), optional ``key_up`` (bool,
                  default ``False``).
                - ``"mouse_move"``: ``dx`` (int), ``dy`` (int), optional
                  ``relative`` (bool, default ``True``).
                - ``"mouse_button"``: ``button`` (``"left"``/``"right"``/
                  ``"middle"``), ``down`` (bool).

        Returns:
            :class:`InjectionResult` with ``skipped=True,
            reason="not_foreground"`` if the target window was not the
            foreground window (nothing was sent to Win32 in that case);
            otherwise ``skipped=False, reason=None`` after dispatching to
            the matching ``send_*`` function.
        """
        target_hwnd = self._target_hwnd_getter()
        foreground_hwnd = self._foreground_getter()

        if not should_inject(foreground_hwnd, target_hwnd):
            return InjectionResult(skipped=True, reason="not_foreground")

        kind = _get_field(action, "kind")
        if kind == "key":
            send_key(
                _get_field(action, "vk_code"),
                key_up=_get_field(action, "key_up", False),
            )
        elif kind == "mouse_move":
            send_mouse_move(
                _get_field(action, "dx"),
                _get_field(action, "dy"),
                relative=_get_field(action, "relative", True),
            )
        elif kind == "mouse_button":
            send_mouse_button(
                _get_field(action, "button"),
                _get_field(action, "down"),
            )
        else:
            raise ValueError(f"Unknown action kind: {kind!r}")

        return InjectionResult(skipped=False, reason=None)


def _get_field(action: Any, name: str, default: Any = ...) -> Any:
    """Read ``name`` off ``action``, whether it's a dict or a plain object."""
    if isinstance(action, dict):
        if default is ...:
            return action[name]
        return action.get(name, default)
    if default is ...:
        return getattr(action, name)
    return getattr(action, name, default)
