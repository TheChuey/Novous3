"""Scratchpad domain logic: atomic single-file storage for agent reads, workspace
maps and sub-agent results (operational short-term memory), plus the separate code
scratchpad the coding agents and the Code Development Squad share."""

import json
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scratchpadMemory.schemas_scratchpadMemory import (
    CodeScratchpadEntry,
    CodeScratchpadKind,
    MemoryType,
    SubAgentStatus,
)

COMPONENT_ROOT = Path(__file__).resolve().parent
LEGACY_SCRATCHPAD = COMPONENT_ROOT.parent / "agentMemory" / "data" / "scratchpad.json"
LEGACY_CODE_SCRATCHPAD = COMPONENT_ROOT.parent / "agentMemory" / "data" / "code_scratchpad.json"


def _write_json_atomic(path: Path, data: Any, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=prefix,
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


class ScratchpadService:
    """Thread-safe read/modify/write access to ``scratchpadMemory/data/scratchpad.json``."""

    DATA_DIR = COMPONENT_ROOT / "data"
    SCRATCHPAD_PATH = DATA_DIR / "scratchpad.json"
    RELATIVE_PATH = "scratchpadMemory/data/scratchpad.json"
    MAX_READ_HISTORY = 20
    SUBAGENT_STATUSES = {status.value for status in SubAgentStatus}
    MEMORY_TYPES = {memory_type.value for memory_type in MemoryType}

    def __init__(self):
        self._lock = threading.RLock()

    def migrate_legacy(self) -> bool:
        """Move a scratchpad left by the former ``agentMemory`` package, once."""
        if self.SCRATCHPAD_PATH.exists() or not LEGACY_SCRATCHPAD.is_file():
            return False
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(LEGACY_SCRATCHPAD), str(self.SCRATCHPAD_PATH))
        return True

    def _read(self) -> dict[str, Any]:
        if not self.SCRATCHPAD_PATH.is_file():
            return {}
        try:
            data = json.loads(self.SCRATCHPAD_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read agent scratchpad '{self.SCRATCHPAD_PATH}': {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Agent scratchpad must contain a JSON object: {self.SCRATCHPAD_PATH}")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        _write_json_atomic(self.SCRATCHPAD_PATH, data, ".scratchpad.")

    def store_read(self, path: str, content: str, request: str = "") -> dict[str, Any]:
        """Store the latest read and retain a bounded history of prior reads."""
        entry = {
            "path": path,
            "request": request,
            "content": content,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            data = self._read()
            history = data.get("read_history")
            if not isinstance(history, list):
                history = []
                previous = data.get("latest_read")
                if isinstance(previous, dict):
                    history.append(previous)
            history.append(entry)
            data["read_history"] = history[-self.MAX_READ_HISTORY:]
            data["latest_read"] = entry
            self._write(data)
        return {"path": self.RELATIVE_PATH, "updated_at": entry["updated_at"]}

    def store_map(self, tree: dict[str, Any], requested_path: str = "") -> dict[str, Any]:
        """Store the latest workspace tree snapshot."""
        entry = {
            "requested_path": requested_path,
            "tree": tree,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            data = self._read()
            data["latest_map"] = entry
            self._write(data)
        return {"path": self.RELATIVE_PATH, "updated_at": entry["updated_at"]}

    def store_subagent_result(self,
        subagent_id: str, task_type: str, status: str, payload: Any
    ) -> dict[str, Any]:
        """Store generic sub-agent task execution payloads in the shared agent scratchpad."""
        if status not in self.SUBAGENT_STATUSES:
            raise ValueError(
                "Sub-agent status must be one of: "
                + ", ".join(sorted(self.SUBAGENT_STATUSES))
            )
        entry = {
            "subagent_id": subagent_id,
            "task_type": task_type,
            "status": status,
            "payload": payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            data = self._read()
            history = data.get("subagent_history")
            if not isinstance(history, list):
                history = []
            history.append(entry)
            data["subagent_history"] = history[-self.MAX_READ_HISTORY:]
            data["latest_subagent_result"] = entry
            self._write(data)
        return {"path": self.RELATIVE_PATH, "updated_at": entry["updated_at"]}

    def read_memory(self, memory_type: str = "all") -> dict[str, Any]:
        """Read saved scratchpad entries with backward compatibility for legacy keys."""
        valid_types = self.MEMORY_TYPES
        if memory_type not in valid_types:
            raise ValueError(f"memory_type must be one of {valid_types}.")
        with self._lock:
            data = self._read()
        if memory_type == "all":
            return data
        if memory_type in ("latest_subagent_result", "latest_rag_result"):
            return {
                "latest_subagent_result": data.get(
                    "latest_subagent_result", data.get("latest_rag_result", {})
                )
            }
        return {memory_type: data[memory_type]} if memory_type in data else {}

    def clear_memory(self) -> dict[str, Any]:
        """Clear saved reads and maps while retaining the scratchpad file."""
        with self._lock:
            self._write({})
        return {"cleared": True, "path": self.RELATIVE_PATH}


class CodeScratchpadService:
    """Shared board for coding work: plans, applied code, test results, fixes and notes.

    The Code Development Squad posts here automatically; chat coding agents read the
    entries and leave notes the squad picks up. Kept apart from the RAG scratchpad.
    """

    DATA_DIR = COMPONENT_ROOT / "data"
    CODE_SCRATCHPAD_PATH = DATA_DIR / "code_scratchpad.json"
    RELATIVE_PATH = "scratchpadMemory/data/code_scratchpad.json"
    MAX_ENTRIES = 200
    MAX_CONTENT_CHARS = 20_000
    KINDS = {kind.value for kind in CodeScratchpadKind}

    def __init__(self):
        self._lock = threading.RLock()

    def migrate_legacy(self) -> bool:
        """Move a code scratchpad left by the former ``agentMemory`` package, once."""
        if self.CODE_SCRATCHPAD_PATH.exists() or not LEGACY_CODE_SCRATCHPAD.is_file():
            return False
        self.CODE_SCRATCHPAD_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(LEGACY_CODE_SCRATCHPAD), str(self.CODE_SCRATCHPAD_PATH))
        return True

    def _read(self) -> list[CodeScratchpadEntry]:
        path = self.CODE_SCRATCHPAD_PATH
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read code scratchpad '{path}': {exc}") from exc
        entries = data.get("entries") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def _write(self, entries: list[CodeScratchpadEntry]) -> None:
        _write_json_atomic(self.CODE_SCRATCHPAD_PATH, {"entries": entries}, ".code_scratchpad.")

    def post(self, author: str, kind: str, title: str, content: str, run_id: str = "") -> CodeScratchpadEntry:
        """Append one entry, keeping only the newest ``MAX_ENTRIES``."""
        if kind not in self.KINDS:
            raise ValueError("Scratchpad kind must be one of: " + ", ".join(sorted(self.KINDS)))
        text = str(content or "")
        entry: CodeScratchpadEntry = {
            "id": uuid.uuid4().hex[:12],
            "run_id": str(run_id or ""),
            "author": str(author or "unknown"),
            "kind": kind,
            "title": str(title or "")[:200],
            "content": text[:self.MAX_CONTENT_CHARS],
            "truncated": len(text) > self.MAX_CONTENT_CHARS,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            entries = self._read()
            entries.append(entry)
            self._write(entries[-self.MAX_ENTRIES:])
        return entry

    def entries(self, limit: int = 50, kind: str = "", author: str = "", run_id: str = "") -> list[CodeScratchpadEntry]:
        """Return matching entries, newest first."""
        with self._lock:
            items = self._read()
        matched = [
            entry
            for entry in reversed(items)
            if (not kind or entry.get("kind") == kind)
            and (not author or entry.get("author") == author)
            and (not run_id or entry.get("run_id") == run_id)
        ]
        return matched[: max(0, int(limit))]

    def latest(self, kind: str) -> CodeScratchpadEntry | None:
        found = self.entries(limit=1, kind=kind)
        return found[0] if found else None

    def count(self) -> int:
        with self._lock:
            return len(self._read())

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._write([])
        return {"cleared": True, "path": self.RELATIVE_PATH}


scratchpad = ScratchpadService()
code_scratchpad = CodeScratchpadService()
