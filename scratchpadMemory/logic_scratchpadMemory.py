"""Scratchpad domain logic: atomic single-file storage for agent reads, workspace
maps and sub-agent results (operational short-term memory), plus the separate code
scratchpad the coding agents and the Code Development Squad share."""

import hashlib
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
    RECALL_DEFAULT_CHARS = 4000
    RECALL_MIN_CHARS = 500
    RECALL_MAX_CHARS = 20000
    INDEX_MAX_ITEMS = 15
    PREVIEW_CHARS = 80
    PAGE_BREAK_WINDOW = 200
    RECALL_TYPES = {"index", "all", "latest_read", "latest_map", "latest_subagent_result", "latest_rag_result"}
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
        return {"path": self.RELATIVE_PATH, "updated_at": entry["updated_at"], "id": self.entry_id("read", entry)}

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
        return {"path": self.RELATIVE_PATH, "updated_at": entry["updated_at"], "id": self.entry_id("map", entry)}

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
        return {"path": self.RELATIVE_PATH, "updated_at": entry["updated_at"],
                "id": self.entry_id("subagent", entry)}

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

    @staticmethod
    def _body(kind: str, entry: dict[str, Any]) -> str:
        """The text a recall pages through: file content, compact tree, or compact payload."""
        if kind == "read":
            return str(entry.get("content", ""))
        if kind == "map":
            return json.dumps(entry.get("tree", {}), ensure_ascii=False, separators=(",", ":"))
        return json.dumps(entry.get("payload", {}), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _label(kind: str, entry: dict[str, Any]) -> str:
        if kind == "read":
            return str(entry.get("path", ""))
        if kind == "map":
            return str(entry.get("requested_path", "")) or "workspace"
        return str(entry.get("subagent_id", ""))

    @classmethod
    def entry_id(cls, kind: str, entry: dict[str, Any]) -> str:
        """Stable short id from kind, time and label, so stored entries need no migration."""
        seed = f"{kind}|{entry.get('updated_at', '')}|{cls._label(kind, entry)}"
        return f"{kind}-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]}"

    def _items(self, data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Every saved entry as (kind, entry), newest first, with the latest copies de-duplicated."""
        found: dict[str, tuple[str, dict[str, Any]]] = {}

        def add(kind: str, entry: Any) -> None:
            if isinstance(entry, dict) and entry.get("updated_at"):
                found.setdefault(f"{kind}|{entry['updated_at']}", (kind, entry))

        for entry in data.get("read_history") or []:
            add("read", entry)
        add("read", data.get("latest_read"))
        add("map", data.get("latest_map"))
        for entry in data.get("subagent_history") or []:
            add("subagent", entry)
        add("subagent", data.get("latest_subagent_result") or data.get("latest_rag_result"))
        return sorted(found.values(), key=lambda pair: pair[1]["updated_at"], reverse=True)

    def _find(self, items: list[tuple[str, dict[str, Any]]], key: str) -> tuple[str, dict[str, Any]]:
        """Resolve an id exactly, else a label to its newest entry."""
        for kind, entry in items:
            if self.entry_id(kind, entry) == key:
                return kind, entry
        for kind, entry in items:
            if self._label(kind, entry) == key:
                return kind, entry
        raise ValueError(
            f"No saved item matches '{key}'. Call read_agent_scratchpad with no arguments to list saved items."
        )

    def window(self, text: str, offset: int = 0, max_chars: int = RECALL_DEFAULT_CHARS) -> dict[str, Any]:
        """Return one page of text. Pages end on a newline near the limit when possible."""
        total = len(text)
        start = min(max(offset, 0), total)
        end = min(start + max_chars, total)
        if end < total:
            newline = text.rfind("\n", max(start, end - self.PAGE_BREAK_WINDOW), end)
            if newline > start:
                end = newline + 1
        return {
            "content": text[start:end],
            "total_chars": total,
            "offset": start,
            "returned_chars": end - start,
            "truncated": end < total,
            "next_offset": end if end < total else None,
            "remaining": total - end,
        }

    @staticmethod
    def _number(value: str, name: str, default: int) -> int:
        text = str(value or "").strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{name} must be a whole number.") from exc

    def recall(self, memory_type: str = "index", item: str = "", offset: str = "0",
               max_chars: str = "4000") -> dict[str, Any]:
        """Agent-facing recall: list saved items, or return one item as a page of text."""
        if memory_type not in self.RECALL_TYPES:
            raise ValueError(
                f"memory_type must be one of {sorted(self.RECALL_TYPES)}. "
                "Call with no arguments to list saved items."
            )
        start = self._number(offset, "offset", 0)
        if start < 0:
            raise ValueError("offset must be 0 or greater.")
        size = min(max(self._number(max_chars, "max_chars", self.RECALL_DEFAULT_CHARS), self.RECALL_MIN_CHARS),
                   self.RECALL_MAX_CHARS)
        with self._lock:
            data = self._read()
        items = self._items(data)

        if item.strip():
            return self._fetch(*self._find(items, item.strip()), start, size)
        if memory_type in ("index", "all"):
            return self._index(items)
        if memory_type in ("latest_subagent_result", "latest_rag_result"):
            return self._latest_subagent(data, start, size)
        kind = "read" if memory_type == "latest_read" else "map"
        match = next(((k, e) for k, e in items if k == kind), None)
        if match is None:
            return {"kind": kind, "item": None,
                    "message": f"Nothing saved as {kind} yet. Call read_agent_scratchpad with no arguments to list saved items."}
        return self._fetch(*match, start, size)

    def _index(self, items: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        rows = []
        for kind, entry in items[: self.INDEX_MAX_ITEMS]:
            body = self._body(kind, entry)
            row = {
                "id": self.entry_id(kind, entry),
                "kind": kind,
                "label": self._label(kind, entry),
                "chars": len(body),
                "updated_at": entry.get("updated_at"),
                "preview": " ".join(body[: self.PREVIEW_CHARS * 2].split())[: self.PREVIEW_CHARS],
            }
            if kind == "read":
                row["request"] = entry.get("request", "")
            if kind == "subagent":
                row["status"] = entry.get("status", "")
            rows.append(row)
        hint = ("Call read_agent_scratchpad(item=<id>) to read one item in pages."
                if items else "Nothing is saved yet.")
        return {"items": rows, "total_items": len(items), "showing": len(rows), "hint": hint}

    def _fetch(self, kind: str, entry: dict[str, Any], offset: int, size: int) -> dict[str, Any]:
        item_id = self.entry_id(kind, entry)
        header = {"id": item_id, "kind": kind, "label": self._label(kind, entry),
                  "updated_at": entry.get("updated_at")}
        if kind == "read":
            header["request"] = entry.get("request", "")
        if kind == "subagent":
            header.update(task_type=entry.get("task_type", ""), status=entry.get("status", ""))
        page = self.window(self._body(kind, entry), offset, size)
        if page["truncated"]:
            page["hint"] = f"Continue with read_agent_scratchpad(item='{item_id}', offset='{page['next_offset']}')."
        return {**header, **page}

    def _latest_subagent(self, data: dict[str, Any], offset: int, size: int) -> dict[str, Any]:
        """Keep the legacy shape the rag-presenter handoff relies on; page it only when too large."""
        entry = data.get("latest_subagent_result", data.get("latest_rag_result", {}))
        if not isinstance(entry, dict) or not entry:
            return {"latest_subagent_result": {}}
        if offset == 0 and len(json.dumps(entry, ensure_ascii=False)) <= size:
            return {"latest_subagent_result": entry}
        page = self.window(self._body("subagent", entry), offset, size)
        windowed = {
            **{key: entry[key] for key in ("subagent_id", "task_type", "status", "updated_at") if key in entry},
            "id": self.entry_id("subagent", entry),
            "payload_format": "json_text",
            "payload": page.pop("content"),
            **page,
        }
        if windowed["truncated"]:
            windowed["hint"] = (f"Continue with read_agent_scratchpad(item='{windowed['id']}', "
                                f"offset='{windowed['next_offset']}').")
        return {"latest_subagent_result": windowed}

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
