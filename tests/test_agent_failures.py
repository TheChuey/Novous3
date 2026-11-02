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
