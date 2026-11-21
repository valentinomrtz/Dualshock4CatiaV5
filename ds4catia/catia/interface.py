"""Abstract interface towards CATIA V5.

This module defines the contract that any CATIA backend (a real COM
automation backend, or a fake/dry-run backend for tests) must implement.
It is deliberately kept free of any Windows/COM-specific code so it can be
imported and used on any platform, including for running the test suite
and developing the DualShock4 input-mapping layer without CATIA installed.

The method semantics below are derived from the project's own research on
CATIA V5's COM Automation API ("CAA Automation" / V5Automation), see
``research_notes/Mando DualShock4 para CATIA V5/catia_automation_api.md``
and the "CATIA V5 no tiene API para gamepads..." section of
``reports/Mando DualShock4 para CATIA V5.md``. A future ``com_backend.py``
implementing :class:`CatiaBackend` against the real ``CATIA.Application``
COM object should use those documents plus the per-method docstrings here
as its starting point.
"""

from abc import ABC, abstractmethod


class CatiaBackendError(Exception):
    """Base class for all errors raised by a :class:`CatiaBackend`."""


class CatiaConnectionError(CatiaBackendError):
    """Raised when an operation requires a live CATIA connection but none
    is available (CATIA not running, COM object lost/disconnected, or
    ``connect()`` was never called successfully)."""


class CatiaCommandError(CatiaBackendError):
    """Raised when CATIA rejects or fails to execute a requested operation
    (e.g. an unknown ``StartCommand`` id, a selection query that CATIA's
    COM layer errors out on, or a macro that raises inside CATIA)."""


class CatiaBackend(ABC):
    """Abstract backend that drives a CATIA V5 session.

    Concrete implementations:

    - A real backend (built by another agent, not part of this module)
      talks to a running CATIA V5 instance over COM, via the
      ``CATIA.Application`` automation object (see research notes).
    - :class:`ds4catia.catia.fake_backend.FakeCatiaBackend` is an in-memory
      stand-in used for tests and dry-run mode, with no CATIA/COM
      dependency at all.

    Every method other than ``connect``/``disconnect``/``is_connected`` is
    expected to require an active connection, and implementations should
    raise :class:`CatiaConnectionError` if called while not connected.
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish the connection to CATIA.

        On a real backend this corresponds to attaching to a running
        CATIA V5 instance via COM, e.g.
        ``win32com.client.Dispatch("CATIA.Application")`` /
        ``GetObject(, "CATIA.Application")`` (there is no "launch CATIA"
        step implied here; CATIA is expected to already be running).
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Release the connection to CATIA.

        On a real backend this drops the reference to the COM
        ``Application`` object. It does not quit CATIA itself.
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """Return whether the backend currently considers itself connected
        to CATIA, without making any CATIA call."""

    @abstractmethod
    def start_command(self, command_id: str) -> None:
        """Trigger a CATIA toolbar/menu command by internal id or display
        name, corresponding to ``Application.StartCommand(id)`` over COM.

        SAFETY NOTE (per research): this is only safe for one-shot,
        non-interactive commands (e.g. "Fit All In", a view preset, a
        single discrete action). It is explicitly NOT safe for commands
        that open a dialog or otherwise require further user interaction
        (multi-step/interactive tools) -- IBM support guidance cited in
        the research notes says ``StartCommand`` is for one-shot commands
        only. Internal command ids are more version-stable than localized
        display-name strings and should be preferred.
        """

    @abstractmethod
    def get_workbench_id(self) -> str:
        """Return the stable internal id of the currently active
        workbench, corresponding to ``Application.GetWorkbenchId()`` over
        COM (e.g. ``"PrtCfg"`` for Part Design, ``"GPSCfg"`` for
        Generative Structural Analysis -- confirmed by this project's
        research).

        There is no COM event for workbench changes, so any caller that
        needs to react to a workbench switch must poll this method.
        """

    @abstractmethod
    def camera_rotate(self, axis: tuple, angle_deg: float) -> None:
        """Rotate the active 3D viewer's camera around ``axis`` (an
        ``(x, y, z)`` tuple) by ``angle_deg`` degrees, corresponding to the
        3D viewer's ``Rotate(axis, angle)`` method (``Viewer3D`` /
        ``Viewpoint3D`` / ``Camera3D`` objects over COM). Research notes
        flag a documented rotation-accuracy caveat (resulting views can
        come out slightly off from the requested angle) and general COM
        call latency, so this is better suited to discrete "nudge" style
        rotations than to perfectly smooth continuous orbiting.
        """

    @abstractmethod
    def camera_translate(self, vector: tuple) -> None:
        """Translate (pan) the active 3D viewer's camera by ``vector`` (an
        ``(x, y, z)`` tuple), corresponding to the 3D viewer's
        ``Translate(vector)`` method over COM.
        """

    @abstractmethod
    def camera_zoom(self, factor: float) -> None:
        """Change the active viewpoint's zoom by ``factor``, corresponding
        to setting the ``Zoom`` property on a ``Viewpoint3D``/``Camera3D``
        object over COM (research notes describe the documented pattern of
        creating/adjusting a camera via ``ActiveWindow.ActiveViewer`` and
        reassigning its viewpoint).
        """

    @abstractmethod
    def selection_add(self, query: str) -> None:
        """Add the object(s) matched by ``query`` to the active document's
        selection, corresponding to ``Document.Selection.Add(...)`` over
        COM. ``query`` identifies what to add the same way a caller would
        identify an object for ``Add`` (e.g. a named object already found
        via :meth:`selection_search`, represented here as a string handle
        for backend-agnostic use); a real backend is responsible for
        resolving it to the COM object(s) to add.
        """

    @abstractmethod
    def selection_clear(self) -> None:
        """Empty the active document's selection, corresponding to
        ``Document.Selection.Clear()`` over COM.
        """

    @abstractmethod
    def selection_search(self, query: str) -> list:
        """Search the active document's specification tree and return the
        matches, corresponding to ``Document.Selection.Search(query)`` over
        COM (e.g. a query string such as ``"Name=Foo,all"``). Research
        notes flag a documented quirk where ``Search`` can return nothing
        until a manual interactive search has been run once after CATIA
        launched; callers should not assume an empty result always means
        "no matches".
        """

    @abstractmethod
    def run_macro(self, name: str) -> None:
        """Run a CATIA VBA macro by name, corresponding to invoking it via
        COM (e.g. ``Application.RunMacro`` or by proxying through
        ``SystemService.Evaluate()`` as ``pycatia`` itself does for calls
        not reachable directly). Research notes stress that CATIA macros
        run in-process and block the CATIA UI while executing, so macros
        bound to controller input should be kept short.
        """
