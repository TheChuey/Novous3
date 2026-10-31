"""Editor domain logic: sandboxed workspace file operations and the session event bus."""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from editor.schemas_editor import EditorEvent as Event, EditorEventType, EditorSessionInfo


class EditorFileOperations:
    """Workspace-relative file CRUD confined to the project root, the agent library,
    existing sub-agent YAML files and any absolute folders listed in
    ``tools/allowed_paths.json``."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    PROJECT_ROOT = REPO_ROOT / "workspace"
    LIBRARY_ROOT = REPO_ROOT / "library"
    SUBAGENT_LIBRARY_ROOT = REPO_ROOT / "subAgentLibrary"
    ALLOWED_PATHS_CONFIG = REPO_ROOT / "tools" / "allowed_paths.json"

    def _configured_write_roots(self) -> list[Path]:
        """Folders from allowed_paths.json that absolute paths may point into (re-read on every call)."""
        try:
            data = json.loads(self.ALLOWED_PATHS_CONFIG.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        roots = []
        for entry in data.get("file_manager_roots", []):
            raw = entry.get("path") if isinstance(entry, dict) else entry
            if raw:
                roots.append(Path(os.path.expandvars(str(raw))).expanduser().resolve())
        return roots

    def resolve_project_path(self, relative_path: str, root: Path | None = None) -> Path:
        if not relative_path:
            raise ValueError("A project-relative path is required.")

        clean_rel = relative_path.replace("\\", "/")
        if clean_rel == "library" or clean_rel.startswith("library/"):
            base_root = self.REPO_ROOT.resolve()
            allowed_root = self.LIBRARY_ROOT.resolve()
            candidate = (base_root / clean_rel).resolve()
            try:
                candidate.relative_to(allowed_root)
            except ValueError:
                raise ValueError(f"Access outside agent library '{allowed_root}' is forbidden.") from None
            return candidate

        if clean_rel == "subAgentLibrary" or clean_rel.startswith("subAgentLibrary/"):
            allowed_root = self.SUBAGENT_LIBRARY_ROOT.resolve()
            candidate = (self.REPO_ROOT / clean_rel).resolve()
            if candidate.parent != allowed_root or candidate.suffix.lower() != ".yaml":
                raise ValueError(
                    f"Only YAML files directly inside sub-agent library '{allowed_root}' can be edited."
                )
            return candidate

        if root is None and Path(relative_path).is_absolute():
            absolute = Path(os.path.expandvars(relative_path)).expanduser().resolve()
            allowed_roots = self._configured_write_roots()
            for allowed in allowed_roots:
                try:
                    absolute.relative_to(allowed)
                    return absolute
                except ValueError:
                    continue
            allowed_list = ", ".join(str(r) for r in allowed_roots) or "none configured"
            raise ValueError(f"Absolute path '{absolute}' is outside the allowed folders: {allowed_list}")

        base_root = (root or self.PROJECT_ROOT).resolve()
        candidate = (base_root / clean_rel).resolve()

        try:
            candidate.relative_to(base_root)
        except ValueError:
            raise ValueError(f"Access outside root path '{base_root}' is forbidden.")

        return candidate

    def read_file_content(self, relative_path: str) -> dict:
        abs_path = self.resolve_project_path(relative_path)
        if not abs_path.is_file():
            raise FileNotFoundError(f"File not found: {relative_path}")
        content = abs_path.read_text(encoding="utf-8")
        return {"path": relative_path, "content": content}

    @staticmethod
    def _is_subagent_path(relative_path: str) -> bool:
        return relative_path.replace("\\", "/").startswith("subAgentLibrary/")

    def write_file_content(self, relative_path: str, content: str) -> dict:
        abs_path = self.resolve_project_path(relative_path)
        if self._is_subagent_path(relative_path) and not abs_path.is_file():
            raise FileNotFoundError(
                f"Sub-agent definition does not exist and cannot be created here: {relative_path}"
            )
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        return {"path": relative_path, "bytes_written": len(content.encode('utf-8'))}

    def delete_path(self, relative_path: str) -> dict:
        if self._is_subagent_path(relative_path):
            raise ValueError("Sub-agent definition files cannot be deleted from the editor.")
        abs_path = self.resolve_project_path(relative_path)
        if abs_path.is_dir():
            if any(abs_path.iterdir()):
                raise IsADirectoryError(f"Directory not empty: {relative_path}")
            abs_path.rmdir()
        elif abs_path.is_file():
            abs_path.unlink()
        else:
            raise FileNotFoundError(f"Path not found: {relative_path}")
        return {"path": relative_path, "deleted": True}

    def create_path(self, relative_path: str, kind: str = "file") -> dict:
        if self._is_subagent_path(relative_path):
            raise ValueError("New sub-agent definition files cannot be created from the editor.")
        abs_path = self.resolve_project_path(relative_path)
        if abs_path.exists():
            raise FileExistsError(f"Path already exists: {relative_path}")
        if kind == "directory":
            abs_path.mkdir(parents=True)
        else:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text("", encoding="utf-8")
        return {"path": relative_path, "created": kind}

    def get_directory_tree(self, relative_path: str = "") -> dict:
        library_requested = relative_path == "library" or relative_path.startswith("library/")
        if library_requested:
            target_dir = self.resolve_project_path(relative_path)
            tree_root = self.LIBRARY_ROOT
            path_prefix = "library"
        else:
            target_dir = self.resolve_project_path(relative_path) if relative_path else self.PROJECT_ROOT
            tree_root = self.PROJECT_ROOT
            path_prefix = ""

        def _build_tree(p: Path):
            rel = str(p.relative_to(tree_root)).replace("\\", "/")
            if path_prefix:
                rel = path_prefix if rel == "." else f"{path_prefix}/{rel}"
            if p.is_file():
                return {"name": p.name, "type": "file", "path": rel}
            children = []
            for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                is_python_cache = child.is_dir() and child.name.lower() in {"__pycache__", "_pycache__"}
                if child.name.startswith(".") or is_python_cache or (
                    child.is_file() and child.suffix.lower() == ".py"
                ):
                    continue
                children.append(_build_tree(child))
            return {"name": p.name, "type": "directory", "path": rel, "children": children}

        if not target_dir.exists():
            raise FileNotFoundError(f"Directory not found: {relative_path}")
        result = _build_tree(target_dir)
        if not relative_path and self.LIBRARY_ROOT.is_dir():
            result["children"].append({
                "name": "library",
                "type": "directory",
                "path": "library",
                "children": [
                    {
                        "name": file.name,
                        "type": "file",
                        "path": f"library/{file.name}",
                    }
                    for file in sorted(self.LIBRARY_ROOT.iterdir(), key=lambda item: item.name.lower())
                    if file.is_file()
                ],
            })
        return result


class EditorSessionManager:
    """Tracks connected editor sessions and fans events out to subscribers."""

    def __init__(self):
        self._sessions: dict[str, EditorSessionInfo] = {}
        self._subscribers: dict[str, asyncio.Queue] = {}
        self._event_log: list[dict] = []
        self._max_log = 200

    # --- session lifecycle -------------------------------------------------
    def create_session(self, client: str = "unknown") -> EditorSessionInfo:
        session_id = uuid.uuid4().hex[:12]
        info = EditorSessionInfo(session_id=session_id, client=client, joined_at=time.time())
        self._sessions[session_id] = info
        self._subscribers[session_id] = asyncio.Queue()
        self.broadcast(Event(type=EditorEventType.SESSION_JOIN, path="", session_id=session_id,
                             data={"client": client}))
        return info

    def drop_session(self, session_id: str) -> None:
        if session_id in self._sessions:
            self.broadcast(Event(type=EditorEventType.SESSION_LEAVE, path="",
                                 session_id=session_id))
        self._sessions.pop(session_id, None)
        self._subscribers.pop(session_id, None)

    def set_active_path(self, session_id: str, path: str) -> None:
        if session_id in self._sessions:
            self._sessions[session_id].active_path = path

    def sessions(self) -> list[EditorSessionInfo]:
        return list(self._sessions.values())

    # --- event bus ---------------------------------------------------------
    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue = self._subscribers.get(session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._subscribers[session_id] = queue
        return queue

    def unsubscribe(self, session_id: str) -> None:
        self._subscribers.pop(session_id, None)

    def broadcast(self, event: Event) -> dict:
        payload = event.to_dict()
        self._event_log.append(payload)
        if len(self._event_log) > self._max_log:
            self._event_log = self._event_log[-self._max_log:]
        for queue in self._subscribers.values():
            queue.put_nowait(payload)
        return payload

    def publish(self, event_type: EditorEventType, path: str, session_id: str, **data) -> dict:
        return self.broadcast(Event(type=event_type, path=path, session_id=session_id, data=data or None))

    def recent_events(self, limit: int = 50) -> list[dict]:
        return self._event_log[-limit:]


# Shared doorway instances used across the application.
editor_files = EditorFileOperations()
session_manager = EditorSessionManager()
