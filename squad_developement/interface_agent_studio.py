"""Application-facing API for Squad Development."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import yaml

from core_engine.logic_agent_management import agent_management
from orchestration.logic_orchestration import RunCapacityError, run_results, squad_registry
from core_engine.logic_core_engine import tool_catalog
from squad_developement import agent_design, experimentation, flow_design, prompt_starter

router = APIRouter(prefix="/api/squad-development", tags=["Squad Development"])


class SquadRequest(BaseModel):
    id: str
    name: str


class AgentRequest(BaseModel):
    id: str
    name: str
    kind: str
    prompt: str
    tools: list[str] = Field(default_factory=list, max_length=200)


class TextRequest(BaseModel):
    content: str


class StarterChoiceRequest(BaseModel):
    variable: str
    value: str


class StarterTemplateRequest(BaseModel):
    name: str
    body: str


class StarterGenerateRequest(BaseModel):
    model: str
    prompt: str




class FlowRequest(BaseModel):
    start: str = ""
    routes: list[dict] = Field(default_factory=list)


class DraftToolRequest(BaseModel):
    content: str
    tool: str


class DraftSkillRequest(BaseModel):
    content: str
    skill_id: str


class RunRequest(BaseModel):
    query: str


def invoke(func, *args):
    try:
        return func(*args)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RunCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/template")
def generic_prompt():
    return {"prompt": invoke(agent_design.get_generic_prompt)}


@router.put("/template")
def update_generic_prompt(request: TextRequest):
    return {"prompt": invoke(agent_design.save_generic_prompt, request.content)}


@router.get("/prompt-starter/defaults")
def starter_defaults():
    return {
        "agent": invoke(agent_management.render_prompt_starter_definition),
        "subagent": invoke(squad_registry.default_subagent_template),
    }


@router.post("/prompt-starter/parse-agent")
def parse_starter_agent(request: TextRequest):
    return invoke(agent_design.parse_prompt_starter_agent, request.content)


@router.post("/format-yaml")
def format_yaml(request: TextRequest):
    return {"content": invoke(agent_design.format_pasted_yaml, request.content)}


@router.get("/tools")
def list_tools():
    return {"tools": tool_catalog.list_tools()}


@router.get("/prompt-starter/choices")
def starter_choices():
    return {"choices": invoke(prompt_starter.get_choices)}


@router.post("/prompt-starter/choices")
def add_starter_choice(request: StarterChoiceRequest):
    return {"choices": invoke(prompt_starter.add_choice, request.variable, request.value)}


@router.post("/prompt-starter/choices/delete")
def delete_starter_choice(request: StarterChoiceRequest):
    return {"choices": invoke(prompt_starter.remove_choice, request.variable, request.value)}


@router.post("/prompt-starter/generate")
def starter_generate(request: StarterGenerateRequest):
    return {"text": invoke(agent_design.generate_text, request.model, request.prompt)}


@router.get("/prompt-starter/templates")
def list_templates():
    return {"templates": invoke(prompt_starter.get_templates)}


@router.post("/prompt-starter/templates")
def save_template(request: StarterTemplateRequest):
    return {"templates": invoke(prompt_starter.save_template, request.name, request.body)}


@router.delete("/prompt-starter/templates/{name}")
def delete_template(name: str):
    return {"templates": invoke(prompt_starter.delete_template, name)}


@router.get("/tools/{name}/instructions")
def tool_instructions(name: str):
    return {"content": invoke(agent_design.tool_instructions, name)}


@router.get("/squads")
def list_squads():
    return {"squads": invoke(flow_design.list_squads)}


@router.post("/squads", status_code=201)
def create_squad(request: SquadRequest):
    return invoke(flow_design.create_squad, request.id, request.name)


@router.get("/squads/{squad_id}")
def get_squad(squad_id: str):
    return invoke(flow_design.read_squad, squad_id)


@router.post("/squads/{squad_id}/agents", status_code=201)
def create_agent(squad_id: str, request: AgentRequest):
    return invoke(agent_design.create_agent, squad_id, request.id, request.name, request.kind, request.prompt, request.tools)


@router.get("/squads/{squad_id}/agents/{agent_id}")
def get_agent(squad_id: str, agent_id: str):
    return invoke(agent_design.get_agent, squad_id, agent_id)


@router.put("/squads/{squad_id}/agents/{agent_id}")
def save_agent(squad_id: str, agent_id: str, request: TextRequest):
    return invoke(agent_design.save_agent, squad_id, agent_id, request.content)


@router.post("/squads/{squad_id}/agents/{agent_id}/draft-tool")
def add_draft_tool(squad_id: str, agent_id: str, request: DraftToolRequest):
    return {"content": invoke(agent_design.add_draft_tool, squad_id, agent_id, request.content, request.tool)}


@router.post("/squads/{squad_id}/agents/{agent_id}/draft-skill")
def add_draft_skill(squad_id: str, agent_id: str, request: DraftSkillRequest):
    return {"content": invoke(agent_design.add_draft_skill, squad_id, agent_id, request.content, request.skill_id)}


@router.get("/squads/{squad_id}/skills")
def list_skills(squad_id: str):
    return {"skills": invoke(agent_design.list_skills, squad_id)}


@router.get("/squads/{squad_id}/skills/{skill_id}")
def get_skill(squad_id: str, skill_id: str):
    return {"content": invoke(agent_design.get_skill, squad_id, skill_id)}


@router.put("/squads/{squad_id}/skills/{skill_id}")
def save_skill(squad_id: str, skill_id: str, request: TextRequest):
    return {"content": invoke(agent_design.save_skill, squad_id, skill_id, request.content)}

@router.put("/squads/{squad_id}/flow")
def save_flow(squad_id: str, request: FlowRequest):
    return invoke(flow_design.save_flow, squad_id, request.start, request.routes)


@router.get("/squads/{squad_id}/notes")
def get_notes(squad_id: str):
    return {"content": invoke(experimentation.get_notes, squad_id)}


@router.put("/squads/{squad_id}/notes")
def save_notes(squad_id: str, request: TextRequest):
    return {"content": invoke(experimentation.save_notes, squad_id, request.content)}


@router.post("/squads/{squad_id}/runs", status_code=202)
def start_run(squad_id: str, request: RunRequest):
    return invoke(experimentation.start_experiment, squad_id, request.query)


@router.get("/runs/{trace_id}")
def get_run(trace_id: str):
    result = run_results.get(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run is not complete.")
    return result
