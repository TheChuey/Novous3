"""RAG domain logic: named knowledge sources, their isolated SQLite indexes, document
parsing/chunking/embedding, the background scanner and semantic search.

Object graph (singletons at the bottom of the module): RagDatabaseEngine is used by
RagConfigStore; both feed DocumentIndexer; DocumentScannerService drives the indexer;
RagMemoryService is the public facade over all four.
"""

import json
import logging
import math
import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import ollama

from editor.logic_editor import editor_files

LOGGER = logging.getLogger(__name__)
COMPONENT_ROOT = Path(__file__).resolve().parent
DATA_DIR = COMPONENT_ROOT / "data"
LEGACY_ROOT = COMPONENT_ROOT.parent / "agentMemory"


def migrate_legacy_layout() -> list[str]:
    """Move RAG state left by the former ``agentMemory`` package, once.

    Only paths that do not exist yet in ``ragMemory/`` are moved, so this is a
    no-op after the first start and never overwrites newer data.
    """
    moved = []
    candidates = [
        (LEGACY_ROOT / "rag_config.json", COMPONENT_ROOT / "rag_config.json"),
        (LEGACY_ROOT / "data" / "rag_indexes", DATA_DIR / "rag_indexes"),
        (LEGACY_ROOT / "data" / "rag_scan_job.json", DATA_DIR / "rag_scan_job.json"),
    ] + [
        (LEGACY_ROOT / "data" / f"knowledge.sqlite3{suffix}", DATA_DIR / f"knowledge.sqlite3{suffix}")
        for suffix in ("", "-wal", "-shm")
    ]
    for source, target in candidates:
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append(str(target))
    return moved


class _ScanStopped(Exception):
    pass


class RagDatabaseEngine:
    """SQLite CRUD for indexed documents, chunks and embedding vectors.

    Each named knowledge source has its own database file; ``use_database``
    routes the operations of the current context to that file.
    """

    DATABASE_PATH = DATA_DIR / "knowledge.sqlite3"

    def __init__(self):
        self._active_path: ContextVar[Path | None] = ContextVar("rag_database_path", default=None)

    @contextmanager
    def use_database(self, path: Path) -> Iterator[None]:
        """Route database operations in this context to a named knowledge-base index."""
        token = self._active_path.set(Path(path))
        try:
            yield
        finally:
            self._active_path.reset(token)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        database_path = self._active_path.get() or self.DATABASE_PATH
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    path TEXT PRIMARY KEY,
                    modified_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    PRIMARY KEY (path, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def replace_document(self,
        path: str,
        modified_ns: int,
        size_bytes: int,
        indexed_at: str,
        chunks: Iterable[tuple[str, list[float]]],
    ) -> None:
        """Atomically replace one indexed document and all of its chunks."""
        with self.connect() as connection:
            connection.execute("DELETE FROM documents WHERE path = ?", (path,))
            connection.execute(
                "INSERT INTO documents(path, modified_ns, size_bytes, indexed_at) VALUES (?, ?, ?, ?)",
                (path, modified_ns, size_bytes, indexed_at),
            )
            connection.executemany(
                "INSERT INTO chunks(path, chunk_index, content, embedding) VALUES (?, ?, ?, ?)",
                [
                    (path, index, content, json.dumps(vector, separators=(",", ":")))
                    for index, (content, vector) in enumerate(chunks)
                ],
            )

    def document_metadata(self, path: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT path, modified_ns, size_bytes, indexed_at FROM documents WHERE path = ?",
                (path,),
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.path, d.modified_ns, d.size_bytes, d.indexed_at, COUNT(c.chunk_index) AS chunks
                FROM documents d LEFT JOIN chunks c ON c.path = d.path
                GROUP BY d.path
                ORDER BY d.path COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, path: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM documents WHERE path = ?", (path,))
        return cursor.rowcount > 0

    def delete_documents_under(self, prefix: str, keep_paths: set[str]) -> list[str]:
        """Remove stale documents below a normalized source directory."""
        normalized_prefix = prefix.rstrip("/\\").replace("\\", "/") + "/"
        normalized_keep = {path.replace("\\", "/") for path in keep_paths}
        with self.connect() as connection:
            rows = connection.execute("SELECT path FROM documents").fetchall()
            stale = [
                row["path"] for row in rows
                if row["path"].replace("\\", "/").startswith(normalized_prefix)
                and row["path"].replace("\\", "/") not in normalized_keep
            ]
            connection.executemany("DELETE FROM documents WHERE path = ?", [(path,) for path in stale])
        return stale

    def clear_all(self) -> int:
        """Delete every indexed document and chunk; returns the document count removed."""
        with self.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM documents")
        return count

    def get_meta(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def list_chunks(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT path, chunk_index, content, embedding FROM chunks"
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "path": row["path"],
                "chunk_index": row["chunk_index"],
                "content": row["content"],
                "embedding": json.loads(row["embedding"]),
            })
        return result

    def get_document_chunks(self, path: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT path, chunk_index, content, embedding
                FROM chunks WHERE path = ? ORDER BY chunk_index
                """,
                (path,),
            ).fetchall()
        return [
            {
                "path": row["path"],
                "chunk_index": row["chunk_index"],
                "content": row["content"],
                "embedding": json.loads(row["embedding"]),
            }
            for row in rows
        ]


class RagConfigStore:
    """Configuration and persistence for named RAG knowledge bases (``rag_config.json``)."""

    CONFIG_PATH = COMPONENT_ROOT / "rag_config.json"
    DATA_DIR = DATA_DIR

    def __init__(self, database: RagDatabaseEngine):
        self.database = database

    def _read_config(self) -> dict:
        if not self.CONFIG_PATH.is_file():
            return {}
        try:
            data = json.loads(self.CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _normalized_config(self) -> dict:
        data = self._read_config()
        original = json.dumps(data, sort_keys=True)
        bases = data.get("knowledge_bases")
        if not isinstance(bases, list) or not bases:
            legacy_path = str(data.get("knowledge_base_path") or "").strip()
            if legacy_path:
                legacy_folder = Path(legacy_path)
                bases = [{
                    "id": "legacy",
                    "name": legacy_folder.name or "Legacy source",
                    "path": legacy_path,
                    "active": True,
                    "legacy_index": True,
                }]
            else:
                bases = []

        valid_bases = []
        nonlegacy_names = {
            str(entry.get("name", "")).strip().casefold()
            for entry in bases
            if isinstance(entry, dict)
            and entry.get("name")
            and not (
                str(entry.get("id")) == "default"
                or str(entry.get("name", "")).strip().casefold() == "default knowledge base"
            )
        }
        used_names = set()
        for entry in bases:
            if not isinstance(entry, dict) or not entry.get("id") or not entry.get("path"):
                continue
            base = dict(entry)
            path = str(base["path"])
            legacy_name = str(base.get("name", "")).strip().casefold() == "default knowledge base"
            legacy_source = str(base.get("id")) == "default" or legacy_name
            if legacy_source and not base.get("legacy_name_migrated"):
                folder_name = Path(path).name.strip() or "Legacy source"
                base["name"] = folder_name
                if folder_name.casefold() in used_names or folder_name.casefold() in nonlegacy_names:
                    base["name"] = f"{folder_name} (legacy index)"
                base["legacy_name_migrated"] = True
            base["name"] = " ".join(str(base.get("name") or Path(path).name or "Knowledge source").split())
            if base["name"].casefold() in used_names:
                suffix = 2
                while f"{base['name']} ({suffix})".casefold() in used_names:
                    suffix += 1
                base["name"] = f"{base['name']} ({suffix})"
            used_names.add(base["name"].casefold())
            valid_bases.append(base)
        bases = valid_bases
        data["knowledge_bases"] = bases
        data.pop("knowledge_base_path", None)

        valid_ids = {
            str(base.get("id"))
            for base in bases
            if isinstance(base, dict) and base.get("id")
        }
        selected = str(data.get("selected_knowledge_base") or "")
        active_ids = [
            str(base["id"]) for base in bases if base.get("active", True)
        ]
        if selected not in valid_ids or not any(
            str(base["id"]) == selected and base.get("active", True)
            for base in bases
        ):
            data["selected_knowledge_base"] = next(
                iter(active_ids),
                None,
            )
        if json.dumps(data, sort_keys=True) != original:
            self._write_config(data)
        return data

    def _write_config(self, data: dict) -> None:
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=self.CONFIG_PATH.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.CONFIG_PATH)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def configured_path(self) -> str:
        """Return the selected named source folder."""
        return str(self.get_knowledge_base()["path"])

    def knowledge_base_path(self, base_id: str | None = None) -> Path:
        """Return the selected or specified knowledge-base folder."""
        base = self.get_knowledge_base(base_id)
        return Path(str(base["path"])).expanduser().resolve()

    def get_knowledge_base(self, base_id: str | None = None) -> dict:
        data = self._normalized_config()
        key = base_id or data.get("selected_knowledge_base")
        if not key:
            raise ValueError("No knowledge source exists. Add a named source first.")
        for base in data["knowledge_bases"]:
            if isinstance(base, dict) and str(base.get("id")) == key:
                return dict(base)
        raise ValueError(f"Unknown knowledge base: {key}")

    def list_knowledge_bases(self, include_archived: bool = True) -> list[dict]:
        data = self._normalized_config()
        bases = [
            dict(base) for base in data["knowledge_bases"]
            if isinstance(base, dict) and (include_archived or base.get("active", True))
        ]
        selected = data["selected_knowledge_base"]
        for base in bases:
            base["selected"] = str(base.get("id")) == selected
        return bases

    def selected_knowledge_base_id(self) -> str:
        selected = self._normalized_config().get("selected_knowledge_base")
        if not selected:
            raise ValueError("No knowledge source exists. Add a named source first.")
        return str(selected)

    def database_path(self, base_id: str | None = None) -> Path | None:
        base = self.get_knowledge_base(base_id)
        if base.get("legacy_index"):
            return None
        return self.DATA_DIR / "rag_indexes" / f"{base['id']}.sqlite3"

    def database_context(self, base_id: str | None = None):
        """Select the isolated index for one named source."""
        path = self.database_path(base_id)
        if path is None:
            return nullcontext()
        return self.database.use_database(path)

    def select_knowledge_base(self, base_id: str) -> dict:
        data = self._normalized_config()
        base = next(
            (
                entry for entry in data["knowledge_bases"]
                if isinstance(entry, dict) and str(entry.get("id")) == str(base_id)
            ),
            None,
        )
        if base is None or not base.get("active", True):
            raise ValueError(f"Active knowledge base not found: {base_id}")
        data["selected_knowledge_base"] = str(base_id)
        self._write_config(data)
        return dict(base)

    def add_knowledge_base(self, name: str, raw_path: str) -> dict:
        clean_name = " ".join(str(name or "").split())
        raw = str(raw_path or "").strip().strip('"')
        if not clean_name:
            raise ValueError("A knowledge-base name is required.")
        if clean_name.casefold() == "default knowledge base":
            raise ValueError("The default knowledge-base name is reserved; choose a branch name.")
        if not raw:
            raise ValueError("A knowledge-base folder path is required.")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError("The RAG folder path must be absolute.")
        path = path.resolve()
        data = self._normalized_config()
        if any(
            str(base.get("name", "")).casefold() == clean_name.casefold()
            for base in data["knowledge_bases"]
        ):
            raise ValueError(f"A knowledge source named '{clean_name}' already exists.")
        base = {
            "id": uuid.uuid4().hex,
            "name": clean_name,
            "path": str(path),
            "active": True,
            "legacy_index": False,
        }
        data["knowledge_bases"].append(base)
        data["selected_knowledge_base"] = base["id"]
        self._write_config(data)
        return base

    def rename_knowledge_base(self, base_id: str, name: str) -> dict:
        clean_name = " ".join(str(name or "").split())
        if not clean_name:
            raise ValueError("A knowledge-source name is required.")
        if clean_name.casefold() == "default knowledge base":
            raise ValueError("The default knowledge-base name is reserved; choose a branch name.")
        data = self._normalized_config()
        base = next(
            (
                entry for entry in data["knowledge_bases"]
                if str(entry.get("id")) == str(base_id)
            ),
            None,
        )
        if base is None:
            raise ValueError(f"Unknown knowledge base: {base_id}")
        if any(
            str(entry.get("id")) != str(base_id)
            and str(entry.get("name", "")).casefold() == clean_name.casefold()
            for entry in data["knowledge_bases"]
        ):
            raise ValueError(f"A knowledge source named '{clean_name}' already exists.")
        base["name"] = clean_name
        self._write_config(data)
        return dict(base)

    def delete_knowledge_base(self, base_id: str) -> dict:
        data = self._normalized_config()
        base = next(
            (
                entry for entry in data["knowledge_bases"]
                if str(entry.get("id")) == str(base_id)
            ),
            None,
        )
        if base is None:
            raise ValueError(f"Unknown knowledge base: {base_id}")
        data["knowledge_bases"].remove(base)
        if data.get("selected_knowledge_base") == str(base_id):
            data["selected_knowledge_base"] = next(
                (
                    str(entry["id"])
                    for entry in data["knowledge_bases"]
                    if entry.get("active", True)
                ),
                None,
            )
        self._write_config(data)
        return dict(base)

    def archive_knowledge_base(self, base_id: str) -> dict:
        data = self._normalized_config()
        base = next(
            (
                entry for entry in data["knowledge_bases"]
                if isinstance(entry, dict) and str(entry.get("id")) == str(base_id)
            ),
            None,
        )
        if base is None:
            raise ValueError(f"Unknown knowledge base: {base_id}")
        base["active"] = False
        if data["selected_knowledge_base"] == str(base_id):
            data["selected_knowledge_base"] = next(
                (
                    str(item["id"])
                    for item in data["knowledge_bases"]
                    if item.get("active", True)
                ),
                None,
            )
        self._write_config(data)
        return dict(base)

    def restore_knowledge_base(self, base_id: str) -> dict:
        data = self._normalized_config()
        base = next(
            (
                entry for entry in data["knowledge_bases"]
                if isinstance(entry, dict) and str(entry.get("id")) == str(base_id)
            ),
            None,
        )
        if base is None:
            raise ValueError(f"Unknown knowledge base: {base_id}")
        base["active"] = True
        data["selected_knowledge_base"] = str(base_id)
        self._write_config(data)
        return dict(base)

    def set_knowledge_base_path(self, raw_path: str) -> Path:
        """Change the selected named source folder."""
        raw = str(raw_path or "").strip().strip('"')
        if not raw:
            raise ValueError("A knowledge-source folder path is required.")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError("The RAG folder path must be absolute.")
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(f"The RAG folder does not exist or is not a directory: {path}")
        data = self._normalized_config()
        selected = data.get("selected_knowledge_base")
        if not selected:
            raise ValueError("Select or add a named knowledge source first.")
        base = next(item for item in data["knowledge_bases"] if str(item["id"]) == selected)
        base["path"] = str(path)
        self._write_config(data)
        return path


class DocumentIndexer:
    """Document discovery, parsing, chunking and local Ollama embedding generation."""

    LEGACY_PREFIX = "workspace/Knowledge Base"
    ROOT_META_KEY = "knowledge_root"
    MANUAL_PREFIX = "manual:"
    EMBEDDING_MODEL = os.environ.get("NOVOUS_EMBEDDING_MODEL", "nomic-embed-text:latest")
    MAX_DOCUMENT_BYTES = 100_000_000
    CHUNK_CHARACTERS = 1400
    CHUNK_OVERLAP = 200
    EMBEDDING_BATCH_SIZE = 16
    SCAN_FILE_DELAY_SECONDS = max(
        0.0, float(os.environ.get("NOVOUS_RAG_SCAN_DELAY_SECONDS", "0.15"))
    )
    TEXT_EXTENSIONS = {
        ".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".csv",
        ".json", ".xml", ".yaml", ".yml", ".log",
        ".py", ".js", ".ts", ".css", ".toml", ".ini", ".sql", ".sh", ".ps1",
    }
    DOCLING_EXTENSIONS = {".pdf", ".docx", ".pptx", ".odt"}

    def __init__(self, config: RagConfigStore, database: RagDatabaseEngine):
        self.config = config
        self.database = database
        self._doc_converter = None
        self.scan_lock = threading.RLock()
        # Set when the background scanner stops; also interrupts per-file throttling.
        self.stop_event = threading.Event()

    def _document_text(self, path: Path) -> str:
        if path.suffix.lower() in self.TEXT_EXTENSIONS:
            return path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in self.DOCLING_EXTENSIONS:
            if self._doc_converter is None:
                try:
                    from docling.document_converter import DocumentConverter
                except ImportError as exc:
                    if path.suffix.lower() == ".pdf":
                        return self._pdf_text_fallback(path, exc)
                    raise RuntimeError(
                        "Docling is required to index PDF, DOCX, PPTX, and ODT files."
                    ) from exc
                self._doc_converter = DocumentConverter()
            try:
                result = self._doc_converter.convert(str(path))
            except ImportError as exc:
                if path.suffix.lower() == ".pdf":
                    return self._pdf_text_fallback(path, exc)
                raise
            return result.document.export_to_markdown()
        raise ValueError(f"Unsupported knowledge-base file type: {path.suffix or '(no extension)'}")

    def _pdf_text_fallback(self, path: Path, docling_error: ImportError) -> str:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError(
                "Docling's PDF parser could not load and the PyMuPDF fallback is unavailable."
            ) from exc

        LOGGER.warning("Docling PDF parser unavailable; using PyMuPDF for %s: %s", path, docling_error)
        with fitz.open(path) as document:
            text = "\n\n".join(page.get_text("text") for page in document)
        if not text.strip():
            raise RuntimeError(
                f"No selectable text could be extracted from {path}; scanned PDFs require a working OCR parser."
            )
        return text

    def _chunk_text(self, text: str) -> list[str]:
        clean = text.strip()
        if not clean:
            return []
        chunks = []
        start = 0
        while start < len(clean):
            end = min(start + self.CHUNK_CHARACTERS, len(clean))
            if end < len(clean):
                boundary = clean.rfind("\n\n", start, end)
                if boundary > start + self.CHUNK_CHARACTERS // 2:
                    end = boundary
            chunk = clean[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(clean):
                break
            start = max(start + 1, end - self.CHUNK_OVERLAP)
        return chunks

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for offset in range(0, len(texts), self.EMBEDDING_BATCH_SIZE):
            response = ollama.embed(model=self.EMBEDDING_MODEL, input=texts[offset:offset + self.EMBEDDING_BATCH_SIZE])
            batch = response.get("embeddings") if isinstance(response, dict) else response.embeddings
            if len(batch) != len(texts[offset:offset + self.EMBEDDING_BATCH_SIZE]):
                raise RuntimeError("Embedding model returned a different number of vectors than requested.")
            vectors.extend([[float(value) for value in vector] for vector in batch])
        if vectors and any(len(vector) != len(vectors[0]) for vector in vectors):
            raise RuntimeError("Embedding model returned vectors with inconsistent dimensions.")
        return vectors

    def index_file(self, path: Path, relative_path: str | None = None, force: bool = False) -> dict:
        """Extract, embed, and persist one supported document."""
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Knowledge-base document not found: {resolved}")
        stat = resolved.stat()
        if stat.st_size > self.MAX_DOCUMENT_BYTES:
            raise ValueError(f"Document exceeds the {self.MAX_DOCUMENT_BYTES}-byte indexing limit: {resolved}")
        path_key = relative_path or resolved.as_posix()
        existing = self.database.document_metadata(path_key)
        if not force and existing and (
            existing["modified_ns"] == stat.st_mtime_ns and existing["size_bytes"] == stat.st_size
        ):
            return {"path": path_key, "status": "unchanged", "chunks": 0}

        chunks = self._chunk_text(self._document_text(resolved))
        embeddings = self._embed(chunks)
        self.database.replace_document(
            path_key,
            stat.st_mtime_ns,
            stat.st_size,
            datetime.now(timezone.utc).isoformat(),
            zip(chunks, embeddings),
        )
        return {"path": path_key, "status": "indexed", "chunks": len(chunks)}

    def index_text(self, title: str, text: str) -> dict:
        """Embed and store pasted text under a manual:<title> key, replacing any entry with that title."""
        clean_title = " ".join(str(title or "").split())
        if not clean_title:
            raise ValueError("A title is required.")
        chunks = self._chunk_text(str(text or ""))
        if not chunks:
            raise ValueError("Text is required.")
        path_key = f"{self.MANUAL_PREFIX}{clean_title}"
        embeddings = self._embed(chunks)
        self.database.initialize()
        self.database.replace_document(
            path_key,
            0,
            len(text.encode("utf-8")),
            datetime.now(timezone.utc).isoformat(),
            zip(chunks, embeddings),
        )
        return {"path": path_key, "status": "indexed", "chunks": len(chunks)}

    def knowledge_base_folder(self, base_id: str | None = None) -> Path:
        """The selected or specified Knowledge Base folder."""
        return self.config.knowledge_base_path(base_id)

    def _purge_previous_folder(self, root: Path) -> list[str]:
        """Remove documents indexed from a different folder than the current one."""
        root_key = root.as_posix()
        previous = self.database.get_meta(self.ROOT_META_KEY)
        removed: list[str] = []
        if previous != root_key:
            if previous:
                removed += self.database.delete_documents_under(previous, set())
            removed += self.database.delete_documents_under(self.LEGACY_PREFIX, set())
            self.database.set_meta(self.ROOT_META_KEY, root_key)
        return removed

    def scan_knowledge_base(self,
        force: bool = False,
        base_id: str | None = None,
        progress=None,
        pause_point=None,
        error_decider=None,
    ) -> dict:
        """Index supported files in one knowledge base and remove stale records."""
        with self.scan_lock:
            with self.config.database_context(base_id):
                return self._scan_knowledge_base(
                    force, base_id, progress, pause_point, error_decider
                )

    def _scan_knowledge_base(self,
        force: bool, base_id: str | None, progress, pause_point, error_decider=None
    ) -> dict:
        root = self.knowledge_base_folder(base_id)
        if not root.is_dir():
            raise FileNotFoundError(f"The RAG folder does not exist: {root}")
        self.database.initialize()
        removed = self._purge_previous_folder(root)
        files = []
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink() or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if path.suffix.lower() not in self.TEXT_EXTENSIONS | self.DOCLING_EXTENSIONS:
                continue
            files.append(path)

        results = []
        skipped = []
        if progress:
            progress(discovered=len(files), current="")
        for index, path in enumerate(files, start=1):
            if pause_point:
                pause_point()
            if progress:
                progress(current=str(path), completed=index - 1)
            key = path.resolve().as_posix()
            while True:
                try:
                    results.append(self.index_file(path, key, force=force))
                    break
                except Exception as exc:
                    LOGGER.exception("Could not index knowledge-base document %s", path)
                    decision = error_decider(path, exc) if error_decider else "error"
                    if decision == "retry":
                        continue
                    failed = {"path": key, "status": "error", "error": str(exc)}
                    if decision in {"skip", "skip_all"}:
                        skipped.append({**failed, "status": "skipped"})
                        results.append({"path": key, "status": "skipped"})
                    else:
                        results.append(failed)
                    break
            if progress:
                    progress(
                    completed=index,
                    indexed=sum(item["status"] == "indexed" for item in results),
                    unchanged=sum(item["status"] == "unchanged" for item in results),
                    errors=[item for item in results if item["status"] == "error"],
                    skipped=skipped,
                    current="",
                )
            if self.SCAN_FILE_DELAY_SECONDS:
                self.stop_event.wait(self.SCAN_FILE_DELAY_SECONDS)

        if pause_point:
            pause_point()
        keep = {path.resolve().as_posix() for path in files}
        stale = self.database.delete_documents_under(root.as_posix(), keep)
        return {
            "folder": str(root),
            "discovered": len(files),
            "indexed": sum(result["status"] == "indexed" for result in results),
            "unchanged": sum(result["status"] == "unchanged" for result in results),
            "errors": [result for result in results if result["status"] == "error"],
            "skipped": skipped,
            "removed": removed + stale,
        }

    def refresh_index(self, force: bool = False) -> dict:
        """Synchronously scan the knowledge-base folder and return indexing results."""
        return self.scan_knowledge_base(force=force)


class DocumentScannerService:
    """Persistent, pausable background indexing job plus the periodic scanner loop."""

    JOB_PATH = DATA_DIR / "rag_scan_job.json"
    SCAN_INTERVAL_SECONDS = 60

    def __init__(self, config: "RagConfigStore", indexer: "DocumentIndexer"):
        self.config = config
        self.indexer = indexer
        self._job_lock = threading.RLock()
        self._job_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._pause_requested = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._error_decision_event = threading.Event()
        self._error_decision = None
        self._scanner_lock = threading.Lock()
        self._scanner_thread: threading.Thread | None = None
        self._job = self._load_job()

    def _load_job(self) -> dict:
        """Restore the persisted job; an interrupted run comes back paused or stopped."""
        try:
            job = json.loads(self.JOB_PATH.read_text(encoding="utf-8"))
            if job.get("status") in {"running", "awaiting_decision"}:
                job["status"] = "paused"
            elif job.get("status") == "stopping":
                job["status"] = "stopped"
                job["resume_at"] = None
                job["current"] = ""
                job["pending_error"] = None
            if job.get("status") == "stopped" and job.get("base_id"):
                stopped_base_ids = set(job.get("stopped_base_ids", []))
                stopped_base_ids.add(job["base_id"])
                job["stopped_base_ids"] = sorted(stopped_base_ids)
        except (OSError, json.JSONDecodeError, AttributeError):
            job = {"status": "idle"}
        return job

    def _save_job(self) -> None:
        self.JOB_PATH.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=self.JOB_PATH.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self._job, handle, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.JOB_PATH)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def get_scan_job(self) -> dict:
        with self._job_lock:
            return dict(self._job)

    def _update_job(self, **changes) -> None:
        with self._job_lock:
            self._job.update(changes)
            self._save_job()

    def _mark_scan_stopped(self) -> None:
        with self._job_lock:
            base_id = self._job.get("base_id")
            stopped_base_ids = set(self._job.get("stopped_base_ids", []))
            if base_id:
                stopped_base_ids.add(base_id)
            self._job.update(
                status="stopped",
                current="",
                pending_error=None,
                resume_at=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
                stopped_base_ids=sorted(stopped_base_ids),
            )
            self._save_job()

    def _pause_point(self) -> None:
        if self._stop_requested.is_set():
            raise _ScanStopped()
        if self._pause_requested.is_set():
            self._update_job(status="paused", current="")
            self._resume_event.wait()
            if self._stop_requested.is_set():
                raise _ScanStopped()
            self._pause_requested.clear()
            self._update_job(status="running")

    def decide_scan_error(self, decision: str) -> dict:
        """Apply the user's choice for a document that failed during indexing."""
        if decision not in {"retry", "skip", "skip_all"}:
            raise ValueError("Choose retry, skip, or skip_all.")
        with self._job_lock:
            if self._job.get("status") != "awaiting_decision":
                raise ValueError("There is no indexing error waiting for a decision.")
            if decision == "skip_all":
                self._job["error_mode"] = "skip_all"
            self._error_decision = decision
            self._job["status"] = "running"
            self._error_decision_event.set()
            self._save_job()
            return dict(self._job)

    def _await_error_decision(self, path: Path, error: Exception) -> str:
        if self._stop_requested.is_set():
            raise _ScanStopped()
        with self._job_lock:
            if self._stop_requested.is_set():
                raise _ScanStopped()
            if self._job.get("error_mode") == "skip_all":
                return "skip"
            self._error_decision = None
            self._error_decision_event.clear()
            self._job.update(
                status="awaiting_decision",
                current="",
                pending_error={"path": str(path), "error": str(error)},
            )
            self._save_job()
        self._error_decision_event.wait()
        if self._stop_requested.is_set():
            raise _ScanStopped()
        with self._job_lock:
            decision = self._error_decision
            self._error_decision = None
            self._job.pop("pending_error", None)
            self._save_job()
        return decision or "skip"

    def _run_scan_job(self, base_id: str, force: bool) -> None:
        try:
            self._update_job(
                base_id=base_id,
                status="stopping" if self._stop_requested.is_set() else "running",
                current="",
                discovered=0,
                completed=0,
                indexed=0,
                unchanged=0,
                errors=[],
                skipped=[],
                pending_error=None,
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=None,
            )

            def update_progress(**changes):
                self._update_job(**changes)

            result = self.indexer.scan_knowledge_base(
                force=force,
                base_id=base_id,
                progress=update_progress,
                pause_point=self._pause_point,
                error_decider=self._await_error_decision,
            )
            status = (
                "completed_with_errors" if result["errors"]
                else "completed_with_skips" if result.get("skipped")
                else "completed"
            )
            self._update_job(
                status=status,
                current="",
                removed=result["removed"],
                errors=result["errors"],
                skipped=result.get("skipped", []),
                pending_error=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        except _ScanStopped:
            self._mark_scan_stopped()
        except FileNotFoundError as exc:
            LOGGER.warning("Knowledge-base scan skipped: %s", exc)
            self._update_job(
                status="unavailable",
                current="",
                errors=[{"error": str(exc)}],
                pending_error=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            LOGGER.exception("Knowledge-base scan failed.")
            self._update_job(
                status="failed",
                current="",
                errors=[{"error": str(exc)}],
                pending_error=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._job_thread = None

    def start_scan_job(self,
        base_id: str | None = None,
        force: bool = False,
        resume_paused: bool = False,
    ) -> dict:
        """Start or resume a persistent background scan for a knowledge base."""
        selected_id = base_id or self.config.selected_knowledge_base_id()
        if not self.config.get_knowledge_base(selected_id).get("active", True):
            raise ValueError("Restore the archived knowledge base before indexing it.")
        root = self.indexer.knowledge_base_folder(selected_id)
        if not root.is_dir():
            message = f"The RAG folder does not exist: {root}"
            self._update_job(
                base_id=selected_id,
                status="unavailable",
                current="",
                errors=[{"error": message}],
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            raise FileNotFoundError(message)
        with self._job_lock:
            if self._job_thread and self._job_thread.is_alive():
                if self._job.get("base_id") != selected_id:
                    raise RuntimeError("Another knowledge-base scan is already running.")
                return dict(self._job)
            if self._job.get("status") == "paused":
                if self._job.get("base_id") != selected_id:
                    raise RuntimeError("Resume or finish the paused knowledge-base scan first.")
                if not resume_paused:
                    return dict(self._job)
            stopped_base_ids = set(self._job.get("stopped_base_ids", []))
            stopped_base_ids.discard(selected_id)
            self._job["stopped_base_ids"] = sorted(stopped_base_ids)
            self._stop_requested.clear()
            self._pause_requested.clear()
            self._resume_event.set()
            self._job["resume_at"] = None
            error_mode = (
                self._job.get("error_mode", "ask")
                if resume_paused
                else "ask"
            )
            self._error_decision_event.clear()
            self._job_thread = threading.Thread(
                target=self._run_scan_job,
                args=(selected_id, force),
                name="agent-memory-rag-scan",
                daemon=True,
            )
            self._job.update(status="queued", base_id=selected_id, error_mode=error_mode)
            self._save_job()
            self._job_thread.start()
            return dict(self._job)

    def pause_scan_job(self) -> dict:
        with self._job_lock:
            if self._job.get("status") not in {"running", "queued"}:
                return dict(self._job)
            self._pause_requested.set()
            self._resume_event.clear()
            self._job["status"] = "pausing"
            self._save_job()
            return dict(self._job)

    def stop_scan_job(self) -> dict:
        """Stop the current scan after its current file finishes."""
        with self._job_lock:
            status = self._job.get("status")
            if status not in {
                "queued", "running", "pausing", "paused", "awaiting_decision", "stopping"
            }:
                return dict(self._job)
            self._stop_requested.set()
            self._pause_requested.clear()
            self._resume_event.set()
            self._error_decision_event.set()
            self._job["resume_at"] = None
            thread = self._job_thread
            if thread is None or not thread.is_alive():
                self._mark_scan_stopped()
            else:
                self._job["status"] = "stopping"
                self._save_job()
            return dict(self._job)

    def resume_scan_job(self) -> dict:
        with self._job_lock:
            base_id = self._job.get("base_id") or self.config.selected_knowledge_base_id()
            if not self.config.get_knowledge_base(base_id).get("active", True):
                raise RuntimeError("Restore the archived knowledge base before resuming indexing.")
            if self._job.get("status") == "paused" and self._job_thread and self._job_thread.is_alive():
                self._pause_requested.clear()
                self._resume_event.set()
                self._job["status"] = "running"
                self._job["resume_at"] = None
                self._save_job()
                return dict(self._job)
        return self.start_scan_job(base_id, resume_paused=True)

    def schedule_scan_resume(self, resume_at: str) -> dict:
        try:
            scheduled = datetime.fromisoformat(resume_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("A valid scheduled resume time is required.") from exc
        if scheduled.tzinfo is None:
            raise ValueError("The scheduled resume time must include a timezone.")
        scheduled = scheduled.astimezone(timezone.utc)
        if scheduled <= datetime.now(timezone.utc):
            raise ValueError("The scheduled resume time must be in the future.")
        with self._job_lock:
            if self._job.get("status") != "paused":
                raise ValueError("Pause indexing before scheduling its resume.")
            self._job["resume_at"] = scheduled.isoformat()
            self._save_job()
            return dict(self._job)

    def _scanner_loop(self) -> None:
        while not self.indexer.stop_event.is_set():
            try:
                job = self.get_scan_job()
                if job.get("status") == "paused" and job.get("resume_at"):
                    try:
                        resume_at = datetime.fromisoformat(job["resume_at"].replace("Z", "+00:00"))
                        if resume_at <= datetime.now(timezone.utc):
                            self.resume_scan_job()
                    except ValueError:
                        LOGGER.warning("Ignoring invalid scheduled RAG resume time: %s", job["resume_at"])
                elif job.get("status") not in {
                    "running", "queued", "pausing", "paused", "stopping", "awaiting_decision"
                }:
                    bases = self.config.list_knowledge_bases(include_archived=False)
                    stopped_base_ids = set(job.get("stopped_base_ids", []))
                    base_ids = [
                        str(base["id"]) for base in bases
                        if str(base["id"]) not in stopped_base_ids
                    ]
                    if base_ids:
                        last_id = job.get("last_auto_base_id")
                        if last_id in base_ids:
                            next_index = (base_ids.index(last_id) + 1) % len(base_ids)
                            next_id = base_ids[next_index]
                        else:
                            next_id = self.config.selected_knowledge_base_id()
                            if next_id not in base_ids:
                                next_id = base_ids[0]
                        self._update_job(last_auto_base_id=next_id)
                        self.start_scan_job(next_id)
            except FileNotFoundError as exc:
                LOGGER.warning(
                    "Knowledge-base folder is unavailable; skipping this scan: %s. "
                    "Check the configured path or reconnect the drive. The scanner will retry in %d seconds.",
                    exc,
                    self.SCAN_INTERVAL_SECONDS,
                )
            except Exception:
                LOGGER.exception("Knowledge-base scan failed.")
            self.indexer.stop_event.wait(self.SCAN_INTERVAL_SECONDS)

    def start_scanner(self) -> bool:
        """Start one background scan loop; performs a scan immediately."""
        with self._scanner_lock:
            if self._scanner_thread and self._scanner_thread.is_alive():
                return False
            self.indexer.stop_event.clear()
            self._scanner_thread = threading.Thread(
                target=self._scanner_loop,
                name="agent-memory-document-indexer",
                daemon=True,
            )
            self._scanner_thread.start()
        return True

    def stop_scanner(self) -> None:
        self.indexer.stop_event.set()


class RagMemoryService:
    """Public facade over the knowledge base used by routers, tools and orchestration."""

    def __init__(
        self,
        config: RagConfigStore,
        database: RagDatabaseEngine,
        indexer: DocumentIndexer,
        scanner: DocumentScannerService,
    ):
        self.config = config
        self.database = database
        self.indexer = indexer
        self.scanner = scanner

    def list_documents(self, base_id: str | None = None) -> list[dict[str, Any]]:
        """List documents currently indexed in the SQLite knowledge base."""
        with self.config.database_context(base_id):
            self.database.initialize()
            return self.database.list_documents()

    def get_document(self, path: str, base_id: str | None = None) -> dict[str, Any]:
        """Read the indexed chunks for one document."""
        with self.config.database_context(base_id):
            self.database.initialize()
            metadata = next((item for item in self.database.list_documents() if item["path"] == path), None)
            if metadata is None:
                raise FileNotFoundError(f"Knowledge-base document is not indexed: {path}")
            metadata["chunks_data"] = self.database.get_document_chunks(path)
            return metadata

    def index_document(self, relative_path: str, force: bool = False) -> dict:
        """Index one file inside the Knowledge Base folder."""
        base_id = self.config.selected_knowledge_base_id()
        root = self.indexer.knowledge_base_folder(base_id)
        requested_path = root / relative_path
        if requested_path.is_symlink():
            raise ValueError("Symbolic links cannot be indexed.")
        candidate = requested_path.resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            raise ValueError("Document path must stay inside the Knowledge Base folder.") from None
        with self.config.database_context(base_id):
            return self.indexer.index_file(candidate, force=force)

    def get_rag_status(self, base_id: str | None = None) -> dict[str, Any]:
        """Describe the selected or specified folder and its isolated index."""
        selected_id = base_id or self.config.selected_knowledge_base_id()
        with self.config.database_context(selected_id):
            self.database.initialize()
            folder = self.indexer.knowledge_base_folder(selected_id)
            documents = self.database.list_documents()
        base = self.config.get_knowledge_base(selected_id)
        return {
            "base_id": base["id"],
            "name": base["name"],
            "path": str(folder),
            "configured_path": str(base["path"]),
            "exists": folder.is_dir(),
            "documents": len(documents),
            "chunks": sum(item["chunks"] for item in documents),
        }

    def get_rag_overview(self, base_id: str | None = None) -> dict[str, Any]:
        """Status plus every indexed document with its type, for the Chat Console dialog."""
        status = self.get_rag_status(base_id)
        documents = []
        types: dict[str, int] = {}
        for item in self.list_documents(base_id):
            manual = item["path"].startswith(self.indexer.MANUAL_PREFIX)
            kind = "manual" if manual else (Path(item["path"]).suffix.lower().lstrip(".") or "none")
            types[kind] = types.get(kind, 0) + 1
            documents.append({**item, "type": kind, "name": item["path"] if manual else Path(item["path"]).name})
        return {**status, "types": types, "files": documents}

    def add_text_document(self, title: str, text: str, base_id: str | None = None) -> dict:
        """Embed pasted text into the vector database."""
        with self.indexer.scan_lock:
            with self.config.database_context(base_id):
                return self.indexer.index_text(title, text)

    def set_rag_folder(self, path: str) -> dict[str, Any]:
        """Change the selected named source folder."""
        self.config.set_knowledge_base_path(path)
        return self.get_rag_status()

    def list_rag_bases(self) -> list[dict[str, Any]]:
        bases = self.config.list_knowledge_bases()
        for base in bases:
            base["exists"] = Path(base["path"]).is_dir()
            with self.config.database_context(str(base["id"])):
                self.database.initialize()
                documents = self.database.list_documents()
            base["documents"] = len(documents)
            base["chunks"] = sum(item["chunks"] for item in documents)
        return bases

    def open_rag_base_folder(self, base_id: str) -> dict[str, Any]:
        """Open a saved knowledge-source folder in the local Windows file manager."""
        path = self.config.knowledge_base_path(base_id)
        if not path.is_dir():
            raise FileNotFoundError(f"Knowledge-source folder is unavailable: {path}")
        if os.name != "nt":
            raise RuntimeError("Opening knowledge-source folders is supported only on Windows.")
        os.startfile(str(path))
        return {"opened": True, "path": str(path)}

    def add_rag_base(self, name: str, path: str) -> dict[str, Any]:
        base = self.config.add_knowledge_base(name, path)
        return {**base, **self.get_rag_status(str(base["id"]))}

    def rename_rag_base(self, base_id: str, name: str) -> dict[str, Any]:
        return self.config.rename_knowledge_base(base_id, name)

    def delete_rag_base(self, base_id: str) -> dict[str, Any]:
        job = self.scanner.get_scan_job()
        if job.get("base_id") == base_id and job.get("status") in {
            "paused", "awaiting_decision"
        }:
            self.scanner.stop_scan_job()
            worker = self.scanner._job_thread
            if worker and worker.is_alive():
                worker.join()

        with self.indexer.scan_lock:
            job = self.scanner.get_scan_job()
            if job.get("base_id") == base_id and job.get("status") in {
                "queued", "running", "pausing", "paused", "stopping", "awaiting_decision"
            }:
                raise RuntimeError("Stop or finish indexing before deleting this knowledge source.")

            self.config.get_knowledge_base(base_id)
            database_file = self.config.database_path(base_id)
            legacy_database_file = self.database.DATABASE_PATH if database_file is None else None
            if legacy_database_file and any(
                str(other["id"]) != str(base_id) and other.get("legacy_index")
                for other in self.config.list_knowledge_bases()
            ):
                raise RuntimeError(
                    "This legacy index is shared by another source and cannot be deleted independently."
                )
            with self.config.database_context(base_id):
                self.database.initialize()
                documents = self.database.list_documents()
                if database_file is None:
                    self.database.clear_all()

            deleted = self.config.delete_knowledge_base(base_id)
            self.scanner._update_job(
                stopped_base_ids=[
                    stopped_id
                    for stopped_id in self.scanner.get_scan_job().get("stopped_base_ids", [])
                    if stopped_id != base_id
                ]
            )
            index_file = database_file or legacy_database_file
            if index_file is not None:
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{index_file}{suffix}").unlink(missing_ok=True)
                try:
                    index_file.parent.rmdir()
                except OSError:
                    pass
            return {
                "deleted": True,
                "id": deleted["id"],
                "name": deleted["name"],
                "documents_removed": len(documents),
                "folder_deleted": False,
            }

    def select_rag_base(self, base_id: str) -> dict[str, Any]:
        self.config.select_knowledge_base(base_id)
        return self.get_rag_status(base_id)

    def archive_rag_base(self, base_id: str) -> dict[str, Any]:
        job = self.scanner.get_scan_job()
        if job.get("base_id") == base_id and (
            job.get("status") in {"queued", "running", "pausing", "stopping", "awaiting_decision"}
            or (job.get("status") == "paused" and job.get("resume_at"))
        ):
            raise RuntimeError("Pause or finish indexing and clear its scheduled resume before archiving.")
        return self.config.archive_knowledge_base(base_id)

    def restore_rag_base(self, base_id: str) -> dict[str, Any]:
        base = self.config.restore_knowledge_base(base_id)
        return {**base, **self.get_rag_status(base_id)}

    def get_rag_scan_job(self) -> dict[str, Any]:
        job = self.scanner.get_scan_job()
        if job.get("base_id"):
            try:
                job["base_name"] = self.config.get_knowledge_base(job["base_id"])["name"]
            except ValueError:
                job["base_name"] = "Unknown knowledge base"
        return job

    def start_rag_scan(self, base_id: str | None = None, force: bool = False) -> dict[str, Any]:
        return self.scanner.start_scan_job(base_id, force=force)

    def pause_rag_scan(self) -> dict[str, Any]:
        return self.scanner.pause_scan_job()

    def stop_rag_scan(self) -> dict[str, Any]:
        return self.scanner.stop_scan_job()

    def resume_rag_scan(self) -> dict[str, Any]:
        return self.scanner.resume_scan_job()

    def schedule_rag_scan_resume(self, resume_at: str) -> dict[str, Any]:
        return self.scanner.schedule_scan_resume(resume_at)

    def clear_rag_index(self, base_id: str | None = None) -> dict[str, Any]:
        """Delete all indexed RAG data; source files are untouched."""
        with self.indexer.scan_lock:
            with self.config.database_context(base_id):
                self.database.initialize()
                removed = self.database.clear_all()
        return {"removed": removed, **self.get_rag_status(base_id)}

    def delete_document(self, path: str, base_id: str | None = None) -> dict[str, Any]:
        """Remove one indexed document and its chunks."""
        with self.config.database_context(base_id):
            self.database.initialize()
            if not self.database.delete_document(path):
                raise FileNotFoundError(f"Knowledge-base document is not indexed: {path}")
        return {"deleted": True, "path": path}

    def refresh_knowledge_base(self, force: bool = False) -> dict:
        """Scan Knowledge Base and index additions or changes."""
        return self.indexer.refresh_index(force=force)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise RuntimeError("Query and document embedding dimensions do not match.")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)

    def search_knowledge_base(self,
        query: str,
        top_k: int = 5,
        relative_path: str = "",
        base_id: str | None = None,
    ) -> dict[str, Any]:
        """Return semantically relevant document passages and their source paths."""
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ValueError("A search query is required.")
        if isinstance(top_k, bool) or not 1 <= int(top_k) <= 20:
            raise ValueError("top_k must be between 1 and 20.")

        selected_id = base_id or self.config.selected_knowledge_base_id()
        base = self.config.get_knowledge_base(selected_id)
        with self.config.database_context(selected_id):
            self.database.initialize()
            chunks = self.database.list_chunks()
        selected_path = ""
        if relative_path.strip():
            source_path = editor_files.resolve_project_path(relative_path)
            if not source_path.is_file():
                raise FileNotFoundError(f"Knowledge-base document not found: {source_path}")
            selected_path = source_path.resolve().as_posix()
            with self.config.database_context(selected_id):
                self.indexer.index_file(source_path, selected_path)
                chunks = self.database.list_chunks()
        if selected_path:
            chunks = [chunk for chunk in chunks if chunk["path"] == selected_path]
        if not chunks:
            return {
                "query": clean_query,
                "knowledge_base": base["name"],
                "knowledge_base_id": selected_id,
                "results": [],
                "message": (
                    "No documents are indexed yet. Place supported files in "
                    f"'{self.indexer.knowledge_base_folder(selected_id)}' and wait for the automatic scan."
                ),
            }

        try:
            response = ollama.embed(model=self.indexer.EMBEDDING_MODEL, input=clean_query)
        except Exception as exc:
            raise RuntimeError(
                f"Could not create a query embedding with '{self.indexer.EMBEDDING_MODEL}': {exc}"
            ) from exc
        vectors = response.get("embeddings") if isinstance(response, dict) else response.embeddings
        if not vectors:
            raise RuntimeError(f"Embedding model '{self.indexer.EMBEDDING_MODEL}' returned no query vector.")
        query_vector = [float(value) for value in vectors[0]]

        compatible_chunks = [
            chunk for chunk in chunks if len(chunk["embedding"]) == len(query_vector)
        ]
        skipped_chunks = len(chunks) - len(compatible_chunks)
        if not compatible_chunks:
            raise RuntimeError(
                "No indexed chunks have embeddings compatible with the query embedding "
                f"({len(query_vector)} dimensions). Reindex this knowledge base."
            )

        ranked = [
            {
                "path": chunk["path"],
                "chunk_index": chunk["chunk_index"],
                "score": self._cosine_similarity(query_vector, chunk["embedding"]),
                "content": chunk["content"],
            }
            for chunk in compatible_chunks
        ]
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return {
            "query": clean_query,
            "knowledge_base": base["name"],
            "knowledge_base_id": selected_id,
            "model": self.indexer.EMBEDDING_MODEL,
            "skipped_incompatible_chunks": skipped_chunks,
            "results": ranked[:int(top_k)],
        }

    def start_document_scanner(self) -> bool:
        """Start automatic indexing of the workspace Knowledge Base folder."""
        return self.scanner.start_scanner()

    def decide_rag_scan_error(self, decision: str) -> dict[str, Any]:
        return self.scanner.decide_scan_error(decision)


migrate_legacy_layout()
rag_database = RagDatabaseEngine()
rag_config = RagConfigStore(rag_database)
document_indexer = DocumentIndexer(rag_config, rag_database)
document_scanner = DocumentScannerService(rag_config, document_indexer)
rag_memory = RagMemoryService(rag_config, rag_database, document_indexer, document_scanner)
