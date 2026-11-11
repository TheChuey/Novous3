"""Consolidated tests grouped by application responsibility."""

# --- Consolidated from test_code_change_preview.py ---

import json

import pytest

from file_manager.logic_file_manager import file_backend
from orchestration.logic_orchestration import telemetry_bus
from tools.logic_tools import code_change_preview, workspace_files


def test_changed_line_numbers_track_replacements_insertions_and_deletions():
    changed = code_change_preview.changed_line_numbers
    assert changed("one\ntwo\nthree", "one\nTWO\nthree", "before") == [2]
    assert changed("one\nthree", "one\ntwo\nthree", "before") == [1]
    assert changed("one\ntwo\nthree", "one\nthree", "after") == [2]


def test_preview_is_read_only_and_emits_only_relative_path_and_lines(
    tmp_path, monkeypatch
):
    file_path = tmp_path / "src" / "app.py"
    file_path.parent.mkdir()
    file_path.write_text("first\nsecond\n", encoding="utf-8")
    monkeypatch.setattr(file_backend, "workspace_root", lambda: tmp_path)
    trace_id = "tr_code_preview_test"
    telemetry_bus.initialize_trace(trace_id)
    source = "first\nchanged\n"

    with telemetry_bus.agent_context(trace_id, "code-builder"):
        preview = code_change_preview.preview("src/app.py", source)

    assert preview == {
        "status": "success",
        "path": "src/app.py",
        "changed_lines": [2],
        "file_exists": True,
    }
    assert file_path.read_text(encoding="utf-8") == "first\nsecond\n"
    event = telemetry_bus.get_trace_history(trace_id)[0]
    assert event["event_type"] == "CODE_CHANGE_PREVIEW"
    assert event["data"] == {
        "path": "src/app.py",
        "changed_lines": [2],
        "file_exists": True,
    }
    assert source not in json.dumps(event)


def test_preview_rejects_paths_outside_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(file_backend, "workspace_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="traversal"):
        code_change_preview.preview("../outside.py", "print('not written')")


def test_preview_marks_new_files_until_they_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(file_backend, "workspace_root", lambda: tmp_path)
    trace_id = "tr_code_new_file_preview"
    telemetry_bus.initialize_trace(trace_id)

    with telemetry_bus.agent_context(trace_id, "code-builder"):
        preview = code_change_preview.preview("new_file.py", "print('new')\n")

    assert preview["file_exists"] is False
    event = telemetry_bus.get_trace_history(trace_id)[0]
    assert event["data"]["file_exists"] is False
    assert not (tmp_path / "new_file.py").exists()


def test_workspace_write_emits_applied_line_event(tmp_path, monkeypatch):
    monkeypatch.setattr(file_backend, "workspace_root", lambda: tmp_path)
    trace_id = "tr_code_applied_test"
    telemetry_bus.initialize_trace(trace_id)

    with telemetry_bus.agent_context(trace_id, "code-builder"):
        result = workspace_files.write_file("app.py", "updated\n")

    assert result["path"] == "app.py"
    event = telemetry_bus.get_trace_history(trace_id)[0]
    assert event["event_type"] == "CODE_CHANGE_APPLIED"
    assert event["data"] == {
        "path": "app.py",
        "changed_lines": [1],
        "file_exists": True,
    }

# --- Consolidated from test_code_scratchpad.py ---

"""Coding agents' shared scratchpad: store, tools, routes, and code-squad integration."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from core_engine.logic_core_engine import Agent
from core_engine.schemas_core_engine import AgentProfile
from orchestration.logic_orchestration import (
    code_squad_pipeline,
    codebuilder_run_state,
    squad_registry,
    subagent_runner,
    telemetry_bus,
)
from orchestration.schemas_orchestration import OrchestrationRequest
from scratchpadMemory.logic_scratchpadMemory import code_scratchpad
from server import app
from tools.logic_tools import read_coding_scratchpad, write_coding_scratchpad


GOOD = "def add(a, b):\n    return a + b\n"


def _block(code):
    return f"```python\n{code}```\nDone."


def test_post_and_read_newest_first_with_filters():
    code_scratchpad.post("code-planer", "plan", "Plan A", "steps", run_id="tr_1")
    code_scratchpad.post("codingagent", "note", "Use type hints", "please", run_id="chat")
    code_scratchpad.post("code-planer", "plan", "Plan B", "steps", run_id="tr_2")
    assert [e["title"] for e in code_scratchpad.entries()] == ["Plan B", "Use type hints", "Plan A"]
    assert [e["title"] for e in code_scratchpad.entries(kind="note")] == ["Use type hints"]
    assert [e["title"] for e in code_scratchpad.entries(author="code-planer", run_id="tr_1")] == ["Plan A"]
    assert code_scratchpad.latest("plan")["title"] == "Plan B"


def test_entry_and_content_caps(monkeypatch):
    monkeypatch.setattr(code_scratchpad, "MAX_ENTRIES", 3)
    for index in range(5):
        code_scratchpad.post("agent", "note", f"n{index}", "x")
    assert code_scratchpad.count() == 3
    entry = code_scratchpad.post("agent", "note", "big", "y" * (code_scratchpad.MAX_CONTENT_CHARS + 10))
    assert len(entry["content"]) == code_scratchpad.MAX_CONTENT_CHARS and entry["truncated"]


def test_rejects_unknown_kind_and_clears():
    with pytest.raises(ValueError):
        code_scratchpad.post("agent", "secret", "t", "c")
    code_scratchpad.post("agent", "note", "t", "c")
    assert code_scratchpad.clear() == {"cleared": True, "path": "scratchpadMemory/data/code_scratchpad.json"}
    assert code_scratchpad.entries() == []


def test_code_scratchpad_routes_read_filter_and_clear():
    code_scratchpad.post("code-planer", "plan", "Plan", "steps", run_id="tr_1")
    code_scratchpad.post("codingagent", "note", "Hint", "text", run_id="chat")
    client = TestClient(app)

    response = client.get("/api/agent-memory/code-scratchpad", params={"kind": "note"})
    assert response.status_code == 200
    assert [entry["title"] for entry in response.json()["entries"]] == ["Hint"]
    assert response.json()["total"] == 2

    assert client.delete("/api/agent-memory/code-scratchpad").json()["cleared"] is True
    assert client.get("/api/agent-memory/code-scratchpad").json()["total"] == 0


def test_write_tool_records_the_calling_agent():
    agent = Agent(
        model=None,
        tools=[write_coding_scratchpad, read_coding_scratchpad],
        profile=AgentProfile(id="codingagent", mode="agent"),
    )
    agent.act(
        {"function": {"name": "write_coding_scratchpad",
                      "arguments": {"agent": "codingagent", "title": "Hint", "content": "Use dataclasses"}}},
        "native",
    )
    entry = code_scratchpad.latest("note")
    assert entry["author"] == "codingagent" and entry["run_id"] == "chat"
    read = json.loads(read_coding_scratchpad(agent="codingagent", title="Hint"))
    assert read["found"] is True and read["content"] == "Use dataclasses"
    missing = json.loads(read_coding_scratchpad(agent="codingagent", title="Nope"))
    assert missing["found"] is False
    assert {"agent": "codingagent", "title": "Hint"} in missing["available"]


@pytest.fixture
def agents(monkeypatch):
    replies, prompts = {}, []

    def fake_stream(agent_id, prompt, trace_id, model=None, should_stop=None):
        prompts.append((agent_id, prompt))
        return replies.get(agent_id, "(no reply)")

    monkeypatch.setattr(subagent_runner, "run_agent_turn_stream", fake_stream)
    monkeypatch.setattr(squad_registry, "record_squad_run", lambda *args: None)
    return replies, prompts


def _run(action, code=""):
    request = OrchestrationRequest(
        squad_id="code-squad", workflow_action=action,
        user_query="make an add function", parameters={"editor_code": code},
    )
    request.trace_id = f"tr_note_{uuid.uuid4().hex}"
    telemetry_bus.initialize_trace(request.trace_id)
    codebuilder_run_state.begin(request.trace_id, code, request.user_query, action)
    return code_squad_pipeline.run(request), request.trace_id


def test_run_all_records_each_step_and_emits_handoffs(agents):
    replies, _ = agents
    replies.update({
        "code-planer": "1. Add add(a, b).",
        "code-builder": _block(GOOD),
        "tester": _block("assert add(1, 2) == 3\n"),
    })
    response, trace_id = _run("all")
    assert response.status == "success"
    kinds = [e["kind"] for e in code_scratchpad.entries(run_id=trace_id)]
    assert kinds == ["summary", "test", "code", "plan"]
    writes = [
        e for e in telemetry_bus.get_trace_history(trace_id)
        if e["event_type"] == "SCRATCHPAD_HANDOFF" and e["data"]["direction"] == "write"
    ]
    assert len(writes) == 4


def test_build_alone_reuses_stored_plan_and_shared_notes(agents):
    replies, prompts = agents
    replies["code-builder"] = _block(GOOD)
    code_scratchpad.post("code-planer", "plan", "Plan", "Name the function add_numbers.", run_id="tr_old")
    code_scratchpad.post("codingagent", "note", "Style", "The user wants type hints.", run_id="chat")
    response, _ = _run("build")
    assert response.status == "success"
    builder_prompt = next(prompt for agent, prompt in prompts if agent == "code-builder")
    assert "Name the function add_numbers." in builder_prompt
    assert "The user wants type hints." in builder_prompt

# --- Consolidated from test_codebuilder.py ---

"""CodeBuilder pillar tests. Standalone: no Ollama, no running host.

Run from the repo root:  python -m pytest tests/test_codebuilder.py -q
"""

import contextlib
import json
import os
import threading
import time

import pytest

from codebuilder import logic_codebuilder
from codebuilder.execution import cancel_run, execute_python_code, run_stream
from codebuilder.logic_codebuilder import EditRejected
from codebuilder.logic_codebuilder import codebuilder_service as service
from codebuilder.schemas_codebuilder import CodeExecutionRequest as Req
from codebuilder.schemas_codebuilder import StructuredEditRequest


def run(code, **kw):
    return execute_python_code(Req(code=code, **kw))


# --- engine: normal paths ---------------------------------------------------
def test_success_keeps_streams_apart():
    res = run("import sys\nprint('out')\nprint('err', file=sys.stderr)")
    assert res.status == "SUCCESS" and res.stdout == "out\n" and res.stderr == "err\n"
    assert res.stuck is None and not res.timed_out


def test_risky_mode_merges_in_order():
    res = run("import sys\nprint('o')\nprint('e', file=sys.stderr)", mode="risky")
    assert res.stdout == "o\ne\n" and res.stderr == ""


def test_runtime_error_reports_user_line():
    res = run("x = 1\nprint(x)\nprint(undefined_name)")
    assert res.status == "ERROR"
    assert res.diagnostics[0].line == 3 and "NameError" in res.diagnostics[0].message


def test_syntax_error_is_structured_and_fast():
    res = run("def f(:\n  pass")
    assert res.status == "ERROR" and res.diagnostics[0].line == 1
    assert "SyntaxError" in res.stderr


def test_input_fails_fast_instead_of_hanging():
    t = time.time()
    res = run("input('name? ')")
    assert time.time() - t < 5 and "EOFError" in res.stderr


# --- engine: stuck handling -------------------------------------------------
def test_infinite_loop_is_stopped_and_located():
    t = time.time()
    res = run("n = 0\nwhile True:\n    n += 1\n", timeout_seconds=1.0)
    assert time.time() - t < 4
    assert res.timed_out and res.status == "ERROR"
    assert res.stuck.reason == "timeout" and res.stuck.line in (2, 3)
    assert "while" in res.stuck.hint.lower()


def test_blocking_call_is_located():
    res = run("import time\nprint('x', end='', flush=True)\ntime.sleep(60)", timeout_seconds=1.0)
    assert res.timed_out and res.stuck.line == 3 and res.stdout == "x"


def test_unbounded_output_is_stopped_early():
    t = time.time()
    res = run("while True:\n    print('x' * 100)", timeout_seconds=10)
    assert time.time() - t < 5
    assert res.stuck.reason == "output_limit" and not res.timed_out


def test_timeout_is_clamped():
    res = run("print(1)", timeout_seconds=-5)  # nonsense input must not disable the limit
    assert res.status == "SUCCESS"


def test_cancel_stops_a_live_run():
    events, ids = [], []

    def consume():
        for ev in run_stream(Req(code="import time\ntime.sleep(60)", timeout_seconds=60, mode="risky")):
            events.append(ev)
            if ev["type"] == "start":
                ids.append(ev["run_id"])

    th = threading.Thread(target=consume)
    th.start()
    deadline = time.time() + 5
    while not ids and time.time() < deadline:
        time.sleep(0.02)
    assert cancel_run(ids[0]) is True
    th.join(10)
    assert not th.is_alive() and events[-1]["stuck"]["reason"] == "cancelled"
    assert cancel_run(ids[0]) is False


@pytest.mark.skipif(os.name == "nt", reason="uses /proc to look for leaked processes")
def test_child_processes_do_not_outlive_a_timeout():
    marker = f"GRANDCHILD_{os.getpid()}"
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)  # {marker}'])\n"
        "time.sleep(300)\n"
    )
    assert run(code, timeout_seconds=1.0).timed_out
    time.sleep(0.4)
    leaked = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
        except OSError:
            continue
        if marker in cmd and int(pid) != os.getpid() and "pytest" not in cmd:
            leaked.append(pid)
    assert leaked == []


def test_closing_the_stream_kills_the_process():
    gen = run_stream(Req(code="import time\nprint('x', flush=True)\ntime.sleep(60)", timeout_seconds=30, mode="risky"))
    for ev in gen:
        if ev["type"] == "stdout":
            break
    t = time.time()
    gen.close()
    assert time.time() - t < 3


# --- service ----------------------------------------------------------------
def test_edit_is_confined_to_the_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEBUILDER_EDIT_ROOT", str(tmp_path))
    ok = service.apply_edit(StructuredEditRequest(target_path="a/b.py", content="x = 1\n"))
    assert ok["status"] == "applied" and (tmp_path / "a" / "b.py").read_text() == "x = 1\n"
    for bad in ("../escape.py", str(tmp_path.parent / "escape.py"), "a"):
        with pytest.raises(EditRejected):
            service.apply_edit(StructuredEditRequest(target_path=bad, content="x"))


def test_agent_meta_has_the_documented_shape():
    meta = service.agent_meta()
    assert {"id", "name", "description", "mode", "tools"} <= set(meta)


# --- HTTP adapter -----------------------------------------------------------
@pytest.fixture()
def client():
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from codebuilder.interface_codebuilder import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_contract(client):
    assert client.get("/api/codebuilder/health").json() == {"status": "ok", "pillar": "codebuilder"}
    body = client.post("/api/codebuilder/execute", json={"code": "print('hi')"}).json()
    assert {"run_id", "status", "stdout", "stderr", "diagnostics"} <= set(body)
    assert client.post("/api/codebuilder/execute/stream", json={"code": "1", "mode": "safe"}).status_code == 400
    assert client.post("/api/codebuilder/execute/run_missing/cancel").status_code == 404


def test_http_stream_event_schema(client):
    import json

    with client.stream("POST", "/api/codebuilder/execute/stream",
                       json={"code": "print('a')", "mode": "risky"}) as resp:
        events = [json.loads(l[6:]) for l in resp.iter_lines() if l.startswith("data: ")]
    assert events[0]["type"] == "start" and events[-1]["type"] == "done"
    assert any(e["type"] == "stdout" and "a" in e["text"] for e in events)


# --- host contract: tool registration --------------------------------------
def test_codebuilder_tools_are_registered_as_core_builtins():
    from core_engine.logic_core_engine import tool_catalog

    tool_catalog.load_tools()
    providers = {name: meta["provider"] for name, meta in tool_catalog.registry.items()}
    for name in ("send_code_to_editor", "run_code_in_editor", "run_python_code", "ask_user_questions"):
        assert providers.get(name) == "codebuilder"


# --- service: questionnaire bridge -----------------------------------------
@contextlib.contextmanager
def fake_questionnaire_host(file_text, answers=None):
    """Stub the workspace read and the questionnaire registry used by ``ask_user``."""
    state = {}

    def read_file_content(relative_path):
        if file_text is None:
            raise FileNotFoundError(relative_path)
        return {"path": relative_path, "content": file_text}

    def create_request(questions, session_id=None):
        event = threading.Event()
        state["record"] = {"request_id": "req_1", "event": event}
        if answers is not None:
            state["answers"] = answers
            event.set()
        return state["record"]

    def resolve_request(request_id):
        return {"answers": state.get("answers", {})} if state.get("answers") is not None else None

    patcher = pytest.MonkeyPatch()
    patcher.setattr(logic_codebuilder.file_backend, "read_file", read_file_content)
    patcher.setattr(logic_codebuilder.questionnaire, "create_request", create_request)
    patcher.setattr(logic_codebuilder.questionnaire, "resolve_request", resolve_request)
    try:
        yield
    finally:
        patcher.undo()


def test_questionnaire_returns_answers_matched_by_id():
    text = json.dumps([{"id": "a", "question": "Q1"}, {"id": "b", "question": "Q2"}])
    with fake_questionnaire_host(text, answers={"a": "one", "b": "two"}):
        out = json.loads(service.ask_user("qs.json", timeout_seconds=5))
    assert out["status"] == "COMPLETED"
    assert [p["answer"] for p in out["qa_pairs"]] == ["one", "two"]


def test_questionnaire_times_out_instead_of_blocking_forever():
    with fake_questionnaire_host('["only question"]', answers=None):
        t = time.time()
        out = service.ask_user("qs.json", timeout_seconds=1)
    assert "timed out" in out and time.time() - t < 5


def test_questionnaire_reports_bad_files_as_text_errors():
    with fake_questionnaire_host("not json"):
        assert service.ask_user("qs.json").startswith("Error")
    with fake_questionnaire_host(None):
        assert "not found" in service.ask_user("qs.json")

# --- Consolidated from test_codebuilder_run_state.py ---

import json

from codebuilder.schemas_codebuilder import CodeExecutionResult, DiagnosticItem
from core_engine.logic_core_engine import tool_catalog
from orchestration import interface_orchestration
from orchestration.logic_orchestration import (
    CodeBuilderRunState,
    code_handoffs,
    codebuilder_run_state,
    telemetry_bus,
)
from scratchpadMemory.logic_scratchpadMemory import scratchpad
from tools.logic_tools import workspace_files


def registered_tool(name):
    tool_catalog.load_tools()
    return tool_catalog.registry[name]["function"]


def test_run_state_keeps_preview_and_execution_scoped_to_trace():
    state = CodeBuilderRunState(max_traces=2)
    state.begin("trace-state-1", "print('before')\n")
    state.begin("trace-state-2", "print('second')\n")

    assert state.save_preview("trace-state-1", "print('after')\n", [1])
    assert state.get_preview("trace-state-1") == {
        "content": "print('after')\n",
        "changed_lines": [1],
    }
    assert state.get_preview("trace-state-2") is None
    assert state.save_execution(
        "trace-state-1",
        "print('before')\n",
        {"status": "ERROR", "stdout": "", "stderr": "NameError"},
    )
    assert state.get_execution("trace-state-1")["status"] == "ERROR"
    assert not state.save_execution(
        "trace-state-1", "print('unrelated')\n", {"status": "SUCCESS"}
    )


def test_preview_editor_code_persists_content_without_telemetry_source():
    trace_id = "tr_editor_preview_handoff"
    original = "def greet():\n    return 'old'\n"
    proposed = "def greet():\n    return 'new'\n"
    codebuilder_run_state.begin(trace_id, original)
    telemetry_bus.initialize_trace(trace_id)

    with telemetry_bus.agent_context(trace_id, "code-builder"):
        response = json.loads(registered_tool("preview_editor_code")(proposed))

    assert response == {"status": "success", "changed_lines": [2]}
    assert codebuilder_run_state.get_preview(trace_id) == {
        "content": proposed,
        "changed_lines": [2],
    }
    event = telemetry_bus.get_trace_history(trace_id)[0]
    assert event["event_type"] == "EDITOR_CODE_PREVIEW"
    assert event["data"] == {"changed_lines": [2]}
    assert proposed not in json.dumps(event)


def test_run_python_code_persists_only_matching_editor_execution(monkeypatch):
    from codebuilder import execution

    trace_id = "tr_editor_execution_handoff"
    source = "print(missing_value)\n"
    result = CodeExecutionResult(
        run_id="run_test",
        status="ERROR",
        stderr="NameError: name 'missing_value' is not defined",
        diagnostics=[
            DiagnosticItem(
                line=1,
                message="NameError: name 'missing_value' is not defined",
            )
        ],
    )
    monkeypatch.setattr(execution, "execute_python_code", lambda _request: result)
    codebuilder_run_state.begin(trace_id, source)
    telemetry_bus.initialize_trace(trace_id)

    with telemetry_bus.agent_context(trace_id, "code-troubleshooter"):
        response = json.loads(registered_tool("run_python_code")(source))

    assert response["status"] == "ERROR"
    assert codebuilder_run_state.get_execution(trace_id)["stderr"] == result.stderr
    event = telemetry_bus.get_trace_history(trace_id)[0]
    assert event["event_type"] == "EDITOR_CODE_EXECUTION"
    assert event["data"] == {"status": "error", "diagnostic_lines": [1]}
    assert source not in json.dumps(event)


def test_code_tester_execution_is_visible_without_highlighting_test_snippet_lines(monkeypatch):
    from codebuilder import execution

    trace_id = "tr_tester_execution_handoff"
    codebuilder_run_state.begin(trace_id, "def add(a, b):\n    return a + b\n")
    telemetry_bus.initialize_trace(trace_id)
    result = CodeExecutionResult(
        run_id="run_test",
        status="SUCCESS",
        stdout="Focused check passed",
    )
    monkeypatch.setattr(execution, "execute_python_code", lambda _request: result)

    with telemetry_bus.agent_context(trace_id, "tester"):
        response = json.loads(registered_tool("run_python_code")("assert 1 + 1 == 2\n"))

    assert response["status"] == "SUCCESS"
    assert codebuilder_run_state.get_execution(trace_id)["source_matches_editor"] is False
    event = telemetry_bus.get_trace_history(trace_id)[0]
    assert event["event_type"] == "EDITOR_CODE_EXECUTION"
    assert event["data"] == {"status": "success", "diagnostic_lines": []}


def test_applied_workspace_editor_source_can_match_tester_diagnostics():
    trace_id = "tr_workspace_editor_source"
    state = CodeBuilderRunState()
    state.begin(trace_id, "print('old')\n", editor_path="src/main.py")
    updated = "def add(a, b):\n    return a + b\n"

    assert not state.save_workspace_source(trace_id, "src/other.py", updated)
    assert state.save_workspace_source(trace_id, "src/main.py", updated)
    assert state.matches_editor_source(trace_id, updated)


def test_editor_handoff_endpoints_return_trace_scoped_content():
    trace_id = "tr_editor_handoff_api"
    codebuilder_run_state.begin(trace_id, "print('old')\n")
    codebuilder_run_state.save_preview(trace_id, "print('new')\n", [1])
    codebuilder_run_state.save_execution(
        trace_id,
        "print('old')\n",
        {"status": "ERROR", "stdout": "", "stderr": "NameError"},
    )

    assert interface_orchestration.get_orchestration_editor_preview(trace_id) == {
        "content": "print('new')\n",
        "changed_lines": [1],
    }
    assert interface_orchestration.get_orchestration_editor_execution(trace_id)["stderr"] == "NameError"


def test_inspect_http_routes_lists_static_route_declarations(monkeypatch):
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/health')\n"
        "def health(): pass\n"
        "@router.api_route('/items', methods=['GET', 'POST'])\n"
        "def items(): pass\n"
    )
    monkeypatch.setattr(
        workspace_files,
        "read_file_bytes",
        lambda path, _limit: {"path": path, "content": source.encode("utf-8")},
    )

    routes = json.loads(registered_tool("inspect_http_routes")("server.py"))

    assert routes["routes"] == [
        {"line": 3, "function": "health", "route": "/health", "methods": ["GET"], "router": "router"},
        {"line": 5, "function": "items", "route": "/items", "methods": ["GET", "POST"], "router": "router"},
    ]


def test_scratchpad_tool_records_handoff_for_current_workflow_only(monkeypatch):
    from scratchpadMemory.logic_scratchpadMemory import squad_scratchpad

    trace_id = "tr_current_scratchpad_handoff"
    telemetry_bus.initialize_trace(trace_id)
    squad_scratchpad.begin(trace_id, "code-squad", "fix the URL handling")
    monkeypatch.setattr(scratchpad, "store_subagent_result", lambda **_kwargs: {"updated_at": "test"})

    with telemetry_bus.agent_context(trace_id, "code-troubleshooter"):
        registered_tool("write_squad_scratchpad")(
            agent="code-troubleshooter",
            title="code_troubleshooting",
            content='{"reason":"URL was not provided"}',
        )

    assert codebuilder_run_state.get_handoff(trace_id) == {
        "subagent_id": "code-troubleshooter",
        "task_type": "code_troubleshooting",
        "status": "success",
        "payload": {"reason": "URL was not provided"},
    }


def test_handoff_normalizer_maps_aliases_and_ignores_speculative_tester_errors():
    normalized = code_handoffs.normalize_handoff(
        {
            "subagent_id": "tester",
            "task_type": "code_test",
            "status": "error",
            "payload": {"tests": ["smoke"], "output": "", "message": "Might fail later"},
        }
    )
    assert normalized["payload"]["checks_run"] == ["smoke"]
    assert normalized["payload"]["summary"] == "Might fail later"
    assert normalized["missing_fields"] == ["observed_output"]
    assert normalized["has_failure_evidence"] is False
    assert normalized["effective_status"] == "nothing_found"

    evidence = code_handoffs.execution_evidence(
        {"status": "ERROR", "stderr": "x" * 10, "diagnostics": [1, 2, 3]},
        text_limit=4,
        diagnostic_limit=2,
    )
    assert evidence["stderr"] == "xxxx" and evidence["stderr_truncated"] is True
    assert evidence["diagnostics"] == [1, 2] and evidence["diagnostics_truncated"] is True

# --- Consolidated from test_codebuilder_sandbox_files.py ---

import pytest

from codebuilder.logic_codebuilder import FileRejected
from codebuilder.logic_codebuilder import script_files as files
from test_environment.logic_test_environment import sandbox_env, sandbox_paths


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "allowed_paths.json"
    config.write_text('{"sandbox_roots": [%s]}' % ('"' + str(docs).replace("\\", "\\\\") + '"'), encoding="utf-8")
    monkeypatch.setattr(sandbox_env, "CONFIG_PATH", config)
    monkeypatch.setattr(sandbox_env, "SCRIPTS_DIR", scripts)
    return scripts, docs


def test_save_and_read_in_scripts_and_extra_root(sandbox):
    scripts, docs = sandbox
    saved = files.save(str(scripts), "app", "print(1)\n")
    assert saved["saved"] and saved["name"] == "app.py"
    assert files.read(saved["path"])["content"] == "print(1)\n"
    assert files.save(str(docs), "doc.py", "x = 1\n")["saved"]
    assert {f["name"] for f in files.listing()["files"]} == {"app.py", "doc.py"}


def test_overwrite_needs_confirmation(sandbox):
    scripts, _ = sandbox
    files.save(str(scripts), "a.py", "1")
    again = files.save(str(scripts), "a.py", "2")
    assert again["exists"] and not again["saved"]
    assert (scripts / "a.py").read_text() == "1"
    assert files.save(str(scripts), "a.py", "2", overwrite=True)["saved"]


def test_escapes_and_bad_names_are_rejected(sandbox, tmp_path):
    scripts, _ = sandbox
    with pytest.raises(FileRejected):
        files.save(str(tmp_path), "outside.py", "x")
    with pytest.raises(FileRejected):
        files.save(str(scripts / ".." ), "escape.py", "x")
    for name in ("..\\x.py", "a/b.py", "bad:name.py"):
        with pytest.raises(FileRejected):
            files.save(str(scripts), name, "x")
    (tmp_path / "secret.py").write_text("x")
    with pytest.raises(FileRejected):
        files.read(str(tmp_path / "secret.py"))


def test_add_remove_clear_roots(sandbox, tmp_path):
    scripts, docs = sandbox
    extra = tmp_path / "extra"
    extra.mkdir()
    sandbox_paths.add_root(str(extra))
    assert sandbox_paths.is_allowed(extra)
    assert sandbox_paths.remove_root(str(extra)) is True
    assert not sandbox_paths.is_allowed(extra)
    sandbox_paths.clear_roots()
    assert [r for r in sandbox_paths.allowed_roots()] == [scripts.resolve()]
    with pytest.raises(ValueError):
        sandbox_paths.remove_root(str(scripts))


def test_run_command_picks_streamlit(sandbox):
    assert files.run_command("C:/a/app.py", "import streamlit as st").startswith("streamlit run")
    assert files.run_command("C:/a/app.py", "print(1)").startswith("python ")


def test_unconfigured_root_falls_back_inside_repo(tmp_path, monkeypatch):
    config = tmp_path / "allowed_paths.json"
    config.write_text('{"sandbox_root": "Z:\\\\definitely\\\\missing\\\\root"}', encoding="utf-8")
    monkeypatch.setattr(sandbox_env, "CONFIG_PATH", config)
    monkeypatch.setattr(sandbox_env, "FALLBACK_ROOT", tmp_path / "fallback")
    assert sandbox_env.sandbox_root() == tmp_path / "fallback"

# --- Consolidated from test_codebuilder_terminal.py ---

import time

from codebuilder.logic_codebuilder import TerminalBusy, TerminalSession


def _wait(session, seconds=10):
    deadline = time.time() + seconds
    while session.running and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)


def test_runs_command_and_buffers_output():
    session = TerminalSession()
    session.run("echo novous-terminal")
    _wait(session)
    text, end = session.read(0)
    assert "novous-terminal" in text and "[process exited with code 0]" in text
    assert session.read(end)[0] == ""


def test_cd_is_limited_to_allowed_folders(tmp_path):
    session = TerminalSession()
    start = session.cwd
    session.run(f'cd "{tmp_path}"')
    assert session.cwd == start
    assert "outside the allowed sandbox paths" in session.read(0)[0]
    (start / "sub").mkdir(exist_ok=True)
    session.run("cd sub")
    assert session.cwd == (start / "sub").resolve()
    session.run("cd does-not-exist")
    assert session.cwd == (start / "sub").resolve()
    (start / "sub").rmdir() if not any((start / "sub").iterdir()) else None


def test_busy_then_stop():
    session = TerminalSession()
    session.run('python -c "import time; time.sleep(30)"')
    try:
        session.run("echo second")
        raised = False
    except TerminalBusy:
        raised = True
    assert raised
    assert session.stop() is True
    _wait(session)
    assert not session.running

# --- Consolidated from test_core_tool_catalog.py ---

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core_engine.logic_core_engine import tool_catalog
from server import app


class TestToolCatalog(unittest.TestCase):
    def setUp(self):
        tool_catalog.load_tools(reload=True)

    def tearDown(self):
        tool_catalog.load_tools(reload=True)

    def test_expected_builtin_tools_load(self):
        tools = tool_catalog.list_tools()
        names = {t["name"] for t in tools}
        expected = {
            # core built-ins (core_engine/logic_core_engine.py)
            "calculator",
            "rag_agent_tool",
            "agent_info",
            "extract_docstrings",
            "search_workspace",
            "project_status",
            "run_python_code",
            "preview_editor_code",
            "preview_code_change",
            "inspect_http_routes",
            "send_code_to_editor",
            "run_code_in_editor",
            "ask_user_questions",
            "read_file",
            "write_file",
            "editor",
            "delete_file",
            "map_files",
            "read_chat_scratchpad",
            "call_subagent",
            "write_chat_scratchpad",
            "read_coding_scratchpad",
            "write_coding_scratchpad",
            "get_knowledge_catalog",
            "search_knowledge_branch",
            "list_knowledge_branch_documents",
            "read_knowledge_branch_document",
        }
        for exp in expected:
            self.assertIn(exp, names)
        self.assertEqual(len(tools), 32)  # core built-ins plus promoted custom tools

    def test_tools_directory_contains_only_the_soc_modules_and_promoted_module(self):
        # allowed_paths.json is private local configuration and may be absent.
        files = {path.name for path in tool_catalog.TOOLS_DIR.iterdir() if path.is_file()} - {"allowed_paths.json"}
        self.assertEqual(
            files,
            {
                "__init__.py",
                "interface_tools.py",
                "logic_tools.py",
                "promoted_tools.py",
                "schemas_tools.py",
            },
        )

    def test_get_load_errors_exists(self):
        errors = tool_catalog.get_load_errors()
        self.assertIsInstance(errors, dict)
        self.assertEqual(errors, {})

    def test_temp_dir_good_and_broken_module(self):
        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            good_file = temp_path / "good_tool.py"
            good_file.write_text(
                "from core_engine.logic_core_engine import tool\n\n"
                "@tool\ndef sample_good_tool():\n    return 'ok'\n",
                encoding="utf-8",
            )
            broken_file = temp_path / "broken_tool.py"
            broken_file.write_text("invalid python code :::\n", encoding="utf-8")

            old_tools_dir = tool_catalog.TOOLS_DIR
            try:
                tool_catalog.TOOLS_DIR = temp_path
                tool_catalog.load_tools(reload=True)

                errors = tool_catalog.get_load_errors()
                self.assertIn("broken_tool.py", errors)
                self.assertIn("sample_good_tool", tool_catalog.registry)

                client = TestClient(app)
                resp = client.get("/api/tools")
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                registered_names = [t["name"] for t in body["tools"]]
                self.assertIn("sample_good_tool", registered_names)
                self.assertTrue(any("broken_tool.py" in w for w in body["warnings"]))
            finally:
                tool_catalog.TOOLS_DIR = old_tools_dir
                tool_catalog.load_tools(reload=True)

    def test_all_agent_refs_resolve_and_report(self):
        report = tool_catalog.get_catalog_report()
        self.assertIn("tools", report)
        self.assertIn("providers", report)
        self.assertIn("load_errors", report)
        self.assertIn("missing_agent_tools", report)
        self.assertIn("warnings", report)

        self.assertEqual(report["missing_agent_tools"], {})
        self.assertEqual(tool_catalog.unknown_tool_names(["calculator", "read_file"]), [])
        self.assertEqual(
            tool_catalog.unknown_tool_names(["calculator", "unknown_tool_xyz"]),
            ["unknown_tool_xyz"],
        )


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_knowledge_source_tools.py ---

import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from core_engine.logic_core_engine import tool_catalog
from ragMemory import logic_ragMemory
from ragMemory.logic_ragMemory import rag_config, rag_memory
from scratchpadMemory.logic_scratchpadMemory import scratchpad
from tools import logic_tools


class TestKnowledgeSourceTools(unittest.TestCase):
    def test_search_tool_targets_archived_source_id(self):
        with patch.object(rag_memory,
            "search_knowledge_base",
            return_value={"knowledge_base_id": "archived-id", "results": []},
        ) as search:
            result = json.loads(
                logic_tools.search_knowledge_branch(
                    "leadership", knowledge_branch_id="archived-id"
                )
            )

        search.assert_called_once_with("leadership", 5, "", "archived-id")
        self.assertEqual(result["knowledge_base_id"], "archived-id")

    def test_knowledge_catalog_uses_branch_names_and_ids(self):
        saved_branch = {
            "id": "branch-id",
            "name": "OLC500 Introduction To Leadership",
            "active": True,
            "selected": True,
            "exists": True,
            "documents": 4,
            "chunks": 12,
        }
        with patch.object(rag_memory, "list_rag_bases", return_value=[saved_branch]):
            result = json.loads(logic_tools.get_knowledge_catalog())

        self.assertEqual(result["title"], "Knowledge TOC")
        self.assertEqual(result["branches"], [{
            "name": "OLC500 Introduction To Leadership",
            "id": "branch-id",
            "status": "active",
            "selected": True,
            "folder_available": True,
            "indexed_documents": 4,
            "chunks": 12,
        }])

    def test_document_read_returns_indexed_text_without_embeddings(self):
        with patch.object(rag_memory,
            "get_document",
            return_value={
                "path": "manual:Leadership notes",
                "chunks_data": [
                    {"content": "First passage", "embedding": [0.1, 0.2]},
                    {"content": "Second passage", "embedding": [0.3, 0.4]},
                ],
            },
        ) as read_document:
            result = json.loads(
                logic_tools.read_knowledge_branch_document(
                    "manual:Leadership notes", "branch-id"
                )
            )

        read_document.assert_called_once_with("manual:Leadership notes", "branch-id")
        self.assertEqual(result["content"], "First passage\n\nSecond passage")
        self.assertEqual(result["chunks"], 2)
        self.assertNotIn("embedding", result)

    def test_rag_retriever_persists_success_nothing_found_and_error(self):
        cases = [
            (
                {
                    "knowledge_base_id": "branch-id",
                    "knowledge_base": "Leadership",
                    "results": [{"path": "notes.docx", "score": 0.8, "content": "Evidence"}],
                },
                "success",
            ),
            (
                {
                    "knowledge_base_id": "branch-id",
                    "knowledge_base": "Leadership",
                    "results": [],
                },
                "nothing_found",
            ),
            (RuntimeError("embedding service unavailable"), "error"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch.object(scratchpad, "DATA_DIR", temp_path), patch.object(
                scratchpad, "SCRATCHPAD_PATH", temp_path / "scratchpad.json"
            ), patch.object(
                rag_config,
                "selected_knowledge_base_id",
                return_value="branch-id",
            ):
                for search_result, expected_status in cases:
                    with self.subTest(status=expected_status):
                        if isinstance(search_result, Exception):
                            search_patch = patch.object(rag_memory,
                                "search_knowledge_base",
                                side_effect=search_result,
                            )
                        else:
                            search_patch = patch.object(rag_memory,
                                "search_knowledge_base",
                                return_value=search_result,
                            )
                        with search_patch:
                            response = json.loads(
                                logic_tools.call_subagent(
                                    "rag-retriever", "Find leadership evidence"
                                )
                            )

                        latest = scratchpad.read_memory(
                            "latest_subagent_result"
                        )["latest_subagent_result"]
                        self.assertEqual(response["status"], expected_status)
                        self.assertEqual(latest["status"], expected_status)
                        self.assertEqual(latest["subagent_id"], "rag-retriever")
                        if expected_status == "error":
                            self.assertIn(
                                "embedding service unavailable",
                                latest["payload"]["error"],
                            )

    def test_vector_search_rejects_index_from_another_embedding_size(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            service = logic_ragMemory.create_rag_memory(temp_dir)
            branch = service.add_rag_base("Leadership", temp_dir)
            with patch.object(
                logic_ragMemory.ollama, "embed",
                return_value={"embeddings": [[1.0, 0.0, 0.0]]},
            ):
                service.add_text_document("notes", "Compatible evidence", branch["id"])
            with patch.object(
                logic_ragMemory.ollama, "embed",
                return_value={"embeddings": [[1.0, 0.0]]},
            ), self.assertRaisesRegex(RuntimeError, "reindex"):
                service.search_knowledge_base("leadership")

    def test_scratchpad_rejects_unsupported_subagent_status(self):
        with self.assertRaisesRegex(ValueError, "Sub-agent status"):
            scratchpad.store_subagent_result(
                "rag-retriever", "rag_retrieval", "no_results", {}
            )

    def test_rag_retriever_persists_bad_parameters_as_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch.object(scratchpad, "DATA_DIR", temp_path), patch.object(
                scratchpad, "SCRATCHPAD_PATH", temp_path / "scratchpad.json"
            ):
                response = json.loads(
                    logic_tools.call_subagent(
                        "rag-retriever", "leadership query", parameters="{invalid"
                    )
                )
                latest = scratchpad.read_memory(
                    "latest_subagent_result"
                )["latest_subagent_result"]

        self.assertEqual(response["status"], "error")
        self.assertEqual(latest["status"], "error")
        self.assertIn("JSON object", latest["payload"]["error"])

    def test_rag_agent_tool_accepts_archived_source_id(self):
        tool_catalog.load_tools()
        with patch.object(
            rag_memory,
            "search_knowledge_base",
            return_value={"knowledge_base_id": "archived-id", "results": []},
        ) as search:
            tool_catalog.registry["rag_agent_tool"]["function"](
                query="leadership", top_k=3, knowledge_base_id="archived-id"
            )

        search.assert_called_once_with("leadership", 3, "", "archived-id")


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_model_profiles.py ---

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelProfiles.logic_modelProfiles import (
    model_profiles,
    size_from_name,
    size_from_parameter_size,
    suggested_profile,
)
from server import app


def facts(model, *, size_b=None, native=None, capabilities=("completion", "tools"), available=True):
    return {
        "model": model, "available": available, "parameter_size": None, "family": None,
        "native_context": native, "capabilities": list(capabilities), "size_b": size_b,
    }


class TestModelSizes(unittest.TestCase):
    def test_size_comes_from_the_tag_and_handles_the_e_prefix(self):
        self.assertEqual(size_from_name("qwen2.5-coder:7b"), 7)
        self.assertEqual(size_from_name("gemma4:e2b"), 2)
        self.assertEqual(size_from_name("qwen3:14b"), 14)
        self.assertEqual(size_from_name("llama3.1:8b-instruct"), 8)
        self.assertIsNone(size_from_name("nomic-embed-text:latest"))
        self.assertIsNone(size_from_name("mystery"))

    def test_parameter_size_text_is_read_in_billions(self):
        self.assertEqual(size_from_parameter_size("7.6B"), 7.6)
        self.assertEqual(size_from_parameter_size("500M"), 0.5)
        self.assertIsNone(size_from_parameter_size(None))

    def test_suggested_tiers_and_native_clamp(self):
        self.assertEqual(suggested_profile(2, 131072),
                         {"num_ctx": 8192, "reply_reserve": 2048, "max_tools": 4})
        self.assertEqual(suggested_profile(7, None),
                         {"num_ctx": 16384, "reply_reserve": 2048, "max_tools": 8})
        self.assertEqual(suggested_profile(14, None)["max_tools"], 12)
        self.assertEqual(suggested_profile(70, None), {"num_ctx": 32768, "reply_reserve": 2048, "max_tools": None})
        self.assertEqual(suggested_profile(None, None)["max_tools"], None)
        self.assertEqual(suggested_profile(2, 4096)["num_ctx"], 4096)


class TestModelProfileService(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(model_profiles, "_fetch_facts",
                                  side_effect=lambda model: facts(model, size_b=7, native=32768))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        model_profiles._facts.clear()

    def test_suggested_values_apply_until_a_profile_is_saved(self):
        effective = model_profiles.effective("qwen2.5-coder:7b")
        self.assertEqual(effective["source"], "suggested")
        self.assertEqual((effective["num_ctx"], effective["max_tools"]), (16384, 8))

        model_profiles.save("qwen2.5-coder:7b", 8192, 1024, 3)
        saved = model_profiles.effective("qwen2.5-coder:7b")
        self.assertEqual(saved["source"], "saved")
        self.assertEqual((saved["num_ctx"], saved["reply_reserve"], saved["max_tools"]), (8192, 1024, 3))

    def test_save_rejects_values_outside_the_model_limits(self):
        with self.assertRaisesRegex(ValueError, "num_ctx must be between"):
            model_profiles.save("qwen2.5-coder:7b", 1024, 256, None)
        with self.assertRaisesRegex(ValueError, "num_ctx must be between"):
            model_profiles.save("qwen2.5-coder:7b", 65536, 256, None)
        with self.assertRaisesRegex(ValueError, "reply_reserve must be between"):
            model_profiles.save("qwen2.5-coder:7b", 8192, 5000, None)
        with self.assertRaisesRegex(ValueError, "max_tools must be"):
            model_profiles.save("qwen2.5-coder:7b", 8192, 1024, 99)

    def test_reset_returns_to_suggested_values(self):
        model_profiles.save("qwen2.5-coder:7b", 8192, 1024, 2)
        reset = model_profiles.reset("qwen2.5-coder:7b")
        self.assertEqual(reset["source"], "suggested")
        self.assertEqual(reset["num_ctx"], 16384)

    def test_saved_context_is_clamped_when_the_model_is_smaller_than_saved(self):
        model_profiles.save("qwen2.5-coder:7b", 32768, 2048, None)
        with patch.object(model_profiles, "_fetch_facts",
                          side_effect=lambda model: facts(model, size_b=7, native=8192)):
            model_profiles._facts.clear()
            self.assertEqual(model_profiles.effective("qwen2.5-coder:7b")["num_ctx"], 8192)

    def test_list_models_reports_each_installed_model_with_its_profile(self):
        listed = {"models": [{"model": "qwen2.5-coder:7b"}, {"name": "gemma4:e2b"}]}
        with patch("modelProfiles.logic_modelProfiles.ollama.list", return_value=listed):
            result = model_profiles.list_models()
        self.assertIsNone(result["error"])
        self.assertEqual([m["model"] for m in result["models"]], ["gemma4:e2b", "qwen2.5-coder:7b"])

    def test_embedding_models_are_left_out_of_the_model_list(self):
        def fetch(model):
            capabilities = ["embedding"] if "embed" in model else ["completion", "tools"]
            return facts(model, size_b=7, native=32768, capabilities=capabilities)

        listed = {"models": [{"model": "nomic-embed-text:latest"}, {"model": "qwen2.5-coder:7b"}]}
        with patch.object(model_profiles, "_fetch_facts", side_effect=fetch), \
                patch("modelProfiles.logic_modelProfiles.ollama.list", return_value=listed):
            model_profiles._facts.clear()
            names = [m["model"] for m in model_profiles.list_models()["models"]]
        self.assertEqual(names, ["qwen2.5-coder:7b"])

    def test_list_models_reports_an_error_when_ollama_is_down(self):
        with patch("modelProfiles.logic_modelProfiles.ollama.list", side_effect=ConnectionError("down")):
            result = model_profiles.list_models()
        self.assertEqual(result["models"], [])
        self.assertIn("Could not list models", result["error"])


class TestModelProfileRoutes(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(model_profiles, "_fetch_facts",
                                  side_effect=lambda model: facts(model, size_b=7, native=32768))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        model_profiles._facts.clear()
        self.client = TestClient(app)

    def test_put_get_and_delete_handle_model_names_with_colons_and_slashes(self):
        model = "library/qwen2.5-coder:7b"
        saved = self.client.put(f"/api/model-profiles/{model}",
                                json={"num_ctx": 8192, "reply_reserve": 1024, "max_tools": 3})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["source"], "saved")

        invalid = self.client.put(f"/api/model-profiles/{model}",
                                  json={"num_ctx": 100, "reply_reserve": 1024, "max_tools": None})
        self.assertEqual(invalid.status_code, 400)

        reset = self.client.delete(f"/api/model-profiles/{model}")
        self.assertEqual(reset.json()["source"], "suggested")

    def test_get_lists_installed_models(self):
        with patch("modelProfiles.logic_modelProfiles.ollama.list",
                   return_value={"models": [{"model": "qwen2.5-coder:7b"}]}):
            response = self.client.get("/api/model-profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["models"][0]["model"], "qwen2.5-coder:7b")


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_rag_and_scratchpad_memory.py ---

import json
import os
from contextlib import nullcontext
import threading
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ragMemory import logic_ragMemory
from ragMemory.logic_ragMemory import document_indexer as indexer
from ragMemory.logic_ragMemory import document_scanner as scanner
from ragMemory.logic_ragMemory import rag_config, rag_database, rag_memory
from scratchpadMemory.logic_scratchpadMemory import scratchpad

_chunk_text = indexer._chunk_text


class TestAgentMemory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)
        self.database_patch = patch.object(
            rag_database, "DATABASE_PATH", self.temp_path / "knowledge.sqlite3"
        )
        self.scratchpad_path_patch = patch.object(
            scratchpad, "SCRATCHPAD_PATH", self.temp_path / "scratchpad.json"
        )
        self.scratchpad_dir_patch = patch.object(
            scratchpad, "DATA_DIR", self.temp_path
        )
        self.rag_config_path_patch = patch.object(
            rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"
        )
        self.rag_data_dir_patch = patch.object(
            rag_config, "DATA_DIR", self.temp_path / "rag-data"
        )
        self.database_patch.start()
        self.scratchpad_path_patch.start()
        self.scratchpad_dir_patch.start()
        self.rag_config_path_patch.start()
        self.rag_data_dir_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.scratchpad_path_patch.stop)
        self.addCleanup(self.scratchpad_dir_patch.stop)
        self.addCleanup(self.rag_config_path_patch.stop)
        self.addCleanup(self.rag_data_dir_patch.stop)
        rag_database.initialize()
        knowledge_folder = self.temp_path / "knowledge"
        knowledge_folder.mkdir()
        rag_config._write_config({
            "knowledge_bases": [{
                "id": "test",
                "name": "Test Knowledge",
                "path": str(knowledge_folder),
                "active": True,
                "legacy_index": True,
            }],
            "selected_knowledge_base": "test",
        })

    def test_scratchpad_stores_latest_read_and_map(self):
        read = scratchpad.store_read("notes.txt", "remember this", "summarize")
        scratchpad.store_read("second.txt", "also remember this", "compare")
        mapped = scratchpad.store_map({"name": "workspace"}, "workspace")

        memory = scratchpad.read_memory()
        self.assertEqual(memory["latest_read"]["content"], "also remember this")
        self.assertEqual(memory["latest_read"]["request"], "compare")
        self.assertEqual(
            [entry["path"] for entry in memory["read_history"]],
            ["notes.txt", "second.txt"],
        )
        self.assertEqual(memory["latest_map"]["tree"], {"name": "workspace"})
        self.assertEqual(read["path"], mapped["path"])

    def test_read_history_is_bounded_and_clear_removes_saved_memory(self):
        for index in range(scratchpad.MAX_READ_HISTORY + 3):
            scratchpad.store_read(f"file-{index}.txt", str(index))

        history = scratchpad.read_memory()["read_history"]
        self.assertEqual(len(history), scratchpad.MAX_READ_HISTORY)
        self.assertEqual(history[0]["path"], "file-3.txt")
        self.assertEqual(history[-1]["path"], f"file-{scratchpad.MAX_READ_HISTORY + 2}.txt")

        self.assertTrue(scratchpad.clear_memory()["cleared"])
        self.assertEqual(scratchpad.read_memory(), {})

    def test_recall_index_lists_newest_first_with_ids_and_previews(self):
        self.assertEqual(scratchpad.recall(), {"items": [], "total_items": 0, "showing": 0,
                                               "hint": "Nothing is saved yet."})
        scratchpad.store_read("notes.txt", "remember this", "summarize")
        scratchpad.store_map({"name": "workspace"}, "workspace")
        scratchpad.store_read("second.txt", "also remember this", "compare")

        index = scratchpad.recall()
        self.assertEqual(index["total_items"], 3)
        self.assertEqual([row["label"] for row in index["items"]], ["second.txt", "workspace", "notes.txt"])
        self.assertEqual(index["items"][0]["request"], "compare")
        self.assertEqual(index["items"][0]["preview"], "also remember this")
        self.assertTrue(all(row["id"].startswith(f"{row['kind']}-") for row in index["items"]))
        self.assertEqual(scratchpad.recall("all"), index)

    def test_recall_pages_reassemble_the_exact_text(self):
        text = "\n".join(f"line {number} " + "x" * 60 for number in range(600))
        scratchpad.store_read("long.txt", text, "summarize")

        pages, offset = [], "0"
        while True:
            page = scratchpad.recall(item="long.txt", offset=offset, max_chars="4000")
            self.assertLessEqual(len(page["content"]), 4000)
            pages.append(page["content"])
            if not page["truncated"]:
                break
            offset = str(page["next_offset"])

        self.assertGreater(len(pages), 1)
        self.assertEqual("".join(pages), text)
        self.assertEqual(page["total_chars"], len(text))
        self.assertIsNone(page["next_offset"])

    def test_recall_item_by_path_is_newest_and_by_id_is_exact(self):
        first = scratchpad.store_read("notes.txt", "first version", "")
        scratchpad.store_read("notes.txt", "second version", "")

        self.assertEqual(scratchpad.recall(item="notes.txt")["content"], "second version")
        self.assertEqual(scratchpad.recall(item=first["id"])["content"], "first version")

    def test_recall_latest_subagent_keeps_legacy_shape_and_pages_when_large(self):
        scratchpad.store_subagent_result("rag-retriever", "retrieval", "success",
                                         {"results": [{"content": "small"}]})
        small = scratchpad.recall(memory_type="latest_subagent_result")["latest_subagent_result"]
        self.assertEqual(small["payload"], {"results": [{"content": "small"}]})

        big_payload = {"results": [{"content": "evidence " * 800}]}
        scratchpad.store_subagent_result("rag-retriever", "retrieval", "success", big_payload)
        paged = scratchpad.recall(memory_type="latest_subagent_result", max_chars="1000")["latest_subagent_result"]
        self.assertEqual(paged["subagent_id"], "rag-retriever")
        self.assertEqual(paged["payload_format"], "json_text")
        self.assertTrue(paged["truncated"])
        self.assertLessEqual(len(paged["payload"]), 1000)
        self.assertEqual(paged["total_chars"],
                         len(json.dumps(big_payload, ensure_ascii=False, separators=(",", ":"))))

    def test_recall_clamps_page_size_and_rejects_bad_arguments(self):
        scratchpad.store_read("notes.txt", "x" * 30000, "")
        self.assertEqual(len(scratchpad.recall(item="notes.txt", max_chars="10")["content"]), 500)
        self.assertEqual(len(scratchpad.recall(item="notes.txt", max_chars="99999")["content"]), 20000)

        with self.assertRaisesRegex(ValueError, "memory_type must be one of"):
            scratchpad.recall(memory_type="everything")
        with self.assertRaisesRegex(ValueError, "whole number"):
            scratchpad.recall(offset="abc")
        with self.assertRaisesRegex(ValueError, "offset must be 0 or greater"):
            scratchpad.recall(offset="-1")
        with self.assertRaisesRegex(ValueError, "No saved item matches"):
            scratchpad.recall(item="missing.txt")

    def test_document_crud_and_semantic_search(self):
        rag_database.replace_document(
            "workspace/Knowledge Base/guide.md",
            1,
            12,
            "2025-01-01T00:00:00+00:00",
            [
                ("cats and kittens", [1.0, 0.0]),
                ("space and planets", [0.0, 1.0]),
            ],
        )
        with patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.9, 0.1]]}):
            result = rag_memory.search_knowledge_base("cats", top_k=1)

        self.assertEqual(result["results"][0]["content"], "cats and kittens")
        self.assertEqual(rag_memory.list_documents()[0]["chunks"], 2)
        self.assertTrue(rag_memory.delete_document("workspace/Knowledge Base/guide.md")["deleted"])
        self.assertEqual(rag_memory.list_documents(), [])

    def test_stale_document_cleanup_normalizes_path_separators(self):
        rag_database.replace_document(
            "workspace\\Knowledge Base\\keep.md", 1, 1, "now", []
        )
        rag_database.replace_document(
            "workspace/Knowledge Base/remove.md", 1, 1, "now", []
        )

        removed = rag_database.delete_documents_under(
            "workspace/Knowledge Base",
            {"workspace/Knowledge Base/keep.md"},
        )

        self.assertEqual(removed, ["workspace/Knowledge Base/remove.md"])
        self.assertEqual(
            [item["path"] for item in rag_database.list_documents()],
            ["workspace\\Knowledge Base\\keep.md"],
        )

    def test_chunking_preserves_text_and_bounds_chunk_size(self):
        text = ("line of knowledge\n\n" * 120).strip()

        chunks = _chunk_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1400 for chunk in chunks))
        self.assertTrue(all(chunk in text for chunk in chunks))

    def test_configured_folder_is_scanned_and_root_change_purges_old_entries(self):

        first = self.temp_path / "first"
        second = self.temp_path / "second"
        first.mkdir()
        second.mkdir()
        (first / "a.md").write_text("alpha", encoding="utf-8")
        (second / "b.md").write_text("beta", encoding="utf-8")
        embed = {"embeddings": [[0.1, 0.2]]}
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"), \
                patch("ragMemory.logic_ragMemory.ollama.embed", return_value=embed):
            self.assertEqual(
                rag_memory.set_rag_folder(str(first))["path"], str(first.resolve())
            )
            result = indexer.scan_knowledge_base()
            self.assertEqual(result["indexed"], 1)
            self.assertEqual(
                [d["path"] for d in rag_memory.list_documents()],
                [(first / "a.md").resolve().as_posix()],
            )

            rag_memory.set_rag_folder(str(second))
            result = indexer.scan_knowledge_base()
            self.assertEqual(len(result["removed"]), 1)
            self.assertEqual(
                [d["path"] for d in rag_memory.list_documents()],
                [(second / "b.md").resolve().as_posix()],
            )

            cleared = rag_memory.clear_rag_index()
            self.assertEqual(cleared["removed"], 1)
            self.assertEqual(cleared["documents"], 0)

    def test_manual_text_is_added_listed_and_deleted(self):
        with patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}):
            result = rag_memory.add_text_document("  My   note ", "some knowledge")
        self.assertEqual(result["path"], "manual:My note")
        overview = rag_memory.get_rag_overview()
        self.assertEqual(overview["types"], {"manual": 1})
        self.assertEqual(overview["files"][0]["chunks"], 1)
        with self.assertRaises(ValueError):
            rag_memory.add_text_document("", "text")
        with self.assertRaises(ValueError):
            rag_memory.add_text_document("title", "   ")
        rag_memory.delete_document("manual:My note")
        self.assertEqual(rag_memory.get_rag_overview()["documents"], 0)

    def test_invalid_rag_folder_is_rejected_without_default_folder(self):
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"):
            with self.assertRaises(ValueError):
                rag_memory.set_rag_folder(str(self.temp_path / "missing"))
            with self.assertRaises(ValueError):
                rag_memory.set_rag_folder("relative/path")
            with self.assertRaisesRegex(ValueError, "required"):
                rag_memory.set_rag_folder("")

    def test_scanner_warns_and_retries_when_configured_folder_is_missing(self):
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, True]

        with patch.object(
            scanner, "get_scan_job", return_value={"status": "idle"},
        ), patch.object(
            scanner, "start_scan_job",
            side_effect=FileNotFoundError("The RAG folder does not exist: missing"),
        ), patch.object(indexer, "stop_event", stop_event), \
                patch.object(logic_ragMemory.LOGGER, "warning") as warning, \
                patch.object(logic_ragMemory.LOGGER, "exception") as exception:
            scanner._scanner_loop()

        warning.assert_called_once()
        self.assertIn("Check the configured path", warning.call_args.args[0])
        self.assertEqual(
            str(warning.call_args.args[1]),
            "The RAG folder does not exist: missing",
        )
        stop_event.wait.assert_called_once_with(scanner.SCAN_INTERVAL_SECONDS)
        exception.assert_not_called()

    def test_named_knowledge_bases_keep_indexes_and_archived_records_separate(self):
        first_folder = self.temp_path / "first"
        second_folder = self.temp_path / "second"
        first_folder.mkdir()
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"), \
                patch.object(rag_config, "DATA_DIR", self.temp_path / "rag-data"), \
                patch.object(scanner, "JOB_PATH", self.temp_path / "missing-folder-job.json"), \
                patch.object(scanner, "_job", {"status": "idle"}), \
                patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}), \
                patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}):
            first = rag_memory.add_rag_base("Business Decisions", str(first_folder))
            second = rag_memory.add_rag_base("Marketing", str(second_folder))
            rag_memory.add_text_document("Decision notes", "business knowledge", first["id"])
            rag_memory.add_text_document("Campaign notes", "marketing knowledge", second["id"])

            self.assertEqual(rag_memory.list_documents(first["id"])[0]["path"], "manual:Decision notes")
            self.assertEqual(rag_memory.list_documents(second["id"])[0]["path"], "manual:Campaign notes")
            self.assertEqual(rag_memory.get_rag_status(second["id"])["name"], "Marketing")
            self.assertFalse(second["exists"])
            self.assertEqual(
                rag_memory.search_knowledge_base("marketing", top_k=1)["results"][0]["content"],
                "marketing knowledge",
            )
            first_results = rag_memory.search_knowledge_base(
                "decision", top_k=1, base_id=first["id"]
            )["results"]
            self.assertEqual(first_results[0]["content"], "business knowledge")
            self.assertEqual(rag_memory.clear_rag_index(second["id"])["removed"], 1)
            self.assertEqual(len(rag_memory.list_documents(first["id"])), 1)
            self.assertEqual(rag_memory.list_documents(second["id"]), [])
            with self.assertRaises(FileNotFoundError):
                rag_memory.start_rag_scan(second["id"])
            self.assertEqual(rag_memory.get_rag_scan_job()["status"], "unavailable")

            rag_memory.archive_rag_base(first["id"])
            archived = next(
                item for item in rag_memory.list_rag_bases()
                if item["id"] == first["id"]
            )
            self.assertFalse(archived["active"])
            self.assertEqual(archived["path"], str(first_folder.resolve()))
            self.assertEqual(archived["documents"], 1)
            archived_results = rag_memory.search_knowledge_base(
                "decision", top_k=1, base_id=first["id"]
            )
            self.assertEqual(archived_results["knowledge_base_id"], first["id"])
            self.assertEqual(
                archived_results["results"][0]["content"],
                "business knowledge",
            )

    def test_default_configuration_migrates_to_a_named_legacy_branch(self):
        folder = self.temp_path / "OLC500"
        folder.mkdir()
        rag_config._write_config({
            "knowledge_base_path": str(folder),
            "knowledge_bases": [
                {
                    "id": "default",
                    "name": "Default Knowledge Base",
                    "path": str(folder),
                    "active": True,
                    "legacy_index": True,
                },
                {
                    "id": "named",
                    "name": "OLC500",
                    "path": str(folder),
                    "active": True,
                    "legacy_index": False,
                },
            ],
            "selected_knowledge_base": "named",
        })

        bases = rag_config.list_knowledge_bases()
        legacy = next(base for base in bases if base["id"] == "default")
        self.assertEqual(legacy["name"], "OLC500 (legacy index)")
        self.assertTrue(legacy["legacy_index"])
        self.assertNotIn("Default Knowledge Base", [base["name"] for base in bases])
        renamed = rag_config.rename_knowledge_base("default", "Historical OLC500")
        self.assertEqual(renamed["name"], "Historical OLC500")
        self.assertEqual(
            rag_config.get_knowledge_base("default")["name"],
            "Historical OLC500",
        )
        self.assertNotIn("knowledge_base_path", json.loads(
            rag_config.CONFIG_PATH.read_text(encoding="utf-8")
        ))

    def test_empty_configuration_has_no_implicit_default_branch(self):
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "empty_config.json"):
            rag_config._write_config({"knowledge_bases": []})
            self.assertEqual(rag_config.list_knowledge_bases(), [])
            with self.assertRaisesRegex(ValueError, "No knowledge source"):
                rag_config.selected_knowledge_base_id()

    def test_deleting_legacy_branch_removes_its_index_not_folder(self):
        folder = self.temp_path / "legacy-branch"
        folder.mkdir()
        source_file = folder / "keep.txt"
        source_file.write_text("Keep this source file.", encoding="utf-8")
        legacy_database = self.temp_path / "legacy-index.sqlite3"
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "legacy_config.json"), \
                patch.object(rag_database, "DATABASE_PATH", legacy_database), \
                patch.object(scanner, "_job", {"status": "idle"}):
            rag_config._write_config({
                "knowledge_bases": [{
                    "id": "legacy",
                    "name": "Legacy source",
                    "path": str(folder),
                    "active": True,
                    "legacy_index": True,
                }],
                "selected_knowledge_base": "legacy",
            })
            rag_database.initialize()
            rag_database.replace_document(
                "manual:notes", 0, 1, "now", [("saved", [0.1, 0.2])]
            )

            result = rag_memory.delete_rag_base("legacy")

        self.assertEqual(result["documents_removed"], 1)
        self.assertFalse(legacy_database.exists())
        self.assertTrue(source_file.exists())

    def test_named_branch_can_be_renamed_and_deleted_without_deleting_folder(self):
        folder = self.temp_path / "branch-files"
        folder.mkdir()
        source_file = folder / "keep.txt"
        source_file.write_text("Do not delete this file.", encoding="utf-8")
        with patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}):
            base = rag_memory.add_rag_base("Course branch", str(folder))
            rag_memory.add_text_document("notes", "indexed knowledge", base["id"])

        database_file = rag_config.database_path(base["id"])
        self.assertTrue(database_file.is_file())
        renamed = rag_memory.rename_rag_base(base["id"], "Leadership course")
        self.assertEqual(renamed["name"], "Leadership course")
        self.assertEqual(renamed["path"], str(folder.resolve()))
        result = rag_memory.delete_rag_base(base["id"])

        self.assertEqual(result["name"], "Leadership course")
        self.assertEqual(result["documents_removed"], 1)
        self.assertFalse(result["folder_deleted"])
        self.assertEqual(source_file.read_text(encoding="utf-8"), "Do not delete this file.")
        self.assertFalse(database_file.exists())
        self.assertNotIn(base["id"], {entry["id"] for entry in rag_memory.list_rag_bases()})

    def test_deleting_paused_branch_cancels_scheduled_resume(self):
        folder = self.temp_path / "paused-branch"
        folder.mkdir()
        base = rag_memory.add_rag_base("Paused source", str(folder))
        job = {
            "status": "paused",
            "base_id": base["id"],
            "resume_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        with patch.object(scanner, "JOB_PATH", self.temp_path / "paused_job.json"), \
                patch.object(scanner, "_job", job), \
                patch.object(scanner, "_job_thread", None), \
                patch.object(scanner, "_stop_requested", threading.Event()), \
                patch.object(scanner, "_pause_requested", threading.Event()), \
                patch.object(scanner, "_resume_event", threading.Event()), \
                patch.object(scanner, "_error_decision_event", threading.Event()):
            result = rag_memory.delete_rag_base(base["id"])

            self.assertTrue(result["deleted"])
            self.assertEqual(scanner.get_scan_job()["status"], "stopped")
            self.assertIsNone(scanner.get_scan_job()["resume_at"])
            self.assertNotIn(base["id"], scanner.get_scan_job()["stopped_base_ids"])

    def test_scan_error_choice_can_skip_one_file_or_skip_all(self):
        folder = self.temp_path / "error-policy"
        folder.mkdir()
        (folder / "first.md").write_text("first", encoding="utf-8")
        (folder / "second.md").write_text("second", encoding="utf-8")
        decisions = iter(["skip", "skip_all"])
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", side_effect=ValueError("parse failed")):
            result = indexer._scan_knowledge_base(
                False,
                "test",
                None,
                None,
                lambda path, error: next(decisions),
            )

        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertTrue(all(item["status"] == "skipped" for item in result["skipped"]))

    def test_scan_error_can_retry_same_file(self):
        folder = self.temp_path / "retry-error"
        folder.mkdir()
        (folder / "retry.md").write_text("retry", encoding="utf-8")
        decisions = iter(["retry"])
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(
                    indexer,
                    "index_file",
                    side_effect=[ValueError("temporary failure"), {"status": "indexed"}],
                ) as index_file:
            result = indexer._scan_knowledge_base(
                False,
                "test",
                None,
                None,
                lambda path, error: next(decisions),
            )

        self.assertEqual(index_file.call_count, 2)
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(result["errors"], [])

    def test_oversized_files_are_skipped_without_asking(self):
        folder = self.temp_path / "oversized"
        folder.mkdir()
        (folder / "big.md").write_text("x" * 20, encoding="utf-8")
        (folder / "small.md").write_text("small", encoding="utf-8")
        decider = Mock()
        with patch.object(indexer, "MAX_DOCUMENT_BYTES", 10), \
                patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", return_value={"status": "indexed"}) as index_file:
            result = indexer._scan_knowledge_base(False, "test", None, None, decider)

        decider.assert_not_called()
        self.assertEqual(index_file.call_count, 1)
        self.assertEqual(result["discovered"], 1)
        self.assertEqual([Path(item["path"]).name for item in result["skipped"]], ["big.md"])
        self.assertIn("indexing limit", result["skipped"][0]["error"])

    def test_google_docs_links_produce_a_warning_not_an_error(self):
        folder = self.temp_path / "google"
        folder.mkdir()
        (folder / "About me.gdoc").write_text('{"doc_id": "abc"}', encoding="utf-8")
        (folder / "Budget.gsheet").write_text('{"doc_id": "def"}', encoding="utf-8")
        (folder / "notes.md").write_text("notes", encoding="utf-8")
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", return_value={"status": "indexed"}) as index_file:
            result = indexer._scan_knowledge_base(False, "test", None, None, None)

        self.assertEqual(index_file.call_count, 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skipped"], [])
        [warning] = result["warnings"]
        self.assertIn("2 Google Docs/Sheets/Slides file(s) cannot be indexed", warning["message"])
        self.assertIn("File > Download", warning["message"])
        self.assertEqual(sorted(Path(path).name for path in warning["paths"]), ["About me.gdoc", "Budget.gsheet"])
        # Saved with the source, so its views show it after other sources are scanned.
        self.assertEqual(rag_memory.get_rag_status("test")["warnings"], result["warnings"])
        [base] = rag_memory.list_rag_bases()
        self.assertEqual(base["warnings"], result["warnings"])

    @unittest.skipUnless(os.name == "nt", "hidden file attributes are Windows-only")
    def test_hidden_system_files_are_not_indexed(self):
        import subprocess

        folder = self.temp_path / "hidden"
        folder.mkdir()
        (folder / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
        (folder / "notes.md").write_text("notes", encoding="utf-8")
        subprocess.run(["attrib", "+h", str(folder / "desktop.ini")], check=True)
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", return_value={"status": "indexed"}) as index_file:
            result = indexer._scan_knowledge_base(False, "test", None, None, None)

        self.assertEqual(result["discovered"], 1)
        self.assertEqual(index_file.call_args.args[0].name, "notes.md")

    def test_scan_progress_reports_each_file_phase(self):
        folder = self.temp_path / "phases"
        folder.mkdir()
        (folder / "notes.md").write_text("phase text", encoding="utf-8")
        updates = []
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(rag_database, "document_metadata", return_value=None), \
                patch.object(rag_database, "replace_document"), \
                patch.object(indexer, "_embed", return_value=[[0.1, 0.2]]):
            result = indexer._scan_knowledge_base(
                False, "test", lambda **changes: updates.append(changes), None, None
            )

        self.assertEqual(result["indexed"], 1)
        phases = [update["phase"] for update in updates if update.get("phase")]
        self.assertEqual(phases, ["embedding"])
        self.assertEqual(updates[-1]["phase"], "")

    def test_scan_error_decision_endpoint_state_can_skip_all(self):
        decision_event = threading.Event()
        job = {
            "status": "awaiting_decision",
            "error_mode": "ask",
            "pending_error": {"path": "problem.pdf", "error": "parser failed"},
        }
        with patch.object(scanner, "_job", job), \
                patch.object(scanner, "_error_decision_event", decision_event):
            updated = scanner.decide_scan_error("skip_all")

        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["error_mode"], "skip_all")
        self.assertTrue(decision_event.is_set())
        with patch.object(scanner, "_job", {"status": "running"}):
            with self.assertRaisesRegex(ValueError, "no indexing error"):
                scanner.decide_scan_error("skip")

    def test_source_names_are_unique_across_archived_branches(self):
        one = self.temp_path / "one"
        two = self.temp_path / "two"
        one.mkdir()
        two.mkdir()
        first = rag_config.add_knowledge_base("First", str(one))
        rag_config.archive_knowledge_base(first["id"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            rag_config.add_knowledge_base("First", str(two))
        with self.assertRaisesRegex(ValueError, "reserved"):
            rag_config.add_knowledge_base("Default Knowledge Base", str(two))

    def test_scan_job_can_pause_schedule_resume_and_persist_progress(self):

        folder = self.temp_path / "scheduled"
        folder.mkdir()
        entered_scan = threading.Event()
        pause_gate = threading.Event()
        finished_scan = threading.Event()
        resume_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

        def controlled_scan(**kwargs):
            kwargs["progress"](discovered=2, completed=0, current="first.md")
            entered_scan.set()
            pause_gate.wait(2)
            kwargs["pause_point"]()
            kwargs["progress"](completed=1, indexed=1, current="")
            finished_scan.set()
            return {"errors": [], "removed": []}

        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "scan_config.json"), \
                patch.object(scanner, "JOB_PATH", self.temp_path / "rag_scan_job.json"), \
                patch.object(scanner, "_job", {"status": "idle"}), \
                patch.object(scanner, "_job_thread", None), \
                patch.object(indexer, "scan_knowledge_base", side_effect=controlled_scan), \
                patch.object(rag_config, "DATA_DIR", self.temp_path / "rag-data"):
            base = rag_config.add_knowledge_base("Scheduled", str(folder))
            base_id = base["id"]
            scanner.start_scan_job(base_id)
            self.assertTrue(entered_scan.wait(2))
            scanner.pause_scan_job()
            pause_gate.set()
            deadline = datetime.now() + timedelta(seconds=2)
            while scanner.get_scan_job()["status"] != "paused" and datetime.now() < deadline:
                threading.Event().wait(0.01)
            self.assertEqual(scanner.get_scan_job()["status"], "paused")

            scanner.schedule_scan_resume(resume_time)
            saved = json.loads((self.temp_path / "rag_scan_job.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["resume_at"], resume_time)
            scanner.resume_scan_job()
            self.assertTrue(finished_scan.wait(2))
            thread = scanner._job_thread
            if thread:
                thread.join(2)
            self.assertEqual(scanner.get_scan_job()["status"], "completed")
            self.assertIsNone(scanner.get_scan_job()["resume_at"])

    def test_stopping_scan_wakes_worker_and_blocks_automatic_restarts(self):
        folder = self.temp_path / "stopping"
        folder.mkdir()
        entered_scan = threading.Event()
        stop_gate = threading.Event()

        def controlled_scan(**kwargs):
            entered_scan.set()
            stop_gate.wait(2)
            kwargs["pause_point"]()
            self.fail("scan continued after it was stopped")

        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "stop_config.json"), \
                patch.object(scanner, "JOB_PATH", self.temp_path / "rag_scan_job.json"), \
                patch.object(scanner, "_job", {"status": "idle"}), \
                patch.object(scanner, "_job_thread", None), \
                patch.object(scanner, "_stop_requested", threading.Event()), \
                patch.object(scanner, "_pause_requested", threading.Event()), \
                patch.object(scanner, "_resume_event", threading.Event()), \
                patch.object(scanner, "_error_decision_event", threading.Event()), \
                patch.object(indexer, "scan_knowledge_base", side_effect=controlled_scan), \
                patch.object(rag_config, "DATA_DIR", self.temp_path / "rag-data"), \
                patch.object(rag_config, "list_knowledge_bases", return_value=[
                    {"id": "stopping", "active": True}
                ]):
            scanner._resume_event.set()
            base = rag_config.add_knowledge_base("Stopping", str(folder))
            scanner.start_scan_job(base["id"])
            self.assertTrue(entered_scan.wait(2))
            result = scanner.stop_scan_job()
            stop_gate.set()
            thread = scanner._job_thread
            if thread:
                thread.join(2)

            self.assertEqual(result["status"], "stopping")
            self.assertEqual(scanner.get_scan_job()["status"], "stopped")
            self.assertIn(base["id"], scanner.get_scan_job()["stopped_base_ids"])
            self.assertFalse(thread and thread.is_alive())
            deleted = rag_memory.delete_rag_base(base["id"])
            self.assertTrue(deleted["deleted"])
            self.assertNotIn(base["id"], scanner.get_scan_job()["stopped_base_ids"])

if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_rag_chroma_store.py ---

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragMemory import interface_ragMemory, logic_ragMemory, mcp_ragMemory

REPO_ROOT = Path(__file__).resolve().parent.parent


def fake_embed(model, input):
    """Deterministic 3-d vectors: 'alpha' texts point one way, everything else another."""
    texts = [input] if isinstance(input, str) else input
    return {"embeddings": [[1.0, 0.0, 0.0] if "alpha" in text else [0.0, 1.0, 0.0] for text in texts]}


class TestRagChromaStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name) / "home"
        self.folder = Path(self.temp_dir.name) / "docs"
        self.folder.mkdir()
        embed_patch = patch.object(logic_ragMemory.ollama, "embed", side_effect=fake_embed)
        embed_patch.start()
        self.addCleanup(embed_patch.stop)
        self.service = logic_ragMemory.create_rag_memory(self.home)

    def test_index_search_and_delete_round_trip(self):
        source = self.service.add_rag_base("Docs", str(self.folder))
        (self.folder / "a.md").write_text("alpha facts", encoding="utf-8")
        (self.folder / "b.md").write_text("beta facts", encoding="utf-8")

        result = self.service.indexer.scan_knowledge_base(base_id=source["id"])
        self.assertEqual(result["indexed"], 2)
        self.assertEqual(
            [item["chunks"] for item in self.service.list_documents(source["id"])], [1, 1]
        )

        hits = self.service.search_knowledge_base("alpha question", top_k=2, base_id=source["id"])
        self.assertEqual(Path(hits["results"][0]["path"]).name, "a.md")
        self.assertAlmostEqual(hits["results"][0]["score"], 1.0, places=5)
        self.assertEqual(hits["skipped_incompatible_chunks"], 0)

        document = self.service.get_document(hits["results"][0]["path"], source["id"])
        self.assertEqual(document["chunks_data"][0]["content"], "alpha facts")
        self.assertEqual(len(document["chunks_data"][0]["embedding"]), 3)

        (self.folder / "a.md").unlink()
        rescan = self.service.indexer.scan_knowledge_base(base_id=source["id"])
        self.assertEqual(len(rescan["removed"]), 1)
        hits = self.service.search_knowledge_base("alpha question", top_k=5, base_id=source["id"])
        self.assertEqual([Path(hit["path"]).name for hit in hits["results"]], ["b.md"])

        cleared = self.service.clear_rag_index(source["id"])
        self.assertEqual(cleared["removed"], 1)
        self.assertEqual(cleared["chunks"], 0)

    def test_factory_keeps_all_state_under_its_home(self):
        source = self.service.add_rag_base("Docs", str(self.folder))
        self.service.add_text_document("note", "alpha note", source["id"])

        self.assertTrue((self.home / "rag_config.json").is_file())
        self.assertTrue((self.home / "data" / "rag_indexes" / f"{source['id']}.sqlite3").is_file())
        self.assertTrue((self.home / "data" / "rag_indexes" / "chroma").is_dir())
        self.assertNotEqual(self.service.config.CONFIG_PATH, logic_ragMemory.rag_config.CONFIG_PATH)

    def test_deleting_a_source_removes_its_vectors(self):
        source = self.service.add_rag_base("Docs", str(self.folder))
        self.service.add_text_document("note", "alpha note", source["id"])
        self.service.delete_rag_base(source["id"])

        again = self.service.add_rag_base("Docs again", str(self.folder))
        with self.service.config.database_context(again["id"]):
            self.assertEqual(self.service.database.list_chunks(), [])
        self.assertFalse(
            (self.home / "data" / "rag_indexes" / f"{source['id']}.sqlite3").exists()
        )

    def test_legacy_json_chunks_migrate_into_chroma_without_embedding(self):
        source_id = "0123456789abcdef"
        self.home.mkdir(parents=True)
        (self.home / "rag_config.json").write_text(json.dumps({
            "knowledge_bases": [{
                "id": source_id, "name": "Old", "path": str(self.folder),
                "active": True, "legacy_index": False,
            }],
            "selected_knowledge_base": source_id,
        }), encoding="utf-8")
        manifest = self.home / "data" / "rag_indexes" / f"{source_id}.sqlite3"
        manifest.parent.mkdir(parents=True)
        connection = sqlite3.connect(manifest)
        connection.executescript(
            """
            CREATE TABLE documents (path TEXT PRIMARY KEY, modified_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL, indexed_at TEXT NOT NULL);
            CREATE TABLE chunks (path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL, content TEXT NOT NULL, embedding TEXT NOT NULL,
                PRIMARY KEY (path, chunk_index));
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO documents VALUES ('old.md', 1, 10, '2025-01-01T00:00:00+00:00');
            INSERT INTO chunks VALUES ('old.md', 0, 'alpha legacy', '[1.0,0.0,0.0]');
            INSERT INTO chunks VALUES ('old.md', 1, 'beta legacy', '[0.0,1.0,0.0]');
            """
        )
        connection.commit()
        connection.close()

        with patch.object(logic_ragMemory.ollama, "embed", side_effect=fake_embed) as embed:
            documents = self.service.list_documents(source_id)
            embed.assert_not_called()
        self.assertEqual(documents[0]["chunks"], 2)
        self.assertTrue(manifest.with_name(f"{manifest.name}.pre-chroma.bak").is_file())
        with sqlite3.connect(manifest) as check:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("chunks", tables)

        hits = self.service.search_knowledge_base("alpha?", top_k=1)
        self.assertEqual(hits["results"][0]["content"], "alpha legacy")

    def test_interface_doorway_and_mcp_tools_use_the_configured_home(self):
        self.addCleanup(interface_ragMemory.configure, None)
        interface_ragMemory.configure(self.home)
        source = interface_ragMemory.add_source("Docs", str(self.folder))
        interface_ragMemory.add_text("note", "alpha note", source["id"])

        self.assertEqual(interface_ragMemory.selected_source_id(), source["id"])
        hits = interface_ragMemory.search_knowledge("alpha?", 3)
        self.assertEqual(hits["results"][0]["path"], "manual:note")

        listed = json.loads(mcp_ragMemory.list_knowledge_sources())
        self.assertEqual([(item["name"], item["chunks"]) for item in listed], [("Docs", 1)])
        found = json.loads(mcp_ragMemory.search_knowledge("alpha?", 1, source["id"]))
        self.assertEqual(found["results"][0]["content"], "alpha note")
        document = json.loads(mcp_ragMemory.read_document("manual:note"))
        self.assertEqual(document["content"], "alpha note")

        interface_ragMemory.configure(None)
        self.assertIs(interface_ragMemory.get_service(), logic_ragMemory.rag_memory)


class TestRagComponentBoundaries(unittest.TestCase):
    def test_package_imports_without_other_novous_components(self):
        code = (
            "import sys\n"
            "for name in ('editor', 'core_engine', 'tools', 'orchestration', 'scratchpadMemory'):\n"
            "    sys.modules[name] = None\n"
            "import ragMemory, ragMemory.mcp_ragMemory\n"
            "print(ragMemory.get_service().indexer.embedder.model)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_ragmemory_and_tests_import_the_logic_module(self):
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT)
            if relative.parts[0] in {"ragMemory", "tests", "venv", ".git"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ragMemory.logic_ragMemory" in text or "from ragMemory import logic_ragMemory" in text:
                offenders.append(relative.as_posix())
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_rag_pdf_indexer_fallback.py ---

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fitz

from ragMemory import logic_ragMemory
from ragMemory.logic_ragMemory import document_indexer as indexer


class TestPdfIndexerFallback(unittest.TestCase):
    def test_uses_pymupdf_when_docling_pdf_backend_cannot_load(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "text.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "PDF text remains searchable")
            document.save(pdf_path)
            document.close()

            converter = Mock()
            converter.convert.side_effect = ImportError("native parser unavailable")
            with patch.object(indexer, "_doc_converter", converter):
                text = indexer._document_text(pdf_path)

        self.assertIn("PDF text remains searchable", text)

    def test_reports_when_fallback_cannot_extract_pdf_text(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "image-only.pdf"
            document = fitz.open()
            document.new_page()
            document.save(pdf_path)
            document.close()

            converter = Mock()
            converter.convert.side_effect = ImportError("native parser unavailable")
            with patch.object(indexer, "_doc_converter", converter):
                with self.assertRaisesRegex(RuntimeError, "scanned PDFs require"):
                    indexer._document_text(pdf_path)


class TestCloudPlaceholderSources(unittest.TestCase):
    def test_failed_cloud_download_reports_how_to_fix_it(self):
        error = OSError(22, "Invalid argument", "notes.docx", 362)
        with patch.object(indexer, "cloud_provider", return_value="OneDrive"), \
                patch.object(indexer, "_is_cloud_placeholder", return_value=True), \
                patch.object(Path, "read_bytes", side_effect=error):
            phases = []
            with self.assertRaisesRegex(
                RuntimeError, r"File not downloaded: OneDrive.*WinError 362.*Always keep on this device"
            ):
                indexer._document_text(Path("C:/OneDrive/notes.docx"), phase=phases.append)

        self.assertEqual(phases, ["downloading from OneDrive"])

    def test_stuck_cloud_download_times_out_with_a_clear_message(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with patch.object(indexer, "cloud_provider", return_value="Google Drive"), \
                patch.object(indexer, "_is_cloud_placeholder", return_value=False), \
                patch.object(indexer, "CLOUD_DOWNLOAD_TIMEOUT_SECONDS", 0.2), \
                patch.object(Path, "read_bytes", side_effect=lambda: release.wait(5) and b""):
            phases = []
            with self.assertRaisesRegex(
                RuntimeError, r"File not downloaded: Google Drive did not deliver it within 0.2 seconds.*Available offline"
            ):
                indexer._document_text(Path("G:/My Drive/notes.docx"), phase=phases.append)

        self.assertEqual(phases, ["reading from Google Drive"])

    def test_google_drive_volume_and_onedrive_folder_are_recognised(self):
        with patch.object(logic_ragMemory, "_volume_label", side_effect=lambda anchor: {
            "G:\\": "Google Drive"
        }.get(anchor, "Windows")), patch.dict(os.environ, {"OneDrive": r"C:\Users\me\OneDrive"}):
            self.assertEqual(indexer.cloud_provider(Path(r"G:\My Drive\Resume")), "Google Drive")
            self.assertEqual(indexer.cloud_provider(Path(r"C:\Users\me\OneDrive\Docs")), "OneDrive")
            self.assertIsNone(indexer.cloud_provider(Path(r"C:\Users\me\Desktop\missing")))

    def test_docling_receives_a_stream_of_the_downloaded_bytes(self):
        from docling.datamodel.base_models import DocumentStream

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.docx"
            path.write_bytes(b"docx bytes")
            converter = Mock()
            converter.convert.return_value.document.export_to_markdown.return_value = "# Notes"
            with patch.object(indexer, "_doc_converter", converter):
                text = indexer._document_text(path)

        source = converter.convert.call_args.args[0]
        self.assertIsInstance(source, DocumentStream)
        self.assertEqual(source.name, "notes.docx")
        self.assertEqual(source.stream.read(), b"docx bytes")
        self.assertEqual(text, "# Notes")

    def test_pdf_without_text_is_parsed_again_with_ocr(self):
        plain = Mock()
        plain.convert.return_value.document.export_to_markdown.return_value = "<!-- image -->"
        ocr = Mock()
        ocr.convert.return_value.document.export_to_markdown.return_value = "Scanned words"
        phases = []
        with patch.object(indexer, "PDF_OCR", "auto"), \
                patch.object(indexer, "_doc_converter", plain), \
                patch.object(indexer, "_ocr_converter", ocr):
            text = indexer._document_text(Path("scan.pdf"), b"%PDF", phases.append)

        self.assertEqual(text, "Scanned words")
        self.assertEqual(phases, ["parsing", "parsing with OCR"])


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_sandbox_env.py ---

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_environment.logic_test_environment import sandbox_env


class SandboxEnvTests(unittest.TestCase):
    def test_package_names_are_validated(self):
        self.assertEqual(sandbox_env.validate_packages(["streamlit", "requests==2.31.0", "numpy>=1.0"]),
                         ["streamlit", "requests==2.31.0", "numpy>=1.0"])
        for bad in ["--index-url http://x", "git+https://x/y", "../local", "a b", "", "pkg;rm"]:
            with self.assertRaises(ValueError, msg=bad):
                sandbox_env.validate_packages([bad])

    def test_third_party_modules_skip_stdlib_and_project_modules(self):
        source = "import os, json\nimport streamlit as st\nfrom cv2 import imread\nfrom tools import interface\nimport a.b\n"
        self.assertEqual(sandbox_env.third_party_modules(source), ["streamlit", "cv2", "a"])
        self.assertEqual(sandbox_env.package_for_module("cv2"), "opencv-python")

    def test_install_uses_sandbox_pip_with_validated_names(self):
        with patch.object(sandbox_env, "ensure_venv", return_value={"ok": True, "output": ""}), \
                patch.object(sandbox_env, "_run", return_value={"ok": True, "output": "done"}) as run:
            result = sandbox_env.install(["streamlit"])
        self.assertTrue(result["ok"])
        command = run.call_args[0][0]
        self.assertEqual(command[1:4], ["-m", "pip", "install"])
        self.assertEqual(command[-1], "streamlit")

    def test_promotion_appends_only_new_packages_to_requirements(self):
        with tempfile.TemporaryDirectory() as temp:
            requirements = Path(temp) / "requirements.txt"
            requirements.write_text("fastapi>=0.110.0\nrequests>=2.0", encoding="utf-8")
            source = "import fastapi\nimport requests\nimport streamlit\nimport cv2\n"
            with patch.object(sandbox_env, "REQUIREMENTS_PATH", requirements), \
                    patch.object(sandbox_env, "list_packages",
                                 return_value=[{"name": "streamlit", "version": "1.40.0"}]):
                added = sandbox_env.add_to_requirements(source)
                again = sandbox_env.add_to_requirements(source)
            self.assertEqual(added, ["streamlit>=1.40.0", "opencv-python"])
            self.assertEqual(again, [])
            lines = requirements.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, ["fastapi>=0.110.0", "requests>=2.0", "streamlit>=1.40.0", "opencv-python"])


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_scratchpad_write.py ---

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_engine import logic_core_engine
from scratchpadMemory.logic_scratchpadMemory import chat_scratchpad
from tools import logic_tools


class WriteChatScratchpadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = patch.object(chat_scratchpad, "PATH", Path(self._tmp.name) / "chat_scratchpad.json")
        self._path.start()
        self._session = logic_core_engine._CURRENT_SESSION_ID.set("session-1")

    def tearDown(self):
        logic_core_engine._CURRENT_SESSION_ID.reset(self._session)
        self._path.stop()
        self._tmp.cleanup()

    def test_empty_content_is_refused_and_nothing_is_stored(self):
        for empty in ("", "   "):
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                logic_tools.write_chat_scratchpad(agent="problem-analyst", title="result", content=empty)
        self.assertEqual(chat_scratchpad.entries("session-1"), [])

    def test_json_content_is_saved_under_agent_and_title(self):
        payload = '{"answers": {"goal": "Move into operations"}, "summary": "Retail leader."}'
        logic_tools.write_chat_scratchpad(agent="problem-analyst", title="result", content=payload)
        entry = chat_scratchpad.entries("session-1")[0]
        self.assertEqual((entry["author"], entry["title"]), ("problem-analyst", "result"))
        read = json.loads(logic_tools.read_chat_scratchpad(agent="problem-analyst", title="result"))
        self.assertEqual(json.loads(read["content"])["answers"]["goal"], "Move into operations")

    def test_empty_answers_object_is_still_stored(self):
        logic_tools.write_chat_scratchpad(agent="problem-analyst", title="result",
                                          content='{"answers": {}, "summary": "No answers were given."}')
        self.assertEqual(len(chat_scratchpad.entries("session-1")), 1)

    def test_write_outside_a_chat_session_is_refused(self):
        token = logic_core_engine._CURRENT_SESSION_ID.set("")
        try:
            with self.assertRaisesRegex(ValueError, "chat session"):
                logic_tools.write_chat_scratchpad(agent="a", title="b", content="c")
        finally:
            logic_core_engine._CURRENT_SESSION_ID.reset(token)


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_tools_read_tool.py ---

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import logic_tools
from tools.logic_tools import FileReadLimitError, workspace_files


class TestReadTool(unittest.TestCase):
    def setUp(self):
        workspace_files._doc_converter = None

    def tearDown(self):
        workspace_files._doc_converter = None

    def _source(self, path: str, content: bytes = b"%PDF") -> dict:
        return {
            "path": path,
            "resolved_path": Path(path),
            "size_bytes": len(content),
            "content": content,
        }

    def test_reads_text_files_as_utf8(self):
        read_bytes = Mock()
        read_bytes.return_value = self._source("notes.txt", "hello".encode("utf-8"))

        with patch.object(workspace_files, "read_file_bytes", read_bytes):
            source, content = workspace_files.read_document("notes.txt")

        self.assertEqual(source["path"], "notes.txt")
        self.assertEqual(content, "hello")

    def test_extracts_pdf_text_with_docling(self):
        read_bytes = Mock()
        read_bytes.return_value = self._source("manual.pdf")
        document = Mock()
        document.export_to_markdown.return_value = "# Manual"
        converter = Mock()
        converter.convert.return_value.document = document

        with (
            patch.object(workspace_files, "read_file_bytes", read_bytes),
            patch.object(workspace_files, "_get_docling_converter", return_value=converter),
        ):
            source, content = workspace_files.read_document("manual.pdf")

        self.assertEqual(content, "# Manual")
        converter.convert.assert_called_once_with("manual.pdf")

    def test_docling_converter_enables_ocr_for_pdf(self):
        input_format = object()
        input_formats = types.ModuleType("docling.datamodel.base_models")
        input_formats.InputFormat = types.SimpleNamespace(PDF=input_format)

        pipeline_options = Mock()
        pipeline_options_module = types.ModuleType("docling.datamodel.pipeline_options")
        pipeline_options_module.PdfPipelineOptions = Mock(return_value=pipeline_options)

        converter_module = types.ModuleType("docling.document_converter")
        converter_class = Mock(return_value=object())
        pdf_format_option = Mock()
        converter_module.DocumentConverter = converter_class
        converter_module.PdfFormatOption = pdf_format_option

        modules = {
            "docling": types.ModuleType("docling"),
            "docling.datamodel": types.ModuleType("docling.datamodel"),
            "docling.datamodel.base_models": input_formats,
            "docling.datamodel.pipeline_options": pipeline_options_module,
            "docling.document_converter": converter_module,
        }
        with patch.dict(sys.modules, modules):
            workspace_files._get_docling_converter()

        self.assertTrue(pipeline_options.do_ocr)
        pdf_format_option.assert_called_once_with(pipeline_options=pipeline_options)
        converter_class.assert_called_once_with(
            format_options={input_format: pdf_format_option.return_value}
        )

    def test_reports_oversized_pdf_with_actionable_message(self):
        read_bytes = Mock()
        read_bytes.side_effect = FileReadLimitError("over limit")

        with patch.object(workspace_files, "read_file_bytes", read_bytes):
            with self.assertRaisesRegex(ValueError, "25 MB.*Split or compress"):
                workspace_files.read_document("manual.pdf")

    def test_reports_unreadable_pdf_without_fabricating_content(
        self,
    ):
        read_bytes = Mock()
        read_bytes.return_value = self._source("locked.pdf")
        converter = Mock()
        converter.convert.side_effect = ValueError("bad PDF")

        with (
            patch.object(workspace_files, "read_file_bytes", read_bytes),
            patch.object(workspace_files, "_get_docling_converter", return_value=converter),
        ):
            with self.assertRaisesRegex(RuntimeError, "encrypted, damaged, unsupported"):
                workspace_files.read_document("locked.pdf")

    def test_stores_extracted_document_text_in_agent_memory(
        self,
    ):
        read_content = Mock()
        store_read = Mock()
        read_content.return_value = (self._source("manual.pdf"), "# Manual")
        store_read.return_value = {
            "path": "agentMemory/data/scratchpad.json",
            "updated_at": "2026-10-21T00:00:00+00:00",
        }

        with (
            patch.object(workspace_files, "read_document", read_content),
            patch.object(logic_tools.scratchpad, "store_read", store_read),
        ):
            result = json.loads(logic_tools.read_file("manual.pdf", "summarize"))

        self.assertEqual(result["content"], "# Manual")
        self.assertEqual(result["content_type"], "document_text")
        store_read.assert_called_once_with("manual.pdf", "# Manual", "summarize")

    def test_long_read_returns_first_page_and_stores_full_text(self):
        long_text = "line\n" * 2000
        read_content = Mock(return_value=(self._source("long.md"), long_text))
        store_read = Mock(return_value={
            "path": "scratchpadMemory/data/scratchpad.json",
            "updated_at": "2026-10-21T00:00:00+00:00",
        })

        with (
            patch.object(workspace_files, "read_document", read_content),
            patch.object(logic_tools.scratchpad, "store_read", store_read),
        ):
            result = json.loads(logic_tools.read_file("long.md", "summarize"))

        store_read.assert_called_once_with("long.md", long_text, "summarize")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["content"]), 4000)
        self.assertEqual(result["total_chars"], len(long_text))
        self.assertEqual(result["next_offset"], len(result["content"]))
        self.assertIn("chat session", result["scratchpad"]["context"])


if __name__ == "__main__":
    unittest.main()
