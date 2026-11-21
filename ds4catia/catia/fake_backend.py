"""In-memory fake :class:`CatiaBackend` for tests and dry-run mode.

This backend never touches CATIA or COM. It records every call it
receives so tests can assert on exactly what the rest of the application
asked CATIA to do, and it lets tests script return values (workbench id
sequences, selection search results) and inject one-shot failures.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from ds4catia.catia.interface import CatiaBackend, CatiaConnectionError


@dataclass
class CallRecord:
    """A single recorded call made against a :class:`FakeCatiaBackend`."""

    method: str
    args: tuple
    kwargs: dict
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "args": self.args,
            "kwargs": self.kwargs,
            "timestamp": self.timestamp,
        }


class FakeCatiaBackend(CatiaBackend):
    """Fake backend that simulates a CATIA connection in memory.

    Every method call (including ``connect``/``disconnect``) is appended
    to ``self.call_log`` as a :class:`CallRecord`. All methods other than
    ``connect`` and ``is_connected`` raise :class:`CatiaConnectionError`
    if called while not connected.
    """

    def __init__(self) -> None:
        self.call_log: list = []
        self._connected: bool = False
        self._workbench_sequence: list = ["PrtCfg"]
        self._workbench_index: int = 0
        self._pending_failure: Exception | None = None
        self.search_results: list = []

    # -- internal helpers -------------------------------------------------

    def _record(self, method: str, args: tuple, kwargs: dict) -> None:
        self.call_log.append(CallRecord(method=method, args=args, kwargs=kwargs))

    def _require_connected(self) -> None:
        if not self._connected:
            raise CatiaConnectionError(
                "FakeCatiaBackend is not connected; call connect() first."
            )

    def _maybe_fail(self) -> None:
        if self._pending_failure is not None:
            exc = self._pending_failure
            self._pending_failure = None
            raise exc

    def _guarded_call(self, method: str, *args, **kwargs) -> None:
        """Common bookkeeping for every non-connection method: require a
        live connection, record the call, then raise a scripted failure
        if one is pending."""
        self._require_connected()
        self._record(method, args, kwargs)
        self._maybe_fail()

    # -- test helpers -------------------------------------------------

    def set_workbench_sequence(self, ids: list) -> None:
        """Script successive ``get_workbench_id()`` return values.

        Each call to :meth:`get_workbench_id` consumes the next id in
        ``ids``; once the list is exhausted, the last id is repeated
        forever.
        """
        if not ids:
            raise ValueError("ids must be a non-empty list")
        self._workbench_sequence = list(ids)
        self._workbench_index = 0

    def fail_next_call(self, exc: Exception) -> None:
        """Make the next call to any method other than ``connect`` or
        ``is_connected`` raise ``exc`` exactly once, after which normal
        behavior resumes."""
        self._pending_failure = exc

    # -- CatiaBackend implementation --------------------------------------

    def connect(self) -> None:
        self._record("connect", (), {})
        self._connected = True

    def disconnect(self) -> None:
        # disconnect() does not require being connected: disconnecting an
        # already-disconnected backend is a harmless no-op, consistent
        # with real-world COM teardown idioms.
        self._record("disconnect", (), {})
        self._connected = False

    def is_connected(self) -> bool:
        self._record("is_connected", (), {})
        return self._connected

    def start_command(self, command_id: str) -> None:
        self._guarded_call("start_command", command_id)

    def get_workbench_id(self) -> str:
        self._guarded_call("get_workbench_id")
        if self._workbench_index < len(self._workbench_sequence) - 1:
            value = self._workbench_sequence[self._workbench_index]
            self._workbench_index += 1
        else:
            value = self._workbench_sequence[-1]
        return value

    def camera_rotate(self, axis: tuple, angle_deg: float) -> None:
        self._guarded_call("camera_rotate", axis, angle_deg)

    def camera_translate(self, vector: tuple) -> None:
        self._guarded_call("camera_translate", vector)

    def camera_zoom(self, factor: float) -> None:
        self._guarded_call("camera_zoom", factor)

    def selection_add(self, query: str) -> None:
        self._guarded_call("selection_add", query)

    def selection_clear(self) -> None:
        self._guarded_call("selection_clear")

    def selection_search(self, query: str) -> list:
        self._guarded_call("selection_search", query)
        return self.search_results

    def run_macro(self, name: str) -> None:
        self._guarded_call("run_macro", name)
