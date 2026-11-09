"""Scratchpad doorway: FastAPI router for /api/agent-memory/scratchpad,
/api/agent-memory/code-scratchpad, /api/agent-memory/chat-scratchpad
and /api/agent-memory/squad-scratchpads."""

from fastapi import APIRouter, HTTPException

from scratchpadMemory.logic_scratchpadMemory import (
    chat_scratchpad, code_scratchpad, scratchpad, squad_scratchpad,
)
from scratchpadMemory.schemas_scratchpadMemory import SquadScratchpadNote

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


@router.get("/api/agent-memory/chat-scratchpad")
def api_read_chat_scratchpad(session_id: str = ""):
    """Return entries from the per-session chat scratchpad, newest first."""
    if not session_id:
        return {"entries": [], "session_id": ""}
    return {"entries": chat_scratchpad.entries(session_id), "session_id": session_id}


@router.get("/api/agent-memory/chat-scratchpad/sessions")
def api_list_chat_scratchpad_sessions():
    """List session IDs that have chat scratchpad entries."""
    with chat_scratchpad._lock:
        sessions = chat_scratchpad._read()
    return {
        "sessions": [
            {"session_id": sid, "count": len(entries)}
            for sid, entries in sessions.items()
            if entries
        ]
    }


@router.delete("/api/agent-memory/chat-scratchpad")
def api_clear_chat_scratchpad(session_id: str = ""):
    """Clear a session's chat scratchpad entries."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required.")
    removed = chat_scratchpad.clear(session_id)
    return {"cleared": True, "session_id": session_id, "removed": removed}


@router.get("/api/agent-memory/squad-scratchpads")
def api_list_squad_scratchpads():
    return {"runs": squad_scratchpad.list_runs()}


@router.get("/api/agent-memory/squad-scratchpads/{run_id}")
def api_read_squad_scratchpad(run_id: str, kind: str = "", author: str = ""):
    run = squad_scratchpad.get(run_id, kind=kind, author=author)
    if run is None:
        raise HTTPException(status_code=404, detail="Squad scratchpad not found.")
    return run


@router.post("/api/agent-memory/squad-scratchpads/{run_id}/notes", status_code=201)
def api_post_squad_scratchpad_note(run_id: str, note: SquadScratchpadNote):
    try:
        return squad_scratchpad.post(run_id, note.author, "note", note.title, note.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Squad scratchpad not found.") from exc


@router.delete("/api/agent-memory/squad-scratchpads/{run_id}")
def api_clear_squad_scratchpad(run_id: str, expected_version: int | None = None):
    try:
        cleared = squad_scratchpad.clear(run_id, expected_version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not cleared:
        raise HTTPException(status_code=404, detail="Squad scratchpad not found.")
    return {"cleared": True, "run_id": run_id}
