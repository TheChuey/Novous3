import time

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestration import logic_orchestration
from core_engine.logic_core_engine import current_session_id
from squad_developement import agent_design, flow_design
from squad_developement.interface_agent_studio import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(flow_design, "ROOT", tmp_path)
    template = tmp_path / "generic_prompt.md"
    template.write_text("## Role\nDescribe this agent's role.\n", encoding="utf-8")
    monkeypatch.setattr(agent_design, "GENERIC_PROMPT_PATH", template)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_generic_prompt_save_and_validation(client):
    url = "/api/squad-development/template"
    assert client.put(url, json={"content": "## Role\nNew template\n"}).json()["prompt"] == "## Role\nNew template\n"
    assert client.get(url).json()["prompt"] == "## Role\nNew template\n"
    assert client.put(url, json={"content": "  "}).status_code == 400
    assert client.put(url, json={"content": "a" * 1_000_001}).status_code == 400
    assert client.get(url).json()["prompt"] == "## Role\nNew template\n"


def test_prompt_starter_choices_persist_and_validate(client, tmp_path):
    url = "/api/squad-development/prompt-starter/choices"
    assert client.get(url).json() == {"choices": {}}
    assert client.post(url, json={"variable": "TASK", "value": " Review evidence "}).json() == {
        "choices": {"TASK": ["Review evidence"]}
    }
    assert client.post(url, json={"variable": "TASK", "value": "Review evidence"}).status_code == 200
    assert client.post(url, json={"variable": "CUSTOM FIELD", "value": "New value"}).status_code == 200
    assert client.get(url).json()["choices"] == {"TASK": ["Review evidence"], "CUSTOM FIELD": ["New value"]}
    assert (tmp_path / "prompt_starter_choices.yaml").exists()
    for data in [
        {"variable": "bad/name", "value": "choice"},
        {"variable": "TASK", "value": " "},
        {"variable": "TASK", "value": "a" * 2001},
    ]:
        assert client.post(url, json=data).status_code == 400
    assert client.get(url).json()["choices"]["TASK"] == ["Review evidence"]


def test_prompt_starter_defaults_match_agent_and_subagent_scaffolds(client):
    response = client.get("/api/squad-development/prompt-starter/defaults")
    assert response.status_code == 200
    defaults = response.json()

    agent_default = defaults["agent"]
    frontmatter = agent_default["prompt"].split("---", 2)[1]
    agent_metadata = yaml.safe_load(frontmatter)
    assert agent_metadata["id"] == "[AGENT ID]"
    assert agent_metadata["name"] == "[AGENT NAME]"
    assert agent_metadata["mode"] == "agent"
    assert agent_metadata["tools"] == []
    assert "You are [AGENT NAME], a Novous workspace agent." in defaults["agent"]["prompt"]
    assert "Serve as the [AGENT NAME] for the workspace environment." in defaults["agent"]["prompt"]

    subagent_yaml = yaml.safe_load(defaults["subagent"]["yaml"])
    assert subagent_yaml["type"] == "subagent"
    assert subagent_yaml["name"] == "[SUB-AGENT NAME]"
    assert subagent_yaml["tools"] == ["read_squad_scratchpad", "write_squad_scratchpad"]
    assert defaults["subagent"]["prompt"] == subagent_yaml["prompt"].replace(
        'agent: "novous-subagent"', 'agent: "[SUB-AGENT ID]"'
    )
    assert 'agent: "[SUB-AGENT ID]"' in defaults["subagent"]["prompt"]
    assert "You are the [SUB-AGENT NAME]." in defaults["subagent"]["prompt"]


def test_prompt_starter_agent_definition_parses_and_validates_tool_choices(client):
    defaults = client.get("/api/squad-development/prompt-starter/defaults").json()["agent"]
    definition_text = defaults["prompt"].replace("tools: []", "tools:\n  - calculator").replace(
        "[AGENT ID]", "starter-agent"
    ).replace(
        "[AGENT NAME]", "Starter Agent"
    )

    response = client.post(
        "/api/squad-development/prompt-starter/parse-agent",
        json={"content": definition_text},
    )
    assert response.status_code == 200
    definition = response.json()
    assert definition["id"] == "starter-agent"
    assert definition["name"] == "Starter Agent"
    assert definition["tools"] == ["calculator"]
    assert definition["prompt"].startswith("# Starter Agent")

    empty_tools = defaults["prompt"].replace("[AGENT ID]", "starter-agent").replace(
        "[AGENT NAME]", "Starter Agent"
    )
    assert client.post(
        "/api/squad-development/prompt-starter/parse-agent",
        json={"content": empty_tools},
    ).json()["tools"] == []

    invalid_tool = definition_text.replace("calculator", "not_registered_tool", 1)
    invalid_response = client.post(
        "/api/squad-development/prompt-starter/parse-agent",
        json={"content": invalid_tool},
    )
    assert invalid_response.status_code == 400
    assert "Unregistered tools" in invalid_response.json()["detail"]


def test_pasted_yaml_formatting_preserves_data_and_rejects_lossy_input(client):
    url = "/api/squad-development/format-yaml"
    original = "id: worker\nprompt: |\n  First line\n  Second line\nnested: {a: 1, b: [one, two]}\n"
    response = client.post(url, json={"content": original})
    assert response.status_code == 200
    formatted = response.json()["content"]
    assert yaml.safe_load(formatted) == yaml.safe_load(original)
    assert "nested:\n" in formatted
    styled = "---\nvalue: on\nnumber: 012\nquoted: 'yes'\nprompt: |\n  One\n  Two\n...\n"
    response = client.post(url, json={"content": styled})
    assert response.status_code == 200
    assert response.json()["content"] == styled
    for text in [
        "id: [broken", "id: a\nid: b\n", "a: 01\n1: x\n01: y\n",
        "id: a # comment\n", 'id: "a#b"\n', "id: &item a\nother: *item\n",
        "id: !custom a\n", "id: a\n---\nid: b\n", "plain text",
    ]:
        assert client.post(url, json={"content": text}).status_code == 400


def create_agent(client, agent_id, kind="agent"):
    return client.post("/api/squad-development/squads/demo/agents", json={
        "id": agent_id, "name": agent_id, "kind": kind, "prompt": "## Role\nBe helpful."
    })


def wait_for_result(client, trace_id):
    for _ in range(100):
        response = client.get(f"/api/squad-development/runs/{trace_id}")
        if response.status_code == 200:
            return response.json()
        time.sleep(0.01)
    pytest.fail("Run did not complete")


def test_squad_files_generic_prompt_and_notes(client, tmp_path):
    template = client.get("/api/squad-development/template").json()["prompt"]
    assert "## Role" in template
    assert client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"}).status_code == 201
    assert create_agent(client, "alpha").status_code == 201
    assert create_agent(client, "worker", "subagent").status_code == 201
    assert sorted(p.name for p in (tmp_path / "demo").iterdir()) == [
        "alpha.md", "documentation.md", "squad.yaml", "worker.yaml"
    ]
    assert "Be helpful" in client.get("/api/squad-development/squads/demo/agents/alpha").json()["content"]
    assert client.put("/api/squad-development/squads/demo/notes", json={"content": "# Ideas\nTry this skill"}).status_code == 200
    assert "Try this skill" in client.get("/api/squad-development/squads/demo/notes").json()["content"]
    assert create_agent(client, "alpha").status_code == 400
    assert client.post("/api/squad-development/squads", json={"id": "..", "name": "Oops"}).status_code == 400


def test_invalid_agent_save_is_rolled_back(client):
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    create_agent(client, "worker", "subagent")
    url = "/api/squad-development/squads/demo/agents/worker"
    original = client.get(url).json()["content"]
    assert client.put(url, json={"content": "id: worker\nprompt: [invalid"}).status_code == 400
    assert client.get(url).json()["content"] == original


def test_agent_save_reports_mismatched_id_and_preserves_saved_content(client):
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    create_agent(client, "worker")
    url = "/api/squad-development/squads/demo/agents/worker"
    original = client.get(url).json()["content"]
    wrong_id = original.replace("id: worker", "id: another-agent")
    response = client.put(url, json={"content": wrong_id})
    assert response.status_code == 400
    assert response.json()["detail"] == "Agent frontmatter must have id: worker."
    assert client.get(url).json()["content"] == original
    updated = original.replace("## Role", "## Purpose")
    assert client.put(url, json={"content": updated}).status_code == 200
    assert client.get(url).json()["content"] == updated


def test_saving_windows_line_endings_does_not_add_blank_lines(client, tmp_path):
    template = "## Role\r\nFirst line\r\nSecond line\r\n"
    assert client.put("/api/squad-development/template", json={"content": template}).status_code == 200
    assert (tmp_path / "generic_prompt.md").read_bytes() == template.encode()
    assert client.get("/api/squad-development/template").json()["prompt"] == template.replace("\r\n", "\n")

    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    assert client.post("/api/squad-development/squads/demo/agents", json={
        "id": "worker", "name": "Worker", "kind": "agent", "prompt": template,
    }).status_code == 201
    agent_path = tmp_path / "demo" / "worker.md"
    assert b"\r\r\n" not in agent_path.read_bytes()
    url = "/api/squad-development/squads/demo/agents/worker"
    content = client.get(url).json()["content"].replace("\n", "\r\n")
    assert client.put(url, json={"content": content}).status_code == 200
    assert agent_path.read_bytes() == content.encode()
    assert client.get(url).json()["content"] == content.replace("\r\n", "\n")
    skill = "First line\r\nSecond line\r\n"
    assert client.put("/api/squad-development/squads/demo/skills/review", json={"content": skill}).status_code == 200
    assert (tmp_path / "demo" / "skill-review.md").read_bytes() == skill.encode()


@pytest.mark.parametrize("kind", ["agent", "subagent"])
def test_add_tool_to_draft_and_save(client, kind):
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    create_agent(client, "worker", kind)
    url = "/api/squad-development/squads/demo/agents/worker"
    original = client.get(url).json()["content"]
    response = client.post(url + "/draft-tool", json={"content": original, "tool": "calculator"})
    assert response.status_code == 200
    draft = response.json()["content"]
    assert "calculator" in draft
    assert "## Tool: calculator" in draft
    assert "Parameters:\n" in draft
    assert "- expression (required):" in draft
    assert "do not assume JSON" in draft
    assert client.get(url).json()["content"] == original
    assert client.put(url, json={"content": draft}).status_code == 200
    assert "calculator" in client.get(url).json()["content"]
    profile = agent_design.parse_agent("demo", "worker")
    assert profile.tools == ["calculator"]
    assert "NO TOOLS" not in profile.system_prompt
    assert "Parameters:\n" in profile.system_prompt
    assert "- expression (required):" in profile.system_prompt
    assert client.post(url + "/draft-tool", json={"content": draft, "tool": "call_subagent"}).status_code == 400
    assert client.post(url + "/draft-tool", json={"content": draft, "tool": "calculator"}).status_code == 400


@pytest.mark.parametrize("kind", ["agent", "subagent"])
def test_skill_storage_and_insertion(client, tmp_path, kind):
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    create_agent(client, "worker", kind)
    skills = "/api/squad-development/squads/demo/skills"
    assert client.put(skills + "/review", json={"content": "Check the evidence."}).status_code == 200
    assert client.get(skills).json()["skills"] == ["review"]
    assert client.get(skills + "/review").json()["content"] == "Check the evidence."
    assert (tmp_path / "demo" / "skill-review.md").is_file()
    assert client.put(skills + "/review", json={"content": "  "}).status_code == 400
    url = "/api/squad-development/squads/demo/agents/worker"
    original = client.get(url).json()["content"]
    response = client.post(url + "/draft-skill", json={"content": original, "skill_id": "review"})
    assert response.status_code == 200
    draft = response.json()["content"]
    assert "## Skill: review" in draft
    assert "Check the evidence." in draft
    assert client.get(url).json()["content"] == original
    assert client.put(url, json={"content": draft}).status_code == 200
    assert "Check the evidence." in agent_design.parse_agent("demo", "worker").system_prompt
    assert client.post(url + "/draft-skill", json={"content": draft, "skill_id": "review"}).status_code == 400


@pytest.mark.parametrize("kind", ["agent", "subagent"])
def test_create_agent_with_selected_tools(client, kind):
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    response = client.post("/api/squad-development/squads/demo/agents", json={
        "id": "worker", "name": "Worker", "kind": kind, "prompt": "## Role\nHelp.",
        "tools": ["calculator"],
    })
    assert response.status_code == 201
    assert agent_design.parse_agent("demo", "worker").tools == ["calculator"]
    assert client.get("/api/squad-development/tools/calculator/instructions").json()["content"].startswith("## Tool: calculator")


def test_prompt_starter_choice_delete(client):
    url = "/api/squad-development/prompt-starter/choices"
    client.post(url, json={"variable": "TASK", "value": "First"})
    client.post(url, json={"variable": "TASK", "value": "Second"})
    response = client.post(url + "/delete", json={"variable": "TASK", "value": "First"})
    assert response.status_code == 200
    assert response.json()["choices"] == {"TASK": ["Second"]}
    assert client.post(url + "/delete", json={"variable": "TASK", "value": "Missing"}).status_code == 404
    assert client.post(url + "/delete", json={"variable": "TASK", "value": "Second"}).json()["choices"] == {}


def test_prompt_starter_generate_returns_model_text(client, monkeypatch):
    from core_engine import logic_core_engine
    from modelProfiles.logic_modelProfiles import model_profiles

    calls = {}

    def fake_chat(**kwargs):
        calls.update(kwargs)
        return {"message": {"content": "A concise role description."}}

    monkeypatch.setattr(logic_core_engine, "serialized_ollama_chat", fake_chat)
    monkeypatch.setattr(model_profiles, "effective", lambda model: {"num_ctx": 4096})

    url = "/api/squad-development/prompt-starter/generate"
    response = client.post(url, json={"model": "test:latest", "prompt": "Write a role.\n\nContext:\n[GOAL]: Plan"})
    assert response.status_code == 200
    assert response.json() == {"text": "A concise role description."}
    assert calls["model"] == "test:latest"
    assert calls["messages"][0]["content"].startswith("Write a role.")
    assert calls["options"] == {"num_ctx": 4096}

    assert client.post(url, json={"model": "", "prompt": "Write"}).status_code == 400
    assert client.post(url, json={"model": "test:latest", "prompt": "  "}).status_code == 400
    assert client.post(url, json={"model": "test:latest", "prompt": "a" * 20_001}).status_code == 400


def test_rule_routes_recovery_and_step_limit(client, monkeypatch):
    class FakeAgent:
        def __init__(self, model, tools, profile, session):
            self.model = model or "test"
            self.profile = profile
            self.failures = {}

        def think(self, prompt):
            context = logic_orchestration.telemetry_bus.current_context()
            assert context and current_session_id() == context[0]
            assert logic_orchestration.squad_scratchpad.get(context[0])["status"] == "running"
            if self.profile.id == "worker":
                raise RuntimeError("step failed")
            return f"{self.profile.id}: {prompt}"

    monkeypatch.setattr(logic_orchestration, "Agent", FakeAgent)
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    create_agent(client, "alpha")
    create_agent(client, "worker", "subagent")
    flow_url = "/api/squad-development/squads/demo/flow"
    flow = {"start": "alpha", "routes": [
        {"from": "alpha", "on": "success", "to": "worker"},
        {"from": "worker", "on": "failure", "to": "alpha"},
    ]}
    assert client.put(flow_url, json=flow).status_code == 200
    response = client.post("/api/squad-development/squads/demo/runs", json={"query": "Do work"})
    assert response.status_code == 202
    result = wait_for_result(client, response.json()["trace_id"])
    assert [s["agent_id"] for s in result["execution_trace"]] == ["alpha", "worker", "alpha"]
    assert result["execution_trace"][1]["status"] == "failure"
    assert "step failed" in result["execution_trace"][2]["result"]
    assert result["status"] == "error"
    assert "Step limit (3)" in result["final_answer"]

    flow["start"] = "worker"
    flow["routes"] = [{"from": "worker", "on": "failure", "to": "alpha"}]
    client.put(flow_url, json=flow)
    response = client.post("/api/squad-development/squads/demo/runs", json={"query": "Do work"})
    result = wait_for_result(client, response.json()["trace_id"])
    assert result["status"] == "success"
    assert [s["status"] for s in result["execution_trace"]] == ["failure", "success"]
    assert "step failed" in result["final_answer"]

    flow["start"] = "alpha"
    flow["routes"] = [{"from": "alpha", "on": "success", "to": "worker"}]
    client.put(flow_url, json=flow)
    response = client.post("/api/squad-development/squads/demo/runs", json={"query": "Do work"})
    result = wait_for_result(client, response.json()["trace_id"])
    assert result["status"] == "error"
    assert "step failed" in result["final_answer"]


def test_flow_rejects_duplicate_outcome_and_missing_start(client):
    client.post("/api/squad-development/squads", json={"id": "demo", "name": "Demo"})
    create_agent(client, "alpha")
    route = {"from": "alpha", "on": "success", "to": "alpha"}
    assert client.put("/api/squad-development/squads/demo/flow", json={
        "start": "alpha", "routes": [route, route]
    }).status_code == 400
    assert client.post("/api/squad-development/squads/demo/runs", json={"query": "test"}).status_code == 400
