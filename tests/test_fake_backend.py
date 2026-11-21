"""Tests for ds4catia.catia.fake_backend.FakeCatiaBackend."""

import pytest

from ds4catia.catia.interface import CatiaBackend, CatiaConnectionError
from ds4catia.catia.fake_backend import FakeCatiaBackend


@pytest.fixture
def backend() -> FakeCatiaBackend:
    return FakeCatiaBackend()


@pytest.fixture
def connected_backend() -> FakeCatiaBackend:
    b = FakeCatiaBackend()
    b.connect()
    return b


# -- abstractness -----------------------------------------------------------


def test_catia_backend_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        CatiaBackend()


# -- connection state ---------------------------------------------------------


def test_starts_disconnected(backend):
    assert backend.is_connected() is False


def test_connect_sets_connected_state(backend):
    backend.connect()
    assert backend.is_connected() is True


def test_disconnect_clears_connected_state(connected_backend):
    connected_backend.disconnect()
    assert connected_backend.is_connected() is False


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


def test_call_after_disconnect_raises_connection_error(connected_backend):
    connected_backend.disconnect()
    with pytest.raises(CatiaConnectionError):
        connected_backend.start_command("Fit All In")


# -- call_log -----------------------------------------------------------------


def test_call_log_records_connect(backend):
    backend.connect()
    assert len(backend.call_log) == 1
    record = backend.call_log[0]
    assert record.method == "connect"
    assert record.args == ()
    assert record.kwargs == {}
    assert record.timestamp is not None


def test_call_log_records_args_and_kwargs(connected_backend):
    connected_backend.camera_rotate((1, 0, 0), 45.0)
    last = connected_backend.call_log[-1]
    assert last.method == "camera_rotate"
    assert last.args == ((1, 0, 0), 45.0)
    assert last.kwargs == {}


def test_call_log_records_multiple_calls_in_order(connected_backend):
    connected_backend.start_command("Fit All In")
    connected_backend.selection_clear()
    connected_backend.run_macro("DoThing")

    methods = [r.method for r in connected_backend.call_log]
    # index 0 is the connect() call made by the fixture
    assert methods == ["connect", "start_command", "selection_clear", "run_macro"]


def test_call_log_entries_expose_dict_view(connected_backend):
    connected_backend.start_command("Fit All In")
    last = connected_backend.call_log[-1]
    d = last.as_dict()
    assert d["method"] == "start_command"
    assert d["args"] == ("Fit All In",)
    assert d["kwargs"] == {}
    assert "timestamp" in d


# -- workbench sequence -------------------------------------------------------


def test_get_workbench_id_default(connected_backend):
    assert connected_backend.get_workbench_id() == "PrtCfg"


def test_set_workbench_sequence_returns_values_in_order(connected_backend):
    connected_backend.set_workbench_sequence(["PrtCfg", "GPSCfg", "PrtCfg"])
    assert connected_backend.get_workbench_id() == "PrtCfg"
    assert connected_backend.get_workbench_id() == "GPSCfg"
    assert connected_backend.get_workbench_id() == "PrtCfg"


def test_set_workbench_sequence_repeats_last_value_once_exhausted(connected_backend):
    connected_backend.set_workbench_sequence(["PrtCfg", "GPSCfg"])
    assert connected_backend.get_workbench_id() == "PrtCfg"
    assert connected_backend.get_workbench_id() == "GPSCfg"
    assert connected_backend.get_workbench_id() == "GPSCfg"
    assert connected_backend.get_workbench_id() == "GPSCfg"


def test_set_workbench_sequence_rejects_empty_list(connected_backend):
    with pytest.raises(ValueError):
        connected_backend.set_workbench_sequence([])


# -- fail_next_call -----------------------------------------------------------


def test_fail_next_call_raises_once_then_recovers(connected_backend):
    connected_backend.fail_next_call(CatiaConnectionError("boom"))

    with pytest.raises(CatiaConnectionError, match="boom"):
        connected_backend.start_command("Fit All In")

    # normal behavior resumes on the next call
    connected_backend.start_command("Fit All In")
    assert connected_backend.call_log[-1].method == "start_command"


def test_fail_next_call_with_custom_exception_type(connected_backend):
    class MyError(Exception):
        pass

    connected_backend.fail_next_call(MyError("custom failure"))
    with pytest.raises(MyError):
        connected_backend.selection_clear()

    # subsequent call behaves normally again
    connected_backend.selection_clear()


def test_fail_next_call_still_records_the_failing_call(connected_backend):
    connected_backend.fail_next_call(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        connected_backend.run_macro("DoThing")

    methods = [r.method for r in connected_backend.call_log]
    assert "run_macro" in methods


# -- selection_search ----------------------------------------------------------


def test_selection_search_defaults_to_empty_list(connected_backend):
    assert connected_backend.selection_search("Name=Foo,all") == []


def test_selection_search_returns_configured_results(connected_backend):
    connected_backend.search_results = ["Part1", "Part2"]
    assert connected_backend.selection_search("Name=Foo,all") == ["Part1", "Part2"]
