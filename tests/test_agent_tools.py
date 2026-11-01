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

    result = agent_library.set_agent_tools("subagent", "worker", ["read_file", "write_agent_scratchpad"])

    assert result["tools"] == ["read_file", "write_agent_scratchpad"]
    text = (subagents / "worker.yaml").read_text(encoding="utf-8")
    assert "    - not_a_real_key" in text
    data, body = agent_library.parse_subagent_yaml("worker")
    assert data["name"] == "Worker"
    assert "## Available Tools\n\n### read_file\n- Provider:" in body
    assert "### write_agent_scratchpad" in body
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
    assert data["tools"] == ["read_agent_scratchpad", "write_agent_scratchpad"]
    assert "## role" in body and '"subagent_id": "doc-writer"' in body
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
