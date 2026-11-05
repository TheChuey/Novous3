import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_engine.logic_core_engine import Agent, agent_library, apply_model_limits, tool_catalog
from core_engine.schemas_core_engine import AgentProfile
from modelProfiles.logic_modelProfiles import model_profiles

FIVE_TOOLS = [
    "call_subagent",
    "read_agent_scratchpad",
    "write_agent_scratchpad",
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
