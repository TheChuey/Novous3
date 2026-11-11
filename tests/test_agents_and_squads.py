"""Consolidated tests grouped by application responsibility."""

# --- Consolidated from test_agent_failures.py ---

import unittest
import uuid
from unittest.mock import patch

from core_engine.logic_core_engine import Agent, ChatService
from core_engine.schemas_core_engine import AgentProfile


def ok_tool(path: str) -> str:
    """Return the path."""
    return path


def broken_tool(path: str) -> str:
    """Always fails inside the tool body."""
    raise RuntimeError("disk on fire")


def call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


class TestToolFailureKinds(unittest.TestCase):
    def setUp(self):
        self.profile = AgentProfile(id="helper", mode="agent", system_prompt="x")
        self.agent = Agent(model="test-model", tools=[ok_tool, broken_tool], profile=self.profile)

    def test_unknown_native_tool_is_counted_and_reported(self):
        reply = self.agent.act(call("missing_tool", {}), "native")

        self.assertEqual(reply, "Error: Tool 'missing_tool' not found.")
        self.assertEqual(self.agent.failures["unknown_tool"], 1)
        self.assertEqual(self.agent.recent_failures[-1]["kind"], "unknown_tool")
        self.assertEqual(self.agent.tool_events[-1]["error_kind"], "unknown_tool")
        self.assertEqual(self.agent.tool_stats["missing_tool"]["errors"], 1)

    def test_invalid_json_arguments_are_bad_arguments(self):
        reply = self.agent.act(call("ok_tool", "{not json"), "native")

        self.assertIn("Arguments are not valid JSON", reply)
        self.assertEqual(self.agent.failures, {"bad_arguments": 1})

    def test_missing_and_unexpected_arguments_are_bad_arguments(self):
        self.agent.act(call("ok_tool", {}), "native")
        self.agent.act(call("ok_tool", {"path": "a", "extra": 1}), "native")

        self.assertEqual(self.agent.failures["bad_arguments"], 2)
        self.assertEqual(self.agent.failures.get("tool_error", 0), 0)

    def test_errors_inside_the_tool_body_are_tool_errors(self):
        reply = self.agent.act(call("broken_tool", {"path": "a"}), "native")

        self.assertIn("disk on fire", reply)
        self.assertEqual(self.agent.failures, {"tool_error": 1})
        self.assertEqual(self.agent.tool_stats["broken_tool"]["errors"], 1)

    def test_successful_calls_record_no_failures(self):
        self.agent.act(call("ok_tool", {"path": "a"}), "native")

        self.assertEqual(self.agent.failures, {})
        self.assertEqual(self.agent.tool_stats["ok_tool"]["successes"], 1)

    def test_recent_failures_keep_only_the_last_ten(self):
        for index in range(12):
            self.agent.note_failure("tool_error", "t", f"failure {index}")

        self.assertEqual(len(self.agent.recent_failures), 10)
        self.assertEqual(self.agent.recent_failures[-1]["message"], "failure 11")

    def test_tool_stats_stop_growing_past_the_cap(self):
        for index in range(Agent.MAX_TOOL_STATS + 5):
            self.agent.act(call(f"nope_{index}", {}), "native")

        self.assertLessEqual(len(self.agent.tool_stats), Agent.MAX_TOOL_STATS + 1)
        self.assertIn("(other)", self.agent.tool_stats)


def test_ollama_chat_and_embedding_inference_are_serialized():
    import threading
    import time

    from core_engine.logic_core_engine import serialized_ollama_chat, serialized_ollama_embed

    active, peak = [0], [0]
    guard = threading.Lock()

    def fake_inference(**kwargs):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.03)
        with guard:
            active[0] -= 1
        return {}

    with patch("core_engine.logic_core_engine.ollama.chat", side_effect=fake_inference), \
         patch("core_engine.logic_core_engine.ollama.embed", side_effect=fake_inference):
        threads = [
            threading.Thread(target=serialized_ollama_chat, kwargs={"model": "test", "messages": []}),
            threading.Thread(target=serialized_ollama_embed, kwargs={"model": "test", "input": ["x"]}),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert peak[0] == 1


class TestTextToolCalls(unittest.TestCase):
    def test_text_call_to_an_unavailable_tool_still_ends_as_a_plain_answer(self):
        profile = AgentProfile(id="helper", mode="agent", system_prompt="x")
        agent = Agent(model="test-model", tools=[ok_tool], profile=profile)
        response = {"message": {"role": "assistant",
                                "content": '{"name": "ghost_tool", "arguments": {"path": "a"}}'}}
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=response):
            reply = agent.think("do it")

        self.assertIn("ghost_tool", reply)
        self.assertEqual(agent.failures["unknown_tool"], 1)

    def test_running_out_of_tool_rounds_is_flagged_and_keeps_the_reply(self):
        profile = AgentProfile(id="helper", mode="agent", system_prompt="x")
        agent = Agent(model="test-model", tools=[ok_tool], profile=profile)
        response = {"message": {"role": "assistant", "content": "",
                                "tool_calls": [call("ok_tool", {"path": "a"})]}}
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=response):
            reply = agent.think("loop forever")

        self.assertEqual(reply, "(Executed maximum tool rounds without final text summary.)")
        self.assertEqual(agent.failures["rounds_exhausted"], 1)
        self.assertTrue(agent.run_stats()["rounds_exhausted"])


class TestRunStats(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(model="test-model", tools=[ok_tool],
                           profile=AgentProfile(id="helper", mode="agent", system_prompt="x"))
        self.agent.num_ctx = 1000

    def test_clean_run_is_ok(self):
        stats = self.agent.run_stats()

        self.assertEqual(stats["level"], "ok")
        self.assertEqual(stats["flags"], [])

    def test_window_near_full_is_critical(self):
        self.agent.peak_prompt_tokens = 960
        stats = self.agent.run_stats()

        self.assertEqual(stats["level"], "crit")
        self.assertIn("context_full", stats["flags"])

    def test_window_filling_is_a_warning(self):
        self.agent.peak_prompt_tokens = 850
        stats = self.agent.run_stats()

        self.assertEqual(stats["level"], "warn")
        self.assertIn("context_filling", stats["flags"])

    def test_tool_failures_are_a_warning_and_named(self):
        self.agent.act(call("ok_tool", {}), "native")
        stats = self.agent.run_stats()

        self.assertEqual(stats["level"], "warn")
        self.assertIn("tool_failures", stats["flags"])
        self.assertEqual(stats["tool_errors"], 1)

    def test_model_error_is_critical(self):
        self.agent.note_failure("model_error", "", "connection refused")

        self.assertEqual(self.agent.run_stats()["level"], "crit")


class TestChatHealth(unittest.TestCase):
    def test_a_failed_model_call_is_reported_in_chat_health(self):
        session = f"test-health-{uuid.uuid4().hex[:8]}"
        service = ChatService()
        try:
            with patch("core_engine.logic_core_engine.ollama.chat", side_effect=ConnectionError("down")):
                result = service.run_single_agent("chat-assitant", "hello", "test-model", session, "workspace")
        finally:
            service.reset_session(session)

        self.assertEqual(result["health"]["level"], "crit")
        self.assertEqual(result["health"]["failures"]["model_error"], 1)
        self.assertEqual(result["health"]["recent_failures"][-1]["kind"], "model_error")


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_agent_tools.py ---

import pytest

from core_engine.logic_core_engine import agent_library
from orchestration.logic_orchestration import squad_registry

PROMPT = """---
id: helper
name: Helper
mode: chat
tools:
  - read_file
  - map_files
skills:
  - summarize
---

# Helper

Body text with tools: mentioned inline.
"""

SUBAGENT = """id: worker
name: Worker
type: subagent
mode: agent
tools:
- read_file
prompt: |
  # Worker
  tools:
    - not_a_real_key
"""


@pytest.fixture
def libraries(tmp_path, monkeypatch):
    library = tmp_path / "library"
    subagents = tmp_path / "subAgentLibrary"
    library.mkdir()
    subagents.mkdir()
    (library / "helper.md").write_text(PROMPT, encoding="utf-8")
    (subagents / "worker.yaml").write_text(SUBAGENT, encoding="utf-8")
    monkeypatch.setattr(agent_library, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(agent_library, "LIBRARY_ROOT", library)
    monkeypatch.setattr(agent_library, "SUBAGENT_LIBRARY_ROOT", subagents)
    return library, subagents


def test_prompt_tools_are_replaced_without_touching_the_rest(libraries):
    library, _ = libraries

    result = agent_library.set_agent_tools("prompt", "helper", ["write_file", "read_file"], mode="agent")

    assert result["tools"] == ["write_file", "read_file"]
    assert result["mode"] == "agent"
    text = (library / "helper.md").read_text(encoding="utf-8")
    assert "  - map_files" not in text
    assert "skills:\n  - summarize\n---" in text
    assert "# Helper\n\nBody text with tools: mentioned inline.\n\n## Available Tools\n\n### write_file\n- Provider:" in text
    assert text.index("### write_file") < text.index("### read_file")
    assert "- Signature: (path: str" in text


def test_existing_tools_section_is_replaced_and_later_sections_kept(libraries):
    library, _ = libraries
    (library / "helper.md").write_text(
        PROMPT.replace("Body text with tools: mentioned inline.\n",
                       "Intro.\n\n## Tools\n\n### old_tool\nstale\n\n## Rules\n\nKeep me.\n\n"
                       "## Tools (chat use only)\nHand-written note.\n"),
        encoding="utf-8",
    )

    agent_library.set_agent_tools("prompt", "helper", ["read_file"])

    text = (library / "helper.md").read_text(encoding="utf-8")
    assert "old_tool" not in text and "stale" not in text
    assert "Intro.\n\n## Available Tools\n\n### read_file\n" in text
    assert "\n\n## Rules\n\nKeep me.\n" in text
    assert "## Tools (chat use only)\nHand-written note.\n" in text


def test_clearing_prompt_tools_writes_an_empty_list_and_drops_the_section(libraries):
    library, _ = libraries
    agent_library.set_agent_tools("prompt", "helper", ["read_file"])

    assert agent_library.set_agent_tools("prompt", "helper", [])["tools"] == []
    assert agent_library.get_agent_tools("prompt", "helper")["mode"] == "chat"
    text = (library / "helper.md").read_text(encoding="utf-8")
    assert "Available Tools" not in text
    assert text.endswith("Body text with tools: mentioned inline.\n")


def test_subagent_tools_leave_prompt_block_alone(libraries):
    _, subagents = libraries

    result = agent_library.set_agent_tools("subagent", "worker", ["read_file", "write_squad_scratchpad"])

    assert result["tools"] == ["read_file", "write_squad_scratchpad"]
    text = (subagents / "worker.yaml").read_text(encoding="utf-8")
    assert "    - not_a_real_key" in text
    data, body = agent_library.parse_subagent_yaml("worker")
    assert data["name"] == "Worker"
    assert "## Available Tools\n\n### read_file\n- Provider:" in body
    assert "### write_squad_scratchpad" in body
    assert "  ## Available Tools\n" in text


def test_call_subagent_writes_a_subagents_section_that_reads_back(libraries):
    library, subagents = libraries
    (subagents / "rag-retriever.yaml").write_text(
        "id: rag-retriever\nname: RAG Retriever\ndescription: Finds   indexed passages.\n", encoding="utf-8")

    result = agent_library.set_agent_tools(
        "prompt", "helper", ["call_subagent"],
        subagents=[{"id": "rag-retriever", "usage": "When the user asks about notes.\nPass top_k=5."},
                   {"id": "worker", "usage": ""}],
    )

    assert result["subagents"] == [
        {"id": "rag-retriever", "usage": "When the user asks about notes.\nPass top_k=5."},
        {"id": "worker", "usage": "Use it when the task matches what this sub-agent does."},
    ]
    text = (library / "helper.md").read_text(encoding="utf-8")
    assert "## Sub-agents\n\nDelegate work with `call_subagent" in text
    assert "### rag-retriever\n- Name: RAG Retriever\n- What it does: Finds indexed passages.\n- When to use:\n" in text
    assert text.index("## Available Tools") < text.index("## Sub-agents")

    # Saving tools again without a sub-agent list keeps the existing entries.
    agent_library.set_agent_tools("prompt", "helper", ["call_subagent", "read_file"])
    assert [entry["id"] for entry in agent_library.get_agent_tools("prompt", "helper")["subagents"]] == [
        "rag-retriever", "worker"]

    # Dropping call_subagent removes the section.
    agent_library.set_agent_tools("prompt", "helper", ["read_file"])
    assert "## Sub-agents" not in (library / "helper.md").read_text(encoding="utf-8")


def test_subagent_section_inside_subagent_prompt_block(libraries):
    _, subagents = libraries
    (subagents / "helper-bot.yaml").write_text("id: helper-bot\nname: Helper Bot\n", encoding="utf-8")

    result = agent_library.set_agent_tools(
        "subagent", "worker", ["call_subagent"], subagents=[{"id": "helper-bot", "usage": "For lookups."}])

    assert result["subagents"] == [{"id": "helper-bot", "usage": "For lookups."}]
    _, body = agent_library.parse_subagent_yaml("worker")
    assert "## Sub-agents" in body and "### helper-bot" in body


def test_bad_subagent_choices_are_rejected(libraries):
    with pytest.raises(ValueError, match="not found"):
        agent_library.set_agent_tools("prompt", "helper", ["call_subagent"], subagents=[{"id": "ghost"}])
    with pytest.raises(ValueError, match="itself"):
        agent_library.set_agent_tools("subagent", "worker", ["call_subagent"], subagents=[{"id": "worker"}])
    with pytest.raises(ValueError, match="'#'"):
        agent_library.set_agent_tools("prompt", "helper", ["call_subagent"],
                                      subagents=[{"id": "worker", "usage": "## sneaky heading"}])


def test_invalid_tool_names_are_rejected(libraries):
    with pytest.raises(ValueError, match="Invalid tool names"):
        agent_library.set_agent_tools("prompt", "helper", ["rm -rf"])


def test_missing_definition_raises(libraries):
    with pytest.raises(FileNotFoundError):
        agent_library.get_agent_tools("subagent", "nobody")


def test_subagent_used_by_a_squad_cannot_be_deleted(libraries):
    _, subagents = libraries
    (subagents / "rag-retriever.yaml").write_text("id: rag-retriever\n", encoding="utf-8")

    with pytest.raises(ValueError, match="RAG Knowledge Squad"):
        squad_registry.delete_subagent("rag-retriever")
    assert (subagents / "rag-retriever.yaml").is_file()


def test_create_subagent_scaffolds_a_loadable_definition(libraries):
    _, subagents = libraries

    result = squad_registry.create_subagent("doc-writer", 'Doc "Writer": v2', "Writes   docs.\nFast.")

    assert result["path"] == "subAgentLibrary/doc-writer.yaml"
    data, body = agent_library.parse_subagent_yaml("doc-writer")
    assert data["name"] == 'Doc "Writer": v2'
    assert data["description"] == "Writes docs. Fast."
    assert data["tools"] == ["read_squad_scratchpad", "write_squad_scratchpad"]
    assert "## role" in body and 'agent: "doc-writer"' in body
    catalog_ids = [agent["id"] for agent in squad_registry.list_agent_catalog()["subagents"]]
    assert "doc-writer" in catalog_ids


def test_create_subagent_rejects_duplicates_and_bad_ids(libraries):
    with pytest.raises(FileExistsError):
        squad_registry.create_subagent("worker", "Worker")
    with pytest.raises(ValueError, match="file name"):
        squad_registry.create_subagent("../escape", "Bad")


def test_unused_subagent_is_deleted(libraries):
    _, subagents = libraries

    assert squad_registry.delete_subagent("worker")["deleted"] is True
    assert not (subagents / "worker.yaml").exists()


def test_call_subagent_respects_the_callers_subagent_list(libraries, monkeypatch):
    import json

    from core_engine import logic_core_engine
    from orchestration.logic_orchestration import subagent_runner
    from tools.logic_tools import call_subagent

    _, subagents = libraries
    (subagents / "other.yaml").write_text("id: other\nname: Other\n", encoding="utf-8")
    agent_library.set_agent_tools("prompt", "helper", ["call_subagent"], subagents=[{"id": "worker"}])
    monkeypatch.setattr(subagent_runner, "run_agent_turn", lambda agent_id, prompt: f"{agent_id} ran")

    token = logic_core_engine._CURRENT_AGENT_ID.set("helper")
    try:
        denied = json.loads(call_subagent("other", "do it"))
        allowed = json.loads(call_subagent("worker", "do it"))
    finally:
        logic_core_engine._CURRENT_AGENT_ID.reset(token)

    assert denied["error"] == "Agent 'helper' may only call: worker"
    assert allowed["result"] == "worker ran"

    # Without a caller (or with an empty list) every sub-agent stays reachable.
    assert json.loads(call_subagent("other", "do it"))["result"] == "other ran"

# --- Consolidated from test_code_squad_pipeline.py ---

"""Code-squad pipeline: agents are mocked; code execution is real."""

import json
import uuid

import pytest

from orchestration.logic_orchestration import (
    code_squad_pipeline,
    codebuilder_run_state,
    run_results,
    squad_registry,
    subagent_runner,
    telemetry_bus,
)
from orchestration.schemas_orchestration import OrchestrationRequest


GOOD = "def add(a, b):\n    return a + b\n"
BROKEN = "def add(a, b):\n    return a + c\n"


def _block(code):
    return f"Here it is:\n```python\n{code}```\nDone."


@pytest.fixture
def agents(monkeypatch):
    """Queue canned replies per agent and record the prompts each one received."""
    replies, prompts = {}, []

    def fake_stream(agent_id, prompt, trace_id, model=None, should_stop=None):
        prompts.append((agent_id, prompt))
        queue = replies.get(agent_id) or ["(no reply)"]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        telemetry_bus.log_event(trace_id, "AGENT_TOKEN", agent_id, "orchestrator", {"agent_id": agent_id, "text": reply})
        return reply

    monkeypatch.setattr(subagent_runner, "run_agent_turn_stream", fake_stream)
    monkeypatch.setattr(squad_registry, "record_squad_run", lambda *args: None)
    return replies, prompts


def _start(action, code="", **params):
    request = OrchestrationRequest(
        squad_id="code-squad",
        workflow_action=action,
        user_query="make an add function",
        parameters={"editor_code": code, **params},
    )
    request.trace_id = f"tr_test_{action}_{uuid.uuid4().hex}"
    telemetry_bus.initialize_trace(request.trace_id)
    codebuilder_run_state.begin(request.trace_id, code, request.user_query, action)
    return request


def _events(trace_id, event_type):
    return [e for e in telemetry_bus.get_trace_history(trace_id) if e["event_type"] == event_type]


def test_extract_python_block_prefers_single_block_then_largest_python_block():
    assert code_squad_pipeline.extract_python_block("```\nonly = 1\n```") == "only = 1\n"
    reply = "```bash\nls\n```\n```python\na = 1\n```\n```py\nb = 2\nc = 3\n```"
    assert code_squad_pipeline.extract_python_block(reply) == "b = 2\nc = 3\n"
    assert code_squad_pipeline.extract_python_block("no code here") is None


def test_build_applies_code_and_emits_editor_update(agents):
    replies, _ = agents
    replies["code-builder"] = [_block(GOOD)]
    request = _start("build")
    response = code_squad_pipeline.run(request)
    assert response.status == "success"
    updates = _events(request.trace_id, "EDITOR_CODE_UPDATE")
    assert updates and updates[-1]["data"]["content"] == GOOD
    assert codebuilder_run_state.get_preview(request.trace_id)["content"] == GOOD


def test_build_without_code_block_is_an_error(agents):
    replies, _ = agents
    replies["code-builder"] = ["I cannot do that."]
    response = code_squad_pipeline.run(_start("build"))
    assert response.status == "error"


def test_test_step_captures_real_traceback(agents):
    replies, _ = agents
    replies["tester"] = [_block("assert add(1, 2) == 3\n")]
    request = _start("test", BROKEN)
    response = code_squad_pipeline.run(request)
    assert response.status == "error"
    last_test = response.result_payload["last_test"]
    assert "NameError" in (last_test.get("stderr") or "")
    assert _events(request.trace_id, "EDITOR_CODE_EXECUTION")[-1]["data"]["status"] == "error"


def test_test_step_does_not_run_main_guard(agents):
    replies, _ = agents
    replies["tester"] = [_block("assert add(2, 2) == 4\n")]
    code = "def add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n    input('blocked?')\n"
    response = code_squad_pipeline.run(_start("test", code))
    assert response.status == "success"


def test_syntax_error_maps_to_editor_line(agents):
    request = _start("test", "x = 1\ndef broken(:\n    pass\n")
    response = code_squad_pipeline.run(request)
    assert response.status == "error"
    assert _events(request.trace_id, "EDITOR_CODE_EXECUTION")[-1]["data"]["diagnostic_lines"] == [2]


def test_fix_loops_until_tests_pass(agents):
    replies, prompts = agents
    replies["tester"] = [_block("assert add(1, 2) == 3\n")]
    replies["code-troubleshooter"] = [_block(BROKEN.replace("c", "d")), _block(GOOD)]
    request = _start("fix", BROKEN)
    response = code_squad_pipeline.run(request)
    assert response.status == "success"
    assert response.result_payload["code"] == GOOD
    fixer_prompts = [p for agent, p in prompts if agent == "code-troubleshooter"]
    assert len(fixer_prompts) == 2 and "NameError" in fixer_prompts[0]


def test_fix_stops_after_max_attempts(agents):
    replies, _ = agents
    replies["tester"] = [_block("assert add(1, 2) == 3\n")]
    replies["code-troubleshooter"] = [
        _block(BROKEN.replace("c", name)) for name in ("d", "e", "f", "g")
    ]
    response = code_squad_pipeline.run(_start("fix", BROKEN))
    assert response.status == "error"
    fixes = [s for s in response.execution_trace if s.phase == "FIX"]
    assert len(fixes) == code_squad_pipeline.MAX_FIX_ATTEMPTS


def test_run_all_plans_builds_and_tests(agents):
    replies, prompts = agents
    replies["code-planer"] = ["1. Add an add(a, b) function."]
    replies["code-builder"] = [_block(GOOD)]
    replies["tester"] = [_block("assert add(1, 2) == 3\n")]
    request = _start("all")
    response = code_squad_pipeline.run(request)
    assert response.status == "success"
    builder_prompt = next(p for agent, p in prompts if agent == "code-builder")
    assert "Add an add(a, b) function" in builder_prompt
    steps = [e["data"]["step"] for e in _events(request.trace_id, "STEP_STATUS")]
    assert steps[0] == "plan" and "build" in steps and "test" in steps


def test_cancel_stops_between_steps(agents, monkeypatch):
    replies, _ = agents
    replies["code-planer"] = ["plan"]
    request = _start("all")

    def cancel_after_plan(*args, **kwargs):
        codebuilder_run_state.cancel(request.trace_id)
        return "plan"

    monkeypatch.setattr(subagent_runner, "run_agent_turn_stream", cancel_after_plan)
    response = code_squad_pipeline.run(request)
    assert response.status == "cancelled"
    assert not _events(request.trace_id, "EDITOR_CODE_UPDATE")


def test_result_is_saved_before_run_complete(agents, monkeypatch):
    replies, _ = agents
    replies["code-builder"] = [_block(GOOD)]
    request = _start("build")
    seen = {}
    original = telemetry_bus.log_event

    def spy(trace_id, event_type, *args, **kwargs):
        if event_type == "RUN_COMPLETE":
            seen["result"] = run_results.get(trace_id)
        return original(trace_id, event_type, *args, **kwargs)

    monkeypatch.setattr(telemetry_bus, "log_event", spy)
    code_squad_pipeline.run(request)
    assert seen["result"] is not None and seen["result"]["status"] == "success"


def test_stream_resume_skips_seen_events():
    trace_id = f"tr_test_resume_{uuid.uuid4().hex}"
    telemetry_bus.initialize_trace(trace_id)
    for index in range(3):
        telemetry_bus.log_event(trace_id, "STEP_STATUS", "orchestrator", "x", {"step": "plan", "status": "running", "attempt": index})
    telemetry_bus.log_event(trace_id, "AGENT_TOKEN", "a", "b", {"agent_id": "a", "text": "live only"})
    telemetry_bus.log_event(trace_id, "RUN_COMPLETE", "orchestrator", "x", {"status": "success", "duration_ms": 1})
    telemetry_bus.finish_trace(trace_id)
    frames = list(telemetry_bus.subscribe(trace_id, after=2))
    sequences = [json.loads(f.split("data: ", 1)[1])["sequence"] for f in frames]
    assert sequences == [3, 4]
    assert all("live only" not in f for f in frames)

# --- Consolidated from test_context_window.py ---

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core_engine import logic_context_window as context_window
from core_engine.logic_core_engine import Agent, ChatService, chat_service, tool_catalog
from core_engine.schemas_core_engine import AgentProfile
from modelProfiles.logic_modelProfiles import model_profiles


def report(messages, **overrides):
    options = {
        "model": "test-model", "messages": messages, "system_prompt": "You are a test.",
        "schema_text": "", "tools_total": 0, "tools_active": 0, "num_ctx": 1000,
        "reply_reserve": 100, "native_context": None, "capabilities": ["completion", "tools"],
        "calibration": 1.0, "measured_prompt_tokens": None,
    }
    options.update(overrides)
    return context_window.build_report(**options)


def turn(text, size):
    """One user turn plus an assistant reply, each about `size` characters."""
    return [{"role": "user", "content": "u" * size}, {"role": "assistant", "content": "a" * size}]


class TestContextReport(unittest.TestCase):
    def test_small_history_fits_and_reports_turns_remaining(self):
        messages = [{"role": "system", "content": "x"}] + turn("hi", 35)
        result = report(messages)

        self.assertEqual(result["forecast"]["turns_dropped"], 0)
        self.assertTrue(all(row["fits"] for row in result["messages"]))
        self.assertGreater(result["forecast"]["turns_remaining"], 0)
        self.assertEqual(result["messages"][0]["turn"], 1)
        self.assertEqual(result["messages"][1]["turn"], 1)
        self.assertNotIn("system", [row["role"] for row in result["messages"]])

    def test_oldest_turns_are_marked_dropped_when_history_exceeds_the_budget(self):
        messages = turn("one", 700) + turn("two", 700) + turn("three", 700)
        result = report(messages, num_ctx=1000, reply_reserve=100)

        dropped = [row for row in result["messages"] if not row["fits"]]
        self.assertTrue(dropped)
        self.assertEqual(dropped[0]["turn"], 1)
        self.assertGreaterEqual(result["forecast"]["turns_dropped"], 1)
        self.assertIn("no longer fit", result["forecast"]["sentence"])
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertIn("turns_dropped", codes)
        self.assertIn("context_full", codes)

    def test_measured_prompt_size_and_calibration_are_used_for_token_counts(self):
        messages = [{"role": "user", "content": "x" * 350}]
        plain = report(messages, calibration=1.0)
        scaled = report(messages, calibration=2.0, measured_prompt_tokens=300)

        self.assertEqual(scaled["messages"][0]["tokens"], plain["messages"][0]["tokens"] * 2)
        self.assertIn("measured_full", {w["code"] for w in report(messages, num_ctx=310,
                                                                  measured_prompt_tokens=305)["warnings"]})

    def test_capped_tools_and_missing_tool_support_are_flagged(self):
        result = report([{"role": "user", "content": "hi"}], tools_total=12, tools_active=4,
                        capabilities=["completion"], schema_text="s" * 100)
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertIn("tools_capped", codes)
        self.assertIn("no_tools_capability", codes)

    def test_message_objects_with_tool_calls_are_read_like_dicts(self):
        call = SimpleNamespace(function=SimpleNamespace(name="read_file", arguments={"path": "a.md"}))
        message = SimpleNamespace(role="assistant", content="", tool_calls=[call])
        tool_result = {"role": "tool", "name": "read_file", "content": "file text"}

        result = report([{"role": "user", "content": "read it"}, message, tool_result])

        roles = [row["role"] for row in result["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool"])
        self.assertIn("read_file", result["messages"][1]["preview"])
        self.assertEqual(result["messages"][2]["tool_name"], "read_file")

    def test_summary_is_the_compact_form_of_the_report(self):
        summary = context_window.summarize(report([{"role": "user", "content": "hi"}]))
        self.assertEqual(set(summary), {"percent", "used_tokens", "num_ctx", "turns_remaining",
                                        "turns_capacity", "turns_in_memory", "turns_dropped",
                                        "sentence", "warnings"})


class TestAgentUsage(unittest.TestCase):
    def setUp(self):
        self.profile = AgentProfile(id="helper", mode="chat", system_prompt="You are a test.")

    def test_non_streaming_usage_is_recorded_and_calibrates_the_estimate(self):
        agent = Agent(model="test-model", tools=[], profile=self.profile)
        response = {"message": {"role": "assistant", "content": "ok"},
                    "prompt_eval_count": 500, "eval_count": 20}
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=response) as chat:
            agent.think("hello there")

        self.assertEqual(chat.call_args.kwargs["options"], {"num_ctx": agent.num_ctx})
        self.assertEqual(agent.last_usage, {"prompt_tokens": 500, "completion_tokens": 20})
        self.assertEqual(agent.usage_totals, {"calls": 1, "prompt_tokens": 500, "completion_tokens": 20})
        self.assertNotEqual(agent.calibration, 1.0)
        self.assertGreaterEqual(agent.calibration, 0.5)
        self.assertLessEqual(agent.calibration, 2.0)

    def test_streaming_usage_comes_from_the_final_chunk(self):
        agent = Agent(model="test-model", tools=[], profile=self.profile)
        chunks = [
            {"message": {"role": "assistant", "content": "he"}},
            {"message": {"role": "assistant", "content": "llo"}, "prompt_eval_count": 120, "eval_count": 7},
        ]
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=iter(chunks)):
            events = list(agent.think_stream("hi"))

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(agent.usage_totals["prompt_tokens"], 120)
        self.assertEqual(agent.usage_totals["completion_tokens"], 7)

    def test_missing_usage_keeps_the_estimate_unchanged(self):
        agent = Agent(model="test-model", tools=[], profile=self.profile)
        with patch("core_engine.logic_core_engine.ollama.chat",
                   return_value={"message": {"role": "assistant", "content": "ok"}}):
            agent.think("hello")

        self.assertIsNone(agent.last_usage)
        self.assertEqual(agent.calibration, 1.0)


class TestChatServiceModelLimits(unittest.TestCase):
    def setUp(self):
        self.service = ChatService()
        self.profile = AgentProfile(id="helper", mode="agent", system_prompt="You are a test.",
                                    tools=["map_files", "read_file", "write_file"])

    def test_profile_caps_the_tools_and_sets_the_window(self):
        model_profiles.save("tiny-model:1b", 8192, 1024, 1)
        agent = Agent(model=None, tools=[], profile=self.profile)
        self.service._apply_model_limits(agent, self.profile, "tiny-model:1b")

        self.assertEqual(agent.num_ctx, 8192)
        self.assertEqual(list(agent.tools), ["map_files"])
        self.assertEqual(agent.tools_available, len(tool_catalog.get_tools(self.profile.tools)))

    def test_pinned_tools_survive_a_small_cap(self):
        rag = AgentProfile(id="rag-presenter", mode="agent", system_prompt="x",
                           tools=["map_files", "call_subagent", "read_chat_scratchpad"])
        model_profiles.save("tiny-model:1b", 8192, 1024, 1)
        agent = Agent(model=None, tools=[], profile=rag)
        self.service._apply_model_limits(agent, rag, "tiny-model:1b")

        self.assertEqual(set(agent.tools), {"call_subagent", "read_chat_scratchpad"})

    def test_changing_model_mid_chat_keeps_the_history(self):
        agent = Agent(model=None, tools=[], profile=self.profile)
        agent.messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "remember 42"}]
        self.service._apply_model_limits(agent, self.profile, "other-model:3b")

        self.assertEqual(agent.model, "other-model:3b")
        self.assertEqual(agent.messages[-1]["content"], "remember 42")

    def test_context_endpoint_reports_a_real_agent_session(self):
        from fastapi.testclient import TestClient
        from server import app

        agent_id = "chat-assitant"
        session = f"test-context-{agent_id}"
        try:
            response = TestClient(app).get("/api/chat/context",
                                           params={"agent_id": agent_id, "session_id": session})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["session_id"], session)
            self.assertEqual(set(body["segments"]), {"system", "tools", "history", "reply_reserve", "free"})
        finally:
            chat_service.reset_session(session)


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_editor_subagent_definitions.py ---

import pytest

from editor.logic_editor import editor_files


def test_subagent_yaml_definition_can_be_opened_in_editor():
    result = editor_files.read_file_content("subAgentLibrary/code-builder.yaml")

    assert result["path"] == "subAgentLibrary/code-builder.yaml"
    assert "id: code-builder" in result["content"]


def test_editor_rejects_subagent_paths_outside_yaml_library():
    with pytest.raises(ValueError, match="Only YAML files directly"):
        editor_files.resolve_project_path("subAgentLibrary/../library/rag-presenter.md")

    with pytest.raises(ValueError, match="Only YAML files directly"):
        editor_files.resolve_project_path("subAgentLibrary/nested/worker.yaml")


def test_editor_cannot_create_or_delete_subagent_definitions():
    with pytest.raises(ValueError, match="cannot be created"):
        editor_files.create_path("subAgentLibrary/new-worker.yaml")

    with pytest.raises(ValueError, match="cannot be deleted"):
        editor_files.delete_path("subAgentLibrary/code-builder.yaml")

    with pytest.raises(FileNotFoundError, match="cannot be created"):
        editor_files.write_file_content("subAgentLibrary/new-worker.yaml", "id: new-worker\n")

# --- Consolidated from test_orchestration.py ---

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


def test_legacy_fanout_runs_members_sequentially_with_one_step_handoff():
    import threading
    import time as _time

    active, peak, lock = [0], [0], threading.Lock()
    prompts = {}

    def fake_turn(agent_id, prompt_input, parameters=None, trace_id=None):
        prompts[agent_id] = prompt_input
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
    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), \
         patch.object(subagent_runner, "read_latest_subagent_scratchpad", return_value={"status": "success"}), \
         patch.object(subagent_runner, "read_scratchpad_snapshot", return_value={}):
        res = pipeline_registry.run_orchestration(req)

    assert res.pattern == "sequential"
    assert res.status == "success"
    assert peak[0] == 1
    assert "Agent: a" in prompts["b"] and "a output" in prompts["b"]
    assert "Agent: b" in prompts["c"] and "b output" in prompts["c"]
    assert "a output" not in prompts["lead"] and "d output" in prompts["lead"]
    assert [s.phase for s in res.execution_trace].count("SUBAGENT_EXECUTION") == 4


def test_legacy_router_runs_all_members_in_saved_order():
    calls = []

    def fake_turn(agent_id, prompt_input, parameters=None, trace_id=None):
        calls.append(agent_id)
        return f"{agent_id} done"

    req = OrchestrationRequest(
        pattern="router", primary_agent="lead", subagents=["a", "b"], user_query="Route me",
    )
    latest, snapshot = _fake_scratchpad()
    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), latest, snapshot:
        res = pipeline_registry.run_orchestration(req)

    assert calls == ["a", "b", "lead"]
    assert res.pattern == "sequential"
    assert [step.agent_id for step in res.execution_trace if step.phase == "SUBAGENT_EXECUTION"] == ["a", "b"]


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
    squad = squad_registry.create_squad("Mixed", "", "lead", ["worker"], "fanout", 3)
    assert squad["default_pattern"] == "sequential" and squad["max_parallel"] == 1

    assert pipeline_registry.resolve_pattern(
        OrchestrationRequest(squad_id=squad["squad_id"], user_query="x")) == "sequential"
    assert pipeline_registry.resolve_pattern(
        OrchestrationRequest(squad_id=squad["squad_id"], pattern="router", user_query="x")) == "sequential"

    updated = squad_registry.update_squad(squad["squad_id"], default_pattern="router", subagents=["worker"])
    assert updated["default_pattern"] == "sequential" and updated["subagents"] == ["worker"]
    assert squad_registry.update_squad("missing", default_pattern="router") is None

    import pytest
    with pytest.raises(ValueError, match="Pattern must be one of"):
        squad_registry.create_squad("Bad", "", "lead", [], "parallel")
    with pytest.raises(ValueError, match="cannot also be a squad member"):
        squad_registry.create_squad("Self", "", "lead", ["lead"])
    with pytest.raises(ValueError, match="must be sub-agents"):
        squad_registry.create_squad("Mixed", "", "lead", ["peer"])


def test_squad_accepts_orchestrator_owned_questionnaire_step(monkeypatch):
    monkeypatch.setattr(
        squad_registry,
        "list_agent_catalog",
        lambda: {
            "agents": [{"id": "problem-orchestrator", "name": "Lead", "description": ""}],
            "subagents": [
                {"id": "problem-statement-builder", "name": "Statement Builder", "description": ""},
                {"id": "inquisitive", "name": "Questioner", "description": ""},
                {"id": "problem-analyst", "name": "Analyst", "description": ""},
            ],
        },
    )

    squad_registry._validate_definition(
        "Thinker",
        "problem-orchestrator",
        ["problem-statement-builder", "inquisitive", "questionnaire", "problem-analyst"],
    )


@pytest.mark.parametrize(
    "legacy_members",
    [
        [
            "problem-orchestrator",
            "inquisitive",
            "questionnaire",
            "problem-analyst",
            "problem-report-builder",
        ],
        ["inquisitive", "problem-analyst", "problem-report-builder"],
    ],
)
def test_legacy_thinker_topology_migrates_to_the_complete_workflow(
    legacy_members, tmp_path, monkeypatch
):
    import json

    path = tmp_path / "orchestration_squads.json"
    path.write_text(
        json.dumps({
            "thinker": {
                "squad_id": "thinker",
                "name": "Thinker",
                "description": "",
                "primary_agent": "problem-orchestrator",
                "subagents": legacy_members,
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(squad_registry, "SQUADS_PATH", path)

    thinker = squad_registry._read_definitions()["thinker"]

    assert thinker["subagents"] == [
        "problem-statement-builder",
        "inquisitive",
        "questionnaire",
        "problem-analyst",
        "problem-report-builder",
    ]


def test_legacy_squad_definitions_normalize_patterns_and_remove_duplicate_leads():
    import json

    from orchestration.logic_orchestration import squad_registry

    squad_registry.SQUADS_PATH.write_text(
        json.dumps({
            "legacy": {
                "squad_id": "legacy",
                "name": "Legacy",
                "description": "",
                "primary_agent": "lead",
                "subagents": ["lead", "worker"],
                "default_pattern": "fanout",
                "max_parallel": 4,
            }
        }),
        encoding="utf-8",
    )

    squad = squad_registry.get_squad("legacy")

    assert squad["default_pattern"] == "sequential"
    assert squad["max_parallel"] == 1
    assert squad["subagents"] == ["worker"]


def test_squad_agent_turn_withholds_dynamic_delegation_tool():
    from orchestration.logic_orchestration import subagent_runner
    from scratchpadMemory.logic_scratchpadMemory import squad_scratchpad

    profile = AgentProfile(
        id="worker",
        mode="chat",
        tools=["call_subagent", "read_squad_scratchpad"],
    )

    class FakeAgent:
        def __init__(self, model, tools, profile):
            self.num_ctx = 0

        def think(self, prompt):
            return "finished"

    squad_scratchpad.begin("delegation-test", "squad", "task")
    with patch.object(subagent_runner, "load_agent", return_value=({}, "")), \
         patch.object(subagent_runner, "_build_profile", return_value=profile), \
         patch.object(subagent_runner, "_emit_usage"), \
         patch("orchestration.logic_orchestration.Agent", FakeAgent):
        assert subagent_runner.run_agent_turn(
            "worker", "task", trace_id="delegation-test"
        ) == "finished"

    assert profile.tools == ["read_squad_scratchpad"]
    assert "Do not call or delegate to other agents" in profile.system_prompt


# --- Consolidated from test_orchestration_observability.py ---

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
    assert {"code-builder", "tester", "code-troubleshooter", "problem-statement-builder"} <= subagent_ids


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

# --- Consolidated from test_orchestration_usage.py ---

import unittest
import uuid
from unittest.mock import patch

from modelProfiles.logic_modelProfiles import model_profiles
from orchestration.logic_orchestration import (
    SQUAD_PINNED_TOOLS,
    subagent_runner,
    summarize_agent_usage,
    telemetry_bus,
)

META = {"id": "worker", "mode": "agent", "tools": ["map_files", "read_squad_scratchpad"], "model": "tiny-model:1b"}


def usage_events(trace_id):
    return [event for event in telemetry_bus.get_trace_history(trace_id) if event["event_type"] == "AGENT_USAGE"]


class TestSquadTurnUsage(unittest.TestCase):
    def setUp(self):
        self.trace_id = f"tr_test_{uuid.uuid4().hex[:8]}"
        telemetry_bus.initialize_trace(self.trace_id)
        patcher = patch.object(subagent_runner, "load_agent", return_value=(dict(META), "You are a worker."))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_turn_reports_its_usage_and_window(self):
        response = {"message": {"role": "assistant", "content": "done"},
                    "prompt_eval_count": 300, "eval_count": 5}
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=response) as chat:
            subagent_runner.run_agent_turn("worker", "hi", trace_id=self.trace_id)

        data = usage_events(self.trace_id)[-1]["data"]
        self.assertEqual(chat.call_args.kwargs["options"]["num_ctx"], data["num_ctx"])
        self.assertEqual(data["num_ctx"], 8192)
        self.assertEqual(data["calls"], 1)
        self.assertEqual(data["prompt_tokens"], 300)
        self.assertEqual(data["completion_tokens"], 5)
        self.assertEqual(data["level"], "ok")

    def test_the_tool_limit_keeps_the_handoff_tools(self):
        model_profiles.save("tiny-model:1b", 8192, 1024, 1)
        response = {"message": {"role": "assistant", "content": "done"}, "prompt_eval_count": 50}
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=response):
            subagent_runner.run_agent_turn("worker", "hi", trace_id=self.trace_id)

        data = usage_events(self.trace_id)[-1]["data"]
        self.assertEqual(data["tools_total"], 2)
        self.assertEqual(data["tools_active"], 1)
        self.assertIn("read_squad_scratchpad", SQUAD_PINNED_TOOLS)

    def test_a_sub_agent_turn_is_credited_to_the_sub_agent(self):
        response = {"message": {"role": "assistant", "content": "done"}, "prompt_eval_count": 40}
        with telemetry_bus.agent_context(self.trace_id, "lead"), \
                patch("core_engine.logic_core_engine.ollama.chat", return_value=response):
            subagent_runner.run_agent_turn("worker", "hi")

        usage = usage_events(self.trace_id)
        self.assertEqual(usage[-1]["sender"], "worker")

    def test_a_model_error_is_reported_before_it_is_raised(self):
        with patch("core_engine.logic_core_engine.ollama.chat", side_effect=ConnectionError("down")):
            with self.assertRaises(ConnectionError):
                subagent_runner.run_agent_turn("worker", "hi", trace_id=self.trace_id)

        data = usage_events(self.trace_id)[-1]["data"]
        self.assertEqual(data["fail_model_error"], 1)
        self.assertEqual(data["level"], "crit")

    def test_no_telemetry_without_a_trace(self):
        response = {"message": {"role": "assistant", "content": "done"}, "prompt_eval_count": 40}
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=response):
            subagent_runner.run_agent_turn("worker", "hi")

        self.assertEqual(usage_events(self.trace_id), [])

    def test_a_streamed_text_turn_reports_usage_from_its_final_chunk(self):
        chunks = [
            {"message": {"role": "assistant", "content": "he"}},
            {"message": {"role": "assistant", "content": "llo"}, "prompt_eval_count": 120, "eval_count": 7},
        ]
        with patch("core_engine.logic_core_engine.ollama.chat", return_value=iter(chunks)) as chat:
            text = subagent_runner.run_agent_turn_stream("worker", "hi", self.trace_id)

        self.assertEqual(text, "hello")
        self.assertEqual(chat.call_args.kwargs["options"]["num_ctx"], 8192)
        data = usage_events(self.trace_id)[-1]["data"]
        self.assertEqual(data["prompt_tokens"], 120)
        self.assertEqual(data["completion_tokens"], 7)
        self.assertEqual(data["num_ctx"], 8192)

    def test_build_profile_carries_max_tool_rounds(self):
        profile = subagent_runner._build_profile("worker", {"id": "worker", "mode": "agent", "max_tool_rounds": 12}, "b")

        self.assertEqual(profile.extras, {"max_tool_rounds": 12})


class TestTelemetryAllowList(unittest.TestCase):
    def test_usage_keys_are_sanitised_and_unknown_keys_dropped(self):
        trace_id = f"tr_test_{uuid.uuid4().hex[:8]}"
        telemetry_bus.initialize_trace(trace_id)
        telemetry_bus.log_event(trace_id, "AGENT_USAGE", "worker", "orchestrator", {
            "agent_id": "worker", "level": "bogus", "flags": ["context_full", "x y"],
            "prompt_tokens": None, "fail_tool_error": -4, "secret": "do not leak",
        })
        telemetry_bus.log_event(trace_id, "TOOL_CALL_END", "worker", "read_file", {
            "tool_name": "read_file", "status": "error", "duration_ms": 3, "error_kind": "nonsense",
        })

        usage, tool_end = telemetry_bus.get_trace_history(trace_id)
        self.assertEqual(usage["data"]["level"], "unknown")
        self.assertEqual(usage["data"]["flags"], "context_full,unknown")
        self.assertEqual(usage["data"]["prompt_tokens"], 0)
        self.assertEqual(usage["data"]["fail_tool_error"], 0)
        self.assertNotIn("secret", usage["data"])
        self.assertEqual(tool_end["data"]["error_kind"], "")


class TestSummary(unittest.TestCase):
    def test_per_agent_totals_and_the_worst_level(self):
        events = [
            {"event_type": "AGENT_USAGE", "sender": "lead", "data": {
                "agent_id": "lead", "model": "m", "num_ctx": 1000, "calls": 2, "prompt_tokens": 300,
                "peak_prompt_tokens": 300, "completion_tokens": 10, "tool_calls": 3, "tool_errors": 1,
                "fail_tool_error": 1, "level": "warn", "flags": "tool_failures"}},
            {"event_type": "AGENT_USAGE", "sender": "worker", "data": {
                "agent_id": "worker", "model": "m", "num_ctx": 1000, "calls": 1, "prompt_tokens": 960,
                "peak_prompt_tokens": 960, "completion_tokens": 4, "tool_calls": 0, "tool_errors": 0,
                "rounds_exhausted": 1, "level": "crit", "flags": "context_full,rounds_exhausted"}},
            {"event_type": "SCRATCHPAD_HANDOFF", "sender": "worker", "data": {"direction": "write"}},
            {"event_type": "SCRATCHPAD_HANDOFF", "sender": "lead", "data": {"direction": "read"}},
        ]
        summary = summarize_agent_usage(events)

        by_agent = {agent["agent_id"]: agent for agent in summary["agents"]}
        self.assertEqual(by_agent["lead"]["tool_errors"], 1)
        self.assertEqual(by_agent["lead"]["level"], "warn")
        self.assertEqual(by_agent["worker"]["percent_peak"], 96.0)
        self.assertEqual(by_agent["worker"]["flags"], ["context_full", "rounds_exhausted"])
        self.assertEqual(summary["summary"]["level"], "crit")
        self.assertEqual(summary["summary"]["percent_peak"], 96.0)
        self.assertEqual(summary["summary"]["scratchpad_reads"], 1)
        self.assertEqual(summary["summary"]["scratchpad_writes"], 1)

    def test_an_empty_run_has_no_agents(self):
        self.assertEqual(summarize_agent_usage([]), {
            "agents": [],
            "summary": {"level": "ok", "percent_peak": 0.0, "tool_calls": 0, "tool_errors": 0,
                        "scratchpad_reads": 0, "scratchpad_writes": 0},
        })


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_squad_scratchpad.py ---

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


def test_missing_expected_handoff_fails_without_primary_synthesis():
    from orchestration.logic_orchestration import AgentOutputValidationError

    request = OrchestrationRequest(
        primary_agent="lead",
        subagents=["inquisitive"],
        user_query="Investigate a problem",
    )
    calls = []

    def failed_turn(agent_id, prompt, parameters=None, trace_id=None):
        calls.append(agent_id)
        raise AgentOutputValidationError("missing investigation questions")

    with patch.object(subagent_runner, "run_agent_turn", side_effect=failed_turn):
        response = pipeline_registry.run_orchestration(request)

    run = squad_scratchpad.get(response.trace_id)
    assert calls == ["inquisitive"]
    assert response.status == "error"
    assert "Workflow stopped at 'inquisitive'" in response.final_answer
    assert run["status"] == "error"
    assert not any(
        entry["author"] == "lead" and entry["title"] == "Primary synthesis"
        for entry in run["entries"]
    )


def test_missing_expected_agent_output_raises_validation_error(monkeypatch):
    from orchestration.logic_orchestration import AgentOutputValidationError

    monkeypatch.setattr(squad_scratchpad, "get", lambda trace_id: {"entries": []})

    with pytest.raises(AgentOutputValidationError, match="expected output"):
        subagent_runner._validate_agent_output("inquisitive", "trace", "No handoff was written")


def test_thinker_pipeline_runs_setup_questionnaire_analysis_and_report_in_order():
    import json

    squad_registry.SQUADS_PATH.write_text(
        json.dumps({
            "thinker": {
                "squad_id": "thinker",
                "name": "Thinker",
                "description": "",
                "primary_agent": "problem-orchestrator",
                "subagents": ["inquisitive", "problem-analyst", "problem-report-builder"],
            }
        }),
        encoding="utf-8",
    )
    request = OrchestrationRequest(squad_id="thinker", user_query="Investigate a problem")
    calls = []
    prompts = {}
    handoffs = {
        "problem-statement-builder": (
            "problem_statement", {"problem": "A problem", "parts": []}
        ),
        "inquisitive": ("questions", {"q1": "When did this begin?"}),
        "problem-analyst": ("result", {"conclusion": {"selected_part": "A"}}),
        "problem-report-builder": ("result", {"report_summary": "Likely cause A"}),
    }

    def fake_turn(agent_id, prompt, parameters=None, trace_id=None):
        calls.append(agent_id)
        prompts[agent_id] = prompt
        if agent_id == "problem-orchestrator":
            return "The investigation points to cause A."
        title, payload = handoffs[agent_id]
        squad_scratchpad.post(
            trace_id, agent_id, "note", title, json.dumps(payload)
        )
        return "handoff saved"

    with patch.object(subagent_runner, "run_agent_turn", side_effect=fake_turn), \
         patch.object(subagent_runner, "read_latest_subagent_scratchpad", return_value={"status": "success"}), \
         patch.object(subagent_runner, "read_scratchpad_snapshot", return_value={}), \
         patch("tools.promoted_tools.ask_user_window", return_value=json.dumps({
             "status": "success", "answers": {"q1": "Three days ago"}
         })):
        response = pipeline_registry.run_orchestration(request)

    run = squad_scratchpad.get(response.trace_id)
    assert calls == [
        "problem-statement-builder",
        "inquisitive",
        "problem-analyst",
        "problem-report-builder",
        "problem-orchestrator",
    ]
    assert "Agent: problem-statement-builder" in prompts["inquisitive"]
    assert "Agent: questionnaire" in prompts["problem-analyst"]
    assert "Agent: problem-report-builder" in prompts["problem-orchestrator"]
    assert any(entry["author"] == "questionnaire" for entry in run["entries"])
    assert response.status == "success"
    assert response.final_answer == "The investigation points to cause A."


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

# --- Consolidated from test_tool_fit.py ---

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_engine.logic_core_engine import Agent, agent_library, apply_model_limits, tool_catalog
from core_engine.schemas_core_engine import AgentProfile
from modelProfiles.logic_modelProfiles import model_profiles

FIVE_TOOLS = [
    "call_subagent",
    "read_chat_scratchpad",
    "write_chat_scratchpad",
    "write_file",
    "ask_user_window",
]


def make_profile(tools, mode="agent", extras=None):
    return AgentProfile(
        id="fit-test", name="Fit Test", description="", mode=mode, role="", purpose="",
        boundaries="", model="", tools=tools, extras=extras or {},
    )


def limits(max_tools):
    return {"num_ctx": 8192, "max_tools": max_tools}


class ToolFitTests(unittest.TestCase):
    def setUp(self):
        tool_catalog.load_tools()

    def fit(self, tools, max_tools, extras=None, mode="agent", model="any-model:1b"):
        agent = Agent(model=model, tools=[], profile=make_profile(tools, mode, extras), session="fit")
        with patch.object(model_profiles, "effective", return_value=limits(max_tools)):
            apply_model_limits(agent, agent.profile, model)
        return agent

    def test_tool_cut_by_the_cap_is_reported_as_dropped(self):
        agent = self.fit(FIVE_TOOLS, max_tools=4)

        self.assertEqual(agent.tools_dropped, ["ask_user_window"])
        self.assertNotIn("ask_user_window", agent.tools)
        stats = agent.run_stats()
        self.assertIn("tools_dropped", stats["flags"])
        self.assertEqual(stats["tools_dropped"], ["ask_user_window"])
        self.assertEqual(stats["level"], "warn")

    def test_agent_pinned_tools_survive_the_cap(self):
        agent = self.fit(FIVE_TOOLS, max_tools=4, extras={"pinned_tools": ["ask_user_window"]})

        self.assertIn("ask_user_window", agent.tools)
        self.assertEqual(agent.tools_dropped, ["write_file"])

    def test_listed_but_unregistered_tool_is_reported_as_missing(self):
        agent = self.fit(["call_subagent", "no_such_tool"], max_tools=None)

        self.assertEqual(agent.tools_missing, ["no_such_tool"])
        stats = agent.run_stats()
        self.assertIn("tools_missing", stats["flags"])
        self.assertEqual(stats["level"], "warn")

    def test_no_cap_and_all_registered_reports_nothing(self):
        agent = self.fit(FIVE_TOOLS, max_tools=None)

        self.assertEqual(agent.tools_dropped, [])
        self.assertEqual(agent.tools_missing, [])
        stats = agent.run_stats()
        self.assertNotIn("tools_dropped", stats["flags"])
        self.assertNotIn("tools_missing", stats["flags"])

    def test_chat_mode_has_no_tools_to_check(self):
        agent = self.fit(FIVE_TOOLS, max_tools=4, mode="chat")

        self.assertEqual(agent.tools_dropped, [])
        self.assertEqual(agent.tools_missing, [])

    def test_pinned_tools_frontmatter_is_read_into_the_profile(self):
        text = (
            "---\n"
            "id: pin-test\n"
            "name: Pin Test\n"
            "mode: agent\n"
            "tools:\n"
            "  - call_subagent\n"
            "  - ask_user_window\n"
            "pinned_tools:\n"
            "  - ask_user_window\n"
            "---\n\n"
            "## Role\nGuide the user.\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pin-test.md"
            path.write_text(text, encoding="utf-8")
            profile = agent_library.load_agent_definition_from_md(path)

        self.assertEqual(profile.extras.get("pinned_tools"), ["ask_user_window"])


class ToolFitReportTests(unittest.TestCase):
    def setUp(self):
        tool_catalog.load_tools()

    def report(self, tools, models):
        from core_engine.logic_core_engine import chat_service
        listing = {"models": models, "error": None}
        with patch.object(agent_library, "load_agent", return_value=make_profile(tools)), \
                patch.object(model_profiles, "list_models", return_value=listing), \
                patch.object(model_profiles, "effective", side_effect=lambda model: limits(
                    next(m["max_tools"] for m in models if m["model"] == model))):
            return chat_service.tool_fit_report("fit-test")

    def test_warns_for_each_model_whose_cap_cuts_tools(self):
        models = [{"model": "small:2b", "max_tools": 4}, {"model": "big:7b", "max_tools": 8}]
        report = self.report(FIVE_TOOLS, models)

        self.assertEqual(report["models"][0]["dropped"], ["ask_user_window"])
        self.assertEqual(report["models"][1]["dropped"], [])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("small:2b cuts 1 tool(s): ask_user_window", report["warnings"][0])

    def test_warns_about_tools_that_are_not_registered(self):
        report = self.report(["call_subagent", "no_such_tool"], [{"model": "big:7b", "max_tools": 8}])

        self.assertTrue(any("no_such_tool" in warning and "not registered" in warning
                            for warning in report["warnings"]))

    def test_no_warnings_when_everything_fits(self):
        report = self.report(FIVE_TOOLS, [{"model": "big:7b", "max_tools": 8}])

        self.assertEqual(report["warnings"], [])


if __name__ == "__main__":
    unittest.main()
