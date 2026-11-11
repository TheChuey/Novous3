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

Repeated tool-call detection is not currently part of the squad workflow.

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
