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

META = {"id": "worker", "mode": "agent", "tools": ["map_files", "read_agent_scratchpad"], "model": "tiny-model:1b"}


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
        self.assertIn("read_agent_scratchpad", SQUAD_PINNED_TOOLS)

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
