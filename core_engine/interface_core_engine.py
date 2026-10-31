"""Core-engine doorway: FastAPI router for /api/agents, /api/chat* and /api/models."""

import json

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from core_engine.logic_core_engine import SessionNotFoundError, agent_library, chat_service, questionnaire
from core_engine.schemas_core_engine import (
    AgentEnvironment,
    AnswerSubmissionRequest,
    ChatRequest,
    CreateAgentRequest,
    ExportSessionRequest,
)
from workspace.logic_workspace import workspace_workflow

router = APIRouter()


@router.get("/api/agents")
def api_list_agents(environment: AgentEnvironment = "workspace"):
    if environment == "workspace":
        return {"agents": chat_service.list_agents()}
    return {"agents": agent_library.list_agents(environment)}


@router.post("/api/agents/create")
def api_create_agent(req: CreateAgentRequest):
    try:
        result = workspace_workflow.scaffold_agent(
            agent_id=req.agent_id.strip(),
            name=req.name.strip(),
            description=req.description.strip(),
            mode=req.mode if req.mode in ("chat", "agent") else "chat",
            squad=req.squad.strip(),
            environment=getattr(req, "environment", "workspace"),
            environment_name=req.environment_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.delete("/api/agents/{agent_id}")
def api_delete_agent(agent_id: str, environment: str = "workspace"):
    try:
        return workspace_workflow.delete_agent(agent_id, environment)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/chat")
async def api_chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message must not be empty.")
    try:
        result = await run_in_threadpool(
            chat_service.run_single_agent, req.agent_id, req.message.strip(), req.model, req.session_id,
            req.environment,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    for event in result["tool_events"]:
        chat_service.publish_tool_event(event, result["session_id"])
    return result


@router.post("/api/chat/stream")
def api_chat_stream(req: ChatRequest):
    """Server-sent events: token / tool_event / done / error."""
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message must not be empty.")
    try:
        agent, session_key, profile = chat_service.get_agent(
            req.agent_id, req.model, req.session_id, req.environment)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def events():
        agent.tool_events = []
        try:
            prepared_message = chat_service.prepare_agent_message(agent, profile, message)
            for event in agent.tool_events:
                chat_service.publish_tool_event(event, session_key)
                yield sse({"type": "tool_event", "event": event})
            for item in agent.think_stream(prepared_message):
                if item["type"] == "tool_event":
                    chat_service.publish_tool_event(item["event"], session_key)
                elif item["type"] == "done":
                    item.update(agent_id=req.agent_id, session_id=session_key, model=agent.model)
                yield sse(item)
        except Exception as exc:
            if agent.messages and agent.messages[-1].get("role") == "user":
                agent.messages.pop()
            yield sse({"type": "error", "detail": (
                f"[engine] Could not reach the model runtime: {exc}\n"
                f"Start Ollama (e.g. `ollama serve`) and pull the model `{agent.model}`, then retry.")})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/chat/reset")
def api_chat_reset(session_id: str):
    return {"reset": chat_service.reset_session(session_id)}


@router.get("/api/chat/pending-questions")
async def api_get_pending_questions():
    """Return active Human-in-the-Loop questionnaires awaiting user input."""
    return {"pending": questionnaire.list_pending()}


@router.post("/api/chat/submit-answers")
async def api_submit_answers(req: AnswerSubmissionRequest):
    """Store user answers and resume the paused agent tool thread."""
    if not questionnaire.submit_answers(req.request_id, req.answers):
        raise HTTPException(status_code=404, detail="Question request not found or already completed.")
    return {"status": "success", "message": "Answers submitted successfully."}


@router.post("/api/chat/export")
def api_export_chat_session(req: ExportSessionRequest):
    try:
        filename, full_markdown = chat_service.export_session(req.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=full_markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/models")
def api_models():
    return chat_service.list_models()
