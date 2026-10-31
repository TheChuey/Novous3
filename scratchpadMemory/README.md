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
