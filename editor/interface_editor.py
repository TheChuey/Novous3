"""Editor doorway: FastAPI router for /api/file/*, /api/directory/*, /api/editor/* and /api/ws."""

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from editor.logic_editor import editor_files, session_manager
from editor.schemas_editor import EditorEventType, FileRequest, PathRequest

router = APIRouter()


# --- REST endpoints --------------------------------------------------------

@router.get("/api/file/read")
def api_read_file(path: str):
    try:
        return editor_files.read_file_content(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/file/write")
def api_write_file(req: FileRequest, session_id: str = "http"):
    try:
        result = editor_files.write_file_content(req.path, req.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session_manager.publish(EditorEventType.FILE_SAVED, req.path, session_id,
                     bytes=result["bytes_written"])
    return result


@router.post("/api/file/create")
def api_create_file(req: PathRequest, session_id: str = "http"):
    try:
        result = editor_files.create_path(req.path, req.kind)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session_manager.publish(EditorEventType.FILE_CREATED, req.path, session_id, kind=req.kind)
    return result


@router.post("/api/file/delete")
def api_delete_file(req: PathRequest, session_id: str = "http"):
    try:
        result = editor_files.delete_path(req.path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session_manager.publish(EditorEventType.FILE_DELETED, req.path, session_id)
    return result


@router.get("/api/directory/tree")
def api_directory_tree(path: str = ""):
    try:
        return editor_files.get_directory_tree(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/editor/sessions")
def api_editor_sessions():
    return {"sessions": [{"session_id": s.session_id, "client": s.client,
                          "active_path": s.active_path} for s in session_manager.sessions()],
            "events": session_manager.recent_events()}


# --- WebSocket event bus ---------------------------------------------------

@router.websocket("/api/ws")
async def api_ws(websocket: WebSocket):
    await websocket.accept()
    session = session_manager.create_session(client=websocket.client.host if websocket.client else "unknown")
    queue = session_manager.subscribe(session.session_id)
    await websocket.send_json({"type": "connected", "session_id": session.session_id,
                               "sessions": len(session_manager.sessions())})

    async def pump_events():
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)

    pump = asyncio.create_task(pump_events())
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "file_opened":
                session_manager.set_active_path(session.session_id, data.get("path", ""))
                session_manager.publish(EditorEventType.FILE_OPENED, data.get("path", ""),
                                session.session_id)
            elif msg_type == "cursor":
                session_manager.publish(EditorEventType.CURSOR_MOVED, data.get("path", ""),
                                session.session_id, line=data.get("line", 0),
                                column=data.get("column", 0))
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        session_manager.drop_session(session.session_id)
        session_manager.unsubscribe(session.session_id)
