# codebuilder/ — code execution pillar

Backs the `/codebuilder` and `/terminal` pages and the CodeBuilder agent tools.

## Layers (dependencies point downward only)

```
interface_codebuilder.py   HTTP adapter: routes, status codes, SSE framing
logic_codebuilder.py       use-cases, sandbox script files, shared sandbox terminal
execution.py               engine: isolated child-process runs + stuck handling
schemas_codebuilder.py     contracts (pydantic only)
```

| Module | Concern | Knows about |
| :-- | :-- | :-- |
| `schemas_codebuilder.py` | request/result models, terminal and file request bodies | pydantic |
| `execution.py` | processes, deadlines, diagnostics | stdlib + schemas |
| `logic_codebuilder.py` | `CodeBuilderService` (agent info, structured edits, questionnaires), `SandboxScriptFiles`, `TerminalSession` | schemas, execution, core engine, editor, test environment |
| `interface_codebuilder.py` | HTTP | FastAPI, logic, execution |

`execution.py` stays a separate module on purpose: it is the isolated runner and
has no dependency on the rest of the app beyond the schemas (and, lazily, the
sandbox interpreter).

## Agent tools

`run_python_code`, `send_code_to_editor`, `run_code_in_editor` and
`ask_user_questions` are core built-in tools (provider `codebuilder`) defined in
`core_engine/logic_core_engine.py`; they delegate to this pillar.

## When code gets stuck

Both modes run in a child process under one supervisor, so user code can never freeze the server.

| Situation | What happens |
| :-- | :-- |
| Infinite loop / long `sleep` / blocking call | Killed at the deadline. `faulthandler` reports the line it was stuck on. |
| Unbounded `print` loop | Stopped at 64 KiB (`output_limit`) instead of spinning until timeout. |
| `input()` | stdin is closed: immediate `EOFError`, no hang. |
| Code spawned child processes | The whole process tree is killed (`taskkill /T` on Windows, process group elsewhere). |
| Browser tab closed / stream abandoned | Generator close kills the run; a watchdog kills it at `timeout + 1s` regardless. |
| User or tool wants to stop it | `POST /api/codebuilder/execute/{run_id}/cancel` (id is in the stream's `start` event). |
| Agent runs hanging code | `run_python_code` returns `timed_out` + `stuck {reason, line, source, hint}` so the model can fix it. |

`timeout_seconds` is clamped to 0.1–480 s. The editor marker lands on the stuck line.

## Behaviour notes

* `safe` mode runs in an isolated child process (~60 ms start-up) and its timeout is enforced.
* `/edit` rejects paths outside the project root (override with `CODEBUILDER_EDIT_ROOT`).

## Tests

`python -m pytest tests/test_codebuilder.py tests/test_codebuilder_terminal.py tests/test_codebuilder_sandbox_files.py -q`
(needs pytest, fastapi, httpx; no Ollama required).
