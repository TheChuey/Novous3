"""Orchestration domain logic: live telemetry, run results and CodeBuilder run state,
the squad registry with persistent health metrics, the sub-agent runner, the
deterministic RAG retriever worker, the ordered squad workflow, the Code Development
Squad pipeline, the pipeline registry and the serialized background run service.

Module singletons (bottom of file): ``telemetry_bus``, ``run_results``,
``codebuilder_run_state``, ``squad_registry``, ``code_handoffs``, ``subagent_runner``,
``rag_retriever``, ``code_squad_pipeline``, ``pipeline_registry`` and ``run_service``.
"""

import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Dict, Iterator, Tuple, Type

import yaml

from core_engine import logic_context_window as context_window
from core_engine.logic_core_engine import (
    Agent,
    agent_library,
    apply_model_limits,
    serialized_ollama_chat,
    tool_catalog,
)
from modelProfiles.logic_modelProfiles import model_profiles
from core_engine.schemas_core_engine import AgentProfile
from orchestration.schemas_orchestration import (
    CodeSquadRunContext,
    OrchestrationRequest,
    OrchestrationResponse,
    TraceStep,
)
from ragMemory import interface_ragMemory as rag
from scratchpadMemory.logic_scratchpadMemory import code_scratchpad, scratchpad, squad_scratchpad

RAG_RETRIEVER_ID = "rag-retriever"
CODE_SQUAD_ID = "code-squad"
DEFAULT_MAX_PARALLEL = 1
COMPONENT_ROOT = Path(__file__).resolve().parent
LEGACY_DATA_DIR = COMPONENT_ROOT.parent / "agentMemory" / "data"
MAX_EDITOR_CONTENT_BYTES = 1_000_000


class EditorBufferTooLargeError(ValueError):
    """Raised when a CodeBuilder editor buffer exceeds the workflow limit."""


class RunCapacityError(RuntimeError):
    """Raised when no more background orchestration runs can be accepted."""


class AgentTurnCancelled(Exception):
    """Raised when a streamed agent turn is stopped by the user."""


class PipelineCancelled(Exception):
    """Raised inside the code-squad pipeline when the user stops the run."""


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


# --- Telemetry -----------------------------------------------------------------------

_EVENT_DATA_KEYS = {
    "AGENT_MESSAGE": {"summary", "status"},
    "TOOL_CALL_START": {"tool_name"},
    "TOOL_CALL_END": {"tool_name", "status", "duration_ms", "error_kind"},
    # Counts and codes only: no prompt, reply or error text crosses the telemetry boundary.
    "AGENT_USAGE": {
        "agent_id", "model", "num_ctx", "prompt_tokens", "peak_prompt_tokens", "completion_tokens",
        "calls", "tool_calls", "tool_errors", "tools_active", "tools_total", "fail_unknown_tool",
        "fail_bad_arguments", "fail_tool_error", "fail_model_error", "rounds_exhausted", "level", "flags",
    },
    "SCRATCHPAD_HANDOFF": {"status", "subagent_id", "result_count", "direction"},
    "ERROR": {"error", "status"},
    "RUN_STARTED": {"squad_id", "pattern"},
    "RUN_COMPLETE": {"status", "duration_ms"},
    "CODE_CHANGE_PREVIEW": {"path", "changed_lines", "file_exists"},
    "CODE_CHANGE_APPLIED": {"path", "changed_lines", "file_exists"},
    "EDITOR_CODE_PREVIEW": {"changed_lines"},
    "EDITOR_CODE_EXECUTION": {"status", "diagnostic_lines"},
    "EDITOR_CODE_UPDATE": {"content", "changed_lines"},
    "AGENT_STARTED": {"agent_id", "step", "attempt"},
    "AGENT_TOKEN": {"agent_id", "text"},
    "AGENT_DONE": {"agent_id", "step", "text"},
    "STEP_STATUS": {"step", "status", "attempt", "summary"},
}
_EVENT_TYPES = frozenset(_EVENT_DATA_KEYS)
# Streamed token chunks are delivered to live listeners only; AGENT_DONE carries
# the full text, so keeping them out of history stops them evicting real events.
_LIVE_ONLY_EVENTS = frozenset({"AGENT_TOKEN"})
_SAFE_STATUSES = {
    "running", "success", "nothing_found", "error", "no_context_found",
    "healthy", "degraded", "failing", "skipped", "cancelled",
}
_MULTILINE_LIMITS = {
    "text": 20_000,
    "content": 1_000_000,
}
_COUNT_KEYS = {
    "duration_ms", "result_count", "attempt", "num_ctx", "prompt_tokens", "peak_prompt_tokens",
    "completion_tokens", "calls", "tool_calls", "tool_errors", "tools_active", "tools_total",
    "fail_unknown_tool", "fail_bad_arguments", "fail_tool_error", "fail_model_error", "rounds_exhausted",
}
_ERROR_KINDS = {"unknown_tool", "bad_arguments", "tool_error", "model_error"}
# Handoff tools a squad agent always keeps, however small its model's tool limit is.
SQUAD_PINNED_TOOLS = ("read_squad_scratchpad", "write_squad_scratchpad")
_TRACE_CONTEXT: ContextVar[tuple[str, str] | None] = ContextVar(
    "orchestration_trace_context", default=None
)


def _bounded_text(value: Any, limit: int = 300) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


def _safe_identifier(value: Any, limit: int = 80) -> str:
    text = _bounded_text(value, limit)
    return text if re.fullmatch(r"[A-Za-z0-9_.-]+", text) else "unknown"


def _sse_frame(trace_id: str, event: dict[str, Any]) -> str:
    payload = f"event: telemetry\ndata: {json.dumps(event)}\n\n"
    if event["event_type"] in _LIVE_ONLY_EVENTS:
        return payload
    return f"id: {trace_id}:{event['sequence']}\n{payload}"


def parse_last_event_id(value: str | None) -> int:
    """Read the sequence number from an SSE Last-Event-ID such as 'tr_x:12'."""
    try:
        return max(0, int(str(value or "").rsplit(":", 1)[-1]))
    except ValueError:
        return 0


class TelemetryBus:
    """Bounded, thread-safe telemetry history and live trace subscriptions."""

    def __init__(
        self,
        max_traces: int = 100,
        max_events_per_trace: int = 500,
        max_listeners_per_trace: int = 8,
        max_listeners: int = 32,
    ):
        self._max_traces = max_traces
        self._max_events_per_trace = max_events_per_trace
        self._max_listeners_per_trace = max_listeners_per_trace
        self._max_listeners = max_listeners
        self._traces: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self._listeners: dict[str, set[queue.Queue]] = {}
        self._sequence: dict[str, int] = {}
        self._finished: set[str] = set()
        self._lock = threading.RLock()

    def _evict_old_traces(self) -> None:
        while len(self._traces) > self._max_traces:
            old_trace_id, _ = self._traces.popitem(last=False)
            if old_trace_id not in self._listeners:
                self._sequence.pop(old_trace_id, None)
                self._finished.discard(old_trace_id)

    @staticmethod
    def _offer(listener: queue.Queue, item: Any) -> None:
        """Queue an item, dropping the oldest one when the listener is full."""
        try:
            listener.put_nowait(item)
        except queue.Full:
            try:
                listener.get_nowait()
            except queue.Empty:
                pass
            listener.put_nowait(item)

    def initialize_trace(self, trace_id: str) -> None:
        with self._lock:
            if trace_id in self._traces:
                raise ValueError(f"Trace ID already exists: {trace_id}")
            self._traces[trace_id] = deque(maxlen=self._max_events_per_trace)
            self._sequence[trace_id] = 0
            self._evict_old_traces()

    @staticmethod
    def _sanitize(key: str, value: Any) -> Any:
        if key in _COUNT_KEYS:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0
        if key == "level":
            return value if value in {"ok", "warn", "crit"} else "unknown"
        if key == "error_kind":
            return value if value in _ERROR_KINDS else ""
        if key == "flags":
            return ",".join(_safe_identifier(flag) for flag in value[:12]) if isinstance(value, list) else ""
        if key in {"tool_name", "subagent_id", "squad_id", "agent_id", "step"}:
            return _safe_identifier(value)
        if key == "path":
            path = _bounded_text(value, 512).replace("\\", "/")
            parts = path.split("/")
            return (
                path
                if path
                and not path.startswith("/")
                and not (len(path) > 1 and path[1] == ":")
                and all(part not in {".", ".."} for part in parts)
                else ""
            )
        if key in {"changed_lines", "diagnostic_lines"}:
            return [
                max(1, int(line))
                for line in value[:120]
                if isinstance(line, int) and not isinstance(line, bool) and line > 0
            ] if isinstance(value, list) else []
        if key == "file_exists":
            return bool(value)
        if key == "status":
            status = _bounded_text(value, 40)
            return status if status in _SAFE_STATUSES else "unknown"
        if key in {"summary", "error"}:
            return _bounded_text(value, 500)
        if key in _MULTILINE_LIMITS:
            return str(value or "")[: _MULTILINE_LIMITS[key]]
        if key == "pattern":
            pattern = _bounded_text(value, 40)
            return pattern if pattern in {"sequential", "fanout", "router"} else "unknown"
        if key == "direction":
            direction = _bounded_text(value, 20)
            return direction if direction in {"read", "write"} else "unknown"
        return _bounded_text(value)

    def log_event(
        self,
        trace_id: str,
        event_type: str,
        sender: str,
        receiver: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if event_type not in _EVENT_TYPES:
            raise ValueError(f"Unknown telemetry event type: {event_type}")
        allowed = _EVENT_DATA_KEYS.get(event_type, {"status"})
        safe_data = {
            key: self._sanitize(key, data[key])
            for key in allowed
            if data and key in data
        }
        live_only = event_type in _LIVE_ONLY_EVENTS
        with self._lock:
            events = self._traces.get(trace_id)
            if events is None:
                events = deque(maxlen=self._max_events_per_trace)
                self._traces[trace_id] = events
                self._sequence[trace_id] = 0
            self._traces.move_to_end(trace_id)
            if not live_only:
                self._sequence[trace_id] += 1
            event = {
                "trace_id": trace_id,
                "sequence": self._sequence[trace_id],
                "event_type": event_type,
                "sender": _safe_identifier(sender),
                "receiver": _safe_identifier(receiver),
                "data": safe_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if not live_only:
                events.append(event)
            listeners = tuple(self._listeners.get(trace_id, ()))
            self._evict_old_traces()
            for listener in listeners:
                self._offer(listener, event)
            return event

    def has_trace(self, trace_id: str) -> bool:
        with self._lock:
            return trace_id in self._traces

    def has_listener_capacity(self, trace_id: str) -> bool:
        with self._lock:
            listeners = self._listeners.get(trace_id, set())
            total_listeners = sum(len(items) for items in self._listeners.values())
            return (
                len(listeners) < self._max_listeners_per_trace
                and total_listeners < self._max_listeners
            )

    def get_trace_history(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._traces.get(trace_id, ()))

    def finish_trace(self, trace_id: str) -> None:
        with self._lock:
            self._finished.add(trace_id)
            for listener in tuple(self._listeners.get(trace_id, ())):
                self._offer(listener, None)

    def subscribe(self, trace_id: str, after: int = 0) -> Iterator[str]:
        """Stream events newer than ``after`` so a reconnect does not replay history."""
        listener: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            if trace_id not in self._traces:
                raise KeyError(trace_id)
            listeners = self._listeners.setdefault(trace_id, set())
            total_listeners = sum(len(items) for items in self._listeners.values())
            if (
                len(listeners) >= self._max_listeners_per_trace
                or total_listeners >= self._max_listeners
            ):
                raise RuntimeError(f"Trace listener limit reached: {trace_id}")
            listeners.add(listener)
            history = list(self._traces[trace_id])
            finished = trace_id in self._finished

        sequence = max(0, int(after or 0))
        try:
            for event in history:
                if event["sequence"] <= sequence:
                    if event["event_type"] == "RUN_COMPLETE":
                        return
                    continue
                sequence = event["sequence"]
                yield _sse_frame(trace_id, event)
                if event["event_type"] == "RUN_COMPLETE":
                    return
            if finished:
                return

            keep_alive_count = 0
            while True:
                try:
                    event = listener.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    keep_alive_count += 1
                    if keep_alive_count >= 40:
                        return
                    continue
                keep_alive_count = 0
                if event is None:
                    with self._lock:
                        if trace_id in self._finished:
                            return
                    continue
                if event["event_type"] in _LIVE_ONLY_EVENTS:
                    yield _sse_frame(trace_id, event)
                    continue
                if event["sequence"] <= sequence:
                    continue
                sequence = event["sequence"]
                yield _sse_frame(trace_id, event)
                if event["event_type"] == "RUN_COMPLETE":
                    return
        finally:
            with self._lock:
                listeners = self._listeners.get(trace_id)
                if listeners:
                    listeners.discard(listener)
                    if not listeners:
                        self._listeners.pop(trace_id, None)

    @contextmanager
    def agent_context(self, trace_id: str, agent_id: str) -> Iterator[None]:
        token = _TRACE_CONTEXT.set((trace_id, agent_id))
        try:
            yield
        finally:
            _TRACE_CONTEXT.reset(token)

    def current_context(self) -> tuple[str, str] | None:
        return _TRACE_CONTEXT.get()


_LEVEL_RANK = {"ok": 0, "warn": 1, "crit": 2}
_FAILURE_KEYS = ("unknown_tool", "bad_arguments", "tool_error", "model_error")


def usage_event_data(agent_id: str, stats: dict[str, Any]) -> dict[str, Any]:
    """The AGENT_USAGE payload for one agent turn, from Agent.run_stats()."""
    failures = stats["failures"]
    data = {
        "agent_id": agent_id,
        "model": stats["model"],
        "num_ctx": stats["num_ctx"],
        "prompt_tokens": stats["prompt_tokens_last"] or 0,
        "peak_prompt_tokens": stats["peak_prompt_tokens"],
        "completion_tokens": stats["completion_tokens"],
        "calls": stats["llm_calls"],
        "tool_calls": stats["tool_calls"],
        "tool_errors": stats["tool_errors"],
        "tools_active": stats["tools_active"],
        "tools_total": stats["tools_total"],
        "rounds_exhausted": failures.get("rounds_exhausted", 0),
        "level": stats["level"],
        "flags": stats["flags"],
    }
    for kind in _FAILURE_KEYS:
        data[f"fail_{kind}"] = failures.get(kind, 0)
    return data


def stream_usage_data(agent_id: str, model: str, num_ctx: int, prompt: int | None,
                      completion: int | None) -> dict[str, Any]:
    """AGENT_USAGE for a text-only streamed turn, which has no Agent object to ask."""
    percent = round(prompt / num_ctx * 100, 1) if prompt and num_ctx else 0.0
    level, flags = context_window.window_status(percent)
    return usage_event_data(agent_id, {
        "model": model, "num_ctx": num_ctx, "prompt_tokens_last": prompt,
        "peak_prompt_tokens": prompt or 0, "completion_tokens": completion or 0, "llm_calls": 1,
        "tool_calls": 0, "tool_errors": 0, "tools_active": 0, "tools_total": 0,
        "failures": {}, "level": level, "flags": flags,
    })


def summarize_agent_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-agent and whole-run figures from one trace's AGENT_USAGE and handoff events."""
    agents: dict[str, dict[str, Any]] = {}
    scratchpad = {"read": 0, "write": 0}
    for event in events:
        event_type = event.get("event_type")
        data = event.get("data") or {}
        if event_type == "SCRATCHPAD_HANDOFF" and data.get("direction") in scratchpad:
            scratchpad[data["direction"]] += 1
        if event_type != "AGENT_USAGE":
            continue
        agent_id = str(data.get("agent_id") or event.get("sender") or "unknown")
        entry = agents.setdefault(agent_id, {
            "agent_id": agent_id, "model": "", "num_ctx": 0, "turns": 0, "llm_calls": 0,
            "prompt_tokens": 0, "peak_prompt_tokens": 0, "completion_tokens": 0,
            "tool_calls": 0, "tool_errors": 0, "failures": {kind: 0 for kind in _FAILURE_KEYS},
            "rounds_exhausted": 0, "level": "ok", "flags": [], "tools_active": 0, "tools_total": 0,
        })
        entry["turns"] += 1
        entry["tools_active"] = int(data.get("tools_active") or 0)
        entry["tools_total"] = int(data.get("tools_total") or 0)
        entry["model"] = str(data.get("model") or entry["model"])
        entry["num_ctx"] = int(data.get("num_ctx") or entry["num_ctx"])
        entry["llm_calls"] += int(data.get("calls") or 0)
        entry["prompt_tokens"] += int(data.get("prompt_tokens") or 0)
        entry["peak_prompt_tokens"] = max(entry["peak_prompt_tokens"], int(data.get("peak_prompt_tokens") or 0))
        entry["completion_tokens"] += int(data.get("completion_tokens") or 0)
        entry["tool_calls"] += int(data.get("tool_calls") or 0)
        entry["tool_errors"] += int(data.get("tool_errors") or 0)
        for kind in _FAILURE_KEYS:
            entry["failures"][kind] += int(data.get(f"fail_{kind}") or 0)
        entry["rounds_exhausted"] += int(data.get("rounds_exhausted") or 0)
        if _LEVEL_RANK.get(data.get("level"), 0) > _LEVEL_RANK[entry["level"]]:
            entry["level"] = data["level"]
        entry["flags"] = sorted(set(entry["flags"]) | {f for f in str(data.get("flags") or "").split(",") if f})
    for entry in agents.values():
        entry["percent_peak"] = (
            round(entry["peak_prompt_tokens"] / entry["num_ctx"] * 100, 1) if entry["num_ctx"] else 0.0
        )
    values = list(agents.values())
    worst = max(values, key=lambda entry: _LEVEL_RANK[entry["level"]], default=None)
    return {
        "agents": values,
        "summary": {
            "level": worst["level"] if worst else "ok",
            "percent_peak": max((entry["percent_peak"] for entry in values), default=0.0),
            "tool_calls": sum(entry["tool_calls"] for entry in values),
            "tool_errors": sum(entry["tool_errors"] for entry in values),
            "scratchpad_reads": scratchpad["read"],
            "scratchpad_writes": scratchpad["write"],
        },
    }


# --- Run results ---------------------------------------------------------------------

class OrchestrationRunResults:
    """Bounded in-memory storage for completed workflow results."""

    def __init__(self, max_results: int = 100):
        self._max_results = max_results
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def save(self, trace_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._results[trace_id] = result
            self._results.move_to_end(trace_id)
            while len(self._results) > self._max_results:
                self._results.popitem(last=False)

    def get(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock:
            result = self._results.get(trace_id)
            return dict(result) if result is not None else None

    def latest_for_squad(self, squad_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next(
                (
                    dict(result)
                    for result in reversed(self._results.values())
                    if result.get("squad_id") == squad_id
                ),
                None,
            )


# --- CodeBuilder run state -----------------------------------------------------------

class CodeBuilderRunState:
    """Bounded, trace-scoped CodeBuilder editor buffers, previews, execution results,
    handoff history and cancellation flags."""

    def __init__(self, max_traces: int = 100):
        self._max_traces = max_traces
        self._states: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def begin(
        self,
        trace_id: str,
        editor_code: str,
        user_query: str = "",
        workflow_action: str = "all",
        editor_path: str = "",
    ) -> None:
        if not isinstance(editor_code, str):
            raise ValueError("The CodeBuilder editor buffer must be text.")
        if len(editor_code.encode("utf-8")) > MAX_EDITOR_CONTENT_BYTES:
            raise EditorBufferTooLargeError("The CodeBuilder editor buffer exceeds the 1 MB limit.")
        with self._lock:
            self._states[trace_id] = {
                "editor_code": editor_code,
                "user_query": user_query,
                "workflow_action": workflow_action,
                "editor_path": editor_path,
                "workspace_source": None,
                "preview": None,
                "execution": None,
                "handoff": None,
                "handoffs": [],
                "cancelled": False,
            }
            self._states.move_to_end(trace_id)
            while len(self._states) > self._max_traces:
                self._states.popitem(last=False)

    def save_preview(self, trace_id: str, content: str, changed_lines: list[int]) -> bool:
        if not isinstance(content, str):
            raise ValueError("The editor preview must be text.")
        if len(content.encode("utf-8")) > MAX_EDITOR_CONTENT_BYTES:
            raise ValueError("The editor preview exceeds the 1 MB limit.")
        with self._lock:
            state = self._states.get(trace_id)
            if state is None:
                return False
            state["preview"] = {"content": content, "changed_lines": list(changed_lines)}
            self._states.move_to_end(trace_id)
            return True

    def _matches_source(self, state: dict[str, Any], code: str) -> bool:
        preview = state.get("preview")
        return (
            code == state["editor_code"]
            or (preview is not None and code == preview["content"])
            or code == state.get("workspace_source")
        )

    def save_execution(
        self,
        trace_id: str,
        code: str,
        result: dict[str, Any],
        allow_non_editor_source: bool = False,
    ) -> bool:
        with self._lock:
            state = self._states.get(trace_id)
            if state is None:
                return False
            source_matches_editor = self._matches_source(state, code)
            if not source_matches_editor and not allow_non_editor_source:
                return False
            state["execution"] = {**result, "source_matches_editor": source_matches_editor}
            self._states.move_to_end(trace_id)
            return True

    def save_workspace_source(self, trace_id: str, path: str, content: str) -> bool:
        def normalize(value: str) -> str:
            return value.replace("\\", "/").removeprefix("./").casefold()

        with self._lock:
            state = self._states.get(trace_id)
            if (
                state is None
                or not state.get("editor_path")
                or normalize(path) != normalize(str(state["editor_path"]))
            ):
                return False
            state["workspace_source"] = content
            self._states.move_to_end(trace_id)
            return True

    def matches_editor_source(self, trace_id: str, code: str) -> bool:
        with self._lock:
            state = self._states.get(trace_id)
            return state is not None and self._matches_source(state, code)

    def save_handoff(
        self, trace_id: str, subagent_id: str, task_type: str, status: str, payload: Any
    ) -> None:
        with self._lock:
            state = self._states.setdefault(
                trace_id,
                {"editor_code": "", "preview": None, "execution": None, "handoff": None, "handoffs": []},
            )
            handoff = {
                "subagent_id": subagent_id,
                "task_type": task_type,
                "status": status,
                "payload": payload,
            }
            handoffs = state.setdefault("handoffs", [])
            if handoffs and handoffs[-1] == handoff:
                state["handoff"] = handoffs[-1]
                return
            handoffs.append(handoff)
            state["handoff"] = handoff
            self._states.move_to_end(trace_id)

    def _get_copy(self, trace_id: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(trace_id)
            value = state.get(key) if state else None
            return dict(value) if value is not None else None

    def get_handoff(self, trace_id: str) -> dict[str, Any] | None:
        return self._get_copy(trace_id, "handoff")

    def get_handoffs(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            state = self._states.get(trace_id)
            handoffs = state.get("handoffs", []) if state else []
            return [dict(handoff) for handoff in handoffs]

    def get_preview(self, trace_id: str) -> dict[str, Any] | None:
        return self._get_copy(trace_id, "preview")

    def get_execution(self, trace_id: str) -> dict[str, Any] | None:
        return self._get_copy(trace_id, "execution")

    def get_editor_code(self, trace_id: str) -> str | None:
        with self._lock:
            state = self._states.get(trace_id)
            return state.get("editor_code") if state else None

    def cancel(self, trace_id: str) -> bool:
        with self._lock:
            state = self._states.get(trace_id)
            if state is None:
                return False
            state["cancelled"] = True
            return True

    def is_cancelled(self, trace_id: str) -> bool:
        with self._lock:
            state = self._states.get(trace_id)
            return bool(state and state.get("cancelled"))

    def discard(self, trace_id: str) -> None:
        with self._lock:
            self._states.pop(trace_id, None)


# --- Squad registry --------------------------------------------------------------------

class SquadRegistry:
    """Registered agent squads and persistent aggregate health metrics."""

    DATA_DIR = COMPONENT_ROOT / "data"
    METRICS_PATH = DATA_DIR / "orchestration_squad_metrics.json"
    SQUADS_PATH = DATA_DIR / "orchestration_squads.json"
    DEFAULT_DEFINITIONS = {
        "rag-squad": {
            "squad_id": "rag-squad",
            "name": "RAG Knowledge Squad",
            "description": "Retrieves evidence from knowledge branches and prepares grounded answers.",
            "primary_agent": "rag-presenter",
            "subagents": ["rag-retriever"],
            "default_pattern": "sequential",
        },
        CODE_SQUAD_ID: {
            "squad_id": CODE_SQUAD_ID,
            "name": "Code Development Squad",
            "description": "Plans code work, builds requested changes, and troubleshoots results.",
            "primary_agent": "code-planer",
            "subagents": ["code-builder", "tester", "code-troubleshooter"],
            "default_pattern": "sequential",
        },
    }

    def __init__(self):
        self._lock = threading.RLock()

    def migrate_legacy(self) -> list[str]:
        """Move squad files left by the former ``agentMemory`` package, once each."""
        moved = []
        for target in (self.SQUADS_PATH, self.METRICS_PATH):
            legacy = LEGACY_DATA_DIR / target.name
            if target.exists() or not legacy.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(target))
            moved.append(target.name)
        return moved

    def _read_json(self, path: Path, label: str) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read {label} '{path}': {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{label} must contain a JSON object: {path}")
        return data

    def _read_metrics(self) -> dict[str, dict[str, Any]]:
        return self._read_json(self.METRICS_PATH, "Squad metrics")

    def _read_definitions(self) -> dict[str, dict[str, Any]]:
        if not self.SQUADS_PATH.is_file():
            return {key: dict(value) for key, value in self.DEFAULT_DEFINITIONS.items()}
        saved = self._read_json(self.SQUADS_PATH, "Squad definitions")
        definitions = {}
        for squad_id, definition in saved.items():
            if not isinstance(definition, dict):
                raise RuntimeError(f"Invalid squad definition for '{squad_id}'.")
            definitions[squad_id] = dict(definition)
            definitions[squad_id]["default_pattern"] = self.normalize_pattern(
                definitions[squad_id].get("default_pattern", "sequential")
            )
            definitions[squad_id]["max_parallel"] = 1
            if definitions[squad_id].get("primary_agent"):
                definitions[squad_id]["subagents"] = [
                    member_id for member_id in definitions[squad_id].get("subagents", [])
                    if member_id != definitions[squad_id]["primary_agent"]
                ]
        self._migrate_code_squad_topology(definitions.get(CODE_SQUAD_ID))
        return definitions

    @staticmethod
    def _migrate_code_squad_topology(code_squad: dict[str, Any] | None) -> None:
        """Rewrite a saved pre-tester code squad to builder -> tester -> troubleshooter."""
        subagents = code_squad.get("subagents") if code_squad else None
        if not isinstance(subagents, list):
            return
        if not {"code-builder", "code-troubleshooter"} <= set(subagents):
            return
        if "code-error-triager" not in subagents and "tester" in subagents:
            return
        legacy_ids = {"code-builder", "code-troubleshooter", "code-error-triager", "tester"}
        extras = [agent_id for agent_id in subagents if agent_id not in legacy_ids]
        code_squad["subagents"] = ["code-builder", "tester", "code-troubleshooter", *extras]

    def list_agent_catalog(self) -> dict[str, list[dict[str, str]]]:
        agents = [
            {
                "id": str(agent["id"]),
                "name": str(agent.get("name") or agent["id"]),
                "description": str(agent.get("description") or ""),
            }
            for agent in agent_library.list_agents()
        ]
        root = agent_library.SUBAGENT_LIBRARY_ROOT
        if not root.is_dir():
            raise RuntimeError(f"Sub-agent library folder does not exist: {root}")
        subagents = []
        for path in sorted(root.glob("*.yaml")):
            try:
                definition = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise RuntimeError(f"Could not load sub-agent definition '{path}': {exc}") from exc
            if not isinstance(definition, dict):
                raise RuntimeError(f"Sub-agent definition must be a YAML object: {path}")
            agent_id = path.stem
            declared_id = str(definition.get("id") or agent_id)
            if declared_id != agent_id:
                raise RuntimeError(
                    f"Sub-agent id '{declared_id}' does not match definition filename '{path.name}'."
                )
            subagents.append(
                {
                    "id": agent_id,
                    "name": str(definition.get("name") or agent_id),
                    "description": str(definition.get("description") or ""),
                }
            )
        return {"agents": agents, "subagents": subagents}

    def list_squads(self) -> list[dict[str, Any]]:
        with self._lock:
            definitions = self._read_definitions()
            metrics = self._read_metrics()
        squads = []
        for squad_id, definition in definitions.items():
            values = metrics.get(squad_id, {})
            squads.append(
                {
                    "default_pattern": "sequential",
                    "max_parallel": DEFAULT_MAX_PARALLEL,
                    **definition,
                    "metrics": {
                        "total_runs": int(values.get("total_runs", 0)),
                        "successful_runs": int(values.get("successful_runs", 0)),
                        "failed_runs": int(values.get("failed_runs", 0)),
                        "average_duration_ms": float(values.get("average_duration_ms", 0)),
                        "last_duration_ms": int(values.get("last_duration_ms", 0)),
                        "last_status": str(values.get("last_status", "healthy")),
                    },
                }
            )
        return squads

    def get_squad(self, squad_id: str) -> dict[str, Any] | None:
        return next((squad for squad in self.list_squads() if squad["squad_id"] == squad_id), None)

    def get_squad_id_for_primary_agent(self, agent_id: str) -> str | None:
        return next(
            (squad["squad_id"] for squad in self.list_squads() if squad["primary_agent"] == agent_id),
            None,
        )

    SQUAD_PATTERNS = ("sequential",)

    @classmethod
    def normalize_pattern(cls, pattern: str | None) -> str:
        if pattern in {"sequential", "fanout", "router"}:
            return "sequential"
        raise ValueError(f"Pattern must be one of: {', '.join(cls.SQUAD_PATTERNS)}")

    def _validate_definition(self, name: str, primary_agent: str, subagents: list[str]) -> None:
        """Members may be sub-agents or other primary agents; the lead cannot be its own member."""
        if not name.strip():
            raise ValueError("Squad name is required.")
        catalog = self.list_agent_catalog()
        agent_ids = {agent["id"] for agent in catalog["agents"]}
        if primary_agent not in agent_ids:
            raise ValueError(f"Primary agent is not available: {primary_agent}")
        subagent_ids = {agent["id"] for agent in catalog["subagents"]}
        if len(set(subagents)) != len(subagents):
            raise ValueError("Sub-agent selections must not contain duplicates.")
        if primary_agent in subagents:
            raise ValueError("The primary agent cannot also be a squad member.")
        unknown = [agent_id for agent_id in subagents if agent_id not in subagent_ids]
        if unknown:
            raise ValueError(
                f"Squad members must be sub-agents; members not available as sub-agents: {', '.join(unknown)}"
            )

    def _validate_runtime(self, pattern: str, max_parallel: int) -> None:
        self.normalize_pattern(pattern)
        if int(max_parallel) < 1:
            raise ValueError("max_parallel must be at least 1.")

    def create_squad(
        self,
        name: str,
        description: str,
        primary_agent: str,
        subagents: list[str],
        default_pattern: str = "sequential",
        max_parallel: int = DEFAULT_MAX_PARALLEL,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        self._validate_definition(clean_name, primary_agent, subagents)
        default_pattern = self.normalize_pattern(default_pattern)
        self._validate_runtime(default_pattern, max_parallel)
        base_id = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
        if not base_id:
            raise ValueError("Squad name must contain letters or numbers.")
        with self._lock:
            definitions = self._read_definitions()
            squad_id = base_id
            suffix = 2
            while squad_id in definitions:
                squad_id = f"{base_id}-{suffix}"
                suffix += 1
            definitions[squad_id] = {
                "squad_id": squad_id,
                "name": clean_name,
                "description": description.strip(),
                "primary_agent": primary_agent,
                "subagents": list(subagents),
                "default_pattern": default_pattern,
                "max_parallel": 1,
            }
            _write_json_atomic(self.SQUADS_PATH, definitions)
        return self.get_squad(squad_id)

    UPDATABLE_FIELDS = ("name", "description", "primary_agent", "subagents", "default_pattern", "max_parallel")

    def update_squad(self, squad_id: str, **fields: Any) -> dict[str, Any] | None:
        """Change a registered squad's members, pattern or labels; ``None`` when it does not exist."""
        unknown = set(fields) - set(self.UPDATABLE_FIELDS)
        if unknown:
            raise ValueError(f"Squad fields cannot be updated: {', '.join(sorted(unknown))}")
        with self._lock:
            definitions = self._read_definitions()
            if squad_id not in definitions:
                return None
            updated = {**definitions[squad_id], **{k: v for k, v in fields.items() if v is not None}}
            updated["subagents"] = list(updated.get("subagents") or [])
            updated["default_pattern"] = self.normalize_pattern(updated.get("default_pattern", "sequential"))
            self._validate_definition(
                str(updated.get("name") or ""), str(updated.get("primary_agent") or ""), updated["subagents"]
            )
            updated["name"] = str(updated["name"]).strip()
            updated["description"] = str(updated.get("description") or "").strip()
            updated["max_parallel"] = 1
            self._validate_runtime(updated.get("default_pattern", "sequential"), updated["max_parallel"])
            definitions[squad_id] = updated
            _write_json_atomic(self.SQUADS_PATH, definitions)
        return self.get_squad(squad_id)

    def delete_squad(self, squad_id: str) -> bool:
        """Remove a registration and its metrics; agent definitions are never touched."""
        with self._lock:
            definitions = self._read_definitions()
            if squad_id not in definitions:
                return False
            del definitions[squad_id]
            _write_json_atomic(self.SQUADS_PATH, definitions)
            metrics = self._read_metrics()
            metrics.pop(squad_id, None)
            _write_json_atomic(self.METRICS_PATH, metrics)
        return True

    SUBAGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
    SUBAGENT_DEFAULT_TOOLS = ("read_squad_scratchpad", "write_squad_scratchpad")

    def create_subagent(self, subagent_id: str, name: str, description: str = "") -> dict[str, Any]:
        """Scaffold ``subAgentLibrary/<id>.yaml`` with the standard role/purpose/handoff prompt."""
        clean = str(subagent_id or "").strip().lower()
        if not self.SUBAGENT_ID_PATTERN.match(clean):
            raise ValueError("Sub-agent file name must be lowercase letters, digits, '-' or '_' (max 64).")
        # Prompt text lives inside a YAML block, so keep these to single lines.
        clean_name = " ".join(str(name or "").split())
        clean_description = " ".join(str(description or "").split())
        if not clean_name:
            raise ValueError("Sub-agent name must not be empty.")
        path = agent_library.SUBAGENT_LIBRARY_ROOT / f"{clean}.yaml"
        if path.exists():
            raise FileExistsError(f"Sub-agent already exists: subAgentLibrary/{clean}.yaml")

        role = clean_description or f"Complete the background tasks assigned to the {clean_name}."
        lines = [
            f"id: {clean}",
            f"name: {json.dumps(clean_name)}",
            f"description: {json.dumps(clean_description)}",
            "type: subagent",
            "mode: agent",
            "model: qwen2.5-coder:latest",
            "environment: workspace",
            "version: 1",
            "tools:",
            *(f"  - {tool}" for tool in self.SUBAGENT_DEFAULT_TOOLS),
            "prompt: |",
            f"  # {clean_name} Prompt",
            "",
            "  ## role",
            f"  You are the {clean_name}. {role}",
            "",
            "  ## purpose",
            "  Carry out the task you are given with the tools listed above and post the result to the shared scratchpad.",
            "",
            "  ## boundaries",
            "  - Only use the tools listed above.",
            "  - Never claim to have read, written or changed anything unless a tool result confirms it.",
            "  - Output clean, structured results without conversational chitchat.",
            "",
            "  ## output format & scratchpad handoff",
            "  Always call `write_squad_scratchpad` with these three values:",
            f'  agent: "{clean}"',
            '  title: "result"',
            "  content: your result as text or JSON",
        ]
        text = "\n".join(lines) + "\n"
        definition = yaml.safe_load(text)
        if definition.get("id") != clean or definition.get("name") != clean_name:
            raise ValueError("Could not build a valid sub-agent definition from that name.")
        agent_library.SUBAGENT_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
        return {"created": True, "subagent_id": clean, "path": f"subAgentLibrary/{clean}.yaml"}

    def delete_subagent(self, subagent_id: str) -> dict[str, Any]:
        """Delete a sub-agent YAML definition unless a registered squad still uses it."""
        clean = agent_library.normalize_agent_id(subagent_id)
        path = agent_library.SUBAGENT_LIBRARY_ROOT / f"{clean}.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"Sub-agent definition not found: subAgentLibrary/{clean}.yaml")
        with self._lock:
            users = [squad["name"] for squad in self.list_squads() if clean in squad.get("subagents", [])]
            if users:
                raise ValueError(
                    f"Sub-agent '{clean}' is used by squad(s): {', '.join(users)}. "
                    "Remove it from those squads first."
                )
            path.unlink()
        return {"deleted": True, "subagent_id": clean, "path": f"subAgentLibrary/{clean}.yaml"}

    def record_squad_run(self, squad_id: str, success: bool, duration_ms: int) -> None:
        if self.get_squad(squad_id) is None:
            raise ValueError(f"Unknown registered squad: {squad_id}")
        with self._lock:
            metrics = self._read_metrics()
            values = metrics.setdefault(
                squad_id,
                {
                    "total_runs": 0,
                    "successful_runs": 0,
                    "failed_runs": 0,
                    "average_duration_ms": 0,
                    "last_duration_ms": 0,
                    "last_status": "healthy",
                },
            )
            total = int(values.get("total_runs", 0)) + 1
            successful = int(values.get("successful_runs", 0)) + int(success)
            failed = int(values.get("failed_runs", 0)) + int(not success)
            duration = max(0, int(duration_ms))
            previous_average = float(values.get("average_duration_ms", 0))
            values.update(
                total_runs=total,
                successful_runs=successful,
                failed_runs=failed,
                average_duration_ms=round(((previous_average * (total - 1)) + duration) / total, 2),
                last_duration_ms=duration,
                last_status=(
                    "failing" if failed / total > 0.3
                    else "degraded" if failed / total > 0.1
                    else "healthy"
                ),
            )
            _write_json_atomic(self.METRICS_PATH, metrics)


# --- Code handoffs -----------------------------------------------------------------------

CODE_SUBAGENT_IDS = {"code-builder", "tester", "code-troubleshooter"}
TASK_TYPE_BY_AGENT = {
    "code-builder": "code_build",
    "tester": "code_test",
    "code-troubleshooter": "code_troubleshooting",
}


class CodeHandoffNormalizer:
    """Normalization and evidence helpers for Code Development handoffs.

    The local models used by the code squad do not always follow the handoff
    contract exactly. These helpers keep the stored audit payload intact while
    giving orchestration a predictable, evidence-aware view of each handoff.
    """

    REQUIRED_FIELDS = {
        "code_build": ("summary", "files_changed", "checks_run"),
        "code_test": ("summary", "checks_run", "observed_output"),
        "code_troubleshooting": (
            "observed_problem",
            "evidence_inspected",
            "root_cause",
            "files_changed",
            "checks_run",
        ),
    }
    ALIASES = {
        "files_changed": ("changed_files", "files_modified", "files"),
        "checks_run": ("tests_run", "checks", "tests"),
        "observed_output": ("output", "stdout", "result"),
        "evidence_inspected": ("evidence", "inspected"),
        "root_cause": ("cause",),
        "remaining_issues": ("issues",),
        "remaining_questions": ("questions",),
        "error": ("failure", "error_message"),
    }
    FAILURE_TEXT_FIELDS = ("error", "stderr", "traceback")
    FAILURE_LIST_FIELDS = ("diagnostics",)

    @staticmethod
    def _non_empty(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, dict, set)):
            return bool(value)
        return True

    def normalize_payload(self, payload: Any) -> dict[str, Any]:
        """Return a structured payload without discarding prose responses."""
        if isinstance(payload, dict):
            normalized = dict(payload)
        elif isinstance(payload, list):
            normalized = {"items": payload}
        elif isinstance(payload, str):
            text = payload.strip()
            if not text:
                normalized = {}
            elif text.startswith(("{", "[")):
                try:
                    decoded = json.loads(text)
                except (TypeError, ValueError):
                    normalized = {"summary": text, "raw_payload": text}
                else:
                    normalized = (
                        dict(decoded) if isinstance(decoded, dict)
                        else {"items": decoded} if isinstance(decoded, list)
                        else {"summary": str(decoded)}
                    )
            else:
                normalized = {"summary": text, "raw_payload": text}
        elif payload is None:
            normalized = {}
        else:
            normalized = {"summary": str(payload)}

        for target, aliases in self.ALIASES.items():
            if self._non_empty(normalized.get(target)):
                continue
            for alias in aliases:
                if self._non_empty(normalized.get(alias)):
                    normalized[target] = normalized[alias]
                    break

        if not self._non_empty(normalized.get("summary")):
            for candidate in ("message", "reason", "root_cause", "observed_problem"):
                if self._non_empty(normalized.get(candidate)):
                    normalized["summary"] = str(normalized[candidate])
                    break
        return normalized

    @staticmethod
    def execution_status(execution: dict[str, Any] | None) -> str:
        return str((execution or {}).get("status") or "").upper()

    def execution_failed(self, execution: dict[str, Any] | None) -> bool:
        if not execution:
            return False
        if self.execution_status(execution) == "ERROR":
            return True
        if execution.get("timed_out") or execution.get("stuck"):
            return True
        return_code = execution.get("return_code")
        return return_code is not None and return_code != 0

    def has_failure_evidence(
        self, handoff: dict[str, Any] | None, execution: dict[str, Any] | None = None
    ) -> bool:
        """Return True only for concrete failures, not speculative prose."""
        if self.execution_failed(execution):
            return True
        payload = self.normalize_payload((handoff or {}).get("payload"))
        if any(self._non_empty(payload.get(key)) for key in self.FAILURE_TEXT_FIELDS + self.FAILURE_LIST_FIELDS):
            return True
        if payload.get("timed_out") or payload.get("stuck"):
            return True
        return_code = payload.get("return_code")
        if return_code is not None and return_code != 0:
            return True
        status = str((handoff or {}).get("status") or "").lower()
        if status == "error" and self._non_empty(payload.get("observed_output")):
            return True
        return status == "error" and payload.get("handoff_source") == "orchestrator_fallback"

    def effective_status(
        self, handoff: dict[str, Any] | None, execution: dict[str, Any] | None = None
    ) -> str:
        """Return the status orchestration should use for routing decisions."""
        entry = handoff or {}
        status = str(entry.get("status") or "error")
        if (
            entry.get("subagent_id") == "tester"
            and status == "error"
            and not self.has_failure_evidence(entry, execution)
        ):
            return "nothing_found"
        return status

    def normalize_handoff(
        self, handoff: dict[str, Any] | None, execution: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return a copy with normalized payload, validation, and effective status."""
        entry = dict(handoff or {})
        payload = self.normalize_payload(entry.get("payload"))
        task_type = str(entry.get("task_type") or "")
        entry["payload"] = payload
        entry["missing_fields"] = [
            name
            for name in self.REQUIRED_FIELDS.get(task_type, ())
            if not self._non_empty(payload.get(name))
        ]
        entry["has_failure_evidence"] = self.has_failure_evidence(entry, execution)
        entry["effective_status"] = self.effective_status(entry, execution)
        return entry

    def normalize_handoffs(
        self, handoffs: list[dict[str, Any]], execution: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return [self.normalize_handoff(handoff, execution) for handoff in handoffs or []]

    @staticmethod
    def _tail(value: Any, limit: int) -> tuple[str, bool]:
        text = str(value or "")
        if len(text) <= limit:
            return text, False
        return text[-limit:], True

    def execution_evidence(
        self,
        execution: dict[str, Any] | None,
        text_limit: int = 8_000,
        diagnostic_limit: int = 20,
    ) -> dict[str, Any]:
        """Return a bounded, prompt-safe view of the latest execution result."""
        if not execution:
            return {}
        evidence: dict[str, Any] = {}
        for key in ("run_id", "status", "return_code", "timed_out", "duration_seconds", "source_matches_editor"):
            if key in execution:
                evidence[key] = execution[key]
        for key in ("stdout", "stderr", "error"):
            text, truncated = self._tail(execution.get(key), text_limit)
            if text:
                evidence[key] = text
                if truncated:
                    evidence[f"{key}_truncated"] = True
        diagnostics = execution.get("diagnostics")
        if isinstance(diagnostics, list) and diagnostics:
            evidence["diagnostics"] = diagnostics[:diagnostic_limit]
            if len(diagnostics) > diagnostic_limit:
                evidence["diagnostics_truncated"] = True
        if execution.get("stuck"):
            evidence["stuck"] = execution["stuck"]
        return evidence


# --- Sub-agent runner ------------------------------------------------------------------

class SubAgentRunner:
    """Runs one turn of a library agent (Markdown) or a background sub-agent (YAML)."""

    TOKEN_FLUSH_SECONDS = 0.15

    def load_agent(self, agent_id: str) -> Tuple[dict[str, Any], str]:
        """Resolve Markdown agent from library/ or YAML sub-agent from subAgentLibrary/."""
        clean = agent_library.normalize_agent_id(agent_id)
        primary_file = agent_library.LIBRARY_ROOT / f"{clean}.md"
        if primary_file.is_file():
            profile = agent_library.load_agent_definition_from_md(primary_file)
            meta = {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "mode": profile.mode,
                "model": profile.model,
                "tools": list(profile.tools),
                "role": profile.role,
                "purpose": profile.purpose,
                "boundaries": profile.boundaries,
                "max_tool_rounds": profile.extras.get("max_tool_rounds"),
            }
            return meta, profile.system_prompt

        subagent_yaml = agent_library.SUBAGENT_LIBRARY_ROOT / f"{clean}.yaml"
        if subagent_yaml.is_file():
            return agent_library.parse_subagent_yaml(clean)

        raise FileNotFoundError(
            f"Agent definition '{agent_id}' not found in library/ or subAgentLibrary/."
        )

    def read_latest_subagent_scratchpad(self) -> dict[str, Any]:
        memory = scratchpad.read_memory("latest_subagent_result")
        return memory.get("latest_subagent_result", {})

    def read_scratchpad_snapshot(self) -> dict[str, Any]:
        return scratchpad.read_memory()

    def _build_profile(self, agent_id: str, meta: dict[str, Any], body: str) -> AgentProfile:
        tools = meta.get("tools") or []
        if not isinstance(tools, list):
            tools = [tools]
        return AgentProfile(
            id=str(meta.get("id") or agent_id),
            name=str(meta.get("name") or agent_id),
            description=str(meta.get("description") or ""),
            mode=str(meta.get("mode") or "agent"),
            system_prompt=body,
            role=str(meta.get("role") or ""),
            purpose=str(meta.get("purpose") or ""),
            boundaries=str(meta.get("boundaries") or ""),
            model=str(meta.get("model") or ""),
            tools=[str(tool) for tool in tools],
            extras={"max_tool_rounds": meta["max_tool_rounds"]} if meta.get("max_tool_rounds") else {},
        )

    def run_agent_turn_stream(
        self,
        agent_id: str,
        prompt_input: str,
        trace_id: str,
        model: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        """Run one text-only agent turn, streaming batched AGENT_TOKEN telemetry.

        Code-squad agents answer with prose and fenced code; the orchestrator applies
        the code itself, so no tool calls are offered to the model here.
        """
        meta, body = self.load_agent(agent_id)
        profile = self._build_profile(agent_id, meta, body)
        messages = [
            {"role": "system", "content": profile.system_prompt},
            {"role": "user", "content": prompt_input},
        ]
        text = ""
        pending = ""
        usage_chunk = None
        last_flush = time.monotonic()
        resolved_model = model or profile.model or "qwen2.5-coder:latest"
        num_ctx = model_profiles.effective(resolved_model)["num_ctx"]
        stream = serialized_ollama_chat(
            model=resolved_model,
            messages=messages,
            stream=True,
            options={"num_ctx": num_ctx},
        )

        def flush() -> None:
            telemetry_bus.log_event(
                trace_id, "AGENT_TOKEN", agent_id, "orchestrator",
                {"agent_id": agent_id, "text": pending},
            )

        try:
            for chunk in stream:
                if should_stop and should_stop():
                    raise AgentTurnCancelled(agent_id)
                if context_window.read_field(chunk, "prompt_eval_count") is not None:
                    usage_chunk = chunk
                piece = (chunk.get("message") or {}).get("content") or ""
                if not piece:
                    continue
                text += piece
                pending += piece
                if time.monotonic() - last_flush >= self.TOKEN_FLUSH_SECONDS:
                    flush()
                    pending = ""
                    last_flush = time.monotonic()
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        if pending:
            flush()
        if trace_id and usage_chunk is not None:
            telemetry_bus.log_event(
                trace_id, "AGENT_USAGE", agent_id, "orchestrator",
                stream_usage_data(
                    agent_id, resolved_model, num_ctx,
                    context_window.read_field(usage_chunk, "prompt_eval_count"),
                    context_window.read_field(usage_chunk, "eval_count"),
                ),
            )
        return text

    @staticmethod
    def _emit_usage(trace_id: str | None, agent_id: str, agent_instance: Agent) -> None:
        if trace_id:
            telemetry_bus.log_event(
                trace_id, "AGENT_USAGE", agent_id, "orchestrator",
                usage_event_data(agent_id, agent_instance.run_stats()),
            )

    _SQUAD_SCRATCHPAD_TOOLS = frozenset(
        ("read_squad_scratchpad", "write_squad_scratchpad", "clear_squad_scratchpad")
    )

    _EXPECTED_OUTPUT: dict[str, tuple[str, str, str]] = {
        "inquisitive":          ("inquisitive",          "questions", "investigation questions"),
        "questionnaire":        ("questionnaire",        "result",    "questionnaire answers"),
        "problem-analyst":      ("problem-analyst",      "result",    "analysis report"),
        "problem-report-builder": ("problem-report-builder", "result", "HTML report"),
    }

    def _validate_agent_output(self, agent_id: str, trace_id: str | None, reply: str) -> str:
        normalized = agent_library.normalize_agent_id(agent_id)
        expected = self._EXPECTED_OUTPUT.get(normalized)
        if not expected or not trace_id:
            return reply
        sp_agent, sp_title, description = expected
        run_data = squad_scratchpad.get(trace_id)
        if run_data is None:
            return reply
        found = any(
            e.get("author") == sp_agent and e.get("title") == sp_title
            for e in run_data.get("entries", [])
        )
        if found:
            return reply
        error_msg = (
            f'{{"error": "Agent \\"{agent_id}\\" finished without writing its expected output '
            f'({description}) to the scratchpad. The investigation cannot continue.", '
            f'"agent": "{agent_id}", "expected_agent": "{sp_agent}", "expected_title": "{sp_title}"}}'
        )
        _print_trace(
            f"\033[91m[VALIDATION] {agent_id} did not write {sp_agent}/{sp_title} "
            f"— stopping chain.\033[0m"
        )
        return error_msg

    def _ensure_squad_run(
        self, trace_id: str | None, agent_id: str, meta: dict, profile: Any, prompt_input: str
    ) -> bool:
        if not trace_id:
            return False
        if not self._SQUAD_SCRATCHPAD_TOOLS.intersection(profile.tools):
            return False
        existing = squad_scratchpad.get(trace_id)
        if existing is not None:
            return False
        squad_id = meta.get("squad") or "ad-hoc"
        query = prompt_input[:200] if prompt_input else agent_id
        squad_scratchpad.begin(trace_id, squad_id, query)
        _print_trace(
            f"\033[92m[SQUAD RUN] Auto-started squad scratchpad {trace_id} "
            f"for {agent_id} (squad={squad_id})\033[0m"
        )
        return True

    def run_agent_turn(
        self,
        agent_id: str,
        prompt_input: str,
        parameters: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> str:
        if trace_id is None:
            # A sub-agent called from a running turn belongs to that run's trace.
            inherited = telemetry_bus.current_context()
            trace_id = inherited[0] if inherited else None
        context = telemetry_bus.agent_context(trace_id, agent_id) if trace_id else nullcontext()
        with context:
            if agent_library.normalize_agent_id(agent_id) == RAG_RETRIEVER_ID:
                return rag_retriever.run(prompt_input, parameters or {})

            meta, body = self.load_agent(agent_id)
            profile = self._build_profile(agent_id, meta, body)
            auto_started = self._ensure_squad_run(trace_id, agent_id, meta, profile, prompt_input)
            squad_run = squad_scratchpad.get(trace_id) if trace_id else None
            if squad_run:
                profile.tools = [name for name in profile.tools if name != "call_subagent"]
                profile.system_prompt += (
                    "\n\n## Squad run constraint\n"
                    "Do not call or delegate to other agents. The orchestrator controls the "
                    "workflow order; complete only your assigned step using the supplied handoff."
                )
            model = profile.model or "qwen2.5-coder:latest"
            agent_instance = Agent(model=model, tools=[], profile=profile)
            if profile.mode == "agent" and profile.tools:
                apply_model_limits(agent_instance, profile, model, SQUAD_PINNED_TOOLS)
            else:
                agent_instance.num_ctx = model_profiles.effective(model)["num_ctx"]
            try:
                reply = str(agent_instance.think(prompt_input))
            except Exception as exc:
                agent_instance.note_failure("model_error", "", str(exc))
                self._emit_usage(trace_id, agent_id, agent_instance)
                if auto_started and trace_id:
                    squad_scratchpad.finish(trace_id, "error")
                raise
            self._emit_usage(trace_id, agent_id, agent_instance)
            reply = self._validate_agent_output(agent_id, trace_id, reply)

            if auto_started and trace_id:
                squad_scratchpad.finish(trace_id, "completed")
            return reply


class RagRetriever:
    """Deterministic retrieval worker: searches a knowledge branch, filters by relevance
    and hands the result to the scratchpad for the presenting agent."""

    RAG_RELEVANCE_THRESHOLD = 0.25

    def run(self, task_prompt: str, parameters: dict) -> str:
        query = str(task_prompt or "").strip()
        payload = {"query": query, "knowledge_branch_id": "", "results": []}
        status = "error"
        try:
            if not isinstance(parameters, dict):
                raise ValueError("Sub-agent parameters must be a JSON object.")
            if not query:
                raise ValueError("A retrieval query is required.")
            branch_id = str(
                parameters.get("knowledge_branch_id")
                or rag.selected_source_id()
            )
            top_k = int(parameters.get("top_k", 5))
            result = rag.search_knowledge(query, top_k, source_id=branch_id)
            payload = {
                "query": query,
                "knowledge_branch_id": result["knowledge_base_id"],
                "knowledge_branch_name": result["knowledge_base"],
                "results": [
                    item for item in result["results"]
                    if float(item["score"]) >= self.RAG_RELEVANCE_THRESHOLD
                ],
                "skipped_incompatible_chunks": result.get(
                    "skipped_incompatible_chunks", 0
                ),
            }
            status = "success" if payload["results"] else "nothing_found"
        except Exception as exc:
            payload["error"] = f"{type(exc).__name__}: {exc}"

        scratchpad.store_subagent_result(
            subagent_id=RAG_RETRIEVER_ID,
            task_type="rag_retrieval",
            status=status,
            payload=payload,
        )
        trace_context = telemetry_bus.current_context()
        if trace_context:
            trace_id, agent_id = trace_context
            telemetry_bus.log_event(
                trace_id, "SCRATCHPAD_HANDOFF", agent_id, "scratchpad",
                {
                    "status": status,
                    "subagent_id": RAG_RETRIEVER_ID,
                    "result_count": len(payload.get("results", [])),
                    "direction": "write",
                },
            )
        return json.dumps(
            {
                "subagent_id": RAG_RETRIEVER_ID,
                "status": status,
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
        )


# --- Execution patterns (RAG squad and user-defined squads) ----------------------------

def _print_trace(message: str) -> None:
    """Print a trace line without failing on terminals that cannot encode box glyphs."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"), flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_count(scratchpad_entry: dict) -> int:
    payload = scratchpad_entry.get("payload")
    results = payload.get("results") if isinstance(payload, dict) else None
    return len(results) if isinstance(results, list) else int(bool(payload))


def _squad_for_request(request: OrchestrationRequest) -> str | None:
    if request.workflow_action != "all":
        raise ValueError("Plan, Build, Test, and Fix actions are only available for code-squad.")
    if request.squad_id:
        squad = squad_registry.get_squad(request.squad_id)
        if squad is None:
            raise ValueError(f"Unknown registered squad: {request.squad_id}")
        request.primary_agent = squad["primary_agent"]
        request.subagents = list(squad["subagents"])
        return request.squad_id
    return squad_registry.get_squad_id_for_primary_agent(request.primary_agent)


class _PatternRun:
    """Mutable state of one squad pattern run."""

    def __init__(self, request: OrchestrationRequest, squad_id: str | None):
        self.request = request
        self.trace_id = request.trace_id or ""
        self.squad_id = squad_id
        self.primary_id = request.primary_agent or "rag-presenter"
        self.members = list(request.subagents or [RAG_RETRIEVER_ID])
        self.steps: list[TraceStep] = []
        self.started = time.perf_counter()

    def add_step(
        self, agent_id: str, phase: str, status: str, details: str, error_reason: str | None = None
    ) -> None:
        self.steps.append(TraceStep(
            step_number=len(self.steps) + 1, agent_id=agent_id, phase=phase, status=status,
            details=details, error_reason=error_reason, timestamp=_now(),
        ))


class BasePattern(ABC):
    """Shared squad run lifecycle: members run (per pattern), scratchpad barrier, primary
    synthesis, then metrics, the saved result and RUN_COMPLETE."""

    name = "sequential"

    @abstractmethod
    def run_members(self, run: _PatternRun) -> tuple[str, dict[str, Any] | None]:
        """Run the squad members; returns extra synthesis context and an optional result payload."""

    @staticmethod
    def _run_scratchpad_context(
        trace_id: str, author: str | None = None, title: str = "result"
    ) -> str:
        run = squad_scratchpad.get(trace_id)
        if not run:
            raise RuntimeError(f"Squad scratchpad not found for run '{trace_id}'.")
        entry = next(
            (item for item in run["entries"]
             if item["title"] == title and (author is None or item["author"] == author)),
            None,
        )
        if entry is None:
            return ""
        return (
            "\n\nPrevious step handoff:\n"
            f"Agent: {entry['author']}\n"
            f"Title: {entry['title']}\n"
            f"Result:\n{entry['content'][:3000]}"
        )

    def execute(self, request: OrchestrationRequest) -> OrchestrationResponse:
        if request.squad_id == CODE_SQUAD_ID:
            return code_squad_pipeline.run(request)

        request.trace_id = request.trace_id or f"tr_{uuid.uuid4().hex}"
        run = _PatternRun(request, _squad_for_request(request))
        squad_scratchpad.begin(run.trace_id, run.squad_id or "unregistered", request.user_query)
        telemetry_bus.log_event(
            run.trace_id, "RUN_STARTED", "orchestrator", run.squad_id or run.primary_id,
            {"squad_id": run.squad_id or "unregistered", "pattern": self.name},
        )
        _print_trace(
            f"\n\033[94m[ORCHESTRATION TRACE: {run.trace_id}]\033[0m Starting {self.name} pipeline."
        )
        extra_payload = None
        try:
            context, extra_payload = self.run_members(run)
            scratchpad_entry = subagent_runner.read_latest_subagent_scratchpad()
            scratchpad_snapshot = subagent_runner.read_scratchpad_snapshot()
            status = (
                str(scratchpad_entry.get("status") or "success")
                if scratchpad_entry
                else "no_context_found"
            )
            if extra_payload and status == "no_context_found":
                status = "success"
            telemetry_bus.log_event(
                run.trace_id, "SCRATCHPAD_HANDOFF", "orchestrator", "scratchpad",
                {
                    "status": status,
                    "subagent_id": scratchpad_entry.get("subagent_id", ""),
                    "result_count": _result_count(scratchpad_entry),
                    "direction": "read",
                },
            )
            run.add_step(
                "scratchpad", "SCRATCHPAD_BARRIER", status,
                "Retrieved latest sub-agent payload from scratchpad",
            )
            final_answer = self._synthesize(run, context)
            if any(step.status == "ERROR" for step in run.steps):
                status = "error"
        except Exception as exc:
            status = "error"
            final_answer = f"Workflow failed: {exc}"
            squad_scratchpad.post(run.trace_id, "orchestrator", "note", "Workflow error", str(exc))
            scratchpad_entry, scratchpad_snapshot = {}, {}
            telemetry_bus.log_event(
                run.trace_id, "ERROR", "orchestrator", run.squad_id or run.primary_id,
                {"error": final_answer, "status": "error"},
            )

        payload = scratchpad_entry.get("payload") if scratchpad_entry else None
        if extra_payload:
            payload = {**extra_payload, "scratchpad_payload": payload}
        return self._finish(run, status, final_answer, payload, scratchpad_snapshot)

    def _run_member(
        self, run: _PatternRun, member_id: str, previous_member: str | None = None
    ) -> tuple[str | None, Exception | None]:
        """Run one member turn with telemetry; never raises."""
        _print_trace(f" ├── Executing member '\033[93m{member_id}\033[0m'...")
        telemetry_bus.log_event(
            run.trace_id, "AGENT_MESSAGE", "orchestrator", member_id,
            {"summary": "Sub-agent phase started", "status": "running"},
        )
        request = run.request
        if member_id == RAG_RETRIEVER_ID:
            prompt = request.user_query
        else:
            prompt = (
                f"Task: {request.user_query}\n"
                f"Parameters: {json.dumps(request.parameters, ensure_ascii=False, default=str)}\n"
                "Work only on this assigned step. If a prior handoff is included, use its relevant "
                "information and add your step's result. Keep the handoff concise, and write it to "
                "the squad scratchpad with your agent ID and the title 'result'."
            )
        if previous_member:
            prompt += self._run_scratchpad_context(
                run.trace_id, author=previous_member, title="result"
            )
        try:
            output = subagent_runner.run_agent_turn(
                member_id, prompt, request.parameters, trace_id=run.trace_id
            )
            if not output or not output.strip():
                raise RuntimeError(f"Sub-agent '{member_id}' returned an empty result.")
        except Exception as exc:
            squad_scratchpad.post(run.trace_id, member_id, "note", "Member failed", str(exc))
            _print_trace(f" │   \033[91m[ERROR]\033[0m Member '{member_id}' failed: {exc}")
            telemetry_bus.log_event(
                run.trace_id, "ERROR", member_id, "orchestrator",
                {"error": f"Sub-agent '{member_id}' failed: {exc}", "status": "error"},
            )
            return None, exc
        run_data = squad_scratchpad.get(run.trace_id)
        has_result = bool(run_data) and any(
            entry["author"] == member_id and entry["title"] == "result"
            for entry in run_data["entries"]
        )
        if not has_result:
            squad_scratchpad.post(run.trace_id, member_id, "note", "result", output)
        telemetry_bus.log_event(
            run.trace_id, "AGENT_MESSAGE", member_id, "orchestrator",
            {"summary": "Sub-agent phase completed", "status": "success"},
        )
        return output, None

    @staticmethod
    def _record_member(run: _PatternRun, member_id: str, error: Exception | None) -> None:
        if error is None:
            run.add_step(member_id, "SUBAGENT_EXECUTION", "SUCCESS", f"Executed worker '{member_id}'")
        else:
            run.add_step(
                member_id, "SUBAGENT_EXECUTION", "ERROR",
                f"Sub-agent '{member_id}' raised an exception", error_reason=str(error),
            )

    def _synthesize(self, run: _PatternRun, context: str) -> str:
        telemetry_bus.log_event(
            run.trace_id, "AGENT_MESSAGE", "orchestrator", run.primary_id,
            {"summary": "Primary synthesis started", "status": "running"},
        )
        prompt = (
            f"User request: {run.request.user_query}\n"
            "Use the selected scratchpad handoffs below to produce the final answer in clear, "
            "user-facing prose. The orchestrator owns the workflow; do not delegate or start "
            "additional agent work."
        )
        if context:
            prompt += f"\n\n{context}"
        final_answer = subagent_runner.run_agent_turn(run.primary_id, prompt, trace_id=run.trace_id)
        squad_scratchpad.post(run.trace_id, run.primary_id, "summary", "Primary synthesis", final_answer)
        run.add_step(
            run.primary_id, "PRIMARY_SYNTHESIS", "SUCCESS",
            f"Primary agent '{run.primary_id}' completed response",
        )
        telemetry_bus.log_event(
            run.trace_id, "AGENT_MESSAGE", run.primary_id, "orchestrator",
            {"summary": "Primary synthesis completed", "status": "success"},
        )
        return final_answer

    def _finish(
        self, run: _PatternRun, status: str, final_answer: str, payload: Any,
        scratchpad_snapshot: dict[str, Any],
    ) -> OrchestrationResponse:
        duration_ms = int((time.perf_counter() - run.started) * 1000)
        if run.squad_id:
            squad_registry.record_squad_run(run.squad_id, status != "error", duration_ms)
        response = OrchestrationResponse(
            trace_id=run.trace_id,
            pattern=self.name,
            query=run.request.user_query,
            status=status,
            final_answer=final_answer,
            result_payload=payload,
            execution_trace=run.steps,
        )
        squad_scratchpad.finish(run.trace_id, status)
        # Save before RUN_COMPLETE so the client's result fetch never races a 404.
        run_results.save(
            run.trace_id,
            {
                **response.model_dump(),
                "squad_id": run.squad_id,
                "scratchpad_snapshot": scratchpad_snapshot,
                "agent_stats": summarize_agent_usage(telemetry_bus.get_trace_history(run.trace_id)),
            },
        )
        telemetry_bus.log_event(
            run.trace_id, "RUN_COMPLETE", "orchestrator", run.squad_id or run.primary_id,
            {"status": status, "duration_ms": duration_ms},
        )
        telemetry_bus.finish_trace(run.trace_id)
        _print_trace(f" \033[94m[ORCHESTRATION TRACE: {run.trace_id}]\033[0m Pipeline complete.\n")
        return response


class SequentialPattern(BasePattern):
    """Executes squad members one after another to avoid local LLM hardware bottlenecks.
    Stops early if a member fails validation (missing expected scratchpad output)."""

    name = "sequential"

    _PROGRAMMATIC_STEPS: dict[str, str] = {
        "questionnaire": "_run_questionnaire_step",
    }

    def run_members(self, run: _PatternRun) -> tuple[str, dict[str, Any] | None]:
        previous_member = None
        for member_id in run.members:
            handler_name = self._PROGRAMMATIC_STEPS.get(member_id)
            if handler_name:
                method = getattr(self, handler_name, None)
                if method:
                    error = method(run)
                    self._record_member(run, member_id, error)
                    if error:
                        _print_trace(
                            f" │   \033[91m[PIPELINE STOPPED]\033[0m "
                            f"Programmatic step '{member_id}' failed — skipping remaining members."
                        )
                        break
                    previous_member = member_id
                    continue

            output, error = self._run_member(run, member_id, previous_member)
            self._record_member(run, member_id, error)
            if error:
                _print_trace(
                    f" │   \033[91m[PIPELINE STOPPED]\033[0m "
                    f"Member '{member_id}' raised an error — skipping remaining members."
                )
                break
            previous_member = member_id
            if output and '"error"' in output and "finished without writing" in output:
                _print_trace(
                    f" │   \033[91m[PIPELINE STOPPED]\033[0m "
                    f"Member '{member_id}' failed validation — skipping remaining members."
                )
                break
        context = (
            self._run_scratchpad_context(run.trace_id, author=previous_member)
            if previous_member else ""
        )
        return context, None

    @staticmethod
    def _run_questionnaire_step(run: _PatternRun) -> Exception | None:
        """Read inquisitive's questions from the scratchpad, call ask_user_window,
        and write the combined result back. Fully programmatic — no LLM involved."""
        _print_trace(f" ├── Executing programmatic step '\033[93mquestionnaire\033[0m'...")
        try:
            sp = squad_scratchpad.get(run.trace_id)
            if not sp:
                raise RuntimeError("No squad scratchpad found.")

            questions_json = None
            for entry in sp.get("entries", []):
                if entry.get("author") == "inquisitive" and entry.get("title") == "questions":
                    raw = entry["content"]
                    questions_json = json.loads(raw) if isinstance(raw, str) else raw
                    break
            if not questions_json:
                raise RuntimeError("Inquisitive did not write questions to the scratchpad.")

            if isinstance(questions_json, list):
                if questions_json and isinstance(questions_json[0], dict):
                    questions_json = questions_json[0]
                else:
                    questions_json = {f"q{i+1}": str(q) for i, q in enumerate(questions_json)}

            if "questions" in questions_json and isinstance(questions_json["questions"], dict):
                questions_json = questions_json["questions"]

            from tools.promoted_tools import ask_user_window
            answer_raw = ask_user_window(json.dumps(questions_json, ensure_ascii=False))
            try:
                answer_data = json.loads(answer_raw)
            except (json.JSONDecodeError, TypeError):
                answer_data = {"status": "error", "message": "Could not parse questionnaire response."}

            result = {
                "questions": questions_json,
                "answers": answer_data.get("answers") if answer_data.get("status") == "success" else None,
                "status": answer_data.get("status", "unknown"),
            }

            squad_scratchpad.post(
                run.trace_id, "questionnaire", "note", "result",
                json.dumps(result, ensure_ascii=False),
            )
            _print_trace(
                f" │   \033[92m[QUESTIONNAIRE]\033[0m "
                f"Status: {result['status']}, answers collected."
            )

            if result["status"] != "success":
                raise RuntimeError(f"Questionnaire failed: {answer_data.get('message', 'unknown error')}")

            return None
        except Exception as exc:
            squad_scratchpad.post(run.trace_id, "questionnaire", "note", "Member failed", str(exc))
            _print_trace(f" │   \033[91m[QUESTIONNAIRE ERROR]\033[0m {exc}")
            return exc


# --- Code Development Squad pipeline ---------------------------------------------------

class CodeSquadPipeline:
    """Deterministic plan -> build -> test -> fix pipeline for the CodeBuilder editor.

    Agents only write text and one fenced Python block. The orchestrator applies that
    code to the editor buffer, runs it, and records factual handoffs, so a small local
    model never has to produce a large tool-call argument to deliver its work.
    """

    MAX_FIX_ATTEMPTS = 3
    SHARED_NOTES = 3
    SHARED_NOTES_CHARS = 2_000
    TEST_TIMEOUT_SECONDS = 15.0
    TEST_NAME = "__codebuilder_test__"
    ACTION_ALIASES = {"troubleshoot": "fix"}
    STEP_AGENTS = {
        "plan": "code-planer",
        "build": "code-builder",
        "test": "tester",
        "fix": "code-troubleshooter",
    }

    # --- code extraction -----------------------------------------------------------
    @staticmethod
    def fenced_code_blocks(response: str) -> list[tuple[str, str]]:
        return [
            ((match.group(1) or "").strip().split(" ", 1)[0].lower(), match.group(2))
            for match in re.finditer(r"```([^\r\n`]*)\r?\n([\s\S]*?)```", response or "")
            if match.group(2).strip()
        ]

    def extract_python_block(self, response: str) -> str | None:
        """Return the module the agent produced: the single block, else the largest Python block."""
        blocks = self.fenced_code_blocks(response)
        if len(blocks) == 1:
            return blocks[0][1]
        python_blocks = [
            content for language, content in blocks if language in {"python", "py", "python3"}
        ]
        if not python_blocks:
            return None
        return max(python_blocks, key=len)

    # --- step bookkeeping ----------------------------------------------------------
    def _check_cancel(self, ctx: CodeSquadRunContext) -> None:
        if codebuilder_run_state.is_cancelled(ctx.trace_id):
            raise PipelineCancelled()

    def _step_status(
        self, ctx: CodeSquadRunContext, step: str, status: str, attempt: int = 0, summary: str = ""
    ) -> None:
        telemetry_bus.log_event(
            ctx.trace_id, "STEP_STATUS", "orchestrator", self.STEP_AGENTS.get(step, step),
            {"step": step, "status": status, "attempt": attempt, "summary": summary},
        )

    def _record(
        self, ctx: CodeSquadRunContext, step: str, status: str, details: str, error: str | None = None
    ) -> None:
        ctx.steps.append(
            TraceStep(
                step_number=len(ctx.steps) + 1,
                agent_id=self.STEP_AGENTS.get(step, step),
                phase=step.upper(),
                status=status,
                details=details,
                error_reason=error,
                timestamp=_now(),
            )
        )

    def _handoff(self, ctx: CodeSquadRunContext, agent_id: str, status: str, payload: dict[str, Any]) -> None:
        codebuilder_run_state.save_handoff(
            ctx.trace_id, agent_id, TASK_TYPE_BY_AGENT.get(agent_id, "code_plan"), status, payload
        )

    def _note(
        self, ctx: CodeSquadRunContext, author: str, kind: str, title: str, content: str,
        status: str = "success",
    ) -> None:
        """Record a step on the coding agents' shared scratchpad; never fail the run over it."""
        squad_scratchpad.post(ctx.trace_id, author, kind, title, content)
        try:
            code_scratchpad.post(author, kind, title, content, run_id=ctx.trace_id)
        except Exception as exc:  # noqa: BLE001 - the scratchpad is a record, not a dependency
            telemetry_bus.log_event(
                ctx.trace_id, "ERROR", "orchestrator", "scratchpad",
                {"error": f"Could not write the code scratchpad: {exc}", "status": "error"},
            )
            return
        telemetry_bus.log_event(
            ctx.trace_id, "SCRATCHPAD_HANDOFF", author, "scratchpad",
            {"status": status, "subagent_id": author, "result_count": 1, "direction": "write"},
        )

    def _shared_notes_section(self, trace_id: str) -> str:
        """Notes chat agents or the user left on the code scratchpad, newest first."""
        try:
            notes = code_scratchpad.entries(limit=self.SHARED_NOTES, kind="note")
        except Exception as exc:  # noqa: BLE001 - notes are optional for the run
            telemetry_bus.log_event(
                trace_id, "ERROR", "orchestrator", "scratchpad",
                {"error": f"Could not read the code scratchpad: {exc}", "status": "error"},
            )
            notes = []
        lines = "\n".join(f"- ({note['author']}) {note['title']}: {note['content']}" for note in notes)
        shared = "\nNotes from other agents on the code scratchpad:\n" + lines[:self.SHARED_NOTES_CHARS] + "\n" if notes else ""
        return shared + self._run_notes_section(trace_id)

    def _run_notes_section(self, trace_id: str) -> str:
        run = squad_scratchpad.get(trace_id, kind="note")
        if run is None:
            raise RuntimeError(f"Squad scratchpad not found for run '{trace_id}'.")
        run_notes = run["entries"][:self.SHARED_NOTES]
        if run_notes:
            lines = "\n".join(f"- ({note['author']}) {note['title']}: {note['content']}" for note in run_notes)
            return "\nNotes from this squad run:\n" + lines[:self.SHARED_NOTES_CHARS] + "\n"
        return ""

    def _ask_agent(self, ctx: CodeSquadRunContext, step: str, prompt: str, attempt: int = 0) -> str:
        agent_id = self.STEP_AGENTS[step]
        self._check_cancel(ctx)
        telemetry_bus.log_event(
            ctx.trace_id, "AGENT_STARTED", "orchestrator", agent_id,
            {"agent_id": agent_id, "step": step, "attempt": attempt},
        )
        try:
            reply = subagent_runner.run_agent_turn_stream(
                agent_id,
                prompt,
                ctx.trace_id,
                model=ctx.model,
                should_stop=lambda: codebuilder_run_state.is_cancelled(ctx.trace_id),
            )
        except AgentTurnCancelled as exc:
            raise PipelineCancelled() from exc
        telemetry_bus.log_event(
            ctx.trace_id, "AGENT_DONE", agent_id, "orchestrator",
            {"agent_id": agent_id, "step": step, "text": reply},
        )
        return reply

    def apply_code(self, ctx: CodeSquadRunContext, code: str, agent_id: str) -> list[int]:
        """Place agent code in the editor buffer and announce it to the frontend."""
        from tools.logic_tools import code_change_preview

        try:
            lines = code_change_preview.changed_line_numbers(ctx.code, code, "after")
        except ValueError:
            lines = []
        codebuilder_run_state.save_preview(ctx.trace_id, code, lines)
        ctx.code = code
        telemetry_bus.log_event(
            ctx.trace_id, "EDITOR_CODE_UPDATE", agent_id, "editor",
            {"content": code, "changed_lines": lines},
        )
        return lines

    @staticmethod
    def _buffer_section(code: str) -> str:
        if not code.strip():
            return "The editor is currently empty."
        return f"Current editor module:\n```python\n{code}\n```"

    # --- test harness ----------------------------------------------------------------
    def _test_harness(self, code: str) -> tuple[str, int]:
        """Return runnable source that imports the module without running its main guard.

        The ``__name__`` override shares the first line when that is valid, so traceback
        line numbers match the editor. Otherwise it gets its own line (offset 1).
        """
        first = code.splitlines()[0] if code else ""
        override = f"__name__ = {self.TEST_NAME!r}"
        # Only simple statements may follow ';' on one line (not def/class/if/@...).
        if first.startswith(("import ", "from ", "#", '"', "'")) and not first.startswith("from __future__"):
            return f"{override}; {code}", 0
        return f"{override}\n{code}", 1

    def _run(self, code: str) -> dict[str, Any]:
        from codebuilder.execution import execute_python_code
        from codebuilder.schemas_codebuilder import CodeExecutionRequest

        result = execute_python_code(
            CodeExecutionRequest(code=code, timeout_seconds=self.TEST_TIMEOUT_SECONDS)
        )
        return result.model_dump()

    @staticmethod
    def _compile_error(code: str) -> dict[str, Any] | None:
        try:
            compile(code, "editor.py", "exec")
        except SyntaxError as exc:
            message = f"{type(exc).__name__}: {exc.msg} (line {exc.lineno})"
            return {
                "status": "ERROR",
                "stdout": "",
                "stderr": message + ("\n" + exc.text if exc.text else ""),
                "return_code": 1,
                "timed_out": False,
                "diagnostics": [
                    {"path": None, "line": exc.lineno, "column": exc.offset, "message": message, "severity": "error"}
                ],
            }
        return None

    def _publish_execution(
        self, ctx: CodeSquadRunContext, result: dict[str, Any], offset: int, agent_id: str
    ) -> dict[str, Any]:
        """Map diagnostics back to editor lines, store the result, and notify the UI."""
        total = len(ctx.code.splitlines())
        diagnostics = []
        for item in result.get("diagnostics") or []:
            item = dict(item)
            line = item.get("line")
            if isinstance(line, int):
                line -= offset
                item["line"] = line if 0 < line <= total else None
            diagnostics.append(item)
        result = {**result, "diagnostics": diagnostics}
        codebuilder_run_state.save_execution(ctx.trace_id, ctx.code, result, allow_non_editor_source=True)
        passed = result.get("status") == "SUCCESS"
        telemetry_bus.log_event(
            ctx.trace_id, "EDITOR_CODE_EXECUTION", agent_id, "editor",
            {
                "status": "success" if passed else "error",
                "diagnostic_lines": sorted({d["line"] for d in diagnostics if d.get("line")})[:120],
            },
        )
        return result

    # --- steps -----------------------------------------------------------------------
    def step_plan(self, ctx: CodeSquadRunContext) -> None:
        self._step_status(ctx, "plan", "running")
        prompt = (
            f"User request: {ctx.query}\n\n{self._buffer_section(ctx.code)}\n{self._shared_notes_section(ctx.trace_id)}\n"
            "Write a short implementation plan for a single Python module: the functions "
            "to add or change, what each does, and how the result will be checked. "
            "Do not write the full code."
        )
        ctx.plan = self._ask_agent(ctx, "plan", prompt).strip()
        self._handoff(ctx, "code-planer", "success" if ctx.plan else "error", {"plan": ctx.plan[:4_000]})
        if ctx.plan:
            self._note(ctx, "code-planer", "plan", f"Plan: {ctx.query[:120]}", ctx.plan)
        self._record(ctx, "plan", "SUCCESS" if ctx.plan else "ERROR", "Created implementation plan")
        self._step_status(ctx, "plan", "success" if ctx.plan else "error")

    def step_build(self, ctx: CodeSquadRunContext) -> bool:
        self._step_status(ctx, "build", "running")
        prompt = f"User request: {ctx.query}\n\n{self._buffer_section(ctx.code)}\n{self._shared_notes_section(ctx.trace_id)}"
        if ctx.plan:
            prompt += f"\nPlan to follow:\n{ctx.plan[:8_000]}\n"
        prompt += (
            "\nReturn the COMPLETE resulting Python module in exactly one ```python block, "
            "then 1-3 short lines saying what you did."
        )
        reply = self._ask_agent(ctx, "build", prompt)
        code = self.extract_python_block(reply)
        if code is None:
            self._handoff(ctx, "code-builder", "error", {"summary": "No Python code block in reply.", "reply": reply[:4_000]})
            self._note(ctx, "code-builder", "code", "Build failed: no Python code block", reply, "error")
            self._record(ctx, "build", "ERROR", "Builder returned no Python code block")
            self._step_status(ctx, "build", "error", summary="The builder did not return a Python code block.")
            return False
        lines = self.apply_code(ctx, code, "code-builder")
        self._handoff(ctx, "code-builder", "success", {"summary": "Applied module to editor.", "changed_lines": len(lines)})
        self._note(ctx, "code-builder", "code", f"Built module ({len(lines)} lines changed)", reply)
        self._record(ctx, "build", "SUCCESS", f"Applied builder code ({len(lines)} changed lines)")
        self._step_status(ctx, "build", "success", summary=f"{len(lines)} lines changed")
        return True

    def step_test(self, ctx: CodeSquadRunContext, attempt: int = 0) -> bool:
        self._step_status(ctx, "test", "running", attempt)
        agent_id = "tester"
        if not ctx.code.strip():
            ctx.last_test = {"status": "ERROR", "stderr": "The editor is empty; there is nothing to test.", "diagnostics": []}
            self._record(ctx, "test", "ERROR", "Nothing to test")
            self._step_status(ctx, "test", "error", attempt, "The editor is empty.")
            return False

        result = self._compile_error(ctx.code)
        offset = 0
        stage = "syntax check"
        snippet = None
        if result is None:
            harness, offset = self._test_harness(ctx.code)
            self._check_cancel(ctx)
            result = self._run(harness)
            stage = "import smoke test"
            if result.get("status") == "SUCCESS":
                prompt = (
                    f"User request: {ctx.query}\n\n{self._buffer_section(ctx.code)}\n\n"
                    f"{self._run_notes_section(ctx.trace_id)}\n"
                    "Write a short Python test snippet that will run directly after this "
                    "module's code (its functions and classes are already defined; do not "
                    "import it). Use plain assert statements on the module's functions, "
                    "with no input(), servers, loops waiting on users, or file writes. "
                    "Return exactly one ```python block and nothing else."
                )
                snippet = self.extract_python_block(self._ask_agent(ctx, "test", prompt, attempt))
                if snippet and snippet.strip():
                    self._check_cancel(ctx)
                    result = self._run(f"{harness}\n\n# --- tester checks ---\n{snippet}")
                    stage = "tester checks"
        result = self._publish_execution(ctx, result, offset, agent_id)
        passed = result.get("status") == "SUCCESS"
        outcome = f"{stage} {'passed' if passed else 'failed'}"
        ctx.last_test = {**code_handoffs.execution_evidence(result), "stage": stage}
        self._handoff(ctx, agent_id, "success" if passed else "error", ctx.last_test)
        evidence = json.dumps(ctx.last_test, ensure_ascii=False, indent=2)[:8_000]
        self._note(
            ctx, agent_id, "test", outcome,
            f"```json\n{evidence}\n```" + (f"\n\nTester checks:\n```python\n{snippet}\n```" if snippet else ""),
            "success" if passed else "error",
        )
        self._record(ctx, "test", "SUCCESS" if passed else "ERROR", outcome)
        self._step_status(ctx, "test", "success" if passed else "error", attempt, outcome)
        return passed

    def step_fix(self, ctx: CodeSquadRunContext, attempt: int = 1) -> bool:
        self._step_status(ctx, "fix", "running", attempt)
        evidence = json.dumps(ctx.last_test or {}, ensure_ascii=False, indent=2)[:8_000]
        prompt = (
            f"User request: {ctx.query}\n\n{self._buffer_section(ctx.code)}\n\n"
            f"{self._run_notes_section(ctx.trace_id)}\n"
            f"The latest test failed with this evidence:\n{evidence}\n\n"
            "Find the root cause in the module and fix it with the smallest correct change. "
            "Interactive or entry-point code must stay under `if __name__ == \"__main__\":`. "
            "Return the COMPLETE corrected module in exactly one ```python block, then 1-3 "
            "short lines explaining the cause and the fix."
        )
        reply = self._ask_agent(ctx, "fix", prompt, attempt)
        code = self.extract_python_block(reply)
        if code is None or code == ctx.code:
            reason = "no Python code block" if code is None else "an unchanged module"
            self._handoff(ctx, "code-troubleshooter", "nothing_found", {"summary": f"Troubleshooter returned {reason}."})
            self._note(ctx, "code-troubleshooter", "fix", f"Fix attempt {attempt}: returned {reason}", reply, "nothing_found")
            self._record(ctx, "fix", "ERROR", f"Troubleshooter returned {reason}")
            self._step_status(ctx, "fix", "error", attempt, f"Returned {reason}.")
            return False
        lines = self.apply_code(ctx, code, "code-troubleshooter")
        self._handoff(ctx, "code-troubleshooter", "success", {"summary": "Applied fix to editor.", "changed_lines": len(lines)})
        self._note(ctx, "code-troubleshooter", "fix", f"Fix attempt {attempt} ({len(lines)} lines changed)", reply)
        self._record(ctx, "fix", "SUCCESS", f"Applied fix attempt {attempt} ({len(lines)} changed lines)")
        self._step_status(ctx, "fix", "success", attempt, f"{len(lines)} lines changed")
        return True

    def _fix_loop(self, ctx: CodeSquadRunContext) -> bool:
        """Fix and retest until the tests pass or attempts run out."""
        for attempt in range(1, self.MAX_FIX_ATTEMPTS + 1):
            if not self.step_fix(ctx, attempt):
                return False
            if self.step_test(ctx, attempt):
                return True
        ctx.notes.append(f"Tests still fail after {self.MAX_FIX_ATTEMPTS} fix attempts.")
        return False

    @staticmethod
    def _summary(ctx: CodeSquadRunContext, action: str, passed: bool | None) -> str:
        lines = [f"**{action.capitalize()}** finished."]
        for step in ctx.steps:
            mark = "✓" if step.status == "SUCCESS" else "✗"
            lines.append(f"- {mark} {step.details}")
        if passed is True:
            lines.append("\nThe module passed its checks.")
        elif passed is False and ctx.last_test:
            detail = (ctx.last_test.get("stderr") or ctx.last_test.get("stdout") or "").strip()
            if detail:
                lines.append(f"\nLast failure:\n```\n{detail[-1_500:]}\n```")
        lines.extend(ctx.notes)
        return "\n".join(lines)

    # --- entry point -----------------------------------------------------------------
    def run(self, request: OrchestrationRequest) -> OrchestrationResponse:
        if not request.trace_id:
            request.trace_id = f"tr_{uuid.uuid4().hex}"
            telemetry_bus.initialize_trace(request.trace_id)
        trace_id = request.trace_id
        action = self.ACTION_ALIASES.get(request.workflow_action, request.workflow_action)
        squad = squad_registry.get_squad(CODE_SQUAD_ID) or {}
        squad_scratchpad.begin(trace_id, CODE_SQUAD_ID, request.user_query)
        params = request.parameters or {}
        editor_code = params.get("editor_code")
        ctx = CodeSquadRunContext(
            trace_id=trace_id,
            request=request,
            code=editor_code if isinstance(editor_code, str) else "",
            plan=str(params.get("plan") or ""),
            model=str(params.get("model") or "") or None,
        )
        if codebuilder_run_state.get_editor_code(trace_id) is None:
            codebuilder_run_state.begin(trace_id, ctx.code, request.user_query, action)
        started = time.perf_counter()
        telemetry_bus.log_event(
            trace_id, "RUN_STARTED", "orchestrator", CODE_SQUAD_ID,
            {"squad_id": CODE_SQUAD_ID, "pattern": squad.get("default_pattern", "sequential")},
        )
        status = "success"
        passed: bool | None = None
        try:
            if action == "plan":
                self.step_plan(ctx)
            elif action == "build":
                if not ctx.plan:
                    stored = code_scratchpad.latest("plan")
                    ctx.plan = stored["content"] if stored else ""
                if not self.step_build(ctx):
                    status = "error"
            elif action == "test":
                passed = self.step_test(ctx)
            elif action == "fix":
                if ctx.last_test is None and self.step_test(ctx):
                    passed = True
                    ctx.notes.append("Tests already pass; no fix was needed.")
                else:
                    passed = self._fix_loop(ctx)
            else:  # all
                self.step_plan(ctx)
                if not self.step_build(ctx):
                    status = "error"
                else:
                    passed = self.step_test(ctx) or self._fix_loop(ctx)
            if passed is False:
                status = "error"
            final_answer = self._summary(ctx, action, passed)
        except PipelineCancelled:
            status = "cancelled"
            final_answer = self._summary(ctx, action, None) + "\n\nStopped by user."
        except Exception as exc:
            status = "error"
            final_answer = self._summary(ctx, action, None) + f"\n\nWorkflow failed: {exc}"
            telemetry_bus.log_event(
                trace_id, "ERROR", "orchestrator", CODE_SQUAD_ID,
                {"error": f"Workflow failed: {exc}", "status": "error"},
            )

        self._note(
            ctx, "orchestrator", "summary", f"{action.capitalize()} {status}: {request.user_query[:120]}",
            final_answer, status if status in {"success", "error"} else "nothing_found",
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        squad_registry.record_squad_run(CODE_SQUAD_ID, status == "success", duration_ms)
        handoffs = codebuilder_run_state.get_handoffs(trace_id)
        response = OrchestrationResponse(
            trace_id=trace_id,
            pattern=request.pattern or "sequential",
            query=request.user_query,
            status=status,
            final_answer=final_answer,
            result_payload={
                "action": action,
                "plan": ctx.plan,
                "code": ctx.code,
                "last_test": ctx.last_test,
            },
            execution_trace=ctx.steps,
        )
        squad_scratchpad.finish(trace_id, status)
        # Save before RUN_COMPLETE so the client's result fetch never races a 404.
        run_results.save(
            trace_id,
            {
                **response.model_dump(),
                "squad_id": CODE_SQUAD_ID,
                "agent_stats": summarize_agent_usage(telemetry_bus.get_trace_history(trace_id)),
                "scratchpad_snapshot": {
                    "latest_subagent_result": handoffs[-1] if handoffs else {},
                    "subagent_history": handoffs,
                },
            },
        )
        telemetry_bus.log_event(
            trace_id, "RUN_COMPLETE", "orchestrator", CODE_SQUAD_ID,
            {"status": status, "duration_ms": duration_ms},
        )
        telemetry_bus.finish_trace(trace_id)
        return response


# --- Dispatch and background runs ------------------------------------------------------

class PipelineRegistry:
    """Runs the controlled sequential workflow and handles legacy pattern aliases.

    Requests for ``code-squad`` always go to the Code Development Squad pipeline.
    """

    PATTERNS: Dict[str, Type[BasePattern]] = {
        "sequential": SequentialPattern,
    }

    def get_pattern(self, pattern_name: str | None) -> BasePattern:
        key = squad_registry.normalize_pattern((pattern_name or "sequential").lower().strip())
        return self.PATTERNS[key]()

    @staticmethod
    def resolve_pattern(request: OrchestrationRequest) -> str:
        """A run's explicit pattern wins; otherwise the registered squad's default applies."""
        if request.pattern:
            return squad_registry.normalize_pattern(request.pattern)
        squad = squad_registry.get_squad(request.squad_id or "") if request.squad_id else None
        return squad_registry.normalize_pattern(str((squad or {}).get("default_pattern") or "sequential"))

    def run_orchestration(self, request: OrchestrationRequest) -> OrchestrationResponse:
        if request.squad_id == CODE_SQUAD_ID:
            return code_squad_pipeline.run(request)
        request.pattern = self.resolve_pattern(request)
        return self.get_pattern(request.pattern).execute(request)


class OrchestrationRunService:
    """Runs one squad workflow at a time and bounds the accepted queue."""

    def __init__(self, max_workers: int = 1, capacity: int = 10):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="orchestration")
        self._capacity = BoundedSemaphore(capacity)

    def start(self, request: OrchestrationRequest) -> dict[str, str]:
        """Validate and queue a run; returns its new ``trace_id`` and ``squad_id``."""
        squad = squad_registry.get_squad(request.squad_id or "")
        if squad is None:
            raise ValueError("A registered squad_id is required.")
        squad_registry._validate_definition(
            squad["name"], squad["primary_agent"], list(squad.get("subagents") or [])
        )
        if request.workflow_action != "all" and squad["squad_id"] != CODE_SQUAD_ID:
            raise ValueError("Plan, Build, Test, and Fix actions are only available for code-squad.")
        editor_code = request.parameters.get("editor_code", "")
        if not isinstance(editor_code, str):
            raise ValueError("The editor_code parameter must be text.")
        if len(editor_code.encode("utf-8")) > MAX_EDITOR_CONTENT_BYTES:
            raise EditorBufferTooLargeError("The CodeBuilder editor buffer exceeds the 1 MB workflow limit.")
        request.pattern = squad_registry.normalize_pattern(request.pattern or squad["default_pattern"])
        if not self._capacity.acquire(blocking=False):
            raise RunCapacityError("Orchestration capacity is full. Try again after a run completes.")
        request.trace_id = f"tr_{uuid.uuid4().hex}"
        try:
            codebuilder_run_state.begin(
                request.trace_id,
                editor_code,
                user_query=request.user_query,
                workflow_action=request.workflow_action,
                editor_path=str(request.parameters.get("editor_path") or ""),
            )
            telemetry_bus.initialize_trace(request.trace_id)
            self._executor.submit(self._run_background, request)
        except Exception:
            # If trace initialization fails, do not leave a stale editor buffer behind.
            codebuilder_run_state.discard(request.trace_id)
            self._capacity.release()
            raise
        return {"trace_id": request.trace_id, "squad_id": squad["squad_id"]}

    def _run_background(self, request: OrchestrationRequest) -> None:
        trace_id = request.trace_id or ""
        try:
            pipeline_registry.run_orchestration(request)
        except Exception:
            if squad_scratchpad.get(trace_id) is not None:
                squad_scratchpad.finish(trace_id, "error")
            history = telemetry_bus.get_trace_history(trace_id)
            run_results.save(
                trace_id,
                {
                    "trace_id": request.trace_id,
                    "squad_id": request.squad_id,
                    "status": "error",
                    "final_answer": "Workflow failed. See the execution trace for details.",
                    "result_payload": None,
                    "execution_trace": [],
                },
            )
            if not any(event["event_type"] == "RUN_COMPLETE" for event in history):
                receiver = request.squad_id or "orchestrator"
                telemetry_bus.log_event(
                    trace_id, "ERROR", "orchestrator", receiver,
                    {"error": "Orchestration execution failed", "status": "error"},
                )
                telemetry_bus.log_event(
                    trace_id, "RUN_COMPLETE", "orchestrator", receiver,
                    {"status": "error", "duration_ms": 0},
                )
                telemetry_bus.finish_trace(trace_id)
        finally:
            self._capacity.release()


telemetry_bus = TelemetryBus()
run_results = OrchestrationRunResults()
codebuilder_run_state = CodeBuilderRunState()
squad_registry = SquadRegistry()
code_handoffs = CodeHandoffNormalizer()
subagent_runner = SubAgentRunner()
rag_retriever = RagRetriever()
code_squad_pipeline = CodeSquadPipeline()
pipeline_registry = PipelineRegistry()
run_service = OrchestrationRunService()
