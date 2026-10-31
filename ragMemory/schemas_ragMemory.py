"""RAG data contracts: knowledge-source configuration, index rows, scan-job state and
search results. Request bodies stay plain JSON objects (``dict``) so that the
existing HTTP validation behaviour of the /api/rag/* routes is unchanged."""

from enum import Enum
from typing import Any, TypedDict


class ScanStatus(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    AWAITING_DECISION = "awaiting_decision"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    COMPLETED_WITH_SKIPS = "completed_with_skips"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ScanDecision(str, Enum):
    RETRY = "retry"
    SKIP = "skip"
    SKIP_ALL = "skip_all"


class KnowledgeBase(TypedDict, total=False):
    id: str
    name: str
    path: str
    active: bool
    legacy_index: bool
    legacy_name_migrated: bool
    selected: bool


class RagConfig(TypedDict, total=False):
    """Shape of ``ragMemory/rag_config.json``."""
    knowledge_bases: list[KnowledgeBase]
    selected_knowledge_base: str | None


class DocumentRecord(TypedDict):
    path: str
    modified_ns: int
    size_bytes: int
    indexed_at: str
    chunks: int


class ChunkData(TypedDict):
    path: str
    chunk_index: int
    content: str
    embedding: list[float]


class ScanJobState(TypedDict, total=False):
    """Shape of ``ragMemory/data/rag_scan_job.json`` and of GET /api/rag/scan."""
    status: str
    base_id: str
    base_name: str
    current: str
    discovered: int
    completed: int
    indexed: int
    unchanged: int
    removed: list[str]
    errors: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    pending_error: dict[str, str] | None
    error_mode: str
    resume_at: str | None
    started_at: str
    finished_at: str | None
    stopped_base_ids: list[str]
    last_auto_base_id: str


class SearchHit(TypedDict):
    path: str
    chunk_index: int
    score: float
    content: str
