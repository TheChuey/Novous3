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
    folder = repo_root / "workspace" / "saved_test_suites" / "squads"
    samples = sorted(folder.glob("*.json"))

    assert len(samples) == 5
    for path in samples:
        suite = json.loads(path.read_text(encoding="utf-8"))
        SuiteSaveRequest(**{k: suite[k] for k in ("name", "category", "description", "agent_id", "model", "steps")})
        assert suite["id"] == path.stem
