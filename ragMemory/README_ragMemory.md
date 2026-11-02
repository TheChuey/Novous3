# ragMemory: knowledge sources (RAG)

`ragMemory` is the long-term knowledge base: named **knowledge sources** (a name plus
a folder) are scanned in the background, split into chunks, embedded with a local
**Ollama** model (`nomic-embed-text` by default) and stored in **ChromaDB**. Agents and
apps then retrieve the passages most relevant to a question.

The component has no dependency on the rest of Novous, so the same code and the same
databases can be reused in other applications (see
[Reuse in another application](#reuse-in-another-application)).

## Knowledge sources

Novous supports multiple named knowledge sources from **Knowledge Sources** in
Chat & Test Controls. Each source connects one folder to a matching name and a
separate index. Select a source to search or manage its indexed files.
No implicit default source is created. Older default-source configuration is
migrated to a named branch, retaining its legacy index until that branch is
explicitly deleted.

Agent tools expose a live Knowledge TOC with each branch's exact saved name,
ID, active/archive status, and indexed-document/chunk counts. Agents should
look up a requested branch by name, then pass its ID to search or document
tools. Searching returns relevant indexed passages for the agent's context;
listing documents returns metadata only, while reading a specific indexed
document returns its extracted text. Archived branches remain accessible by ID
because archiving preserves the index.

Sources may point to folders that are currently unavailable; their name and
path remain saved, and indexing reports that the folder cannot be scanned.
Archiving removes a source from active selection without deleting its path or
index. Archived sources can be restored later.

Branches can be renamed without changing their folder or index. Deleting a
branch removes its saved source entry and indexed vectors; it never deletes
files from the linked folder. A branch cannot be deleted while its scan is
queued, running, pausing, stopping, awaiting a file-error decision, or paused.
Deleting a paused branch cancels any scheduled resume and stops its paused
worker before removing the branch. For other active scans, use **Stop
indexing** to stop after the current file finishes; wait until the job status
is **Stopped** before deleting the branch. A stopped source is excluded from
automatic scans until indexing is manually started again.

## Indexing

Indexing runs as a background job. The dialog shows progress and document
errors, and supports pausing or stopping between files, resuming, and
scheduling a resume.
Scheduled resumes use the local time selected in the browser and require the
Novous server and computer to remain running and awake. Completed files remain
indexed; on resume, unchanged files are skipped. Stale-document cleanup occurs
only after a scan reaches completion.

When indexing a file fails, the scan pauses for a choice: retry that file, skip
only that file and ask again on a later error, or skip all remaining errors.
Completion reports checked, indexed, unchanged, skipped, and failed counts so
skipped files are not mistaken for successfully indexed files.

Supported files: `.txt .md .markdown .rst .html .htm .csv .json .xml .yaml .yml
.log .py .js .ts .css .toml .ini .sql .sh .ps1` read as text, and `.pdf .docx
.pptx .odt` through Docling. If Docling's PDF parser cannot load, PDFs with
selectable text fall back to PyMuPDF; scanned PDFs that need OCR still require a
working OCR-capable parser. Text is split into 1,400-character chunks with a
200-character overlap, preferring paragraph breaks.

The automatic scanner cycles through active sources for updates every
60 seconds, with a small delay between files (see `RAG_SCAN_DELAY_SECONDS`).

## Storage

Everything lives under one folder, **`RAG_HOME`** (inside Novous: `ragMemory/`):

```
RAG_HOME/
  rag_config.json                 # sources: id, name, folder path, active, selected
  data/
    rag_scan_job.json             # current/last background index job
    rag_indexes/
      <source id>.sqlite3         # manifest: documents (path, mtime, size, chunks) + meta
      chroma/                     # ChromaDB: one collection "kb_<source id>" per source
```

* The **manifest** decides what needs reindexing (file modified time and size) and
  records the embedding model and vector size (`meta` table).
* The **Chroma collection** stores each chunk's text, its embedding and
  `{path, chunk_index}` metadata, using cosine distance. A search hit's `score` is the
  cosine similarity, `1 - distance`.
* Each index refuses vectors from a different embedding model or size. After you change
  `RAG_EMBEDDING_MODEL`, clear and reindex the existing sources.

**Migration from the old format:** before Chroma, vectors were stored as JSON text in
a `chunks` table inside each `.sqlite3` file. The first time an old index is opened,
its stored vectors are copied into Chroma (no re-embedding), the original file is
kept as `<id>.sqlite3.pre-chroma.bak`, and the old table is dropped. You can delete
the `.bak` files once you're happy with the result. On first start, state from the
former `agentMemory/` package is also moved here; nothing is overwritten.

## Code layout

| Module | Responsibility |
| --- | --- |
| `interface_ragMemory.py` | **The only doorway into the component.** The `/api/rag/*` FastAPI `router`, plus the Python functions `configure`, `get_service`, `list_knowledge_sources`, `selected_source_id`, `search_knowledge`, `list_documents`, `read_document`, `add_text`, `add_source`, `start_scan`, `scan_status` and `start_document_scanner` |
| `mcp_ragMemory.py` | MCP server (stdio) exposing the knowledge base as tools; calls only the interface |
| `schemas_ragMemory.py` | `RagConfig`, `KnowledgeBase`, `DocumentRecord`, `ChunkData`, `ScanJobState`, `ScanStatus`, `ScanDecision`, `SearchHit` |
| `logic_ragMemory.py` | `RagDatabaseEngine` (SQLite manifest + Chroma vectors), `OllamaEmbedder`, `RagConfigStore` (`rag_config.json`), `DocumentIndexer` (parse, chunk, embed), `DocumentScannerService` (background job and scanner loop), `RagMemoryService` (facade) and the `create_rag_memory(home)` factory |
| `pyproject.toml` | Makes the folder pip-installable as `novous-ragmemory` (import name `ragMemory`) |

Within Novous, `server.py` mounts `interface_ragMemory.router` and calls
`start_document_scanner()` at startup. `tools/logic_tools.py`, `orchestration/` and
`core_engine/` use the interface functions. Nothing outside this folder (except the
tests) imports `logic_ragMemory`.

## Reuse in another application

### 1. Prerequisites

* Python 3.10+.
* [Ollama](https://ollama.com) running locally, with the embedding model pulled:

  ```
  ollama pull nomic-embed-text
  ```

  To use Ollama on another machine, set `OLLAMA_HOST`, for example
  `OLLAMA_HOST=http://192.168.1.20:11434`.

### 2. Install

Pick one of the following:

* **pip, editable** (picks up changes made in Novous automatically):

  ```
  pip install -e "E:/NovousV3_claude/ragMemory[documents,mcp]"
  ```

* **pip from git**:

  ```
  pip install "novous-ragmemory[documents,mcp] @ git+<repo-url>#subdirectory=ragMemory"
  ```

* **Copy the folder**: copy `ragMemory/` (without `data/` and `rag_config.json`, unless
  you want the databases too) into the other project's root and install its
  dependencies: `pip install ollama "chromadb>=1.0" fastapi`, plus `docling
  docling-slim[format-pdf-docling] pymupdf` for PDF/Office files and `mcp` for the MCP
  server.

Extras: `documents` (PDF/DOCX/PPTX/ODT parsing) and `mcp` (the MCP server).

### 3. Configure

| Variable | Default | Meaning |
| --- | --- | --- |
| `RAG_HOME` | the `ragMemory/` folder | Where `rag_config.json` and `data/` live |
| `RAG_EMBEDDING_MODEL` | `nomic-embed-text:latest` | Ollama embedding model (`NOVOUS_EMBEDDING_MODEL` still works) |
| `RAG_EMBEDDING_BATCH_SIZE` | `16` | Chunks per Ollama embed call |
| `RAG_SCAN_DELAY_SECONDS` | `0.15` | Pause between files while scanning; `0` disables it (`NOVOUS_RAG_SCAN_DELAY_SECONDS` still works) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server address |

**Outside Novous, always set `RAG_HOME`** (or call `configure(home)`). Otherwise a pip
install would write its data into `site-packages`. Set environment variables before
`ragMemory` is first imported.

### 4. Use it from Python

```python
from ragMemory import interface_ragMemory as rag

rag.configure("C:/MyApp/rag")                      # rag_config.json + data/ go here

source = rag.add_source("Manuals", "C:/MyApp/manuals")   # absolute folder; becomes selected
rag.add_text("faq", "Resetting the device: hold power for 10 s.", source["id"])

rag.start_scan(source["id"])                       # background indexing of the folder
print(rag.scan_status()["status"])                 # queued / running / completed ...

hits = rag.search_knowledge("how do I reset it?", top_k=5, source_id=source["id"])
for hit in hits["results"]:
    print(round(hit["score"], 3), hit["path"], hit["content"][:80])
```

To check for folder changes every 60 seconds, call `rag.start_document_scanner()`.
`rag.get_service()` returns the full `RagMemoryService` (rename, archive, pause/resume,
clear index, and so on) when the doorway functions aren't enough.

### 5. Use it from a FastAPI app

```python
from fastapi import FastAPI
from ragMemory import interface_ragMemory as rag

rag.configure("C:/MyApp/rag")
app = FastAPI()
app.include_router(rag.router)                      # adds /api/rag/*

@app.on_event("startup")
def start_rag():
    rag.start_document_scanner()
```

The routes are the ones the Novous UI uses: `GET /api/rag/bases`, `POST /api/rag/bases`,
`GET /api/rag/documents`, `POST /api/rag/documents` (add text), `POST /api/rag/reindex`,
`GET /api/rag/scan`, and the rest in `interface_ragMemory.py`.

### 6. Use it from any agent, through MCP

The MCP server offers the tools `list_knowledge_sources`, `search_knowledge`,
`list_documents`, `read_document`, `add_text`, `add_source`, `reindex_source` and
`scan_status`.

```
python -m ragMemory.mcp_ragMemory --home C:/MyApp/rag          # read/search + on-demand indexing
python -m ragMemory.mcp_ragMemory --home C:/MyApp/rag --scan   # also auto-scan folders
ragmemory-mcp --home C:/MyApp/rag                               # same, when pip-installed
```

Claude Code:

```
claude mcp add ragmemory -- python -m ragMemory.mcp_ragMemory --home C:/MyApp/rag
```

Claude Desktop and other MCP clients (`mcpServers` config):

```json
{
  "mcpServers": {
    "ragmemory": {
      "command": "python",
      "args": ["-m", "ragMemory.mcp_ragMemory", "--home", "C:/MyApp/rag"],
      "env": { "OLLAMA_HOST": "http://localhost:11434" }
    }
  }
}
```

If you copied the folder instead of installing it, add `"cwd"` pointing to the folder
that *contains* `ragMemory/`, so that `-m ragMemory...` resolves.

### 7. Reuse the databases

To move an existing knowledge base (for example the one Novous built) to another app or
machine:

1. Stop indexing: stop the Novous server, or wait until the scan status isn't running.
2. Copy `rag_config.json` and the whole `data/` folder from the old `RAG_HOME` into the
   new app's `RAG_HOME`. The `.sqlite3` manifests and the `chroma/` folder belong
   together, so copy both.
3. Use the **same embedding model** (`RAG_EMBEDDING_MODEL`) in the new app. A
   different model would produce incompatible vectors, and search would ask you to
   reindex.

Search and document reads work straight away, even if the source folders don't exist
on the new machine. Only rescans need the folders. To point a source at its new
location, use `rag.get_service().set_rag_folder(path)` (for the selected source) or edit
`path` in `rag_config.json`. Paths are stored as absolute paths, and documents indexed
from the old path are replaced on the next full scan.

Several apps can point `RAG_HOME` at the **same** folder to share one knowledge base,
as long as only one of them runs the background scanner.
