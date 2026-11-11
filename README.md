# Novous

Novous is a local-first workspace for building and running AI agents and squads.
It combines a FastAPI backend with a browser-based interface, agent and tool
management, knowledge retrieval, scratchpad memory, and a CodeBuilder workflow.

## Run locally

```bash
python -m pip install -r requirements.txt
python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The default host and port
can be changed with `PROJECT_MANAGER_HOST` and `PROJECT_MANAGER_PORT`.

Agent inference uses the configured Ollama models. Start Ollama and make sure
the models selected in Novous are available before running agents. The web
interface and non-inference features can be explored without running a squad.

## What’s included

- **Agents and tools:** Configure primary agents, sub-agents, prompts, and tools.
- **Squads:** Build an ordered workflow with one lead and sequential sub-agent
  steps. Each step receives the task and the preceding step’s result, rather
  than the full conversation history. The lead synthesizes the final result.
- **CodeBuilder:** Plan, build, test, and troubleshoot code through a controlled
  pipeline.
- **Knowledge and memory:** Search indexed knowledge sources and use scratchpads
  for agent notes and run-scoped handoffs.
- **Workspace utilities:** Edit files, manage sandboxed files, and use the test
  environment and tool workbench.

Squad runs are serialized to reduce local model contention. Ollama chat and
embedding requests also share a process-local inference lock. These controls
do not coordinate with other Novous processes or external Ollama clients.

## Project layout

| Path | Purpose |
| --- | --- |
| `core_engine/` | Agent runtime, tools, model integration, and core APIs |
| `orchestration/` | Ordered squad execution, CodeBuilder pipeline, and run telemetry |
| `squads/` | Squad definitions and squad-folder management |
| `library/`, `subAgentLibrary/` | Primary-agent and sub-agent definitions |
| `ragMemory/` | Knowledge-source indexing and retrieval |
| `scratchpadMemory/` | Agent, coding, and squad-run scratchpads |
| `codebuilder/` | Code execution and sandbox terminal |
| `test_environment/` | Test environment and tool workbench |
| `frontend/` | Browser interface |
| `tests/` | Automated tests |
| `app_documentation/` | Application overview and generated snapshots |

## Tests

Run the automated test suite with:

```bash
python -m pytest
```

The orchestration component has additional design and API notes in
[orchestration/README_orchestration.md](orchestration/README_orchestration.md).
The maintained application overview is in
[app_documentation/README.md](app_documentation/README.md).
