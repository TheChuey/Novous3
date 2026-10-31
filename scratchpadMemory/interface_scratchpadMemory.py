"""Scratchpad doorway: FastAPI router for /api/agent-memory/scratchpad and
/api/agent-memory/code-scratchpad."""

from fastapi import APIRouter

from scratchpadMemory.logic_scratchpadMemory import code_scratchpad, scratchpad

router = APIRouter()


@router.get("/api/agent-memory/scratchpad")
def api_read_agent_scratchpad():
    """Return scratchpad data for the Chat Console."""
    return scratchpad.read_memory()


@router.delete("/api/agent-memory/scratchpad")
def api_clear_agent_scratchpad():
    """Clear saved agent reads and workspace maps."""
    return scratchpad.clear_memory()


@router.get("/api/agent-memory/code-scratchpad")
def api_read_code_scratchpad(limit: int = 200, kind: str = "", author: str = "", run_id: str = ""):
    """Return the coding agents' shared scratchpad, newest first, for the Code Builder."""
    return {
        "entries": code_scratchpad.entries(min(max(limit, 1), 200), kind, author, run_id),
        "total": code_scratchpad.count(),
    }


@router.delete("/api/agent-memory/code-scratchpad")
def api_clear_code_scratchpad():
    """Remove every coding scratchpad entry."""
    return code_scratchpad.clear()
