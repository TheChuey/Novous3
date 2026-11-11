"""Consolidated tests grouped by application responsibility."""

# --- Consolidated from test_file_manager.py ---

import base64
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from file_manager.interface_file_manager import router
from file_manager.logic_file_manager import file_backend as backend


def test_file_manager_uses_configured_root(tmp_path, monkeypatch):
    root = tmp_path / "configured"
    root.mkdir()
    alternate_root = tmp_path / "alternate"
    alternate_root.mkdir()
    config = tmp_path / "allowed_paths.json"
    config.write_text(json.dumps({
        "map_roots": ["read-only"],
        "file_manager_roots": [
            {"name": "Main", "path": str(root)},
            {"name": "Alternate", "path": str(alternate_root)},
        ],
        "active_file_manager_root": str(root),
    }), encoding="utf-8")
    monkeypatch.setattr(backend, "CONFIG_PATH", config)

    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "ignored.pyc").touch()
    backend.create_path("notes/hello.txt")
    assert (root / "notes" / "hello.txt").is_file()
    assert backend.directory_tree()["name"] == root.name
    assert [entry["name"] for entry in backend.list_entries()] == ["notes"]
    selected = backend.select_root(str(alternate_root))
    assert selected["active_path"] == str(alternate_root)
    assert backend.workspace_root() == alternate_root
    saved_config = json.loads(config.read_text(encoding="utf-8"))
    assert saved_config["map_roots"] == ["read-only"]
    assert saved_config["active_file_manager_root"] == str(alternate_root)


def _hash(path: str) -> str:
    value = path or "/"
    encoded = base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
    return f"l1_{encoded}"


def test_workspace_file_operations_and_traversal_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "workspace_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.post(
        "/api/file-manager/create",
        json={"path": "docs", "kind": "directory"},
    ).status_code == 200
    write_response = client.post(
        "/api/file-manager/write",
        json={"path": "docs/readme.md", "content": "workspace file"},
    )
    assert write_response.json()["bytes_written"] == len("workspace file")
    assert client.get("/api/file-manager/read", params={"path": "docs/readme.md"}).json() == {
        "path": "docs/readme.md",
        "content": "workspace file",
    }

    listing = client.get("/api/file-manager/list", params={"path": "docs"}).json()
    assert [entry["name"] for entry in listing["entries"]] == ["readme.md"]
    assert client.post(
        "/api/file-manager/move",
        json={"source_path": "docs/readme.md", "destination_path": "readme.md"},
    ).json()["moved"]
    assert client.get("/api/file-manager/read", params={"path": "../outside.txt"}).status_code == 400


def test_select_root_only_accepts_configured_directories(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    config = tmp_path / "allowed_paths.json"
    config.write_text(json.dumps({
        "file_manager_roots": [
            {"name": "Root", "path": str(root)},
            {"name": "Alternate", "path": str(alternate)},
        ],
        "active_file_manager_root": str(root),
    }), encoding="utf-8")
    monkeypatch.setattr(backend, "CONFIG_PATH", config)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/api/file-manager/roots").json() == {
        "roots": [
            {"name": "Root", "path": str(root)},
            {"name": "Alternate", "path": str(alternate)},
        ],
        "missing": [],
        "active_path": str(root),
    }
    assert client.post(
        "/api/file-manager/roots/select",
        json={"path": str(tmp_path)},
    ).status_code == 400
    assert client.post(
        "/api/file-manager/roots/select",
        json={"path": str(alternate)},
    ).json()["active_path"] == str(alternate)
    assert client.get("/api/file-manager/tree").json()["name"] == "alternate"


def test_manage_file_manager_roots_adds_and_removes_paths(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    added_root = tmp_path / "added"
    added_root.mkdir()
    config = tmp_path / "allowed_paths.json"
    original_config = {
        "map_roots": ["keep-map-root"],
        "file_manager_roots": [{"name": "Root", "path": str(root)}],
        "active_file_manager_root": str(root),
        "sandbox_roots": ["keep-sandbox-root"],
    }
    config.write_text(json.dumps(original_config), encoding="utf-8")
    monkeypatch.setattr(backend, "CONFIG_PATH", config)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    added = client.post(
        "/api/file-manager/roots/manage",
        json={"action": "add", "paths": [str(added_root), str(root)]},
    )
    assert added.status_code == 200
    assert added.json()["added"] == [str(added_root)]
    assert added.json()["unchanged"] == [str(root)]
    saved_config = json.loads(config.read_text(encoding="utf-8"))
    assert saved_config["map_roots"] == original_config["map_roots"]
    assert saved_config["sandbox_roots"] == original_config["sandbox_roots"]
    assert len(saved_config["file_manager_roots"]) == 2

    removed = client.post(
        "/api/file-manager/roots/manage",
        json={"action": "remove", "paths": [str(root)]},
    )
    assert removed.status_code == 200
    assert removed.json()["active_path"] == str(added_root)
    assert removed.json()["removed"] == [str(root)]

    last_root = client.post(
        "/api/file-manager/roots/manage",
        json={"action": "remove", "paths": [str(added_root)]},
    )
    assert last_root.status_code == 400
    assert backend.configured_roots() == [{"name": "added", "path": str(added_root)}]

    invalid_path = client.post(
        "/api/file-manager/roots/manage",
        json={"action": "add", "paths": [str(tmp_path / "missing")]},
    )
    assert invalid_path.status_code == 400


def test_elfinder_open_and_put_use_the_shared_workspace_service(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "workspace_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    opened = client.post(
        "/api/elfinder/connector",
        data={"cmd": "open", "target": _hash("")},
    ).json()
    assert opened["cwd"]["hash"] == _hash("")

    created = client.post(
        "/api/elfinder/connector",
        data={"cmd": "mkfile", "target": _hash(""), "name": "note.txt"},
    ).json()
    target_hash = created["added"][0]["hash"]
    updated = client.post(
        "/api/elfinder/connector",
        data={"cmd": "put", "target": target_hash, "content": "from elFinder"},
    ).json()

    assert updated["changed"][0]["path"] == "note.txt"
    assert client.get(
        "/api/file-manager/read", params={"path": "note.txt"}
    ).json()["content"] == "from elFinder"

    folder = client.post(
        "/api/elfinder/connector",
        data={"cmd": "mkdir", "target": _hash(""), "name": "folder"},
    ).json()["added"][0]
    assert client.post(
        "/api/elfinder/connector",
        data={
            "cmd": "paste",
            "src[]": target_hash,
            "dst": folder["hash"],
            "cut": "0",
        },
    ).json()["added"][0]["path"] == "folder/note.txt"

    renamed = client.post(
        "/api/elfinder/connector",
        data={
            "cmd": "rename",
            "target": _hash("folder/note.txt"),
            "name": "renamed.txt",
        },
    ).json()
    assert renamed["added"][0]["path"] == "folder/renamed.txt"
    assert client.post(
        "/api/elfinder/connector",
        data={"cmd": "tree", "target": _hash("")},
    ).json()["tree"][0]["hash"] == _hash("folder")
    assert "note.txt" in client.post(
        "/api/elfinder/connector",
        data={"cmd": "ls", "target": _hash("")},
    ).json()["list"].values()

    moved = client.post(
        "/api/elfinder/connector",
        data={
            "cmd": "paste",
            "src[]": target_hash,
            "dst": folder["hash"],
            "cut": "1",
        },
    ).json()
    assert moved["added"][0]["path"] == "folder/note.txt"
    assert moved["removed"] == [target_hash]

    assert client.post(
        "/api/elfinder/connector",
        data={"cmd": "rm", "targets[]": _hash("folder")},
    ).json()["removed"] == [_hash("folder")]

    rejected = client.post(
        "/api/elfinder/connector",
        data={"cmd": "rm", "targets[]": _hash("")},
    ).json()
    assert "error" in rejected


def test_missing_root_is_skipped_and_can_be_removed(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    gone = tmp_path / "gone"
    config_path = tmp_path / "allowed_paths.json"
    config_path.write_text(json.dumps({
        "file_manager_roots": [
            {"name": "Root", "path": str(root)},
            {"name": "Gone", "path": str(gone)},
        ],
        "active_file_manager_root": str(gone),
    }), encoding="utf-8")
    monkeypatch.setattr(backend, "CONFIG_PATH", config_path)

    assert backend.configured_roots() == [{"name": "Root", "path": str(root.resolve())}]
    assert backend.missing_roots() == [{"name": "Gone", "path": str(gone.resolve())}]
    assert backend.workspace_root() == root.resolve()

    result = backend.manage_roots("remove", [str(gone)])
    assert result["removed"] == [str(gone.resolve())]
    assert result["missing"] == []
    assert backend.missing_roots() == []

# --- Consolidated from test_test_suites.py ---

import json
from pathlib import Path

import pytest

from test_environment import logic_test_suites
from test_environment.logic_test_suites import MAX_RUNS_KEPT, TestSuiteStore, slugify
from test_environment.schemas_test_environment import SuiteRunRequest, SuiteSaveRequest


def _payload(**overrides):
    base = {
        "name": "RAG table of contents",
        "category": "squads",
        "description": "",
        "agent_id": "rag-presenter",
        "model": "qwen2.5-coder:latest",
        "steps": [{"text": "Show the table of contents", "enabled": True,
                   "expect": {"contains": None, "tool": "get_knowledge_catalog"}}],
    }
    base.update(overrides)
    return base


def _run(index=0):
    return {
        "run_at": f"2026-01-01T00:00:{index:02d}+00:00",
        "agent_id": "rag-presenter",
        "model": "qwen2.5-coder:latest",
        "duration_ms": 10,
        "passed": 1,
        "failed": 0,
        "steps": [{"text": "x", "status": "passed", "reply": "ok", "duration_ms": 1,
                   "tool_events": [], "checks": []}],
    }


def test_save_then_list_and_get_round_trip():
    store = TestSuiteStore()
    saved = store.save_suite(_payload())

    assert saved["id"] == "rag-table-of-contents"
    assert saved["steps"][0]["expect"]["tool"] == "get_knowledge_catalog"
    assert [s["id"] for s in store.list_suites("squads")] == ["rag-table-of-contents"]
    assert store.get_suite("squads", "rag-table-of-contents")["name"] == "RAG table of contents"


def test_resaving_keeps_created_and_updates_steps():
    store = TestSuiteStore()
    first = store.save_suite(_payload())
    second = store.save_suite(_payload(steps=[{"text": "Another step", "enabled": True, "expect": None}]))

    assert second["created"] == first["created"]
    assert store.get_suite("squads", "rag-table-of-contents")["steps"][0]["text"] == "Another step"


def test_list_filters_by_category_and_rejects_unknown_category():
    store = TestSuiteStore()
    store.save_suite(_payload())
    store.save_suite(_payload(name="Fix a bug", category="prompts", agent_id="code-planer"))

    assert [s["category"] for s in store.list_suites("prompts")] == ["prompts"]
    assert len(store.list_suites()) == 2
    with pytest.raises(ValueError):
        store.list_suites("unknown")


def test_names_cannot_escape_the_category_folder(tmp_path):
    store = TestSuiteStore()
    store.save_suite(_payload(name="../../../escape attempt"))

    written = list(logic_test_suites.SUITES_ROOT.rglob("*.json"))
    assert [p.name for p in written] == ["escape-attempt.json"]
    assert written[0].parent == logic_test_suites.SUITES_ROOT / "squads"
    assert not (tmp_path / "escape-attempt.json").exists()


def test_slugify_rejects_empty_names():
    with pytest.raises(ValueError):
        slugify("   !!!   ")


def test_delete_removes_suite_and_its_history():
    store = TestSuiteStore()
    store.save_suite(_payload())
    store.append_result("squads", "rag-table-of-contents", _run())

    store.delete_suite("squads", "rag-table-of-contents")

    assert store.list_suites("squads") == []
    assert store.get_results("squads", "rag-table-of-contents") == {"runs": []}
    with pytest.raises(FileNotFoundError):
        store.get_suite("squads", "rag-table-of-contents")


def test_run_history_is_newest_first_and_capped():
    store = TestSuiteStore()
    store.save_suite(_payload())
    for index in range(MAX_RUNS_KEPT + 3):
        store.append_result("squads", "rag-table-of-contents", _run(index))

    runs = store.get_results("squads", "rag-table-of-contents")["runs"]
    assert len(runs) == MAX_RUNS_KEPT
    assert runs[0]["run_at"].startswith(f"2026-01-01T00:00:{MAX_RUNS_KEPT + 2:02d}")


def test_run_history_requires_an_existing_suite():
    with pytest.raises(FileNotFoundError):
        TestSuiteStore().append_result("squads", "missing", _run())


def test_request_schema_rejects_bad_payloads():
    with pytest.raises(ValueError):
        SuiteSaveRequest(**_payload(category="other"))
    with pytest.raises(ValueError):
        SuiteSaveRequest(**_payload(steps=[]))
    with pytest.raises(ValueError):
        SuiteRunRequest(run_at="x", agent_id="a", model="m", steps=[{"text": "t", "status": "bogus"}])


def test_shipped_sample_squad_suites_are_valid():
    repo_root = Path(__file__).resolve().parent.parent
    folder = repo_root / "test_environment" / "data" / "saved_suites" / "squads"
    samples = sorted(folder.glob("*.json"))

    assert len(samples) >= 5
    for path in samples:
        suite = json.loads(path.read_text(encoding="utf-8"))
        SuiteSaveRequest(**{k: suite[k] for k in ("name", "category", "description", "agent_id", "model", "steps")})
        assert suite["id"] == path.stem

# --- Consolidated from test_tool_workbench.py ---

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from core_engine.logic_core_engine import Agent, tool_catalog
from test_environment.interface_test_environment import api_promote_tool, api_test_tool_with_llm
from test_environment.logic_test_environment import tool_workbench
from test_environment.schemas_test_environment import ToolWorkbenchLLMRequest, ToolWorkbenchRequest
from tools.logic_tools import tool_registry


class ToolWorkbenchTests(unittest.TestCase):
    def test_extracts_primary_function_and_builds_agent_schema(self):
        function = tool_workbench.get_function_definition(
            'def calculate(weight: float, *, region: str = "US"):\n'
            '    """Calculate a value."""\n'
            "    return weight\n"
        )

        schema = tool_workbench.get_tool_schema(function)["function"]
        self.assertEqual(schema["name"], "calculate")
        # Agents send every parameter as a string, so the test does too.
        self.assertEqual(schema["parameters"]["properties"]["weight"]["type"], "string")
        self.assertEqual(schema["parameters"]["properties"]["region"]["type"], "string")
        self.assertEqual(schema["parameters"]["required"], ["weight"])

    def test_workbench_schema_matches_the_agent_schema(self):
        source = 'def calculate(weight: float, *, region: str = "US"):\n    """Calculate a value."""\n    return weight\n'
        namespace = {}
        exec(source, namespace)
        agent = Agent.__new__(Agent)  # schema building needs no model or session
        agent_schema = agent._ollama_schema(namespace["calculate"])
        workbench_schema = tool_workbench.get_tool_schema(tool_workbench.get_function_definition(source))
        self.assertEqual(workbench_schema, agent_schema)

    def test_description_override_replaces_docstring(self):
        function = tool_workbench.get_function_definition('def calculate(weight: float):\n    """Docstring."""\n    return weight\n')
        schema = tool_workbench.get_tool_schema(function, description="Use for weights.")
        self.assertEqual(schema["function"]["description"], "Use for weights.")

    def test_rejects_syntax_errors_and_variadic_arguments(self):
        with self.assertRaises(ValueError):
            tool_workbench.get_function_definition("def broken(:\n")
        with self.assertRaisesRegex(ValueError, "variadic"):
            tool_workbench.get_function_definition("def unsafe(*args):\n    return args\n")

    def test_runs_source_locally_and_decodes_output(self):
        source = "def add(left: int, right: int):\n    return left + right\n"
        result = tool_workbench.run_python_tool(source, {"left": 2, "right": 3})

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["output"], 5)
        self.assertEqual(result["exit_code"], 0)

    def test_returns_python_errors_and_times_out_long_running_code(self):
        error_result = tool_workbench.run_python_tool(
            "def fail():\n    raise ValueError('test error')\n", {}
        )
        self.assertEqual(error_result["status"], "ERROR")
        self.assertIn("ValueError: test error", error_result["stderr"])

        with patch.object(tool_workbench, "LOCAL_RUN_TIMEOUT_SECONDS", 0.1):
            timeout_result = tool_workbench.run_python_tool(
                "import time\ndef wait():\n    time.sleep(2)\n", {}
            )
        self.assertEqual(timeout_result["error_code"], "ERR_TOOL_TIMEOUT")

    def test_llm_tool_call_returns_and_executes_function_arguments(self):
        source = "def add(left: int, right: int):\n    return left + right\n"
        arguments = {"left": 8, "right": 5}
        execution = {"status": "SUCCESS", "error_code": None, "output": 13}
        ollama = Mock()
        ollama.chat.return_value = {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {"name": "add", "arguments": arguments}
                }],
            }
        }
        request = ToolWorkbenchLLMRequest(
            model="test-model",
            tool_code=source,
            user_prompt="Add eight and five.",
        )

        with patch.dict(sys.modules, {"ollama": ollama}):
            with patch.object(
                tool_workbench, "run_python_tool", return_value=execution
            ) as run_tool:
                result = api_test_tool_with_llm(request)

        run_tool.assert_called_once_with(source, arguments, timeout=5)
        self.assertEqual(result["function_input"], arguments)
        self.assertEqual(result["tool_execution_result"], execution)

    def test_llm_response_is_returned_when_model_does_not_call_tool(self):
        ollama = Mock()
        ollama.chat.return_value = {
            "message": {
                "content": "I could not determine the required values.",
                "tool_calls": [],
            }
        }
        request = ToolWorkbenchLLMRequest(
            model="test-model",
            tool_code="def add(left: int, right: int):\n    return left + right\n",
            user_prompt="Add eight and five.",
        )

        with patch.dict(sys.modules, {"ollama": ollama}):
            result = api_test_tool_with_llm(request)

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_code"], "ERR_NO_TOOL_CALL")
        self.assertEqual(
            result["llm_text_response"],
            "I could not determine the required values.",
        )
        self.assertEqual(result["tool_calls_detected"], [])

    def test_failed_function_execution_still_returns_llm_arguments(self):
        source = "def divide(left: int, right: int):\n    return left // right\n"
        arguments = {"left": 8, "right": 0}
        execution = {
            "status": "ERROR",
            "error_code": "ERR_TOOL_EXECUTION",
            "stderr": "ZeroDivisionError",
        }
        ollama = Mock()
        ollama.chat.return_value = {
            "message": {
                "content": "Dividing eight by zero.",
                "tool_calls": [{
                    "function": {"name": "divide", "arguments": arguments}
                }],
            }
        }
        request = ToolWorkbenchLLMRequest(
            model="test-model",
            tool_code=source,
            user_prompt="Divide eight by zero.",
        )

        with patch.dict(sys.modules, {"ollama": ollama}):
            with patch.object(
                tool_workbench, "run_python_tool", return_value=execution
            ):
                result = api_test_tool_with_llm(request)

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["function_input"], arguments)
        self.assertEqual(result["llm_text_response"], "Dividing eight by zero.")

    def test_unexpected_llm_tool_call_is_returned_for_diagnostics(self):
        tool_call = {
            "function": {"name": "other_function", "arguments": {"value": 8}}
        }
        ollama = Mock()
        ollama.chat.return_value = {
            "message": {
                "content": "Trying an unrelated function.",
                "tool_calls": [tool_call],
            }
        }
        request = ToolWorkbenchLLMRequest(
            model="test-model",
            tool_code="def add(left: int, right: int):\n    return left + right\n",
            user_prompt="Add eight and five.",
        )

        with patch.dict(sys.modules, {"ollama": ollama}):
            result = api_test_tool_with_llm(request)

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_code"], "ERR_UNEXPECTED_TOOL_CALL")
        self.assertEqual(result["llm_text_response"], "Trying an unrelated function.")
        self.assertEqual(result["tool_calls_detected"], [tool_call])

    def test_dry_run_checks_the_call_without_running_the_tool(self):
        source = "def add(left: int, right: int):\n    return left + right\n"
        ollama = Mock()
        ollama.chat.return_value = {"message": {"content": "", "tool_calls": [
            {"function": {"name": "add", "arguments": {"left": "8", "right": "5"}}}]}}

        with patch.dict(sys.modules, {"ollama": ollama}):
            with patch.object(tool_workbench, "run_python_tool") as run_tool:
                result = tool_workbench.test_with_llm(source, "test-model", "Add.", 5, execute=False)

        run_tool.assert_not_called()
        self.assertEqual(result["status"], "CALL_VALID")
        self.assertEqual(result["function_input"], {"left": "8", "right": "5"})
        self.assertEqual(result["origin"], "native")

    def test_text_json_call_is_marked_with_its_origin(self):
        source = "def add(left: str, right: str):\n    return left + right\n"
        ollama = Mock()
        ollama.chat.return_value = {"message": {
            "content": '{"name": "add", "arguments": {"left": "8", "right": "5"}}', "tool_calls": []}}

        with patch.dict(sys.modules, {"ollama": ollama}):
            result = tool_workbench.test_with_llm(source, "test-model", "Add.", 5, execute=False)

        self.assertEqual(result["status"], "CALL_VALID")
        self.assertEqual(result["origin"], "text_json")

    def test_misspelled_tool_name_gets_a_near_miss_hint(self):
        source = "def ask_user_questions(json_data: str):\n    return json_data\n"
        ollama = Mock()
        ollama.chat.return_value = {"message": {"content": "", "tool_calls": [
            {"function": {"name": "ask_user_question", "arguments": {"json_data": "{}"}}}]}}

        with patch.dict(sys.modules, {"ollama": ollama}):
            result = tool_workbench.test_with_llm(source, "test-model", "Ask.", 5, execute=False)

        self.assertEqual(result["error_code"], "ERR_UNEXPECTED_TOOL_CALL")
        self.assertIn("misspelling", result["message"])

    def test_model_without_tool_support_is_reported_not_failed(self):
        ollama = Mock()
        ollama.chat.side_effect = RuntimeError("registry.ollama.ai/library/gemma: model does not support tools")

        with patch.dict(sys.modules, {"ollama": ollama}):
            result = tool_workbench.test_with_llm(
                "def add(left: int):\n    return left\n", "no-tools-model", "Add.", 5)

        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual(result["error_code"], "ERR_MODEL_NO_TOOL_SUPPORT")

    def test_missing_llm_arguments_are_rejected_before_execution(self):
        ollama = Mock()
        ollama.chat.return_value = {
            "message": {"content": "", "tool_calls": [
                {"function": {"name": "add", "arguments": {"left": 8}}}]}
        }
        request = ToolWorkbenchLLMRequest(
            model="m",
            tool_code="def add(left: int, right: int):\n    return left + right\n",
            user_prompt="Add.",
        )
        with patch.dict(sys.modules, {"ollama": ollama}):
            result = api_test_tool_with_llm(request)
        self.assertEqual(result["error_code"], "ERR_INVALID_TOOL_ARGUMENTS")
        self.assertIsNone(result["tool_execution_result"])
        self.assertEqual(result["function_input"], {"left": 8})

    def test_generate_function_extracts_code_and_retries_once(self):
        replies = iter([
            "```python\nthis is not python(\n```",
            "```python\ndef add(a: int, b: int) -> int:\n    \"\"\"Add.\"\"\"\n    return a + b\n```",
        ])
        chat = Mock(side_effect=lambda **_: {"message": {"content": next(replies)}})
        result = tool_workbench.generate_python_function("add numbers", "m", chat=chat)
        self.assertEqual(result["function_name"], "add")
        self.assertEqual(chat.call_count, 2)

    def test_generate_function_fails_after_invalid_retry(self):
        chat = Mock(return_value={"message": {"content": "no code here"}})
        with self.assertRaises(ValueError):
            tool_workbench.generate_python_function("x", "m", chat=chat)

    def test_builds_test_harness_for_any_signature_and_runs_it(self):
        source = (
            "def f(a: float, name: str, tags: list, n: int = 3, *, flag: bool = False):\n"
            "    print('hello', a, name, tags, n, flag)\n    return n\n"
        )
        harness = tool_workbench.build_test_harness(source)
        self.assertIn("a = 1.0", harness)
        self.assertIn('name = "test"', harness)
        self.assertIn("tags = []", harness)
        self.assertIn("n = 3", harness)
        self.assertIn("flag = False", harness)
        self.assertIn("result = f(a=a, name=name, tags=tags, n=n, flag=flag)", harness)
        with_block = source + "\n\n" + harness
        self.assertEqual(tool_workbench.strip_test_harness(with_block).strip(), source.strip())
        result = tool_workbench.run_python_tool(with_block, {})
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("hello 1.0 test [] 3 False", result["stdout"])

    def test_harness_errors_are_reported_from_script_run(self):
        source = "def f(a: int):\n    return 1 // 0\n"
        result = tool_workbench.run_python_tool(source + "\n" + tool_workbench.build_test_harness(source), {})
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("ZeroDivisionError", result["stderr"])

    def test_rejects_failed_test_before_promotion(self):
        request = ToolWorkbenchRequest(
            tool_code="def add(left: int, right: int):\n    return left + right\n",
            function_input={"left": 2, "right": 3},
        )
        failed_result = {"status": "ERROR", "error_code": "ERR_TOOL_EXECUTION"}
        with patch.object(tool_workbench, "run_python_tool", return_value=failed_result):
            with patch.object(tool_workbench, "promote_python_tool") as promote:
                with self.assertRaises(HTTPException) as error:
                    api_promote_tool(request)

        self.assertEqual(error.exception.status_code, 400)
        promote.assert_not_called()

    def test_promotes_passing_tool_into_catalog(self):
        first_name = "workbench_test_add"
        second_name = "workbench_test_double"
        first_source = (
            f"def {first_name}(left: int, right: int):\n"
            "    return left + right\n"
        )
        second_source = f"def {second_name}(value: int):\n    return value * 2\n"
        with tempfile.TemporaryDirectory() as directory:
            promoted_path = Path(directory) / "promoted.py"
            with patch.object(tool_registry, "PROMOTED_MODULE_PATH", promoted_path):
                try:
                    promoted = api_promote_tool(ToolWorkbenchRequest(
                        tool_code=first_source, function_input={"left": 2, "right": 3}
                    ))
                    self.assertEqual(promoted["promoted"]["name"], first_name)
                    self.assertEqual(
                        promoted["promoted"]["path"], "tools/promoted_tools.py"
                    )
                    second_promoted = api_promote_tool(ToolWorkbenchRequest(
                        tool_code=second_source, function_input={"value": 3}
                    ))
                    self.assertEqual(second_promoted["promoted"]["name"], second_name)
                    promoted_source = promoted_path.read_text(encoding="utf-8")
                    self.assertLess(
                        promoted_source.index(first_name), promoted_source.index(second_name)
                    )
                    self.assertEqual(
                        tool_catalog.registry[first_name]["function"](2, 3), 5
                    )
                    self.assertEqual(
                        tool_catalog.registry[second_name]["function"](3), 6
                    )
                finally:
                    tool_catalog.registry.pop(first_name, None)
                    tool_catalog.registry.pop(second_name, None)

    def test_promotes_tool_with_top_level_import(self):
        tool_name = "workbench_test_fraction"
        source = (
            "import fractions as workbench_test_fraction_module\n\n"
            f"def {tool_name}(value: int):\n"
            "    return str(workbench_test_fraction_module.Fraction(value, 2))\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            promoted_path = Path(directory) / "promoted.py"
            with patch.object(tool_registry, "PROMOTED_MODULE_PATH", promoted_path):
                try:
                    result = api_promote_tool(ToolWorkbenchRequest(
                        tool_code=source, function_input={"value": 3}
                    ))
                    self.assertEqual(result["promoted"]["name"], tool_name)
                    self.assertEqual(
                        tool_catalog.registry[tool_name]["function"](3), "3/2"
                    )
                    promoted_source = promoted_path.read_text(encoding="utf-8")
                    self.assertIn(
                        "import fractions as workbench_test_fraction_module\n\n"
                        f"@tool\ndef {tool_name}",
                        promoted_source,
                    )
                finally:
                    tool_catalog.registry.pop(tool_name, None)

    def test_custom_tool_source_can_be_loaded_and_removed_from_library(self):
        tool_name = "workbench_library_tool"
        remaining_name = "workbench_library_remaining"
        source = (
            f"@tool\ndef {tool_name}(value: int):\n    return value * 2\n\n\n"
            f"@tool\ndef {remaining_name}(value: int):\n    return value + 1\n\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            promoted_path = Path(directory) / "promoted.py"
            promoted_path.write_text(source, encoding="utf-8")
            with patch.object(tool_registry, "PROMOTED_MODULE_PATH", promoted_path):
                try:
                    exec(source, {"tool": tool_catalog.tool, "__name__": "tools.promoted_tools"})
                    loaded = tool_workbench.get_custom_tool_source(tool_name)
                    self.assertEqual(
                        loaded["source"],
                        f"@tool\ndef {tool_name}(value: int):\n    return value * 2\n",
                    )
                    self.assertEqual(loaded["path"], "tools/promoted_tools.py")
                    self.assertIn(tool_name, tool_catalog.registry)
                    self.assertIn(remaining_name, tool_catalog.registry)

                    removed = tool_workbench.delete_custom_tool(tool_name)
                    self.assertTrue(removed["deleted"])
                    self.assertTrue(promoted_path.exists())
                    self.assertNotIn(tool_name, tool_catalog.registry)
                    self.assertIn(remaining_name, tool_catalog.registry)
                    self.assertIn(remaining_name, promoted_path.read_text(encoding="utf-8"))

                    tool_workbench.delete_custom_tool(remaining_name)
                    self.assertNotIn(remaining_name, tool_catalog.registry)
                    self.assertTrue(promoted_path.exists())
                    self.assertNotIn(remaining_name, promoted_path.read_text(encoding="utf-8"))
                finally:
                    tool_catalog.registry.pop(tool_name, None)
                    tool_catalog.registry.pop(remaining_name, None)


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_tools_interface.py ---

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import os

from fastapi.testclient import TestClient

from core_engine.logic_core_engine import Agent, tool_catalog
from core_engine.schemas_core_engine import AgentProfile
from ragMemory.logic_ragMemory import document_scanner, rag_config, rag_memory
from scratchpadMemory.logic_scratchpadMemory import scratchpad
from server import app
from editor.logic_editor import editor_files
from file_manager.logic_file_manager import file_backend
from tools.logic_tools import read_chat_scratchpad, tool_registry, workspace_files


class TestToolsInterface(unittest.TestCase):
    def setUp(self):
        tool_catalog.load_tools(reload=True)

    def tearDown(self):
        tool_catalog.load_tools(reload=True)

    def test_agent_tool_events_include_bounded_return_results(self):
        def successful_tool():
            return {"path": "notes/result.txt", "written": True}

        def failing_tool():
            raise ValueError("write failed")

        agent = Agent(
            model="test-model",
            tools=[successful_tool, failing_tool],
            profile=AgentProfile(mode="agent"),
        )
        agent.act(
            {"function": {"name": "successful_tool", "arguments": {}}},
            "native",
        )
        agent.act(
            {"function": {"name": "failing_tool", "arguments": {}}},
            "native",
        )

        success, failure = agent.tool_events
        self.assertEqual(success["status"], "success")
        self.assertEqual(
            json.loads(success["result"]),
            {"path": "notes/result.txt", "written": True},
        )
        self.assertEqual(failure["status"], "error")
        self.assertEqual(failure["error"], "write failed")

    def test_agent_executes_tool_json_embedded_in_explanatory_text(self):
        calls = []

        def read_chat_scratchpad(agent="", title="", index="0"):
            calls.append(title)
            return '{"latest_subagent_result":{"status":"success"}}'

        agent = Agent(
            model="test-model",
            tools=[read_chat_scratchpad],
            profile=AgentProfile(mode="agent"),
        )
        responses = [
            {
                "message": {
                    "content": (
                        "Here is the JSON for a function call:\n"
                        '{"name": "read_chat_scratchpad", "parameters": '
                        '{"agent": "rag-retriever", "title": "result"}}'
                    )
                }
            },
            {"message": {"content": "Scratchpad retrieval succeeded."}},
        ]
        with patch("core_engine.logic_core_engine.ollama.chat", side_effect=responses):
            reply = agent.think("Check the latest sub-agent result")

        self.assertEqual(calls, ["result"])
        self.assertEqual(reply, "Scratchpad retrieval succeeded.")
        self.assertEqual(agent.tool_events[0]["tool"], "read_chat_scratchpad")
        self.assertEqual(agent.tool_events[0]["origin"], "text_json")

    def test_rag_presenter_format_answer_json_becomes_markdown(self):
        agent = Agent(
            model="test-model",
            tools=[],
            profile=AgentProfile(id="rag-presenter", mode="agent"),
        )
        response = (
            "Here is the response:\n"
            '{"name":"format_answer","parameters":{"query":"leadership",'
            '"answer":"The plan emphasizes servant leadership.",'
            '"evidence":[{"path":"leadership-plan.docx","content":"Evidence"}]}}'
        )
        with patch(
            "core_engine.logic_core_engine.ollama.chat",
            return_value={"message": {"content": response}},
        ):
            reply = agent.think("Tell me about the leadership plan")

        self.assertEqual(
            reply,
            "The plan emphasizes servant leadership.\n\n"
            "**Sources**\n- leadership-plan.docx",
        )

    def test_streaming_agent_does_not_emit_embedded_tool_call_as_chat_text(self):
        calls = []

        def read_chat_scratchpad(agent="", title="", index="0"):
            calls.append(title)
            return '{"latest_subagent_result":{"status":"success"}}'

        agent = Agent(
            model="test-model",
            tools=[read_chat_scratchpad],
            profile=AgentProfile(mode="agent"),
        )
        stream_responses = [
            [
                {"message": {"content": "Here is the JSON for a function call:\n"}},
                {
                    "message": {
                        "content": (
                            '{"name": "read_chat_scratchpad", "parameters": '
                            '{"agent": "rag-retriever", "title": "result"}}'
                        )
                    }
                },
            ],
            [{"message": {"content": "Scratchpad retrieval succeeded."}}],
        ]
        with patch(
            "core_engine.logic_core_engine.ollama.chat",
            side_effect=[
                iter(stream_responses[0]),
                iter(stream_responses[1]),
            ],
        ):
            events = list(agent.think_stream("Check the latest result"))

        self.assertEqual(calls, ["result"])
        self.assertEqual(
            [event["text"] for event in events if event["type"] == "token"],
            ["Scratchpad retrieval succeeded."],
        )
        self.assertEqual(
            [event["event"]["tool"] for event in events if event["type"] == "tool_event"],
            ["read_chat_scratchpad"],
        )

    def test_scratchpad_api_reads_and_clears_saved_memory(self):
        client = TestClient(app)
        with patch.object(scratchpad, "read_memory", return_value={"latest_read": {"path": "a.txt"}}):
            response = client.get("/api/agent-memory/scratchpad")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"latest_read": {"path": "a.txt"}})

        with patch.object(scratchpad, "clear_memory", return_value={"cleared": True}):
            response = client.delete("/api/agent-memory/scratchpad")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cleared": True})

    def test_agent_tool_event_truncates_large_return_results(self):
        def large_tool():
            return "x" * 4000

        agent = Agent(
            model="test-model",
            tools=[large_tool],
            profile=AgentProfile(mode="agent"),
        )
        agent.act(
            {"function": {"name": "large_tool", "arguments": {}}},
            "native",
        )

        event = agent.tool_events[0]
        self.assertEqual(len(event["result"]), 3000)
        self.assertTrue(event["result_truncated"])

    def test_python_doorway_delegates_to_file_and_editor_services(self):
        with (
            patch.object(
                file_backend,
                "directory_tree",
                return_value={"path": "docs"},
            ) as tree,
            patch.object(
                file_backend,
                "list_directory",
                return_value={"entries": []},
            ) as listing,
            patch.object(
                file_backend,
                "read_file",
                return_value={"path": "docs/readme.md", "content": "hello"},
            ) as read,
            patch.object(
                file_backend,
                "write_file",
                return_value={"path": "docs/readme.md", "bytes_written": 5},
            ) as write,
            patch.object(
                file_backend,
                "delete_path",
                return_value={"path": "docs/old.txt", "deleted": True},
            ) as delete,
            patch.object(
                editor_files,
                "read_file_content",
                return_value={"path": "readme.md", "content": "editor"},
            ) as editor_read,
            patch.object(
                editor_files,
                "write_file_content",
                return_value={"path": "readme.md", "bytes_written": 6},
            ) as editor_write,
            patch.object(
                editor_files,
                "get_directory_tree",
                return_value={"path": "", "children": []},
            ) as editor_tree,
            patch.object(
                editor_files,
                "delete_path",
                return_value={"path": "old.txt", "deleted": True},
            ) as editor_delete,
        ):
            self.assertEqual(workspace_files.map_files("docs"), {"path": "docs"})
            self.assertEqual(workspace_files.list_directory("docs"), {"entries": []})
            self.assertEqual(
                workspace_files.read_file("docs/readme.md")["content"], "hello"
            )
            self.assertEqual(
                workspace_files.write_file("docs/readme.md", "hello")["bytes_written"], 5
            )
            self.assertTrue(workspace_files.delete_file("docs/old.txt")["deleted"])
            self.assertEqual(
                workspace_files.editor("read", path="readme.md")["content"], "editor"
            )
            self.assertEqual(
                workspace_files.editor("write", path="readme.md", content="editor")[
                    "bytes_written"
                ],
                6,
            )
            self.assertEqual(
                workspace_files.editor("tree")["children"], []
            )
            self.assertTrue(workspace_files.editor("delete", path="old.txt")["deleted"])

        tree.assert_called_once_with("docs")
        listing.assert_called_once_with("docs")
        read.assert_called_once_with("docs/readme.md")
        write.assert_called_once_with("docs/readme.md", "hello")
        delete.assert_called_once_with("docs/old.txt")
        editor_read.assert_called_once_with("readme.md")
        editor_write.assert_called_once_with("readme.md", "editor")
        editor_tree.assert_called_once_with("")
        editor_delete.assert_called_once_with("old.txt")

    def test_editor_doorway_rejects_unknown_action(self):
        with self.assertRaisesRegex(ValueError, "Unsupported editor action"):
            workspace_files.editor("rename", path="readme.md")

    def test_map_page_endpoint_uses_allowed_root_mapping_service(self):
        tree = {
            "name": "root",
            "type": "directory",
            "children": [{"name": "visible.txt", "type": "file"}],
        }
        with patch.object(workspace_files, "map_files", return_value=tree):
            response = TestClient(app).post(
                "/api/tools/map-page",
                json={
                    "path": "",
                    "max_depth": 1,
                    "include_files": True,
                    "offset": 0,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("visible.txt", response.json()["text"])

    def test_agent_file_tools_use_the_shared_doorway(self):
        functions = {
            name: tool_catalog.registry[name]["function"]
            for name in ("map_files", "read_file", "write_file", "editor", "delete_file")
        }
        with (
            patch.object(workspace_files, "map_files", return_value={"name": "root"}) as map_files,
            patch.object(workspace_files,
                "read_file_bytes",
                return_value={
                    "path": "docs/readme.md",
                    "resolved_path": Path("docs/readme.md"),
                    "size_bytes": 5,
                    "content": b"hello",
                },
            ) as read_file_bytes,
            patch.object(scratchpad, "store_read", return_value={
                "path": "scratchpadMemory/data/scratchpad.json",
                "updated_at": "2026-10-21T00:00:00+00:00",
            }),
            patch.object(scratchpad, "store_map", return_value={
                "path": "scratchpadMemory/data/scratchpad.json",
                "updated_at": "2026-10-21T00:00:00+00:00",
            }),
            patch.object(workspace_files,
                "write_file",
                side_effect=[
                    {"path": "docs/readme.md", "bytes_written": 5},
                ],
            ) as write_file,
            patch.object(workspace_files, "editor", return_value={"path": "readme.md"}) as editor,
            patch.object(workspace_files,
                "delete_file",
                return_value={"path": "docs/old.txt", "deleted": True},
            ) as delete_file,
        ):
            self.assertEqual(
                json.loads(functions["map_files"]("docs"))["name"], "root"
            )
            self.assertEqual(
                json.loads(functions["read_file"]("docs/readme.md"))["content"],
                "hello",
            )
            read_file_bytes.assert_called_once_with(
                "docs/readme.md", 1_000_000
            )
            self.assertEqual(
                json.loads(functions["write_file"]("docs/readme.md", "hello"))[
                    "bytes_written"
                ],
                5,
            )
            self.assertEqual(
                json.loads(functions["editor"]("tree"))["path"], "readme.md"
            )
            self.assertTrue(
                json.loads(functions["delete_file"]("docs/old.txt"))["deleted"]
            )

        map_files.assert_called_once_with("docs")
        self.assertEqual(write_file.call_count, 1)
        editor.assert_called_once_with("tree", path="", content="")
        delete_file.assert_called_once_with("docs/old.txt")

    def test_register_get_remove_round_trip(self):
        source = (
            "def calc_square(n: int) -> int:\n"
            "    \"\"\"Calculate square of n.\"\"\"\n"
            "    return n * n\n"
        )
        with tempfile.TemporaryDirectory() as td:
            promoted_path = Path(td) / "promoted.py"
            header = (
                '"""Custom tools promoted from the Function Testing Workbench."""\n'
                'from core_engine.logic_core_engine import tool\n\n'
                '# PROMOTION_MARKER: tools appended below will be registered as custom tools\n\n'
            )
            promoted_path.write_text(header, encoding="utf-8", newline="\n")
            initial_bytes = promoted_path.read_bytes()

            with patch.object(tool_registry, "PROMOTED_MODULE_PATH", promoted_path):
                # 1. Register
                reg_result = tool_registry.register_tool(source, "calc_square")
                self.assertEqual(reg_result["name"], "calc_square")
                self.assertEqual(reg_result["path"], "tools/promoted_tools.py")

                # 2. Get tool callable
                fn = tool_registry.get_tool("calc_square")
                self.assertIsNotNone(fn)
                self.assertEqual(fn(5), 25)

                library = {t["name"]: t for t in tool_registry.list_tools_directory()}
                self.assertTrue(library["calc_square"]["custom"])

                # 3. Get tool source
                src_info = tool_registry.get_tool_source("calc_square")
                self.assertEqual(src_info["name"], "calc_square")
                self.assertIn("calc_square", src_info["source"])
                self.assertEqual(src_info["path"], "tools/promoted_tools.py")

                # 4. Remove tool
                del_result = tool_registry.remove_tool("calc_square")
                self.assertTrue(del_result["deleted"])
                self.assertIsNone(tool_registry.get_tool("calc_square"))

                # 5. Check byte-identical
                after_bytes = promoted_path.read_bytes()
                self.assertEqual(initial_bytes, after_bytes)

    def test_router_endpoints(self):
        client = TestClient(app)

        # GET /api/tools
        resp = client.get("/api/tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("tools", data)
        self.assertIn("providers", data)
        self.assertIn("warnings", data)
        self.assertIn("calculator", [t["name"] for t in data["tools"]])

        # GET /api/tools/calculator
        resp = client.get("/api/tools/calculator")
        self.assertEqual(resp.status_code, 200)
        calc_data = resp.json()
        self.assertEqual(calc_data["name"], "calculator")
        self.assertEqual(calc_data["provider"], "core_engine")

        # GET /api/tools/health
        resp = client.get("/api/tools/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "healthy")

    def test_rag_branch_rename_and_delete_routes(self):
        client = TestClient(app)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        (folder / "keep.txt").write_text("Keep user files.", encoding="utf-8")
        config_path = folder / "rag_config.json"
        index_dir = folder / "indexes"
        with patch.object(rag_config, "CONFIG_PATH", config_path), \
                patch.object(rag_config, "DATA_DIR", index_dir), \
                patch.object(document_scanner, "_job", {"status": "idle"}):
            base = rag_memory.add_rag_base("Original branch", str(folder))
            renamed = client.put(
                f"/api/rag/bases/{base['id']}",
                json={"name": "Renamed branch"},
            )
            self.assertEqual(renamed.status_code, 200, renamed.text)
            self.assertEqual(renamed.json()["name"], "Renamed branch")

            deleted = client.delete(f"/api/rag/bases/{base['id']}")
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(deleted.json()["name"], "Renamed branch")
            self.assertFalse(deleted.json()["folder_deleted"])
            self.assertEqual((folder / "keep.txt").read_text(encoding="utf-8"), "Keep user files.")
            self.assertEqual(rag_memory.list_rag_bases(), [])

    @unittest.skipUnless(os.name == "nt", "Opening folders uses os.startfile, which exists only on Windows.")
    def test_open_rag_branch_folder_route(self):
        client = TestClient(app)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        config_path = folder / "rag_config.json"
        index_dir = folder / "indexes"
        with patch.object(rag_config, "CONFIG_PATH", config_path), \
                patch.object(rag_config, "DATA_DIR", index_dir), \
                patch.object(document_scanner, "_job", {"status": "idle"}):
            base = rag_memory.add_rag_base("Local branch", str(folder))
            with patch("ragMemory.logic_ragMemory.os.startfile", create=True) as open_folder:
                response = client.post(f"/api/rag/bases/{base['id']}/open-folder")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"opened": True, "path": str(folder.resolve())})
            open_folder.assert_called_once_with(str(folder.resolve()))

            missing = client.post("/api/rag/bases/unknown/open-folder")
            self.assertEqual(missing.status_code, 404)

    def test_registered_tool_library_shows_only_tools_package_functions(self):
        client = TestClient(app)
        response = client.get("/api/tools/library")

        self.assertEqual(response.status_code, 200, response.text)
        names = {tool["name"] for tool in response.json()["tools"]}
        self.assertEqual(
            names,
            {
                "map_files",
                "read_file",
                "write_file",
                "create_folder",
                "editor",
                "delete_file",
                "read_chat_scratchpad",
                "call_subagent",
                "write_chat_scratchpad",
                "read_coding_scratchpad",
                "write_coding_scratchpad",
                "read_squad_scratchpad",
                "write_squad_scratchpad",
                "clear_squad_scratchpad",
                "get_knowledge_catalog",
                "search_knowledge_branch",
                "list_knowledge_branch_documents",
                "read_knowledge_branch_document",
                "ask_user_window",  # promoted custom tool in tools/promoted_tools.py
            },
        )
        self.assertGreater(len(tool_catalog.list_tools()), len(names))

        with tempfile.TemporaryDirectory() as td:
            promoted_path = Path(td) / "promoted.py"
            with patch.object(tool_registry, "PROMOTED_MODULE_PATH", promoted_path):
                # POST /api/tools/register
                tool_code = "def custom_api_test(x: int) -> int:\n    return x + 10\n"
                reg_resp = client.post(
                    "/api/tools/register",
                    json={"source": tool_code, "name": "custom_api_test"},
                )
                self.assertEqual(reg_resp.status_code, 200)
                self.assertEqual(reg_resp.json()["name"], "custom_api_test")

                # GET /api/tools/{name}
                get_resp = client.get("/api/tools/custom_api_test")
                self.assertEqual(get_resp.status_code, 200)
                self.assertTrue(get_resp.json()["custom"])

                # DELETE /api/tools/{name}
                del_resp = client.delete("/api/tools/custom_api_test")
                self.assertEqual(del_resp.status_code, 200)

                # GET after delete -> 404
                get_after = client.get("/api/tools/custom_api_test")
                self.assertEqual(get_after.status_code, 404)

    def test_scratchpad_recall_schema_tells_the_model_when_to_use_it(self):
        agent = Agent(model="test-model", tools=[], profile=AgentProfile(mode="agent"))
        schema = agent._ollama_schema(read_chat_scratchpad)["function"]

        self.assertIn("agent", schema["description"])
        self.assertIn("title", schema["description"])
        for name in ("agent", "title", "index", "offset"):
            self.assertNotEqual(schema["parameters"]["properties"][name]["description"], f"Parameter {name}")

    def test_workbench_tool_source_includes_undecorated_test_source(self):
        response = TestClient(app).get("/api/testing/tool-workbench/tools/read_chat_scratchpad")

        self.assertEqual(response.status_code, 200, response.text)
        tool = response.json()
        self.assertEqual(tool["path"], "tools/logic_tools.py")
        self.assertIn("@tool(", tool["source"])
        self.assertNotIn("@tool", tool["test_source"])
        self.assertIn("def read_chat_scratchpad(", tool["test_source"])
        compile(tool["test_source"], "test_source", "exec")


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_workspace_component.py ---

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import app
from squads.interface_squad_folders import router as squad_folders_router
from squads.logic_squad_folders import squad_manager


class TestSquadFolderRoutes(unittest.TestCase):
    def test_squads_router_only_exposes_squad_folder_routes(self):
        paths = {route.path for route in squad_folders_router.routes}
        self.assertEqual(
            paths,
            {
                "/api/squads",
                "/api/squads/create",
                "/api/squads/rename",
                "/api/squads/delete",
            },
        )

    def test_squad_folder_routes_create_rename_and_delete_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(squad_manager, "SQUAD_ROOT", Path(temp_dir)):
                client = TestClient(app)
                response = client.post(
                    "/api/squads/create",
                    json={"name": "Research Team", "description": "Research work"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue((Path(temp_dir) / "research-team" / "squad.json").is_file())

                response = client.post(
                    "/api/squads/rename",
                    json={"name": "Research Team", "new_name": "Review Team"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["squad"]["id"], "review-team")

                response = client.get("/api/squads")
                self.assertEqual(response.status_code, 200)
                self.assertEqual([item["name"] for item in response.json()["squads"]], ["Review Team"])

                response = client.post("/api/squads/delete", json={"name": "Review Team"})
                self.assertEqual(response.status_code, 200)
                self.assertFalse((Path(temp_dir) / "review-team").exists())


if __name__ == "__main__":
    unittest.main()

# --- Consolidated from test_squads_architect.py ---

import pytest

from core_engine.logic_core_engine import agent_library
from squads.logic_squads import squad_architect
from squads.schemas_squads import SquadBlueprint, SquadMember

LEAD = """---
id: lead
name: Lead
description: Runs the squad.
mode: chat
tools: []
---

# Lead

Coordinate the squad.
"""

PEER = LEAD.replace("id: lead", "id: peer").replace("name: Lead", "name: Peer")

WORKER = """id: worker
name: Worker
description: Does background work.
type: subagent
mode: agent
tools:
  - write_squad_scratchpad
prompt: |
  # Worker
"""


@pytest.fixture
def libraries(tmp_path, monkeypatch):
    library = tmp_path / "library"
    subagents = tmp_path / "subAgentLibrary"
    library.mkdir()
    subagents.mkdir()
    (library / "lead.md").write_text(LEAD, encoding="utf-8")
    (library / "peer.md").write_text(PEER, encoding="utf-8")
    (subagents / "worker.yaml").write_text(WORKER, encoding="utf-8")
    monkeypatch.setattr(agent_library, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(agent_library, "LIBRARY_ROOT", library)
    monkeypatch.setattr(agent_library, "SUBAGENT_LIBRARY_ROOT", subagents)
    return library, subagents


def blueprint(**overrides):
    data = {
        "name": "Research Team",
        "pattern": "sequential",
        "lead": SquadMember(agent_id="lead", tools=["read_squad_scratchpad"]),
        "members": [SquadMember(agent_id="worker")],
    }
    data.update(overrides)
    return SquadBlueprint(**data)


def test_describe_agent_reports_kind_tools_and_mode(libraries):
    assert squad_architect.describe_agent("lead")["kind"] == "agent"
    worker = squad_architect.describe_agent("worker")
    assert worker["kind"] == "subagent"
    assert worker["tools"] == ["write_squad_scratchpad"]
    assert squad_architect.describe_agent("ghost") is None


def test_validate_reports_structural_errors_and_handoff_warnings(libraries):
    result = squad_architect.validate(blueprint(
        lead=SquadMember(agent_id="worker"),
        members=[SquadMember(agent_id="ghost", tools=["no_such_tool"])],
    ))
    assert not result.ok
    joined = " | ".join(result.errors)
    assert "must be a primary agent" in joined
    assert "Member not found: ghost" in joined
    assert "unknown tools no_such_tool" in joined

    peer_result = squad_architect.validate(blueprint(members=[SquadMember(agent_id="peer")]))
    assert not peer_result.ok
    assert any("must be a sub-agent" in error for error in peer_result.errors)

    ok = squad_architect.validate(blueprint(lead=SquadMember(agent_id="lead")))
    assert ok.ok
    assert any("read_squad_scratchpad" in warning for warning in ok.warnings)


def test_save_blueprint_registers_squad_and_applies_tools_and_allow_list(libraries):
    saved = squad_architect.save_blueprint(blueprint(
        pattern="fanout",
        members=[SquadMember(agent_id="worker")],
    ))

    plan = saved["blueprint"]
    assert plan["pattern"] == "sequential"
    assert [member["id"] for member in plan["members"]] == ["worker"]
    assert plan["lead"]["tools"] == ["read_squad_scratchpad"]
    assert plan["lead"]["mode"] == "agent"
    assert plan["lead"]["allowed_subagents"] == []

    updated = squad_architect.save_blueprint(blueprint(
        squad_id=plan["squad_id"], pattern="sequential", members=[SquadMember(agent_id="worker")],
    ))
    assert updated["blueprint"]["pattern"] == "sequential"
    assert [member["id"] for member in updated["blueprint"]["members"]] == ["worker"]

    with pytest.raises(ValueError, match="cannot also be a squad member"):
        squad_architect.save_blueprint(blueprint(members=[SquadMember(agent_id="lead")]))
    with pytest.raises(ValueError, match="dynamic agent delegation is disabled"):
        squad_architect.save_blueprint(blueprint(lead_delegates=True))
    with pytest.raises(LookupError):
        squad_architect.save_blueprint(blueprint(squad_id="missing"))


def test_attach_and_detach_tool(libraries):
    attached = squad_architect.attach_tool("peer", "read_file")
    assert attached["tools"] == ["read_file"] and attached["mode"] == "agent"
    assert squad_architect.attach_tool("peer", "read_file")["tools"] == ["read_file"]
    assert squad_architect.detach_tool("peer", "read_file")["tools"] == []
    with pytest.raises(ValueError, match="Unknown tools"):
        squad_architect.attach_tool("peer", "no_such_tool")
    with pytest.raises(FileNotFoundError):
        squad_architect.attach_tool("ghost", "read_file")


def test_create_member_scaffolds_either_kind(libraries):
    agent = squad_architect.create_member("agent", "analyst", "Analyst", "Analyses things.")
    sub = squad_architect.create_member("subagent", "fetcher", "Fetcher", "Fetches things.")
    assert agent["kind"] == "agent" and agent["id"] == "analyst"
    assert sub["kind"] == "subagent" and "write_squad_scratchpad" in sub["tools"]
