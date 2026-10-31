"""Scratchpad data contracts: memory views, sub-agent statuses, stored entries and the
coding agents' shared board."""

from enum import Enum
from typing import Any, TypedDict


class SubAgentStatus(str, Enum):
    SUCCESS = "success"
    NOTHING_FOUND = "nothing_found"
    ERROR = "error"


class MemoryType(str, Enum):
    ALL = "all"
    LATEST_READ = "latest_read"
    LATEST_MAP = "latest_map"
    LATEST_SUBAGENT_RESULT = "latest_subagent_result"
    LATEST_RAG_RESULT = "latest_rag_result"


class ReadEntry(TypedDict):
    path: str
    request: str
    content: str
    updated_at: str


class MapEntry(TypedDict):
    requested_path: str
    tree: dict[str, Any]
    updated_at: str


class SubAgentEntry(TypedDict):
    subagent_id: str
    task_type: str
    status: str
    payload: Any
    updated_at: str


class ScratchpadState(TypedDict, total=False):
    latest_read: ReadEntry
    read_history: list[ReadEntry]
    latest_map: MapEntry
    latest_subagent_result: SubAgentEntry
    subagent_history: list[SubAgentEntry]


class StoredReceipt(TypedDict):
    path: str
    updated_at: str


class CodeScratchpadKind(str, Enum):
    PLAN = "plan"
    CODE = "code"
    TEST = "test"
    FIX = "fix"
    SUMMARY = "summary"
    NOTE = "note"


class CodeScratchpadEntry(TypedDict):
    id: str
    run_id: str
    author: str
    kind: str
    title: str
    content: str
    truncated: bool
    created_at: str
