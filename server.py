import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from codebuilder.interface_codebuilder import router as codebuilder_router
from core_engine.interface_core_engine import router as core_router
from editor.interface_editor import router as editor_router
from file_manager.interface_file_manager import router as file_manager_router
from modelProfiles.interface_modelProfiles import router as model_profiles_router
from orchestration.interface_orchestration import router as orchestration_router
from ragMemory.interface_ragMemory import router as rag_router
from scratchpadMemory.interface_scratchpadMemory import router as scratchpad_router
from squads.interface_squads import router as squads_router
from test_environment.interface_test_environment import router as test_router
from tools.interface_tools import router as tools_router
from workspace.interface_workspace import router as workspace_router

app = FastAPI(title="Novous AGENT FACTORY", version="4.0.0")


@app.on_event("startup")
def migrate_sandbox_environment():
    """Move legacy state into place, then start the background workers."""
    import threading

    from orchestration.logic_orchestration import squad_registry
    from ragMemory.interface_ragMemory import start_document_scanner
    from scratchpadMemory.logic_scratchpadMemory import code_scratchpad, scratchpad
    from test_environment.logic_test_environment import sandbox_env

    scratchpad.migrate_legacy()
    code_scratchpad.migrate_legacy()
    squad_registry.migrate_legacy()
    threading.Thread(target=sandbox_env.migrate_legacy, daemon=True).start()
    start_document_scanner()


# Mount Component Backend Routers
app.include_router(tools_router)
app.include_router(core_router)
app.include_router(editor_router)
app.include_router(workspace_router)
app.include_router(test_router)
app.include_router(codebuilder_router)
app.include_router(file_manager_router)
app.include_router(model_profiles_router)
app.include_router(orchestration_router)
app.include_router(rag_router)
app.include_router(scratchpad_router)
app.include_router(squads_router)

class RevalidateFrontendAssets:
    """Make browsers re-check pages, scripts and styles on every load, so UI updates show
    up without a hard refresh (unchanged files still come back as cheap 304s). API routes,
    including the SSE streams, pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message["headers"] = headers + [(b"cache-control", b"no-cache")]
            await send(message)

        await self.app(scope, receive, send_with_header)


app.add_middleware(RevalidateFrontendAssets)


# Serve Static Assets (CSS, JS)
app.mount("/static", StaticFiles(directory=Path("frontend") / "static"), name="static")

# Mount Page Routes
@app.get("/")
def index_page():
    return FileResponse("frontend/pages/index.html")

@app.get("/editor")
def editor_page():
    return FileResponse("frontend/pages/editor.html")

@app.get("/codebuilder")
def codebuilder_page():
    return FileResponse("frontend/pages/codeBuilder.html")

@app.get("/chat")
def chat_page():
    return FileResponse("frontend/pages/chat.html")

@app.get("/terminal")
def terminal_page():
    return FileResponse("frontend/pages/terminal.html")

@app.api_route("/test", methods=["GET", "POST"])
def test_page():
    return FileResponse("frontend/pages/testing.html")

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("PROJECT_MANAGER_HOST", "127.0.0.1")
    port = int(os.getenv("PROJECT_MANAGER_PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=True)
