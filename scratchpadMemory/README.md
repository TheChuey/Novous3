# Agent scratchpad

The scratchpad is the agents' operational short-term memory, separate from the
long-term knowledge base in `ragMemory/`. It stores:

- `latest_read` plus a bounded `read_history` (the last 20 file reads),
- `latest_map`, the most recent workspace tree from `map_files`,
- `latest_subagent_result` plus a bounded `subagent_history`; a sub-agent status
  must be `success`, `nothing_found` or `error`.

It is not a document store: use workspace `read_file` for files, and the
knowledge tools for indexed branches.

## Code layout

| Module | Responsibility |
| --- | --- |
| `interface_scratchpadMemory.py` | `GET`/`DELETE /api/agent-memory/scratchpad` |
| `schemas_scratchpadMemory.py` | `ScratchpadState`, `ReadEntry`, `MapEntry`, `SubAgentEntry`, `MemoryType`, `SubAgentStatus` |
| `logic_scratchpadMemory.py` | `ScratchpadService`: thread-safe, atomic writes to `data/scratchpad.json` |

Agents use it through the `read_agent_scratchpad` and `write_agent_scratchpad`
tools. A scratchpad left by the former `agentMemory/` package is moved to
`scratchpadMemory/data/scratchpad.json` on first start.

## Squad run scratchpads

Every squad run, including CodeBuilder runs, has a separate scratchpad in
`scratchpadMemory/data/squad_scratchpad.json`. It records the participating agents'
responses, code workflow entries and user notes, with the same 200-entry and
20,000-character-per-entry limits as the coding scratchpad. It does not replace
the general or coding scratchpads, nor change how agents use those scratchpads.
Sequential squad members and the lead receive a bounded excerpt of recent entries
from their own run. CodeBuilder's plan, build, test and fix steps also receive recent
notes from the run; fan-out members start independently and the lead sees their results.
Tool-capable agents can additionally be assigned `read_squad_scratchpad` and
`post_squad_scratchpad` in the agent tool settings. These tools operate only on
the current squad run and report an error outside one. The CodeBuilder pipeline
continues to use its existing text-only agents and orchestrator-written entries.

Open **Squad scratchpads** in Runs or CodeBuilder to select a run, filter by agent
or type, search, add notes, and export Markdown or standalone HTML. Print / Save
PDF uses the browser print dialog. Closing a completed scratchpad offers to save
the entire run or discard it before clearing; cancelling keeps it. Closing the
browser or a running scratchpad does not clear it: unsaved runs remain available
after a restart, and interrupted runs are marked as such. Browser download settings
determine whether a Save As dialog appears; when only a download can be started,
you must confirm it was saved before clearing. If entries change during saving,
the app keeps the scratchpad and asks you to refresh. Keep in mind that these scratchpads
may contain agent output and code, and are stored locally until cleared.

The REST endpoints under `/api/agent-memory/squad-scratchpads` list runs and
read, add notes to, or delete an individual run. An active run cannot be deleted.

## Recalling memory

`read_agent_scratchpad` is a paged recall, so repeated lookups in a long chat
stay small. The stored file is unchanged; only what the tool returns is bounded.

- **No arguments** returns an index: up to 15 newest items, each with `id`, `kind`
  (`read`, `map`, `subagent`), `label` (file path, map path or sub-agent id), `chars`,
  `updated_at` and an 80-character `preview`.
- **`item=<id or path>`** returns one page of that item's text. A path means its newest
  read; an id means that exact version. Pass `offset=<next_offset>` to continue.
  `max_chars` defaults to 4000 and is clamped to 500–20000.
- **`memory_type='latest_read' | 'latest_map' | 'latest_subagent_result'`** returns the
  newest of that kind. A large `latest_subagent_result` is paged inside `payload`
  (`payload_format: json_text`); small ones keep the original shape that the RAG
  handoff reads.

Entry ids are derived from kind, timestamp and label, so existing files need no
migration. `read_file` saves the full text here but returns only the first page, and
tells the agent how to fetch the rest.

## Sub-agent payloads (`write_agent_scratchpad`)

- Each write is stored in `subagent_history` and becomes `latest_subagent_result`.
- `payload` is a JSON object, passed as a string. Any keys are kept, for example
  `{"answers": {...}, "summary": "..."}`.
- An empty payload (blank text, `{}` or `[]`) is refused with an error, so a write
  never reports success with nothing stored. An empty `answers` object with a
  `summary` saying no answers were given is a valid record.
- Read it back with `read_agent_scratchpad(memory_type="latest_subagent_result")`, or
  with `item=<id>` for a full entry from the index.
- The chat's scratchpad panel and its Markdown export show any payload keys, not just
  the retrieval fields (`results`, `query`, `knowledge_branch_name`, `error`).
