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
        "pattern": "fanout",
        "lead": SquadMember(agent_id="lead", tools=["read_squad_scratchpad"]),
        "members": [SquadMember(agent_id="peer"), SquadMember(agent_id="worker")],
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
        pattern="router",
    ))
    assert not result.ok
    joined = " | ".join(result.errors)
    assert "must be a primary agent" in joined
    assert "Member not found: ghost" in joined
    assert "unknown tools no_such_tool" in joined
    assert "router pattern needs at least two members" in joined

    ok = squad_architect.validate(blueprint(lead=SquadMember(agent_id="lead")))
    assert ok.ok
    assert any("read_squad_scratchpad" in warning for warning in ok.warnings)


def test_save_blueprint_registers_squad_and_applies_tools_and_allow_list(libraries):
    saved = squad_architect.save_blueprint(blueprint(
        members=[SquadMember(agent_id="peer", tools=["read_file"]), SquadMember(agent_id="worker")],
        lead_delegates=True,
    ))

    plan = saved["blueprint"]
    assert plan["pattern"] == "fanout"
    assert [member["id"] for member in plan["members"]] == ["peer", "worker"]
    assert plan["lead"]["tools"] == ["read_squad_scratchpad", "call_subagent"]
    assert plan["lead"]["mode"] == "agent"
    # Only sub-agent members go on the delegation allow-list; peers run through the pattern.
    assert plan["lead"]["allowed_subagents"] == ["worker"]
    assert agent_library.allowed_subagents("lead") == ["worker"]
    assert squad_architect.describe_agent("peer")["tools"] == ["read_file"]

    updated = squad_architect.save_blueprint(blueprint(
        squad_id=plan["squad_id"], pattern="sequential", members=[SquadMember(agent_id="worker")],
    ))
    assert updated["blueprint"]["pattern"] == "sequential"
    assert [member["id"] for member in updated["blueprint"]["members"]] == ["worker"]

    with pytest.raises(ValueError, match="cannot also be a squad member"):
        squad_architect.save_blueprint(blueprint(members=[SquadMember(agent_id="lead")]))
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
