"""Application project settings and model-runtime health."""

import json
import time
from pathlib import Path


class ProjectStateService:
    """Read and persist project metadata retained for the project-status tool."""

    PROJECT_FILE = Path(__file__).resolve().parent.parent / "workspace" / "project.json"

    def _default_project(self) -> dict:
        return {
            "name": "Novous Workspace",
            "version": "1.0.0",
            "description": "",
            "default_model": "qwen2.5-coder:latest",
            "squad_root": "",
            "active_agent": "",
        }

    def get_project_state(self) -> dict:
        if not self.PROJECT_FILE.is_file():
            return self._default_project()
        try:
            return json.loads(self.PROJECT_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_project()

    def save_project_state(self, state: dict) -> dict:
        current = self.get_project_state()
        current.update({key: value for key, value in state.items() if value is not None})
        self.PROJECT_FILE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        return current


class SystemHealthService:
    """Report application uptime and whether the model runtime is reachable."""

    START_TIME = time.time()

    def get_health(self) -> dict:
        ollama_ok = False
        ollama_detail = "unreachable"
        try:
            import ollama
            ollama.list()
            ollama_ok = True
            ollama_detail = "connected"
        except Exception as exc:
            ollama_detail = str(exc)

        return {
            "status": "ok" if ollama_ok else "degraded",
            "ollama": {"reachable": ollama_ok, "detail": ollama_detail},
            "uptime_seconds": round(time.time() - self.START_TIME, 1),
            "timestamp": time.time(),
        }


project_state = ProjectStateService()
system_health = SystemHealthService()
