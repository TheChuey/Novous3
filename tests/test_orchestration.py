"""Automated Pytest suite for multi-subagent orchestration and hardware-aware patterns."""

from pathlib import Path
from unittest.mock import patch

import yaml

from orchestration.logic_orchestration import pipeline_registry, subagent_runner
from orchestration.schemas_orchestration import OrchestrationRequest
from ragMemory.logic_ragMemory import rag_config, rag_memory
from scratchpadMemory.logic_scratchpadMemory import scratchpad

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_subagent_yaml_loading():
    yaml_path = REPO_ROOT / "subAgentLibrary" / "code-analyzer.yaml"
    assert yaml_path.is_file()
    content = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert content["id"] == "code-analyzer"
    assert content["type"] == "subagent"


def test_readme_orchestration_exists():
    readme_path = REPO_ROOT / "orchestration" / "README_orchestration.md"
    assert readme_path.is_file()
    text = readme_path.read_text(encoding="utf-8")
    assert "Prompt Design Principles" in text


def test_orchestration_sequential_default_pattern():
    req = OrchestrationRequest(
        pattern="sequential",
        primary_agent="rag-presenter",
        subagents=["code-analyzer", "rag-retriever"],
        user_query="Run hardware-safe sequential execution",
        parameters={"test": True},
    )

    def fake_turn(agent_id, prompt_input, parameters=None, trace_id=None):
        return f"{agent_id} completed: {prompt_input[:40]}"

    with patch.object(
        subagent_runner, "run_agent_turn",
        side_effect=fake_turn,
    ), patch.object(
        subagent_runner, "read_latest_subagent_scratchpad",
        return_value={"status": "success", "payload": {"test": True}},
    ), patch.object(
        subagent_runner, "read_scratchpad_snapshot",
        return_value={"latest_subagent_result": {"status": "success"}},
    ):
        res = pipeline_registry.run_orchestration(req)

    assert res.pattern == "sequential"
    assert res.trace_id.startswith("tr_")
    assert len(res.execution_trace) >= 3
    assert res.final_answer.startswith("rag-presenter completed: User request:")
    assert res.result_payload == {"test": True}


def test_scratchpad_subagent_result_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(scratchpad, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        scratchpad, "SCRATCHPAD_PATH", tmp_path / "scratchpad.json"
    )

    scratchpad.store_subagent_result(
        "code-analyzer", "code_analysis", "success", {"issues_found": []}
    )
    memory = scratchpad.read_memory("latest_subagent_result")

    assert memory["latest_subagent_result"]["subagent_id"] == "code-analyzer"
    assert memory["latest_subagent_result"]["payload"] == {"issues_found": []}


def test_rag_presenter_delegates_to_retriever_and_reads_scratchpad(tmp_path, monkeypatch):
    prompt = (REPO_ROOT / "library" / "rag-presenter.md").read_text(
        encoding="utf-8"
    )
    assert "Novous chat runtime calls `call_subagent`" in prompt
    assert "do not call the sub-agent a second time" in prompt.lower()

    monkeypatch.setattr(scratchpad, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        scratchpad, "SCRATCHPAD_PATH", tmp_path / "scratchpad.json"
    )

    retrieval_payload = {
        "query": "What does the source say?",
        "knowledge_branch_id": "branch-1",
        "knowledge_branch_name": "Test branch",
        "results": [
            {"path": "notes.md", "content": "Relevant evidence", "score": 0.9}
        ],
        "skipped_incompatible_chunks": 0,
    }

    def fake_chat(**kwargs):
        assert any(
            "Relevant evidence" in str(message.get("content", ""))
            for message in kwargs["messages"]
        )
        if fake_chat.calls == 0:
            fake_chat.calls += 1
            return {
                "message": {
                    "content": (
                        "Here is the JSON for a function call:\n"
                        '{"name": "read_chat_scratchpad", "parameters": '
                        '{"agent": "rag-retriever", "title": "result"}}'
                    )
                }
            }
        return {"message": {"content": "The source says relevant evidence."}}

    fake_chat.calls = 0

    with patch.object(
        rag_memory, "search_knowledge_base",
        return_value={
            "knowledge_base_id": "branch-1",
            "knowledge_base": "Test branch",
            "results": retrieval_payload["results"],
        },
    ) as search, patch.object(
        rag_config, "selected_knowledge_base_id",
        return_value="branch-1",
    ), patch(
        "core_engine.logic_core_engine.ollama.chat",
        side_effect=fake_chat,
    ) as chat:
        from core_engine.logic_core_engine import chat_service

        try:
            answer = chat_service.run_single_agent(
                "rag-presenter",
                "What does the source say?",
                session_id="test-rag-presenter-delegation",
            )
        finally:
            chat_service.reset_session("test-rag-presenter-delegation")

    assert answer["reply"] == "The source says relevant evidence."
    search.assert_called_once_with("What does the source say?", 5, "", "branch-1")
    assert chat.call_count == 2
    assert [event["tool"] for event in answer["tool_events"]] == [
        "call_subagent",
        "read_chat_scratchpad",
        "read_chat_scratchpad",
    ]
    result = scratchpad.read_memory("latest_subagent_result")
    assert result["latest_subagent_result"]["subagent_id"] == "rag-retriever"
    assert result["latest_subagent_result"]["payload"] == retrieval_payload


def _fake_scratchpad():
    return (
        patch.object(subagent_runner, "read_latest_subagent_scratchpad", return_value={}),
        patch.object(subagent_runner, "read_scratchpad_snapshot", return_value={}),
    )


def test_fanout_runs_every_member_with_bounded_parallelism():
    import threading
    import time as _time

    active, peak, lock = [0], [0], threading.Lock()

    def fake_turn(agent_id, prompt_input, parameters=None, trace_id=None):
        if agent_id == "lead":
            return f"lead saw: {prompt_input}"
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        _time.sleep(0.05)
        with lock:
            active[0] -= 1
        return f"{agent_id} output"

    req = OrchestrationRequest(
        pattern="fanout", primary_agent="lead", subagents=["a", "b", "c", "d"],
        user_query="Compare options", parameters={"max_parallel": 2},
    )
    latest, snapshot = _fake_scratchpad()
    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), latest, snapshot:
        res = pipeline_registry.run_orchestration(req)

    assert res.pattern == "fanout"
    assert res.status == "success"
    assert peak[0] == 2
    assert res.result_payload["fanout_results"] == {m: f"{m} output" for m in "abcd"}
    assert "--- c ---\nc output" in res.final_answer
    assert [s.phase for s in res.execution_trace].count("SUBAGENT_EXECUTION") == 4


def test_router_runs_only_chosen_members_and_drops_unknown_ids():
    calls = []

    def fake_turn(agent_id, prompt_input, parameters=None, trace_id=None):
        calls.append(agent_id)
        if agent_id == "lead" and "routing" in prompt_input:
            return 'Sure: {"subagents": ["ghost", "b"], "reason": "b knows this"}'
        return f"{agent_id} done"

    req = OrchestrationRequest(
        pattern="router", primary_agent="lead", subagents=["a", "b"], user_query="Route me",
    )
    latest, snapshot = _fake_scratchpad()
    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), latest, snapshot:
        res = pipeline_registry.run_orchestration(req)

    assert calls == ["lead", "b", "lead"]
    assert res.result_payload["routing"] == {"chosen": ["b"], "reason": "b knows this", "skipped": ["a"]}
    assert res.execution_trace[0].phase == "ROUTING"


def test_router_falls_back_to_first_member_on_unparseable_reply():
    def fake_turn(agent_id, prompt_input, parameters=None, trace_id=None):
        return "no json here"

    req = OrchestrationRequest(pattern="router", primary_agent="lead", subagents=["a", "b"], user_query="x")
    latest, snapshot = _fake_scratchpad()
    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), latest, snapshot:
        res = pipeline_registry.run_orchestration(req)

    assert res.result_payload["routing"]["chosen"] == ["a"]
    assert res.result_payload["routing"]["reason"].startswith("Fallback")


def test_squad_default_pattern_applies_unless_the_run_overrides_it(monkeypatch):
    from orchestration.logic_orchestration import squad_registry

    monkeypatch.setattr(
        squad_registry, "list_agent_catalog",
        lambda: {
            "agents": [{"id": "lead", "name": "Lead", "description": ""},
                       {"id": "peer", "name": "Peer", "description": ""}],
            "subagents": [{"id": "worker", "name": "Worker", "description": ""}],
        },
    )
    squad = squad_registry.create_squad("Mixed", "", "lead", ["peer", "worker"], "fanout", 3)
    assert squad["default_pattern"] == "fanout" and squad["max_parallel"] == 3

    assert pipeline_registry.resolve_pattern(
        OrchestrationRequest(squad_id=squad["squad_id"], user_query="x")) == "fanout"
    assert pipeline_registry.resolve_pattern(
        OrchestrationRequest(squad_id=squad["squad_id"], pattern="router", user_query="x")) == "router"

    updated = squad_registry.update_squad(squad["squad_id"], default_pattern="router", subagents=["worker"])
    assert updated["default_pattern"] == "router" and updated["subagents"] == ["worker"]
    assert squad_registry.update_squad("missing", default_pattern="router") is None

    import pytest
    with pytest.raises(ValueError, match="Pattern must be one of"):
        squad_registry.create_squad("Bad", "", "lead", [], "parallel")
    with pytest.raises(ValueError, match="cannot also be a squad member"):
        squad_registry.create_squad("Self", "", "lead", ["lead"])
