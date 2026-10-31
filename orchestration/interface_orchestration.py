"""Orchestration doorway: FastAPI router for /api/orchestration/* (squads, runs, the
SSE telemetry stream and CodeBuilder run state)."""

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from orchestration.logic_orchestration import (
    CODE_SQUAD_ID,
    EditorBufferTooLargeError,
    RunCapacityError,
    codebuilder_run_state,
    parse_last_event_id,
    pipeline_registry,
    run_results,
    run_service,
    squad_registry,
    telemetry_bus,
)
from orchestration.schemas_orchestration import (
    OrchestrationRequest,
    OrchestrationResponse,
    SquadCreateRequest,
)

router = APIRouter(prefix="/api/orchestration", tags=["Orchestration"])


@router.get("/squads")
def get_registered_squads():
    return {"squads": squad_registry.list_squads()}


@router.get("/catalog")
def get_agent_catalog():
    return squad_registry.list_agent_catalog()


@router.post("/squads", status_code=201)
def create_registered_squad(request: SquadCreateRequest):
    try:
        squad = squad_registry.create_squad(
            request.name,
            request.description,
            request.primary_agent,
            request.subagents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"squad": squad}


@router.delete("/squads/{squad_id}")
def delete_registered_squad(squad_id: str):
    if not squad_registry.delete_squad(squad_id):
        raise HTTPException(status_code=404, detail="Squad not found.")
    return {"deleted": True, "squad_id": squad_id}


@router.get("/squads/{squad_id}/latest")
def get_latest_squad_run(squad_id: str):
    if squad_registry.get_squad(squad_id) is None:
        raise HTTPException(status_code=404, detail="Squad not found.")
    return {"result": run_results.latest_for_squad(squad_id)}


@router.post("/run", response_model=OrchestrationResponse)
def run_orchestration_endpoint(request: OrchestrationRequest):
    if request.workflow_action != "all" and request.squad_id != CODE_SQUAD_ID:
        raise HTTPException(
            status_code=400,
            detail="Plan, Build, Test, and Fix actions require code-squad.",
        )
    try:
        return pipeline_registry.run_orchestration(request)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Orchestration execution failed: {exc}"
        ) from exc


@router.post("/run/start")
def start_orchestration_run(request: OrchestrationRequest):
    try:
        return run_service.start(request)
    except EditorBufferTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RunCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/runs/{trace_id}")
def get_orchestration_run_result(trace_id: str):
    result = run_results.get(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow result not found yet.")
    return result


@router.post("/runs/{trace_id}/cancel")
def cancel_orchestration_run(trace_id: str):
    if not codebuilder_run_state.cancel(trace_id):
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    return {"cancelled": True, "trace_id": trace_id}


@router.get("/runs/{trace_id}/editor-preview")
def get_orchestration_editor_preview(trace_id: str):
    preview = codebuilder_run_state.get_preview(trace_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="No editor-buffer preview is available yet.")
    return preview


@router.get("/runs/{trace_id}/editor-execution")
def get_orchestration_editor_execution(trace_id: str):
    execution = codebuilder_run_state.get_execution(trace_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="No editor-buffer execution result is available yet.")
    return execution


@router.get("/runs/{trace_id}/scratchpad")
def get_orchestration_run_scratchpad(trace_id: str):
    result = run_results.get(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow result not found yet.")
    snapshot = result.get("scratchpad_snapshot")
    if not snapshot:
        raise HTTPException(
            status_code=404, detail="This workflow did not capture a scratchpad snapshot."
        )
    return snapshot


@router.get("/stream/{trace_id}")
def stream_orchestration_trace(
    trace_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
):
    if not telemetry_bus.has_trace(trace_id):
        raise HTTPException(status_code=404, detail="Trace not found.")
    if not telemetry_bus.has_listener_capacity(trace_id):
        raise HTTPException(status_code=429, detail="Trace stream capacity is full.")
    return StreamingResponse(
        telemetry_bus.subscribe(trace_id, max(after, parse_last_event_id(last_event_id))),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
