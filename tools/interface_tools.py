"""Tools doorway: FastAPI router for /api/tools/* (catalog, library, health, custom
tool registration and the paged workspace map)."""

from fastapi import APIRouter, HTTPException

from core_engine.logic_core_engine import tool_catalog
from tools.logic_tools import ToolConflictError, tool_registry, workspace_files
from tools.schemas_tools import MapPageRequest, RegisterToolRequest

router = APIRouter()


@router.get("/api/tools")
def api_list_tools():
    rep = tool_registry.catalog_report()
    return {
        "tools": tool_registry.list_tools(),
        "providers": tool_registry.provider_bindings(),
        "warnings": rep.get("warnings", []),
    }


@router.get("/api/tools/library")
def api_tool_library():
    return {"tools": tool_registry.list_tools_directory()}


@router.get("/api/tools/health")
def api_tools_health():
    errors = tool_catalog.get_load_errors()
    return {
        "status": "healthy" if not errors else "degraded",
        "load_errors": errors,
        "warnings": tool_registry.catalog_report().get("warnings", []),
    }


@router.post("/api/tools/register")
def api_register_tool(req: RegisterToolRequest):
    try:
        return tool_registry.register_tool(req.source, req.name)
    except ToolConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/tools/map-page")
def api_map_page(req: MapPageRequest):
    tool_catalog.load_tools()
    try:
        return workspace_files.render_map_page(req.path, req.max_depth, req.include_files, req.offset)
    except (PermissionError, ValueError, NotADirectoryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/tools/{name}")
def api_get_tool(name: str):
    tool_catalog.load_tools()
    meta = tool_catalog.registry.get(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found.")
    promoted = set(tool_registry.list_promoted_tool_names())
    return {
        "name": meta["name"],
        "provider": meta["provider"],
        "description": meta["description"],
        "signature": meta["signature"],
        "custom": meta["name"] in promoted or meta.get("custom", False),
    }


@router.delete("/api/tools/{name}")
def api_delete_tool(name: str):
    try:
        return tool_registry.remove_tool(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
