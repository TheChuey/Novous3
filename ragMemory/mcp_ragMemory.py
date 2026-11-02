"""MCP doorway: exposes the knowledge base as tools to any MCP client over stdio.

    python -m ragMemory.mcp_ragMemory --home <RAG_HOME> [--scan]

Every tool calls ``interface_ragMemory``; ``--home`` picks the folder holding
``rag_config.json`` and ``data/`` and ``--scan`` also runs the background scanner.
"""

import argparse
import json

from mcp.server.fastmcp import FastMCP

from ragMemory import interface_ragMemory as rag

mcp = FastMCP("ragMemory")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


@mcp.tool()
def list_knowledge_sources() -> str:
    """List knowledge sources: exact names, IDs, status and indexed document/chunk counts. Call this first to find a source ID."""
    return _json([
        {
            "name": base["name"],
            "id": str(base["id"]),
            "status": "active" if base.get("active", True) else "archived",
            "selected": bool(base.get("selected")),
            "folder": base.get("path"),
            "folder_available": bool(base.get("exists")),
            "indexed_documents": int(base.get("documents", 0)),
            "chunks": int(base.get("chunks", 0)),
        }
        for base in rag.list_knowledge_sources()
    ])


@mcp.tool()
def search_knowledge(query: str, top_k: int = 5, source_id: str = "") -> str:
    """Find the passages most relevant to a question in one knowledge source (default: the selected one)."""
    return _json(rag.search_knowledge(query, top_k, source_id=source_id or None))


@mcp.tool()
def list_documents(source_id: str = "") -> str:
    """List indexed document paths and chunk counts in a source (metadata only)."""
    return _json(rag.list_documents(source_id or None))


@mcp.tool()
def read_document(path: str, source_id: str = "") -> str:
    """Read the full indexed text of one document; pass a path exactly as list_documents shows it."""
    document = rag.read_document(path, source_id or None)
    return _json({
        "path": document["path"],
        "chunks": len(document["chunks_data"]),
        "content": "\n\n".join(chunk["content"] for chunk in document["chunks_data"]),
    })


@mcp.tool()
def add_text(title: str, text: str, source_id: str = "") -> str:
    """Embed a piece of text into a source as the document 'manual:<title>' (replaces one with the same title)."""
    return _json(rag.add_text(title, text, source_id or None))


@mcp.tool()
def add_source(name: str, folder: str) -> str:
    """Register a named knowledge source for an absolute folder path; index it with reindex_source."""
    return _json(rag.add_source(name, folder))


@mcp.tool()
def reindex_source(source_id: str = "", force: bool = False) -> str:
    """Start background indexing of a source's folder; follow progress with scan_status."""
    return _json(rag.start_scan(source_id or None, force=force))


@mcp.tool()
def scan_status() -> str:
    """Report the current or last indexing job: status, progress and errors."""
    return _json(rag.scan_status())


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the ragMemory knowledge base over MCP (stdio).")
    parser.add_argument("--home", help="Folder holding rag_config.json and data/ (default: RAG_HOME).")
    parser.add_argument("--scan", action="store_true", help="Also run the automatic background scanner.")
    args = parser.parse_args()
    if args.home:
        rag.configure(args.home)
    if args.scan:
        rag.start_document_scanner()
    mcp.run()


if __name__ == "__main__":
    main()
