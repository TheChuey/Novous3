"""Squads doorway: FastAPI routes for metadata squad folders."""

from fastapi import APIRouter, HTTPException

from squads.logic_squad_folders import squad_manager
from squads.schemas_squad_folders import SquadRenameRequest, SquadRequest

router = APIRouter(tags=["Squad folders"])


@router.get("/api/squads")
def api_list_squads():
    return {"squads": squad_manager.list_squads()}


@router.post("/api/squads/create")
def api_create_squad(req: SquadRequest):
    try:
        return squad_manager.create_squad(req.name, req.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/squads/rename")
def api_rename_squad(req: SquadRenameRequest):
    try:
        return squad_manager.rename_squad(req.name, req.new_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/squads/delete")
def api_delete_squad(req: SquadRequest):
    try:
        return squad_manager.delete_squad(req.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
