"""RAG doorway: FastAPI router for /api/rag/* and the document-scanner startup hook."""

from fastapi import APIRouter, HTTPException

from ragMemory.logic_ragMemory import rag_memory

router = APIRouter()


def start_document_scanner() -> bool:
    """Start automatic background indexing of the configured knowledge sources."""
    return rag_memory.start_document_scanner()


@router.get("/api/rag/config")
def api_get_rag_config():
    try:
        return rag_memory.get_rag_status()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/rag/documents")
def api_list_rag_documents(base_id: str | None = None):
    try:
        return rag_memory.get_rag_overview(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/rag/bases")
def api_list_rag_bases():
    return {"bases": rag_memory.list_rag_bases()}


@router.post("/api/rag/bases/{base_id}/open-folder")
def api_open_rag_base_folder(base_id: str):
    try:
        return rag_memory.open_rag_base_folder(base_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.post("/api/rag/bases")
def api_add_rag_base(payload: dict):
    try:
        return rag_memory.add_rag_base(
            str(payload.get("name", "")),
            str(payload.get("path", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/rag/bases/{base_id}")
def api_rename_rag_base(base_id: str, payload: dict):
    try:
        return rag_memory.rename_rag_base(base_id, str(payload.get("name", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rag/bases/{base_id}")
def api_delete_rag_base(base_id: str):
    try:
        return rag_memory.delete_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/rag/bases/{base_id}/select")
def api_select_rag_base(base_id: str):
    try:
        return rag_memory.select_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/rag/bases/{base_id}/archive")
def api_archive_rag_base(base_id: str):
    try:
        return rag_memory.archive_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/rag/bases/{base_id}/restore")
def api_restore_rag_base(base_id: str):
    try:
        return rag_memory.restore_rag_base(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/rag/documents")
def api_add_rag_text(payload: dict):
    try:
        return rag_memory.add_text_document(
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
        return rag_memory.delete_document(
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
        return rag_memory.set_rag_folder(str(payload.get("path", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rag/reindex")
def api_reindex_rag(base_id: str | None = None):
    try:
        return rag_memory.start_rag_scan(base_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/rag/scan")
def api_get_rag_scan():
    return rag_memory.get_rag_scan_job()


@router.post("/api/rag/scan/pause")
def api_pause_rag_scan():
    return rag_memory.pause_rag_scan()


@router.post("/api/rag/scan/stop")
def api_stop_rag_scan():
    return rag_memory.stop_rag_scan()


@router.post("/api/rag/scan/resume")
def api_resume_rag_scan():
    try:
        return rag_memory.resume_rag_scan()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/rag/scan/decision")
def api_decide_rag_scan_error(payload: dict):
    try:
        return rag_memory.decide_rag_scan_error(str(payload.get("decision", "")))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/rag/scan/schedule")
def api_schedule_rag_scan(payload: dict):
    try:
        return rag_memory.schedule_rag_scan_resume(str(payload.get("resume_at", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/rag/index")
def api_clear_rag_index(base_id: str | None = None):
    try:
        return rag_memory.clear_rag_index(base_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
