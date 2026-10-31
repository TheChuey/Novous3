import asyncio
import json
from threading import BoundedSemaphore
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from core_engine.logic_core_engine import Agent, tool_catalog
from core_engine.schemas_core_engine import AgentProfile
from orchestration import interface_orchestration
from orchestration.logic_orchestration import (
    SequentialPattern,
    TelemetryBus,
    rag_retriever,
    run_results,
    run_service,
    squad_registry,
    subagent_runner,
    telemetry_bus,
)
from orchestration.schemas_orchestration import OrchestrationRequest
from ragMemory.logic_ragMemory import rag_config, rag_memory
from scratchpadMemory.logic_scratchpadMemory import scratchpad


@pytest.fixture
def fake_rag_turns(monkeypatch):
    """Replace agent turns and scratchpad reads for sequential RAG-squad runs."""

    def install(final_answer, latest, snapshot):
        monkeypatch.setattr(
            subagent_runner, "run_agent_turn",
            lambda agent_id, prompt, parameters=None, trace_id=None: final_answer(agent_id),
        )
        monkeypatch.setattr(subagent_runner, "read_latest_subagent_scratchpad", lambda: latest)
        monkeypatch.setattr(subagent_runner, "read_scratchpad_snapshot", lambda: snapshot)

    return install


def test_squad_metrics_are_persisted_and_exposed():
    squad_registry.record_squad_run("rag-squad", True, 120)
    squad_registry.record_squad_run("rag-squad", False, 80)

    squad = squad_registry.get_squad("rag-squad")
    assert squad["primary_agent"] == "rag-presenter"
    assert squad["subagents"] == ["rag-retriever"]
    assert squad["metrics"] == {
        "total_runs": 2,
        "successful_runs": 1,
        "failed_runs": 1,
        "average_duration_ms": 100.0,
        "last_duration_ms": 80,
        "last_status": "failing",
    }
    saved = json.loads(squad_registry.METRICS_PATH.read_text(encoding="utf-8"))
    assert saved["rag-squad"]["total_runs"] == 2


def test_code_squad_uses_planner_builder_tester_and_troubleshooter():
    squad = squad_registry.get_squad("code-squad")
    assert squad["primary_agent"] == "code-planer"
    assert squad["subagents"] == ["code-builder", "tester", "code-troubleshooter"]


def test_saved_code_squad_migrates_legacy_members_to_tester_pipeline():
    squad_registry.SQUADS_PATH.write_text(
        json.dumps(
            {
                "code-squad": {
                    "squad_id": "code-squad",
                    "name": "Code Development Squad",
                    "description": "Existing saved squad.",
                    "primary_agent": "code-planer",
                    "subagents": ["code-builder", "code-troubleshooter"],
                    "default_pattern": "sequential",
                }
            }
        ),
        encoding="utf-8",
    )

    squad = squad_registry.get_squad("code-squad")

    assert squad["subagents"] == ["code-builder", "tester", "code-troubleshooter"]


def test_legacy_squad_files_move_once(tmp_path, monkeypatch):
    from orchestration import logic_orchestration

    legacy_dir = tmp_path / "agentMemory" / "data"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / squad_registry.SQUADS_PATH.name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(logic_orchestration, "LEGACY_DATA_DIR", legacy_dir)

    assert squad_registry.migrate_legacy() == [squad_registry.SQUADS_PATH.name]
    assert squad_registry.SQUADS_PATH.read_text(encoding="utf-8") == "{}"
    assert squad_registry.migrate_legacy() == []


def test_code_planner_runs_as_a_read_only_tool_using_agent():
    metadata, prompt = subagent_runner.load_agent("code-planer")
    assert metadata["mode"] == "agent"
    assert {"map_files", "read_file", "project_status"} <= set(metadata["tools"])
    assert "write_file" not in metadata["tools"]
    assert tool_catalog.unknown_tool_names(metadata["tools"]) == []
    assert "read-only" in prompt


def test_squad_catalog_includes_new_code_subagents():
    catalog = squad_registry.list_agent_catalog()
    agent_ids = {agent["id"] for agent in catalog["agents"]}
    subagent_ids = {agent["id"] for agent in catalog["subagents"]}
    assert "code-planer" in agent_ids
    assert {"code-builder", "tester", "code-troubleshooter"} <= subagent_ids


def test_code_subagents_are_text_only_and_return_one_python_block():
    for agent_id in ("code-builder", "tester", "code-troubleshooter"):
        metadata, prompt = subagent_runner.load_agent(agent_id)
        assert metadata["id"] == agent_id
        assert metadata["tools"] == []
        assert "```python block" in prompt


def test_dynamic_squad_creation_and_deletion_preserve_agent_definitions(monkeypatch):
    real_catalog = squad_registry.list_agent_catalog
    monkeypatch.setattr(
        squad_registry,
        "list_agent_catalog",
        lambda: {
            "agents": [{"id": "code-planer", "name": "Coding Planner", "description": ""}],
            "subagents": [
                {"id": "code-builder", "name": "Code Builder", "description": ""},
                {"id": "code-troubleshooter", "name": "Troubleshooter", "description": ""},
            ],
        },
    )
    squad_registry.record_squad_run("code-squad", True, 10)

    created = squad_registry.create_squad(
        "My Code Team",
        "Build and troubleshoot.",
        "code-planer",
        ["code-builder", "code-troubleshooter"],
    )
    assert created["squad_id"] == "my-code-team"
    assert created["subagents"] == ["code-builder", "code-troubleshooter"]
    assert squad_registry.create_squad("My Code Team", "", "code-planer", [])["squad_id"] == "my-code-team-2"

    assert squad_registry.delete_squad("my-code-team")
    assert not squad_registry.delete_squad("my-code-team")
    assert squad_registry.get_squad("code-squad") is not None
    assert squad_registry.get_squad("code-squad")["metrics"]["total_runs"] == 1
    assert "code-builder" in {item["id"] for item in real_catalog()["subagents"]}


def test_squad_creation_rejects_unknown_and_duplicate_members():
    with pytest.raises(ValueError, match="Primary agent is not available"):
        squad_registry.create_squad("Team", "", "no-such-agent", [])
    with pytest.raises(ValueError, match="duplicates"):
        squad_registry.create_squad("Team", "", "code-planer", ["tester", "tester"])
    with pytest.raises(ValueError, match="not available"):
        squad_registry.create_squad("Team", "", "code-planer", ["no-such-worker"])


def test_deleting_last_squad_keeps_registry_empty(monkeypatch):
    monkeypatch.setattr(
        squad_registry,
        "list_agent_catalog",
        lambda: {"agents": [{"id": "planner", "name": "Planner", "description": ""}], "subagents": []},
    )
    squad = squad_registry.create_squad("Only Squad", "", "planner", [])

    assert squad_registry.delete_squad(squad["squad_id"])
    for remaining in squad_registry.list_squads():
        squad_registry.delete_squad(remaining["squad_id"])
    assert squad_registry.list_squads() == []


def test_telemetry_is_bounded_and_excludes_sensitive_payloads():
    bus = TelemetryBus(max_traces=2, max_events_per_trace=2)
    bus.initialize_trace("trace-safe")
    bus.log_event(
        "trace-safe",
        "TOOL_CALL_END",
        "rag-retriever",
        "search_knowledge_base",
        {
            "tool_name": "search_knowledge_base",
            "status": "success",
            "duration_ms": 12,
            "arguments": {"query": "private question"},
            "result": "private document text",
        },
    )
    bus.log_event(
        "trace-safe", "AGENT_MESSAGE", "orchestrator", "rag-presenter",
        {"summary": "Synthesis started", "prompt": "private question"},
    )
    bus.log_event(
        "trace-safe", "RUN_COMPLETE", "orchestrator", "rag-squad",
        {"status": "success", "duration_ms": 20},
    )

    history = bus.get_trace_history("trace-safe")
    assert len(history) == 2
    serialized = json.dumps(history)
    assert "private question" not in serialized
    assert "private document text" not in serialized
    assert "arguments" not in serialized


def test_telemetry_sanitizes_paths_statuses_and_identifiers():
    bus = TelemetryBus()
    event = bus.log_event(
        "trace-sanitize", "CODE_CHANGE_PREVIEW", "bad sender!", "editor",
        {"path": "../secret.py", "changed_lines": [0, 2, True, "3"], "file_exists": 1},
    )
    assert event["sender"] == "unknown"
    assert event["data"] == {"path": "", "changed_lines": [2], "file_exists": True}
    status = bus.log_event("trace-sanitize", "ERROR", "a", "b", {"status": "<script>", "error": "x\ny"})
    assert status["data"] == {"status": "unknown", "error": "x y"}
    with pytest.raises(ValueError):
        bus.log_event("trace-sanitize", "NOT_AN_EVENT", "a", "b")


def test_sequential_run_emits_trace_and_persists_metrics(fake_rag_turns):
    trace_id = "tr_observability_test"
    telemetry_bus.initialize_trace(trace_id)
    latest = {
        "subagent_id": "rag-retriever",
        "status": "success",
        "payload": {"results": [{"path": "notes.md"}]},
    }
    fake_rag_turns(lambda agent_id: f"{agent_id} completed", latest, {"latest_subagent_result": latest})

    response = SequentialPattern().execute(
        OrchestrationRequest(
            trace_id=trace_id,
            squad_id="rag-squad",
            primary_agent="rag-presenter",
            subagents=["rag-retriever"],
            user_query="do not include this private question in telemetry",
        )
    )

    events = telemetry_bus.get_trace_history(trace_id)
    event_types = [event["event_type"] for event in events]
    assert response.trace_id == trace_id
    assert response.status == "success"
    assert run_results.get(trace_id)["final_answer"] == "rag-presenter completed"
    assert run_results.get(trace_id)["scratchpad_snapshot"]["latest_subagent_result"]["subagent_id"] == "rag-retriever"
    assert "SCRATCHPAD_HANDOFF" in event_types
    assert event_types[-1] == "RUN_COMPLETE"
    assert "private question" not in json.dumps(events)
    assert squad_registry.get_squad("rag-squad")["metrics"]["total_runs"] == 1
    assert list(telemetry_bus.subscribe(trace_id))[-1].startswith(f"id: {trace_id}:")


def test_non_code_squads_reject_step_actions():
    with pytest.raises(HTTPException) as error:
        interface_orchestration.run_orchestration_endpoint(
            OrchestrationRequest(squad_id="rag-squad", workflow_action="build", user_query="x")
        )
    assert error.value.status_code == 400


def test_async_run_requires_a_registered_squad(monkeypatch):
    class NoRunExecutor:
        def submit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(run_service, "_executor", NoRunExecutor())

    with pytest.raises(HTTPException) as error:
        interface_orchestration.start_orchestration_run(OrchestrationRequest(user_query="test"))
    assert error.value.status_code == 400


def test_async_run_rejects_oversized_editor_buffer_and_full_capacity(monkeypatch):
    class NoRunExecutor:
        def submit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(run_service, "_executor", NoRunExecutor())
    monkeypatch.setattr(run_service, "_capacity", BoundedSemaphore(1))

    with pytest.raises(HTTPException) as too_large:
        interface_orchestration.start_orchestration_run(
            OrchestrationRequest(
                squad_id="code-squad", user_query="x", parameters={"editor_code": "x" * 1_000_001}
            )
        )
    assert too_large.value.status_code == 413

    interface_orchestration.start_orchestration_run(OrchestrationRequest(squad_id="code-squad", user_query="x"))
    with pytest.raises(HTTPException) as full:
        interface_orchestration.start_orchestration_run(OrchestrationRequest(squad_id="code-squad", user_query="x"))
    assert full.value.status_code == 429


def test_cancel_endpoint_flags_known_runs_only():
    from orchestration.logic_orchestration import codebuilder_run_state

    codebuilder_run_state.begin("tr_cancel_me", "")
    assert interface_orchestration.cancel_orchestration_run("tr_cancel_me") == {
        "cancelled": True,
        "trace_id": "tr_cancel_me",
    }
    assert codebuilder_run_state.is_cancelled("tr_cancel_me")
    with pytest.raises(HTTPException) as error:
        interface_orchestration.cancel_orchestration_run("tr_unknown_run")
    assert error.value.status_code == 404


def test_rag_worker_reports_safe_scratchpad_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(scratchpad, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scratchpad, "SCRATCHPAD_PATH", tmp_path / "scratchpad.json")
    trace_id = "tr_rag_handoff_test"
    telemetry_bus.initialize_trace(trace_id)
    result_payload = {
        "knowledge_base_id": "branch-1",
        "knowledge_base": "Test branch",
        "results": [{"path": "notes.md", "content": "private evidence", "score": 0.9}],
    }

    with patch.object(rag_memory, "search_knowledge_base", return_value=result_payload), \
            patch.object(rag_config, "selected_knowledge_base_id", return_value="branch-1"), \
            telemetry_bus.agent_context(trace_id, "rag-retriever"):
        rag_retriever.run("private query", {})

    handoff = next(
        event
        for event in telemetry_bus.get_trace_history(trace_id)
        if event["event_type"] == "SCRATCHPAD_HANDOFF"
    )
    assert handoff["data"] == {
        "status": "success",
        "subagent_id": "rag-retriever",
        "result_count": 1,
        "direction": "write",
    }
    assert "private query" not in json.dumps(handoff)
    assert "private evidence" not in json.dumps(handoff)


def test_agent_tool_events_report_status_without_arguments_or_results():
    def private_tool(query):
        return f"document content for {query}"

    trace_id = "tr_tool_privacy_test"
    telemetry_bus.initialize_trace(trace_id)
    agent = Agent(
        model=None,
        tools=[private_tool],
        profile=AgentProfile(id="observed-agent", mode="agent"),
    )

    with telemetry_bus.agent_context(trace_id, "observed-agent"):
        agent.act(
            {"function": {"name": "private_tool", "arguments": {"query": "private user question"}}},
            "native",
        )

    events = telemetry_bus.get_trace_history(trace_id)
    assert [event["event_type"] for event in events] == ["TOOL_CALL_START", "TOOL_CALL_END"]
    assert events[-1]["data"]["status"] == "success"
    serialized = json.dumps(events)
    assert "private user question" not in serialized
    assert "document content" not in serialized


def test_text_tool_calls_accept_function_wrapped_objects():
    def echo(text):
        return text

    agent = Agent(model=None, tools=[echo], profile=AgentProfile(id="a", mode="agent"))
    calls = agent._extract_text_tool_calls(
        '{"function": {"name": "echo", "arguments": {"text": "hi"}}} {"name": "echo", "parameters": {"text": "yo"}}'
    )
    assert calls == [
        {"function": {"name": "echo", "arguments": {"text": "hi"}}},
        {"function": {"name": "echo", "arguments": {"text": "yo"}}},
    ]


def test_max_tool_rounds_comes_from_the_agent_definition():
    from core_engine.logic_core_engine import agent_library

    profile = agent_library.load_agent_definition_from_md(agent_library.LIBRARY_ROOT / "codingagent.md")
    assert Agent(model=None, tools=[], profile=profile).max_tool_rounds == 12
    assert Agent(model=None, tools=[], profile=AgentProfile(id="x")).max_tool_rounds == Agent.MAX_TOOL_ROUNDS


def test_async_run_endpoint_streams_a_completed_trace(monkeypatch, fake_rag_turns):
    class InlineExecutor:
        def submit(self, function, request):
            function(request)

    monkeypatch.setattr(run_service, "_executor", InlineExecutor())
    monkeypatch.setattr(run_service, "_capacity", BoundedSemaphore(1))
    fake_rag_turns(
        lambda agent_id: "Completed.",
        {"subagent_id": "rag-retriever", "status": "nothing_found", "payload": {"results": []}},
        {"latest_subagent_result": {"subagent_id": "rag-retriever"}},
    )

    started = interface_orchestration.start_orchestration_run(
        OrchestrationRequest(squad_id="rag-squad", user_query="private request text")
    )
    stream = interface_orchestration.stream_orchestration_trace(started["trace_id"])
    assert stream.media_type == "text/event-stream"

    async def read_stream():
        return [chunk async for chunk in stream.body_iterator]

    frames = asyncio.run(read_stream())
    body = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for chunk in frames
    )
    assert "RUN_COMPLETE" in body
    assert "private request text" not in body
    assert "SCRATCHPAD_HANDOFF" in body
    scratchpad_response = interface_orchestration.get_orchestration_run_scratchpad(started["trace_id"])
    assert scratchpad_response["latest_subagent_result"]["subagent_id"] == "rag-retriever"
    latest = interface_orchestration.get_latest_squad_run("rag-squad")
    assert latest["result"]["trace_id"] == started["trace_id"]
