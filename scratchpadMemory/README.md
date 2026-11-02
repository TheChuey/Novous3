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
