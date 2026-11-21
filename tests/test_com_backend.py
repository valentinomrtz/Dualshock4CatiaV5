"""Tests for ds4catia.catia.com_backend.ComCatiaBackend.

What CANNOT be verified in this (Linux, no Windows, no pywin32, no real
CATIA) environment, and is therefore NOT covered here -- left to the
user's manual verification checklist on an actual Windows + CATIA V5
machine:

- That ``win32com.client.Dispatch("CATIA.Application")`` /
  ``GetObject(Class="CATIA.Application")`` actually attach to a running
  CATIA V5 instance.
- That any of the real COM calls this backend makes (``StartCommand``,
  ``GetWorkbenchId``, the camera ``Rotate``/``Translate``/``Zoom``
  sequence, ``Selection.Add``/``Clear``/``Search``, ``RunMacro`` /
  ``SystemService.Evaluate``) do what CATIA's real COM surface actually
  does, including version-specific quirks documented in the research
  notes (rotation-accuracy caveats, ``Search`` returning nothing until a
  manual search has run once, etc.).
- Real ``pywintypes.com_error`` shapes/messages from an actual failing
  COM call.

What IS covered here, because it is this module's own logic rather than
real COM/Windows behavior:

- The module imports cleanly on Linux even though ``win32com`` is not
  installed (the lazy-import pattern mirrors
  ``ds4catia/input_injection/sendinput.py``).
- Instantiating :class:`ComCatiaBackend` never touches COM, so it never
  fails on Linux either.
- Every public method requires a prior successful ``connect()`` and
  raises :class:`CatiaConnectionError` otherwise -- this is this
  backend's own guard logic, not a real CATIA call.
- ``connect()`` without ``win32com`` installed raises
  ``CatiaConnectionError`` rather than letting ``ImportError``/
  ``AttributeError`` escape.
- Given a fake COM object injected directly into ``self._app`` (standing
  in for whatever ``win32com.client.Dispatch`` would have returned), each
  method calls the expected COM surface with the expected arguments, and
  translates a simulated COM-style failure into the contract's own
  exception types (:class:`CatiaConnectionError`/:class:`CatiaCommandError`)
  instead of leaking it raw. This exercises this module's own
  navigation/translation logic against a stand-in object, not Windows COM
  itself.
"""

import types

import pytest

from ds4catia.catia.interface import CatiaCommandError, CatiaConnectionError


def test_module_imports_without_error_on_linux():
    """Importing the module must succeed even without pywin32 installed."""
    import ds4catia.catia.com_backend  # noqa: F401


from ds4catia.catia.com_backend import ComCatiaBackend  # noqa: E402
from ds4catia.catia.com_backend import _ComError  # noqa: E402


def test_instantiation_does_not_touch_com():
    # Must not raise even though win32com is unavailable on this platform.
    backend = ComCatiaBackend()
    assert backend.is_connected() is False


# -- connect() without pywin32 -------------------------------------------------


def test_connect_without_pywin32_raises_connection_error(monkeypatch):
    import ds4catia.catia.com_backend as com_backend_module

    monkeypatch.setattr(com_backend_module, "win32com", None)
    backend = ComCatiaBackend()
    with pytest.raises(CatiaConnectionError):
        backend.connect()
    assert backend.is_connected() is False


# -- guard: every method requires connect() first -----------------------------


@pytest.fixture
def backend() -> ComCatiaBackend:
    return ComCatiaBackend()


@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.start_command("Fit All In"),
        lambda b: b.get_workbench_id(),
        lambda b: b.camera_rotate((0, 0, 1), 90.0),
        lambda b: b.camera_translate((1, 0, 0)),
        lambda b: b.camera_zoom(1.5),
        lambda b: b.selection_add("Name=Foo"),
        lambda b: b.selection_clear(),
        lambda b: b.selection_search("Name=Foo,all"),
        lambda b: b.run_macro("MyMacro"),
    ],
)
def test_calls_without_connecting_raise_connection_error(backend, call):
    with pytest.raises(CatiaConnectionError):
        call(backend)


def test_disconnect_clears_connection_state(backend):
    backend._app = object()  # simulate a live connection without real COM
    backend.disconnect()
    assert backend.is_connected() is False
    with pytest.raises(CatiaConnectionError):
        backend.start_command("Fit All In")


# -- fake COM object helpers ----------------------------------------------------


class FakeViewer:
    def __init__(self):
        self.rotate_calls = []
        self.translate_calls = []
        self.Viewpoint3D = types.SimpleNamespace(Zoom=1.0)
        self.updated = False

    def Rotate(self, axis, angle):
        self.rotate_calls.append((axis, angle))

    def Translate(self, vector):
        self.translate_calls.append(vector)

    def Update(self):
        self.updated = True


class FakeSelection:
    def __init__(self, search_results=None):
        self.added = []
        self.cleared = False
        self.search_results = search_results if search_results is not None else []
        self.search_queries = []

    def Add(self, query):
        self.added.append(query)

    def Clear(self):
        self.cleared = True

    def Search(self, query):
        self.search_queries.append(query)
        return self.search_results


class FakeDocument:
    def __init__(self, viewer=None, selection=None):
        self.ActiveWindow = types.SimpleNamespace(ActiveViewer=viewer)
        self.Selection = selection


class FakeApplication:
    """A plain Python object standing in for the COM ``Application``
    object ``win32com.client.Dispatch`` would normally return."""

    def __init__(self, active_document=None):
        self.Visible = True
        self.ActiveDocument = active_document
        self.start_command_calls = []
        self.run_macro_calls = []
        self.evaluate_calls = []
        self.workbench_id = "PrtCfg"
        self._fail_start_command = None
        self._run_macro_attr = True

    def StartCommand(self, command_id):
        if self._fail_start_command is not None:
            raise self._fail_start_command
        self.start_command_calls.append(command_id)

    def GetWorkbenchId(self):
        return self.workbench_id

    @property
    def RunMacro(self):
        if not self._run_macro_attr:
            raise AttributeError("RunMacro not exposed on this COM surface")

        def _run_macro(name):
            self.run_macro_calls.append(name)

        return _run_macro

    @property
    def SystemService(self):
        app = self

        class _SystemService:
            def Evaluate(self, vbscript):
                app.evaluate_calls.append(vbscript)

        return _SystemService()


def _connected_backend(app: FakeApplication) -> ComCatiaBackend:
    backend = ComCatiaBackend()
    backend._app = app
    return backend


# -- is_connected() liveness probe --------------------------------------------


def test_is_connected_true_for_live_app():
    backend = _connected_backend(FakeApplication())
    assert backend.is_connected() is True


def test_is_connected_false_and_clears_app_when_probe_raises():
    class DeadApp:
        @property
        def Visible(self):
            raise _ComError("the reference is dead")

    backend = ComCatiaBackend()
    backend._app = DeadApp()
    assert backend.is_connected() is False
    assert backend._app is None


# -- start_command / get_workbench_id ------------------------------------------


def test_start_command_calls_through_with_given_id():
    app = FakeApplication()
    backend = _connected_backend(app)
    backend.start_command("Fit All In")
    assert app.start_command_calls == ["Fit All In"]


def test_start_command_translates_attribute_error_to_command_error():
    class BrokenApp:
        Visible = True

        @property
        def StartCommand(self):
            raise AttributeError("no StartCommand on this surface")

    backend = _connected_backend(BrokenApp())
    with pytest.raises(CatiaCommandError):
        backend.start_command("Fit All In")


def test_get_workbench_id_returns_app_value():
    app = FakeApplication()
    app.workbench_id = "GPSCfg"
    backend = _connected_backend(app)
    assert backend.get_workbench_id() == "GPSCfg"


# -- camera ---------------------------------------------------------------------


def test_camera_rotate_calls_viewer_rotate_with_axis_as_list():
    viewer = FakeViewer()
    app = FakeApplication(active_document=FakeDocument(viewer=viewer))
    backend = _connected_backend(app)

    backend.camera_rotate((0, 0, 1), 90.0)

    assert viewer.rotate_calls == [([0, 0, 1], 90.0)]


def test_camera_translate_calls_viewer_translate():
    viewer = FakeViewer()
    app = FakeApplication(active_document=FakeDocument(viewer=viewer))
    backend = _connected_backend(app)

    backend.camera_translate((1, 2, 3))

    assert viewer.translate_calls == [[1, 2, 3]]


def test_camera_zoom_sets_zoom_and_reassigns_viewpoint():
    viewer = FakeViewer()
    app = FakeApplication(active_document=FakeDocument(viewer=viewer))
    backend = _connected_backend(app)

    backend.camera_zoom(2.5)

    assert viewer.Viewpoint3D.Zoom == 2.5
    assert viewer.updated is True


def test_camera_rotate_with_no_active_document_raises_command_error():
    app = FakeApplication(active_document=None)
    backend = _connected_backend(app)

    with pytest.raises(CatiaCommandError):
        backend.camera_rotate((0, 0, 1), 45.0)


def test_camera_translate_com_error_translated_to_command_error():
    class ExplodingViewer(FakeViewer):
        def Translate(self, vector):
            raise _ComError("CATIA refused the translate")

    app = FakeApplication(active_document=FakeDocument(viewer=ExplodingViewer()))
    backend = _connected_backend(app)

    with pytest.raises(CatiaCommandError):
        backend.camera_translate((1, 0, 0))


# -- selection --------------------------------------------------------------------


def test_selection_add_calls_through():
    selection = FakeSelection()
    app = FakeApplication(active_document=FakeDocument(selection=selection))
    backend = _connected_backend(app)

    backend.selection_add("Name=Foo,all")

    assert selection.added == ["Name=Foo,all"]


def test_selection_clear_calls_through():
    selection = FakeSelection()
    app = FakeApplication(active_document=FakeDocument(selection=selection))
    backend = _connected_backend(app)

    backend.selection_clear()

    assert selection.cleared is True


def test_selection_search_returns_list_of_results():
    selection = FakeSelection(search_results=["Part1", "Part2"])
    app = FakeApplication(active_document=FakeDocument(selection=selection))
    backend = _connected_backend(app)

    result = backend.selection_search("Name=Foo,all")

    assert result == ["Part1", "Part2"]
    assert selection.search_queries == ["Name=Foo,all"]


def test_selection_search_handles_none_result_as_empty_list():
    class NoneSearchSelection(FakeSelection):
        def Search(self, query):
            return None

    app = FakeApplication(
        active_document=FakeDocument(selection=NoneSearchSelection())
    )
    backend = _connected_backend(app)

    assert backend.selection_search("Name=Foo,all") == []


def test_selection_add_wrong_type_com_error_translated():
    class RejectingSelection(FakeSelection):
        def Add(self, query):
            raise _ComError("runtime error 438")

    app = FakeApplication(active_document=FakeDocument(selection=RejectingSelection()))
    backend = _connected_backend(app)

    with pytest.raises(CatiaCommandError):
        backend.selection_add("SomeDocumentNotAFeature")


# -- run_macro --------------------------------------------------------------------


def test_run_macro_uses_run_macro_when_available():
    app = FakeApplication()
    backend = _connected_backend(app)

    backend.run_macro("MyMacro")

    assert app.run_macro_calls == ["MyMacro"]
    assert app.evaluate_calls == []


def test_run_macro_falls_back_to_system_service_evaluate():
    app = FakeApplication()
    app._run_macro_attr = False
    backend = _connected_backend(app)

    backend.run_macro("MyMacro")

    assert app.run_macro_calls == []
    assert len(app.evaluate_calls) == 1
    assert "MyMacro" in app.evaluate_calls[0]


def test_run_macro_com_error_from_run_macro_itself_not_retried_via_evaluate():
    class FailingRunMacroApp(FakeApplication):
        @property
        def RunMacro(self):
            def _run_macro(name):
                raise _ComError("macro raised inside CATIA")

            return _run_macro

    app = FailingRunMacroApp()
    backend = _connected_backend(app)

    with pytest.raises(CatiaCommandError):
        backend.run_macro("MyMacro")

    assert app.evaluate_calls == []
