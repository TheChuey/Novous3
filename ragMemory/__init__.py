"""Long-term RAG knowledge base: named sources, Chroma vector indexes, Ollama embeddings
and the document scanner. Use it through ``ragMemory.interface_ragMemory`` (re-exported here)."""

from ragMemory.interface_ragMemory import (
    add_source,
    add_text,
    configure,
    get_service,
    list_documents,
    list_knowledge_sources,
    read_document,
    router,
    scan_status,
    search_knowledge,
    selected_source_id,
    start_document_scanner,
    start_scan,
)

__all__ = [
    "add_source",
    "add_text",
    "configure",
    "get_service",
    "list_documents",
    "list_knowledge_sources",
    "read_document",
    "router",
    "scan_status",
    "search_knowledge",
    "selected_source_id",
    "start_document_scanner",
    "start_scan",
]
