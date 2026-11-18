# Novous application guide

Novous is a local-first agent workspace. A FastAPI application serves the
browser interface and mounts component APIs; the components separate request
interfaces, schemas, and service logic.

## Main components

| Component | Responsibility |
| --- | --- |
| `core_engine/` | Agent runtime, tools, model integration, and core APIs |
| `orchestration/` | Squad runs, ordered execution, CodeBuilder pipeline, and telemetry |
| `squads/` | Squad definitions and squad-folder management |
| `library/`, `subAgentLibrary/` | Primary-agent and sub-agent definitions and prompts |
| `ragMemory/` | Knowledge-source indexing and retrieval |
| `scratchpadMemory/` | General, coding, and run-scoped scratchpad data |
| `codebuilder/` | Code editing, execution, and sandbox terminal |
| `test_environment/` | Test environment and tool workbench |
| `frontend/` | Static browser UI |
| `tests/` | Automated tests |

## Squad execution

A squad run has one primary lead and an ordered list of sub-agent steps. The
orchestrator runs those steps sequentially; fanout and router pattern names
received through older API configurations are normalized to the sequential
workflow.

Each sub-agent receives the task and only the immediately preceding sub-agent’s
`result` handoff. If a sub-agent returns text without writing that handoff, the
orchestrator records the text under that sub-agent’s name with the title
`result`. The lead receives the final handoff for synthesis, not the full run
transcript. Prompts remain editable for experimentation.

Squad runs use a single background worker, and Ollama chat and embedding
requests share a process-local lock. This reduces simultaneous local model work
but does not serialize requests from other application processes or external
Ollama clients.

The Code Development Squad uses its dedicated plan → build → test → fix
pipeline. Its steps are orchestrated by Novous rather than delegated dynamically
between agents.

## Memory and context

Scratchpads have separate purposes: general agent recall, coding notes, and
run-scoped squad handoffs. Squad handoffs carry the agent identity, a title, and
content; run-scoped entries remain available in the scratchpad viewer. The
orchestrator passes only the relevant preceding result to each step to keep
model context focused.

Every scratchpad write tool (`write_chat_scratchpad`, `write_coding_scratchpad`,
`write_squad_scratchpad`) is paired with its matching read tool
(`read_chat_scratchpad`, `read_coding_scratchpad`, `read_squad_scratchpad`).
Both halves' docstrings name the pairing, state the write's exact return shape,
and require the caller to read its own entry back with the same `agent`/`title`
to verify the save before reporting success. Every agent whose prompt lists a
write tool now also lists its matching read tool, and each agent's instructions
spell out the required write-then-read-back-then-verify sequence. Custom tools
added through the Tool Workbench are steered toward the same convention — see
[the tool library guide](../docs/tool-library.md).

Repeated tool-call detection is not currently part of the squad workflow.

A system prompt's instructions lose influence as a chat session's message
history grows, and small local models can stop following a tool-use rule
after a few turns even though the rule is still present in the (now distant)
system message. An agent definition can set `remind_every_turn: true` plus a
`tool_reminder: "..."` string in its frontmatter (plain top-level keys, next
to `max_tool_rounds`) to have that reminder text appended to the outgoing
message on every turn, not just read once at session start.
`ChatService.prepare_agent_message` (`core_engine/logic_core_engine.py`)
applies this for any agent whose profile sets it, generalizing the
hardcoded RAG-retrieval handoff that already worked the same way for
`rag-presenter`/`knowledge`. Both chat entry points (`/api/chat` and
`/api/chat/stream`) call this before every turn, and the agent definition is
re-read from disk on every turn, so toggling the field in an agent's `.md`
file takes effect on the next message with no restart. There is no settings
UI for this yet; turn it on or off by hand-editing the agent's frontmatter,
the same way `max_tool_rounds` is set today. It does not apply to sub-agents
run through squads, which call `Agent.think` directly and bypass
`prepare_agent_message`.

## Agent creation

Agents created through Prompt Starter (or the Squad Development dialog) are
scaffolded as `library/*.md` or `subAgentLibrary/*.yaml` files and listed by
`GET /api/agents`, which re-reads the library on every call. A file that fails
to parse (malformed frontmatter, an unresolved variable, etc.) is now reported
under that response's `errors` field, keyed by agent ID, instead of silently
vanishing from every listing. Prompt Starter checks this immediately after
creating an agent and surfaces the parse error directly if the new agent isn't
actually usable yet.

## Running and testing

From the repository root, install dependencies and start the application:

```bash
python -m pip install -r requirements.txt
python server.py
```

The default address is `http://127.0.0.1:8000`. Configure and start Ollama with
the models selected in the application before running agents.

Run automated tests with:

```bash
python -m pytest
```

See [the orchestration guide](../orchestration/README_orchestration.md) for
workflow details and API behavior. The dated files in this directory are
generated source snapshots and may describe earlier revisions; use this guide
and the current component documentation for the maintained behavior.
