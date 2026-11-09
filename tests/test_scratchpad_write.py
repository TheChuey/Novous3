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
