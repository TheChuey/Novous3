import unittest
from unittest.mock import Mock, patch

from tools import logic_tools


class WriteAgentScratchpadTests(unittest.TestCase):
    def test_empty_payload_is_refused_and_nothing_is_stored(self):
        store = Mock()
        with patch.object(logic_tools.scratchpad, "store_subagent_result", store):
            for empty in ("", "   ", "{}", "[]"):
                with self.assertRaisesRegex(ValueError, "payload is empty"):
                    logic_tools.write_agent_scratchpad(subagent_id="problem-identifier",
                                                       task_type="critical_thinking", payload=empty)
        store.assert_not_called()

    def test_json_payload_is_stored_as_an_object(self):
        store = Mock(return_value={"id": "x"})
        payload = '{"answers": {"goal": "Move into operations"}, "summary": "Retail leader seeking ops roles."}'
        with patch.object(logic_tools.scratchpad, "store_subagent_result", store):
            logic_tools.write_agent_scratchpad(subagent_id="problem-identifier",
                                               task_type="critical_thinking", payload=payload)
        store.assert_called_once()
        self.assertEqual(store.call_args.kwargs["payload"]["answers"]["goal"], "Move into operations")

    def test_empty_answers_object_is_still_stored(self):
        store = Mock(return_value={"id": "x"})
        with patch.object(logic_tools.scratchpad, "store_subagent_result", store):
            logic_tools.write_agent_scratchpad(subagent_id="problem-identifier", task_type="critical_thinking",
                                               payload='{"answers": {}, "summary": "No answers were given."}')
        store.assert_called_once()


if __name__ == "__main__":
    unittest.main()
