"""Real COM Automation backend for CATIA V5, implementing
:class:`ds4catia.catia.interface.CatiaBackend` against the live
``CATIA.Application`` object on Windows.

Research basis
---------------
Every COM pattern used in this module comes from this project's own
research, not from memory/guesswork:

- ``research_notes/Mando DualShock4 para CATIA V5/catia_automation_api.md``
- The "CATIA V5 no tiene API para gamepads..." section of
  ``reports/Mando DualShock4 para CATIA V5.md``

Key points from that research this module follows:

- Connect via ``win32com.client.Dispatch("CATIA.Application")``, with a
  ``GetObject(Class="CATIA.Application")`` fallback (mirrors the
  VBScript idiom ``Set CATIA = GetObject(, "CATIA.Application")`` with a
  ``CreateObject`` fallback cited in the research notes).
- Use **late binding** only. ``win32com.client.Dispatch`` already performs
  late (dynamic) dispatch as long as no type-library wrapper has been
  generated/cached via ``makepy``/``gencache`` for this ProgID; this
  module never calls ``gencache.EnsureDispatch`` or imports a generated
  wrapper, specifically because the research notes cite a real project
  that had to switch from early-bound CATIA type-library references to
  late binding to stop breaking across CATIA service packs/releases
  (eng-tips thread 459898, R18->R26).
- ``Application.StartCommand(id)`` for one-shot, non-interactive commands
  only (IBM support guidance cited in the research notes and already
  documented on the abstract method in ``interface.py``).
- ``Application.GetWorkbenchId()`` for a one-shot read of the active
  workbench; there is no change-notification event for this.
- Camera control via ``ActiveDocument.ActiveWindow.ActiveViewer``, whose
  ``Rotate``/``Translate`` methods and whose ``Viewpoint3D``/``Camera3D``
  zoom factor are the documented (via pycatia/forum sources) way to drive
  the 3D viewport programmatically.
- Selection via ``ActiveDocument.Selection`` (``Add``/``Clear``/``Search``).
- Macros: ``Application.RunMacro`` is attempted first; if the installed
  COM surface does not expose it (older/alternate CATIA COM surfaces, or
  an environment where only ``SystemService.Evaluate`` reaches macros --
  the research notes note that ``pycatia`` itself proxies some calls
  through ``SystemService.Evaluate()`` because not everything is reachable
  directly from Python/COM), this module falls back to invoking the macro
  through ``Application.SystemService.Evaluate(<VBScript>)``.

Linux-friendly import story
----------------------------
This module is developed and unit-tested (for its own guard/translation
logic) on Linux, where ``win32com`` (part of ``pywin32``) is not
installed and CATIA does not exist. Exactly like
``ds4catia/input_injection/sendinput.py`` does for its Win32 imports, the
``win32com``/``pywintypes`` imports below are therefore performed lazily
and defensively at module load time, falling back to ``None`` on
failure, so that simply ``import``-ing this module never raises on a
non-Windows system. Only :meth:`ComCatiaBackend.connect` (and, only if it
somehow got called while disconnected despite the connection guard, any
other method making a real COM call) will raise at call time if
``win32com`` is unavailable -- which is expected and intentional: this
backend cannot be meaningfully exercised end-to-end outside Windows with
a real CATIA V5 installation.
"""

from __future__ import annotations

from typing import Any, Optional

from ds4catia.catia.interface import (
    CatiaBackend,
    CatiaCommandError,
    CatiaConnectionError,
)

# ---------------------------------------------------------------------------
# Lazy / defensive imports of the Windows-only COM modules.
#
# On a non-Windows system (this dev environment, Linux) these fail to
# import because pywin32 itself is not installed there. We catch that and
# fall back to None so that importing *this* module never raises. Every
# method below that needs one of these checks for None first and raises a
# clear CatiaConnectionError instead of letting an ImportError/NameError
# escape.
# ---------------------------------------------------------------------------

try:
    import win32com.client  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only without pywin32
    win32com = None  # type: ignore[assignment]

try:
    import pywintypes  # type: ignore[import-not-found]

    _ComError: type = pywintypes.com_error
except Exception:  # pragma: no cover - exercised only without pywin32
    pywintypes = None  # type: ignore[assignment]

    class _ComError(Exception):  # type: ignore[no-redef]
        """Stand-in for ``pywintypes.com_error`` when pywin32 is not
        installed (this Linux dev environment).

        ``pywintypes.com_error`` is the exception pywin32 raises for a
        failed COM call (HRESULT failure) and is what every method below
        actually catches and translates on a real Windows+pywin32
        install. On Linux ``pywintypes`` does not exist, so this module
        needs a concrete type to reference in its ``except`` clauses and
        in type-translation tests; this class is never raised by real
        code on Linux (there is no COM to fail), but it lets
        ``tests/test_com_backend.py`` simulate a COM-style failure
        (raising ``_ComError`` from a fake COM object) and assert that
        this backend's own translation logic turns it into
        :class:`CatiaConnectionError`/:class:`CatiaCommandError`, without
        requiring pywin32 to be installed.
        """


_CATIA_PROGID = "CATIA.Application"


class ComCatiaBackend(CatiaBackend):
    """Real :class:`CatiaBackend` that drives a running CATIA V5 instance
    over COM Automation (``CATIA.Application``).

    This only runs on Windows, with ``pywin32`` installed and a CATIA V5
    instance already running (this backend does not launch CATIA). Every
    public method other than ``connect``/``disconnect``/``is_connected``
    requires :meth:`is_connected` to be true and raises
    :class:`CatiaConnectionError` otherwise, mirroring the convention used
    by :class:`ds4catia.catia.fake_backend.FakeCatiaBackend` so callers can
    treat both backends identically.

    Any ``pywintypes.com_error`` raised by the underlying COM call is
    translated to :class:`CatiaConnectionError` or
    :class:`CatiaCommandError` (never left to propagate as a pywin32-
    specific exception type), so that code written against the abstract
    :class:`CatiaBackend` interface never needs to know whether it is
    talking to this real backend or to the fake one.
    """

    def __init__(self) -> None:
        self._app: Optional[Any] = None

    # -- internal helpers ---------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise CatiaConnectionError(
                "ComCatiaBackend is not connected to CATIA; call connect() "
                "first (and ensure a CATIA V5 instance is running)."
            )

    def _navigate(self, path_description: str, getter) -> Any:
        """Run ``getter`` (a zero-arg callable that walks a chain of COM
        attributes, e.g. ``ActiveDocument.ActiveWindow.ActiveViewer``) and
        translate any failure into a clear :class:`CatiaCommandError`.

        Navigating a COM attribute chain like this raises an
        ``AttributeError``-free but still opaque ``pywintypes.com_error``
        (or, via ``win32com``'s dynamic dispatch, occasionally an
        ``AttributeError``) when an intermediate step is missing -- e.g.
        there is no ``ActiveDocument`` because no document is open, or no
        ``ActiveWindow``/``ActiveViewer`` for the current document type.
        Rather than let either of those escape as a cryptic low-level
        error, this wraps both into a single, actionable
        :class:`CatiaCommandError`.
        """
        try:
            return getter()
        except _ComError as exc:
            raise CatiaCommandError(
                f"COM error while accessing {path_description}: {exc}"
            ) from exc
        except AttributeError as exc:
            raise CatiaCommandError(
                f"Could not access {path_description} (no active document/"
                f"window/viewer?): {exc}"
            ) from exc

    def _run_guarded(self, description: str, action) -> Any:
        """Run ``action`` (a zero-arg callable making one or more COM
        calls) with the standard connected-check + COM-error-translation
        wrapping shared by every public method below."""
        self._require_connected()
        try:
            return action()
        except _ComError as exc:
            raise CatiaCommandError(f"CATIA rejected {description}: {exc}") from exc
        except AttributeError as exc:
            raise CatiaCommandError(
                f"Unexpected CATIA COM object shape while performing "
                f"{description}: {exc}"
            ) from exc

    # -- connection lifecycle -------------------------------------------------

    def connect(self) -> None:
        """Attach to a running CATIA V5 instance over COM.

        Tries ``win32com.client.Dispatch("CATIA.Application")`` first
        (the standard Python/pywin32 attach pattern documented in the
        research notes); if that fails, falls back to
        ``win32com.client.GetObject(Class="CATIA.Application")`` (the
        ``GetObject``-only attach idiom, equivalent to the VBScript
        ``GetObject(, "CATIA.Application")`` pattern also cited there).
        Both are late-bound dynamic-dispatch calls -- no type library is
        generated or imported, by design (see module docstring).
        """
        if win32com is None:
            raise CatiaConnectionError(
                "pywin32 (win32com.client) is not available on this "
                "platform; ComCatiaBackend requires Windows with pywin32 "
                "installed and a running CATIA V5 instance."
            )

        last_error: Optional[Exception] = None
        try:
            app = win32com.client.Dispatch(_CATIA_PROGID)
        except _ComError as exc:
            last_error = exc
            app = None
        except Exception as exc:  # pragma: no cover - defensive, non-COM failure
            last_error = exc
            app = None

        if app is None:
            try:
                app = win32com.client.GetObject(Class=_CATIA_PROGID)
            except _ComError as exc:
                raise CatiaConnectionError(
                    "Could not connect to CATIA via COM (tried Dispatch and "
                    f"GetObject for {_CATIA_PROGID!r}); is CATIA V5 running? "
                    f"Last error: {exc}"
                ) from exc
            except Exception as exc:  # pragma: no cover - defensive
                raise CatiaConnectionError(
                    "Could not connect to CATIA via COM (tried Dispatch and "
                    f"GetObject for {_CATIA_PROGID!r}); is CATIA V5 running? "
                    f"Last error: {last_error or exc}"
                ) from exc

        self._app = app

    def disconnect(self) -> None:
        """Release the reference to the COM ``Application`` object.

        This does not quit CATIA; it simply drops this backend's handle
        to it, matching the contract documented on
        :meth:`CatiaBackend.disconnect`.
        """
        self._app = None

    def is_connected(self) -> bool:
        """Whether this backend currently holds a COM ``Application``
        reference that still looks alive.

        Beyond the simple ``self._app is not None`` check, this makes one
        trivial COM call (reading the ``Visible`` property) to detect a
        COM reference that has gone stale -- e.g. CATIA was closed from
        the taskbar after ``connect()`` was called, which does not clear
        ``self._app`` on its own. If that call raises, the reference is
        treated as dead and ``self._app`` is cleared so a later call
        re-raises :class:`CatiaConnectionError` consistently instead of
        this check silently swallowing the problem.

        This is a deliberate design choice beyond the bare minimum the
        abstract contract asks for ("without making any CATIA call" is
        satisfied by the ``FakeCatiaBackend`` in-memory flag; a real COM
        reference has no equivalent in-process flag, so a cheap liveness
        probe is the only way to avoid every other method failing with a
        confusing low-level COM error against a dead reference).
        """
        if self._app is None:
            return False
        try:
            self._app.Visible  # noqa: B018 - property access is the probe
            return True
        except _ComError:
            self._app = None
            return False
        except AttributeError:
            # Dynamic dispatch surfaced something unexpected; treat as dead
            # rather than letting an unrelated AttributeError leak out of
            # is_connected().
            self._app = None
            return False

    # -- commands / workbench -------------------------------------------------

    def start_command(self, command_id: str) -> None:
        """See :meth:`CatiaBackend.start_command`.

        SAFETY NOTE (inherited from the abstract contract and the
        project's research): only safe for one-shot, non-interactive
        commands. Prefer internal command ids over localized display
        names -- the research notes cite a case where a display-name
        string stopped working across a CATIA version change while the
        internal id kept working.
        """
        self._run_guarded(
            f"StartCommand({command_id!r})",
            lambda: self._app.StartCommand(command_id),
        )

    def get_workbench_id(self) -> str:
        """See :meth:`CatiaBackend.get_workbench_id`. One-shot read; there
        is no COM event for workbench changes, so callers must poll."""
        return self._run_guarded(
            "GetWorkbenchId()",
            lambda: self._app.GetWorkbenchId(),
        )

    # -- camera ---------------------------------------------------------------

    def _active_viewer(self) -> Any:
        """Return ``ActiveDocument.ActiveWindow.ActiveViewer``, the
        documented entry point (per research notes / pycatia) for camera
        control: it exposes ``Rotate``/``Translate`` directly and is also
        where a ``Viewpoint3D``/``Camera3D`` object's zoom factor is read
        from/written back to.
        """
        return self._navigate(
            "ActiveDocument.ActiveWindow.ActiveViewer",
            lambda: self._app.ActiveDocument.ActiveWindow.ActiveViewer,
        )

    def camera_rotate(self, axis: tuple, angle_deg: float) -> None:
        """See :meth:`CatiaBackend.camera_rotate`.

        Calls the active viewer's ``Rotate(axis, angle)`` method. Per the
        research notes, CATIA's own rotation-accuracy can come out
        slightly off from the requested angle -- this method does not
        attempt to compensate for that; callers that need an exact final
        orientation should verify/correct via a follow-up read rather than
        assuming this call lands exactly on ``angle_deg``.
        """

        def do() -> None:
            viewer = self._active_viewer()
            ax = list(axis)
            viewer.Rotate(ax, float(angle_deg))

        self._run_guarded(f"camera_rotate(axis={axis!r}, angle_deg={angle_deg!r})", do)

    def camera_translate(self, vector: tuple) -> None:
        """See :meth:`CatiaBackend.camera_translate`. Calls the active
        viewer's ``Translate(vector)`` method."""

        def do() -> None:
            viewer = self._active_viewer()
            viewer.Translate(list(vector))

        self._run_guarded(f"camera_translate(vector={vector!r})", do)

    def camera_zoom(self, factor: float) -> None:
        """See :meth:`CatiaBackend.camera_zoom`.

        Follows the documented pattern (research notes / pycatia
        ``Viewpoint3D``): read the viewer's current ``Viewpoint3D``, set
        its ``Zoom`` factor, then reassign it back onto the viewer so the
        change actually takes effect (per the forum-documented
        create/adjust-camera-then-reassign idiom, rather than assuming a
        zoom property is writable in place on the live viewer object).
        """

        def do() -> None:
            viewer = self._active_viewer()
            viewpoint = viewer.Viewpoint3D
            viewpoint.Zoom = float(factor)
            viewer.Viewpoint3D = viewpoint
            viewer.Update()

        self._run_guarded(f"camera_zoom(factor={factor!r})", do)

    # -- selection -------------------------------------------------------------

    def _selection(self) -> Any:
        """Return ``ActiveDocument.Selection``.

        Per the research notes, when a Product document's spec tree is
        showing, the correct ``Selection`` object to use is the one tied
        to that Product document -- using the wrong document's
        ``Selection`` gives "unpredictable" results. This backend always
        uses ``ActiveDocument.Selection``, which is correct as long as
        the intended document is in fact the active one; it is the
        caller's responsibility to ensure that (this backend has no way
        to know which document the operator "means").
        """
        return self._navigate(
            "ActiveDocument.Selection",
            lambda: self._app.ActiveDocument.Selection,
        )

    def selection_add(self, query: str) -> None:
        """See :meth:`CatiaBackend.selection_add`.

        ``query`` is passed straight through to ``Selection.Add``. Per
        the research notes, adding an object of the wrong type (e.g. a
        ``Document`` instead of a ``Product``/feature) raises a COM
        runtime error (438) rather than failing gracefully -- that is
        translated to :class:`CatiaCommandError` like any other COM
        failure here.
        """
        self._run_guarded(
            f"selection_add({query!r})",
            lambda: self._selection().Add(query),
        )

    def selection_clear(self) -> None:
        """See :meth:`CatiaBackend.selection_clear`."""
        self._run_guarded("selection_clear()", lambda: self._selection().Clear())

    def selection_search(self, query: str) -> list:
        """See :meth:`CatiaBackend.selection_search`.

        Per the research notes, ``Search`` has a documented quirk where it
        can return nothing until a manual interactive search has been run
        once after CATIA launched -- this method does not work around
        that (there is no known programmatic fix), it simply returns
        whatever CATIA gives back, converted to a plain ``list`` so
        callers get a backend-agnostic result type (the fake backend
        already returns a plain list).
        """

        def do() -> list:
            result = self._selection().Search(query)
            return list(result) if result is not None else []

        return self._run_guarded(f"selection_search({query!r})", do)

    # -- macros -----------------------------------------------------------------

    def run_macro(self, name: str) -> None:
        """See :meth:`CatiaBackend.run_macro`.

        Tries ``Application.RunMacro(name)`` first, since that is the
        direct, documented-by-analogy COM surface for running a named
        macro. Some CATIA COM surfaces/versions do not expose a callable
        ``RunMacro`` the way this backend expects (the research notes are
        explicit that "not all CATIA V5 functions have been exposed in
        the type libraries", and that ``pycatia`` itself proxies some
        otherwise-unreachable calls through
        ``Application.SystemService.Evaluate()``); if the direct call
        raises ``AttributeError`` (the COM object has no such member) this
        falls back to invoking the macro via
        ``SystemService.Evaluate(<VBScript>)``, matching that documented
        ``pycatia`` fallback pattern. A COM error (as opposed to a missing
        attribute) from ``RunMacro`` itself is treated as a real failure
        and is not retried via ``Evaluate``, since in that case the macro
        was found but failed to run.

        Per the research notes, CATIA macros run in-process and block the
        CATIA UI while executing -- any macro bound to controller input
        should be kept short.
        """

        def do() -> None:
            try:
                run_macro_fn = self._app.RunMacro
            except AttributeError:
                run_macro_fn = None

            if run_macro_fn is not None:
                run_macro_fn(name)
                return

            # Fallback: proxy through SystemService.Evaluate(), the same
            # mechanism pycatia itself uses for calls not reachable
            # directly from Python/COM.
            escaped = name.replace('"', '""')
            vbscript = f'CATIA.RunMacro "{escaped}"'
            self._app.SystemService.Evaluate(vbscript)

        self._run_guarded(f"run_macro({name!r})", do)
