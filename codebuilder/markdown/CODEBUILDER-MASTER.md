# CodeBuilder & Tool Architecture Reference

## Overview

CodeBuilder provides isolated Python code execution, Monaco editor integration, a shared
sandbox terminal and human-in-the-loop questionnaires for Novous agents. Agents reach it
through tools registered in the core tool catalog.

---

## 1. Tool catalog (`core_engine/logic_core_engine.py`)

`ToolCatalog` (singleton `tool_catalog`) owns the registry of invocable agent tools.
`load_tools(reload=False)` registers, in order:

1. **First-party tool modules** — `tools/logic_tools.py`, imported once. Its functions use
   the `@tool` decorator (`from core_engine.logic_core_engine import tool`). On `reload=True`
   the registry is cleared and these functions are re-registered without re-importing the
   module, so the services they use keep their state.
2. **Plugin files** — every other `*.py` in `tools/` except `__init__.py`,
   `interface_tools.py`, `schemas_tools.py` and private `_*` files. This includes
   `tools/promoted_tools.py`. A file that fails to load is reported in `load_errors` and
   leaves no partial registrations.
3. **Core built-ins** — declared with `@tool_catalog.builtin(provider=...)` in the core
   engine: `calculator`, `rag_agent_tool`, `agent_info`, `extract_docstrings`,
   `search_workspace`, `run_python_code`, `send_code_to_editor`, `run_code_in_editor`,
   `ask_user_questions`, `project_status`.

`get_catalog_report()` parses `library/*.md` agent definitions and reports tools that agents
reference but the registry lacks.

## 2. Agent-facing tools (`tools/logic_tools.py`)

| Tool | Provider | Backed by |
| :-- | :-- | :-- |
| `read_file`, `write_file`, `delete_file` | `tools` | `WorkspaceFileTools` -> `FileBackendService` |
| `map_files` | `file_manager` | `WorkspaceFileTools.map_files` + scratchpad |
| `editor` | `editor` | `EditorFileOperations` |
| `read_agent_scratchpad`, `write_agent_scratchpad` | `scratchpadMemory` | `ScratchpadService` |
| `call_subagent` | `orchestration` | `RagRetriever` / `SubAgentRunner` |
| `get_knowledge_catalog`, `search_knowledge_branch`, `list_knowledge_branch_documents`, `read_knowledge_branch_document` | `ragMemory` | `RagMemoryService` |

Every file path goes through `file_manager` and is validated against
`tools/allowed_paths.json`.

## 3. Promotion flow (`tools/promoted_tools.py`)

Tools built and tested in the Tool Workbench are promoted into the running catalog:

1. `ToolWorkbench.promote` re-runs the tool; a failing tool is rejected (HTTP 400).
2. `ToolRegistryService.register_tool` decorates the source with `@tool` and appends it
   below the `# PROMOTION_MARKER` line of `tools/promoted_tools.py`.
3. The block is executed in the promoted module's namespace, so the tool is available
   without a restart. On failure the file is rolled back.
4. A name that is already registered raises `ToolConflictError` (HTTP 409).
5. `ToolRegistryService.remove_tool` removes the function span from the file and the
   registry; additions and removals are byte-identical round-trips.

A promoted-tools file still importing from the old `core_engine.tool_catalog` module is
rewritten to the new import automatically.

## 4. HTTP routes (`tools/interface_tools.py`)

- `GET /api/tools` — `{tools, providers, warnings}`; promoted tools carry `custom: true`.
- `GET /api/tools/library` — tools defined by modules in the `tools` package.
- `GET /api/tools/health` — catalog status and `load_errors`.
- `GET /api/tools/{name}`, `DELETE /api/tools/{name}`, `POST /api/tools/register`.
- `POST /api/tools/map-page` — paged folder tree for the active file-manager root.

## 5. CodeBuilder pillar

See `codebuilder/README.md` for the execution engine, stuck-code handling, the
`/api/codebuilder/*` routes, the sandbox terminal and script files.
