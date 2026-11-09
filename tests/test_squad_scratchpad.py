"""Run-scoped squad scratchpad storage, API, and orchestration integration."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from orchestration.logic_orchestration import (
    AgentTurnCancelled, pipeline_registry, squad_registry, subagent_runner, telemetry_bus,
)
from orchestration.schemas_orchestration import OrchestrationRequest
from scratchpadMemory.logic_scratchpadMemory import SquadScratchpadService, squad_scratchpad
from server import app


def test_isolated_runs_filters_caps_and_recovery(monkeypatch):
    monkeypatch.setattr(squad_scratchpad, "MAX_ENTRIES", 2)
    squad_scratchpad.begin("one", "squad-a", "first")
    squad_scratchpad.begin("two", "squad-b", "second")
    squad_scratchpad.post("one", "alice", "plan", "plan", "x")
    squad_scratchpad.post("one", "bob", "note", "note", "y")
    squad_scratchpad.post("one", "alice", "code", "code", "z" * 20_001)
    squad_scratchpad.post("two", "bob", "test", "test", "other")
    run = squad_scratchpad.get("one", author="alice")
    assert run["total"] == 2
    assert len(run["entries"]) == 1
    assert run["entries"][0]["truncated"] is True
    assert len(run["entries"][0]["content"]) == 20_000
    assert squad_scratchpad.get("two")["entries"][0]["content"] == "other"
    with pytest.raises(ValueError):
        squad_scratchpad.clear("one")
    with pytest.raises(ValueError):
        squad_scratchpad.post("one", "alice", "invalid", "x", "x")
    restarted = SquadScratchpadService()
    monkeypatch.setattr(restarted, "PATH", squad_scratchpad.PATH)
    assert restarted.get("one")["status"] == "interrupted"
    assert restarted.clear("one")
    assert restarted.get("two") is not None


def test_concurrent_posts_are_not_lost():
    squad_scratchpad.begin("run", "squad", "request")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: squad_scratchpad.post("run", str(i), "note", "title", str(i)), range(30)))
    assert squad_scratchpad.get("run")["total"] == 30


def test_routes_and_clear_protection():
    squad_scratchpad.begin("run", "squad", "request")
    client = TestClient(app)
    assert client.delete("/api/agent-memory/squad-scratchpads/run").status_code == 409
    assert client.post("/api/agent-memory/squad-scratchpads/run/notes", json={"title": "hint", "content": "text"}).status_code == 201
    assert client.get("/api/agent-memory/squad-scratchpads/run", params={"author": "user"}).json()["entries"][0]["title"] == "hint"
    assert client.get("/api/agent-memory/squad-scratchpads").json()["runs"][0]["run_id"] == "run"
    squad_scratchpad.finish("run", "success")
    saved_version = client.get("/api/agent-memory/squad-scratchpads/run").json()["version"]
    squad_scratchpad.post("run", "user", "note", "new", "new content")
    assert client.delete(
        "/api/agent-memory/squad-scratchpads/run", params={"expected_version": saved_version}
    ).status_code == 409
    assert client.get("/api/agent-memory/squad-scratchpads/run").json()["total"] == 2
    assert client.delete("/api/agent-memory/squad-scratchpads/run").status_code == 200
    assert client.get("/api/agent-memory/squad-scratchpads/run").status_code == 404
    assert client.post("/api/agent-memory/squad-scratchpads/run/notes", json={"title": "hint", "content": "text"}).status_code == 404


@pytest.mark.parametrize("pattern", ["sequential", "fanout", "router"])
def test_generic_squad_captures_each_agent(pattern):
    request = OrchestrationRequest(
        pattern=pattern, primary_agent="lead", subagents=["worker-a", "worker-b"],
        user_query="do work",
    )

    def fake_turn(agent_id, prompt, parameters=None, trace_id=None):
        if "routing a task" in prompt:
            return '{"subagents": ["worker-a", "worker-b"], "reason": "both"}'
        return f"response from {agent_id}"

    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), \
         patch.object(subagent_runner, "read_latest_subagent_scratchpad", return_value={"status": "success"}), \
         patch.object(subagent_runner, "read_scratchpad_snapshot", return_value={}):
        response = pipeline_registry.run_orchestration(request)
    run = squad_scratchpad.get(response.trace_id)
    assert run["status"] == "success"
    assert {entry["author"] for entry in run["entries"]} == {"lead", "worker-a", "worker-b"}
    assert any(entry["content"] == "response from worker-a" for entry in run["entries"])


def test_code_squad_captures_steps_and_keeps_coding_scratchpad():
    request = OrchestrationRequest(squad_id="code-squad", workflow_action="plan", user_query="make a plan")
    with patch.object(subagent_runner, "run_agent_turn_stream", return_value="The plan"), \
         patch.object(squad_registry, "record_squad_run"):
        response = pipeline_registry.run_orchestration(request)
    run = squad_scratchpad.get(response.trace_id)
    assert run["status"] == "success"
    assert any(entry["author"] == "code-planer" and entry["content"] == "The plan" for entry in run["entries"])
    from scratchpadMemory.logic_scratchpadMemory import code_scratchpad
    assert code_scratchpad.entries(run_id=response.trace_id)


def test_cancelled_code_squad_keeps_its_summary():
    request = OrchestrationRequest(squad_id="code-squad", workflow_action="plan", user_query="work")
    with patch.object(subagent_runner, "run_agent_turn_stream", side_effect=AgentTurnCancelled("code-planer")), \
         patch.object(squad_registry, "record_squad_run"):
        response = pipeline_registry.run_orchestration(request)
    run = squad_scratchpad.get(response.trace_id)
    assert run["status"] == "cancelled"
    assert any("Stopped by user." in entry["content"] for entry in run["entries"])
    assert squad_scratchpad.clear(response.trace_id)


def test_failed_member_is_recorded():
    request = OrchestrationRequest(
        pattern="sequential", primary_agent="lead", subagents=["worker"], user_query="work",
    )

    def fake_turn(agent_id, prompt, parameters=None, trace_id=None):
        if agent_id == "worker":
            raise RuntimeError("model unavailable")
        return "lead response"

    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), \
         patch.object(subagent_runner, "read_latest_subagent_scratchpad", return_value={"status": "success"}), \
         patch.object(subagent_runner, "read_scratchpad_snapshot", return_value={}):
        response = pipeline_registry.run_orchestration(request)
    run = squad_scratchpad.get(response.trace_id)
    assert run["status"] == "error"
    assert any(entry["author"] == "worker" and "model unavailable" in entry["content"] for entry in run["entries"])


def test_sequential_members_receive_only_their_run_context():
    squad_scratchpad.begin("other", "other-squad", "private")
    squad_scratchpad.post("other", "agent", "note", "other", "unrelated secret")
    prompts = {}

    def fake_turn(agent_id, prompt, parameters=None, trace_id=None):
        prompts[agent_id] = prompt
        return f"output from {agent_id}"

    request = OrchestrationRequest(
        pattern="sequential", primary_agent="lead", subagents=["first", "second"], user_query="work",
    )
    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), \
         patch.object(subagent_runner, "read_latest_subagent_scratchpad", return_value={"status": "success"}), \
         patch.object(subagent_runner, "read_scratchpad_snapshot", return_value={}):
        pipeline_registry.run_orchestration(request)
    assert "output from first" in prompts["second"]
    assert "output from second" in prompts["lead"]
    assert all("unrelated secret" not in prompt for prompt in prompts.values())


def test_agent_handoff_is_recorded_under_active_agent():
    from tools.logic_tools import write_squad_scratchpad

    squad_scratchpad.begin("run", "squad", "request")
    with telemetry_bus.agent_context("run", "worker"):
        write_squad_scratchpad(agent="worker", title="review", content='{"finding": "evidence"}')
    entry = squad_scratchpad.get("run")["entries"][0]
    assert entry["author"] == "worker" and entry["title"] == "review"
    assert '"finding": "evidence"' in entry["content"]


def test_squad_agent_tools_are_trace_scoped():
    import json
    from tools.logic_tools import read_squad_scratchpad, write_squad_scratchpad

    squad_scratchpad.begin("run", "squad", "request")
    squad_scratchpad.begin("other", "squad", "another request")
    squad_scratchpad.post("other", "worker", "note", "other", "other run")
    with pytest.raises(ValueError, match="active squad run"):
        read_squad_scratchpad(agent="worker", title="other")
    with telemetry_bus.agent_context("run", "worker"):
        result = json.loads(write_squad_scratchpad(agent="worker", title="note", content="run content"))
        assert result["agent"] == "worker" and result["store"] == "squad"
        found = json.loads(read_squad_scratchpad(agent="worker", title="note"))
        assert found["found"] is True and found["content"] == "run content"
        missing = json.loads(read_squad_scratchpad(agent="worker", title="other"))
        assert missing["found"] is False and missing["available"] == [{"agent": "worker", "title": "note"}]
        with pytest.raises(ValueError, match="agent and title"):
            read_squad_scratchpad(agent="", title="note")


def test_squad_read_pages_long_content_and_picks_by_index():
    import json
    from tools.logic_tools import read_squad_scratchpad

    squad_scratchpad.begin("run", "squad", "request")
    squad_scratchpad.post("run", "worker", "note", "result", "old")
    squad_scratchpad.post("run", "worker", "note", "result", "x" * 9_000)
    with telemetry_bus.agent_context("run", "worker"):
        newest = json.loads(read_squad_scratchpad(agent="worker", title="result"))
        assert newest["total_matches"] == 2 and newest["content"] == "x" * 8_000
        assert newest["next_offset"] == 8_000
        rest = json.loads(read_squad_scratchpad(agent="worker", title="result", offset="8000"))
        assert rest["content"] == "x" * 1_000 and "next_offset" not in rest
        older = json.loads(read_squad_scratchpad(agent="worker", title="result", index="1"))
        assert older["content"] == "old"


def test_clear_tool_removes_only_the_matching_entries():
    import json
    from tools.logic_tools import clear_squad_scratchpad, write_squad_scratchpad

    squad_scratchpad.begin("run", "squad", "request")
    squad_scratchpad.begin("other", "squad", "another request")
    squad_scratchpad.post("other", "worker", "note", "other", "other run")
    squad_scratchpad.post("run", "worker", "plan", "drop me", "plan entry")
    with telemetry_bus.agent_context("run", "worker"):
        write_squad_scratchpad(agent="worker", title="keep", content="keep me")
        before = squad_scratchpad.get("run")["version"]
        assert json.loads(clear_squad_scratchpad(kind="plan"))["removed"] == 1
        assert squad_scratchpad.get("run")["version"] == before + 1
        with pytest.raises(ValueError, match="kind must be one of"):
            clear_squad_scratchpad(kind="bogus")
        assert json.loads(clear_squad_scratchpad())["removed"] == 1
        assert squad_scratchpad.get("run")["total"] == 0
        assert squad_scratchpad.get("run")["status"] == "running"
    assert squad_scratchpad.get("other")["total"] == 1
