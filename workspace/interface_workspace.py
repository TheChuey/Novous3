"""Workspace doorway: FastAPI router for /api/project, /api/health, /api/squads* and
/api/workspace/agents/*."""

from fastapi import APIRouter, HTTPException

from core_engine.logic_core_engine import agent_library
from workspace.logic_workspace import project_state, squad_manager, workspace_workflow
from workspace.schemas_workspace import ProjectRequest, ScaffoldRequest, SquadRenameRequest, SquadRequest

router = APIRouter()


@router.get("/api/project")
def api_get_project():
    state = project_state.get_project_state()
    state["agents"] = agent_library.list_agents()
    state["squads"] = squad_manager.list_squads()
    return state


@router.post("/api/project")
def api_save_project(req: ProjectRequest):
    try:
        return project_state.save_project_state(req.model_dump())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/health")
def api_health():
    return project_state.get_health()


# --- Agent scaffolding (workspace owns the canonical storage) --------------

@router.post("/api/workspace/agents/scaffold")
def api_scaffold_agent(req: ScaffoldRequest):
    try:
        return workspace_workflow.scaffold_agent(
            req.agent_id, req.name, req.description, req.mode, req.squad)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/api/workspace/agents/{agent_id}")
def api_update_agent(agent_id: str, req: dict):
    try:
        return workspace_workflow.save_agent_meta(agent_id, req)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/workspace/agents/{agent_id}/squad")
def api_agents_by_squad(squad: str = ""):
    return {"squad": squad, "agents": workspace_workflow.agents_for_squad(squad)}


# --- Squads ----------------------------------------------------------------

@router.get("/api/squads")
def api_list_squads():
    return {"squads": squad_manager.list_squads()}


@router.post("/api/squads/create")
def api_create_squad(req: SquadRequest):
    try:
        return squad_manager.create_squad(req.name, req.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/squads/rename")
def api_rename_squad(req: SquadRenameRequest):
    try:
        return squad_manager.rename_squad(req.name, req.new_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/squads/delete")
def api_delete_squad(req: SquadRequest):
    try:
        return squad_manager.delete_squad(req.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
