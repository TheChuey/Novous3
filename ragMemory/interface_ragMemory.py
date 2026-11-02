"""RAG doorway: the only entry point into ragMemory for everything outside the package.

* ``router``: the FastAPI routes under /api/rag/*; ``server.py`` (or any FastAPI app)
  mounts it with ``app.include_router(router)``.
* Python functions for tools, orchestration, the MCP server and other applications:
  ``configure`` picks the data home, the rest delegate to the active RagMemoryService.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ragMemory import logic_ragMemory
from ragMemory.logic_ragMemory import RagMemoryService

router = APIRouter()
_service: RagMemoryService | None = None


def configure(home: str | Path | None = None) -> RagMemoryService:
    """Use the RAG config and indexes under ``home``; ``None`` restores the default home."""
    global _service
    if _service is not None:
        _service.scanner.stop_scanner()
    _service = logic_ragMemory.create_rag_memory(home) if home else None
    return get_service()


def get_service() -> RagMemoryService:
    """The active RAG service (the module default unless ``configure`` chose a home)."""
    return _service or logic_ragMemory.rag_memory


def start_document_scanner() -> bool:
    """Start automatic background indexing of the configured knowledge sources."""
    return get_service().start_document_scanner()


def list_knowledge_sources() -> list[dict[str, Any]]:
    """Every named source with its id, folder, status and indexed document/chunk counts."""
    return get_service().list_rag_bases()


def selected_source_id() -> str:
    return get_service().config.selected_knowledge_base_id()


def search_knowledge(
    query: str, top_k: int = 5, document_path: str = "", source_id: str | None = None
) -> dict[str, Any]:
    """Semantic search in one source; ``document_path`` limits it to that (absolute) file."""
    return get_service().search_knowledge_base(query, top_k, document_path, source_id)


def list_documents(source_id: str | None = None) -> list[dict[str, Any]]:
    return get_service().list_documents(source_id)


def read_document(path: str, source_id: str | None = None) -> dict[str, Any]:
    """Indexed metadata plus ``chunks_data`` (text and vectors) of one document."""
    return get_service().get_document(path, source_id)


def add_text(title: str, text: str, source_id: str | None = None) -> dict[str, Any]:
    """Embed pasted text as the document ``manual:<title>``."""
    return get_service().add_text_document(title, text, source_id)


def add_source(name: str, folder: str) -> dict[str, Any]:
    """Register a named source for an absolute folder path and select it."""
    return get_service().add_rag_base(name, folder)


def start_scan(source_id: str | None = None, force: bool = False) -> dict[str, Any]:
    """Start a background index job for one source; poll ``scan_status``."""
    return get_service().start_rag_scan(source_id, force=force)


def scan_status() -> dict[str, Any]:
    return get_service().get_rag_scan_job()


@router.get("/api/rag/config")
def api_get_rag_config():
    try:
        return get_service().get_rag_status()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/rag/documents")
def api_list_rag_documents(base_id: str | None = None):
    try:
        return get_service().get_rag_overview(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/rag/bases")
def api_list_rag_bases():
    return {"bases": get_service().list_rag_bases()}


@router.post("/api/rag/bases/{base_id}/open-folder")
def api_open_rag_base_folder(base_id: str):
    try:
        return get_service().open_rag_base_folder(base_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.post("/api/rag/bases")
def api_add_rag_base(payload: dict):
    try:
        return get_service().add_rag_base(
            str(payload.get("name", "")),
            str(payload.get("path", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/rag/bases/{base_id}")
def api_rename_rag_base(base_id: str, payload: dict):
    try:
        return get_service().rename_rag_base(base_id, str(payload.get("name", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rag/bases/{base_id}")
def api_delete_rag_base(base_id: str):
    try:
        return get_service().delete_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/rag/bases/{base_id}/select")
def api_select_rag_base(base_id: str):
    try:
        return get_service().select_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/rag/bases/{base_id}/archive")
def api_archive_rag_base(base_id: str):
    try:
        return get_service().archive_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/rag/bases/{base_id}/restore")
def api_restore_rag_base(base_id: str):
    try:
        return get_service().restore_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/rag/documents")
def api_add_rag_text(payload: dict):
    try:
        return get_service().add_text_document(
            str(payload.get("title", "")),
            str(payload.get("text", "")),
            str(payload.get("base_id") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {exc}") from exc


@router.post("/api/rag/documents/delete")
def api_delete_rag_document(payload: dict):
    try:
        return get_service().delete_document(
            str(payload.get("path", "")),
            str(payload.get("base_id") or "") or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/api/rag/config")
def api_set_rag_config(payload: dict):
    try:
        return get_service().set_rag_folder(str(payload.get("path", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rag/reindex")
def api_reindex_rag(base_id: str | None = None):
    try:
        return get_service().start_rag_scan(base_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/rag/scan")
def api_get_rag_scan():
    return get_service().get_rag_scan_job()


@router.post("/api/rag/scan/pause")
def api_pause_rag_scan():
    return get_service().pause_rag_scan()


@router.post("/api/rag/scan/stop")
def api_stop_rag_scan():
    return get_service().stop_rag_scan()


@router.post("/api/rag/scan/resume")
def api_resume_rag_scan():
    try:
        return get_service().resume_rag_scan()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rag/scan/decision")
def api_decide_rag_scan_error(payload: dict):
    try:
        return get_service().decide_rag_scan_error(str(payload.get("decision", "")))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/rag/scan/schedule")
def api_schedule_rag_scan(payload: dict):
    try:
        return get_service().schedule_rag_scan_resume(str(payload.get("resume_at", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rag/index")
def api_clear_rag_index(base_id: str | None = None):
    try:
        return get_service().clear_rag_index(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
