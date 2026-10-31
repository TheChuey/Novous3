"""Generate a comprehensive, AI-agent-readable snapshot of the Novous application."""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_scope import ROOT, fence_for, iter_source_files, iter_tree

TODAY = datetime.now().strftime("%Y%m%d")
try:
    COMMIT = os.popen("git rev-parse --short HEAD").read().strip()
except Exception:
    COMMIT = "unknown"

MAX_FILE_BYTES = 200_000

OUT = ROOT / "app_documentation" / f"NOVOUS_APP_SNAPSHOT_{TODAY}_new.md"

KEY_COMPONENTS = """Every backend component follows a three-module separation of concerns:
interface_<component>.py (FastAPI router / doorway, no domain logic), schemas_<component>.py
(pydantic models, dataclasses, enums, state shapes) and logic_<component>.py (OOP services
with module-level singletons).

- server.py: FastAPI app entrypoint serving the static UI and mounting every component router
- core_engine/: agent library, Think-Act-Observe runtime, tool catalog with built-in tools, HITL questionnaires, chat sessions
- orchestration/: sub-agent runner, deterministic RAG retriever, execution patterns, the Code Development Squad pipeline, squad registry with health metrics, live telemetry (SSE) and the pipeline registry
- subAgentLibrary/: pure-YAML definitions for background sub-agents
- editor/: workspace file operations and the editor session event bus (REST + WebSocket)
- ragMemory/: named knowledge sources, isolated SQLite indexes, document indexer, background scanner, semantic search
- scratchpadMemory/: shared agent scratchpad (latest reads, workspace maps, sub-agent results) and the separate code scratchpad for the coding agents
- codebuilder/: code execution pillar (isolated child-process engine in execution.py), sandbox terminal and script files
- file_manager/: sandboxed file CRUD service and REST/elFinder API
- tools/: unified workspace file tools, custom-tool promotion, and the agent-facing tool functions
- frontend/: static assets and HTML pages
- library/: centralized primary-agent definitions, including rag-presenter
- workspace/: project configuration, squads and agent scaffolding
- test_environment/: sandbox virtualenv, folder allow-list and the Tool Workbench
- tests/: the full pytest suite for every component
- about/: app identity source synced to the index page and app documentation
- scripts/: documentation and migration generators
- app_documentation/: generated snapshot and master copy documents"""


def get_lang(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return {
        "py": "python",
        "js": "javascript",
        "json": "json",
        "html": "html",
        "css": "css",
        "md": "markdown",
        "txt": "text",
        "bat": "batch",
        "sh": "bash",
        "yaml": "yaml",
        "yml": "yaml",
        "cfg": "ini",
        "ini": "ini",
    }.get(ext, ext)


def read_source(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"<unreadable: {exc}>"
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        text = text[:MAX_FILE_BYTES] + "\n... <truncated> ..."
    return text


def write_tree(f):
    f.write("## 3) File Structure Snapshot\n\n")
    for rel, is_dir in iter_tree(ROOT):
        f.write(f"- {rel}{'/' if is_dir else ''}\n")
    f.write("\n")


def main():
    files = list(iter_source_files(ROOT))
    with OUT.open("w", encoding="utf-8") as f:
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d')}\n")
        f.write("Name: Novous Application Snapshot\n")
        f.write(f"Filename: {OUT.name}\n")
        f.write(f"Commit: {COMMIT}\n")
        f.write("Description: Self-contained reference for an AI agent. Captures Novous app purpose, architecture, file structure, and complete source code. Designed for grounding/understanding without repo access.\n\n")
        f.write("# NOVOUS APPLICATION SNAPSHOT\n\n")
        f.write("## 1) Overview\n\n")
        f.write("Novous is an agent orchestration/workspace system with a FastAPI backend, a static frontend, a core engine for agents and tools, a sub-agent orchestration pillar with enforced RAG retrieval, editor integration, shared agent memory, a code builder, a sandboxed file manager, a repository-root agent library, and a test environment.\n\n")
        f.write(f"Key components:\n{KEY_COMPONENTS}\n\n")
        f.write("## 2) Table of Contents\n\n")
        f.write("1. Overview\n")
        f.write("2. Table of Contents\n")
        f.write("3. File Structure Snapshot\n")
        f.write("4. Complete Source Code (by file)\n\n")
        write_tree(f)
        f.write("## 4) Complete Source Code (by file)\n\n")
        for rel, path in files:
            lang = get_lang(path.suffix)
            text = read_source(path)
            fence = fence_for(text)
            f.write(f"### {rel}\n\n")
            f.write(f"{fence}{lang}\n{text}\n{fence}\n\n")

    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes, {len(files)} files)")


if __name__ == "__main__":
    main()
