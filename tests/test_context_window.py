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
                           tools=["map_files", "call_subagent", "read_agent_scratchpad"])
        model_profiles.save("tiny-model:1b", 8192, 1024, 1)
        agent = Agent(model=None, tools=[], profile=rag)
        self.service._apply_model_limits(agent, rag, "tiny-model:1b")

        self.assertEqual(set(agent.tools), {"call_subagent", "read_agent_scratchpad"})

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
