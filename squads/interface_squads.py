"""Squads doorway: FastAPI router for /api/squads-architect/* (squad blueprints, members
and per-agent tool assignment). Runs go through /api/orchestration/run/start."""

from fastapi import APIRouter, HTTPException

from squads.logic_squads import squad_architect
from squads.schemas_squads import (
    AgentPromptRequest,
    BlueprintValidation,
    MemberCreateRequest,
    SquadBlueprint,
    ToolAssignmentRequest,
)

router = APIRouter(prefix="/api/squads-architect", tags=["Squads"])


@router.get("/overview")
def get_squads_overview():
    try:
        return squad_architect.overview()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/agents/{agent_id}")
def get_squad_agent(agent_id: str):
    try:
        node = squad_architect.describe_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if node is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return node


@router.get("/agents/{agent_id}/prompt")
def get_agent_prompt(agent_id: str):
    try:
        text = squad_architect.get_agent_prompt(agent_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if text is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return {"agent_id": agent_id, "prompt": text}


@router.put("/agents/{agent_id}/prompt")
def set_agent_prompt(agent_id: str, req: AgentPromptRequest):
    try:
        return squad_architect.set_agent_prompt(agent_id, req.prompt)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/blueprints/{squad_id}")
def get_squad_blueprint(squad_id: str):
    blueprint = squad_architect.get_blueprint(squad_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail="Squad not found.")
    return blueprint


@router.post("/blueprints/validate", response_model=BlueprintValidation)
def validate_squad_blueprint(blueprint: SquadBlueprint):
    return squad_architect.validate(blueprint)


@router.post("/blueprints")
def save_squad_blueprint(blueprint: SquadBlueprint):
    try:
        return squad_architect.save_blueprint(blueprint)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/members", status_code=201)
def create_squad_member(request: MemberCreateRequest):
    try:
        return squad_architect.create_member(request.kind, request.agent_id, request.name, request.description)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tools/attach")
def attach_agent_tool(request: ToolAssignmentRequest):
    try:
        return squad_architect.attach_tool(request.agent_id, request.tool)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tools/detach")
def detach_agent_tool(request: ToolAssignmentRequest):
    try:
        return squad_architect.detach_tool(request.agent_id, request.tool)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
