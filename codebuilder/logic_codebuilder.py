"""CodeBuilder host-side logic: the use-cases behind the CodeBuilder page (agent
metadata, structured edits, human-in-the-loop questionnaires), sandboxed script
files, and the shared sandbox terminal.

Process isolation for code runs stays in ``codebuilder/execution.py``.
"""

import codecs
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from codebuilder.execution import _kill_tree, _popen_group_kwargs
from codebuilder.schemas_codebuilder import MAX_COMMAND_CHARS, StructuredEditRequest
from core_engine.logic_core_engine import agent_library, questionnaire
from editor.logic_editor import editor_files
from file_manager.logic_file_manager import file_backend
from scratchpadMemory.logic_scratchpadMemory import scratchpad

# Pass this as json_file_path to read the questionnaire from the shared scratchpad instead of a file.
QUESTIONS_SCRATCHPAD_SOURCE = "scratchpad"
from test_environment.logic_test_environment import sandbox_env, sandbox_paths

PILLAR_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_BUFFER_CHARS = 200_000
_CD = re.compile(r"^cd(?:\s+/d)?(?:\s+(.*))?$", re.IGNORECASE)
_SANDBOX = re.compile(r"^sandbox(?:\s+(\w+))?(?:\s+(.*))?$", re.IGNORECASE)


class EditRejected(ValueError):
    """A structured edit was refused (the HTTP layer maps this to 400)."""


class FileRejected(ValueError):
    pass


class TerminalBusy(Exception):
    pass


class CodeBuilderService:
    """Pillar use-cases: health, agent profile, structured edits and questionnaires."""

    _FALLBACK_AGENT: Dict[str, Any] = {
        "id": "codebuilder-agent",
        "name": "CodeBuilder Specialist",
        "description": "Agent dedicated to writing, refactoring, and diagnosing Python code.",
        "mode": "agent",
        "tools": [
            "read_file", "write_file", "create_file", "list_directory",
            "run_python_code", "send_code_to_editor", "run_code_in_editor",
        ],
    }

    # --------------------------------------------------------------------------- #
    # Pillar info
    # --------------------------------------------------------------------------- #
    def health(self) -> Dict[str, str]:
        return {"status": "ok", "pillar": "codebuilder"}

    def agent_meta(self) -> Dict[str, Any]:
        """Resolve the specialist profile metadata from the centralized agent library."""
        for agent_id in ("codingagent", "codebuilder-agent"):
            try:
                profile = agent_library.load_agent(agent_id, "codebuilder")
                return {
                    "id": profile.id,
                    "name": profile.name,
                    "description": profile.description,
                    "mode": profile.mode,
                    "model": profile.model,
                    "tools": profile.tools,
                    "squad": "",
                    "environment": "workspace",
                }
            except FileNotFoundError:
                continue
        return dict(self._FALLBACK_AGENT)

    # --------------------------------------------------------------------------- #
    # Host bridge: agents
    # --------------------------------------------------------------------------- #
    def list_agents(self) -> List[dict]:
        """Agents of the ``codebuilder`` environment (resolved by the host's agent factory)."""
        return agent_library.list_agents("codebuilder")

    # --------------------------------------------------------------------------- #
    # Structured edits
    # --------------------------------------------------------------------------- #
    def _edit_root(self) -> Path:
        return Path(os.environ.get("CODEBUILDER_EDIT_ROOT") or Path.cwd()).resolve()

    def apply_edit(self, req: StructuredEditRequest) -> Dict[str, Any]:
        """Write an approved file update. Targets must stay inside the project root."""
        root = self._edit_root()
        requested = Path(req.target_path)
        target = (requested if requested.is_absolute() else root / requested).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            raise EditRejected(f"Target path must stay inside the project root ({root}).") from None
        if target.is_dir():
            raise EditRejected("Target path points to a directory, not a file.")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.content, encoding="utf-8")
        return {
            "status": "applied",
            "target_path": str(target),
            "bytes_written": len(req.content.encode("utf-8")),
            "explanation": req.explanation,
        }

    def _questions_from_scratchpad(self) -> Any:
        """Return the questionnaire an agent saved last (the newest squad handoff, titled "questionnaire")."""
        entry = scratchpad.read_memory("latest_subagent_result").get("latest_subagent_result") or {}
        if entry.get("task_type") != "questionnaire":
            raise ValueError(
                "No questionnaire was found in the scratchpad. Save it with write_squad_scratchpad "
                "(title \"questionnaire\") immediately before calling ask_user_questions."
            )
        payload = entry.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Error parsing the questionnaire saved in the scratchpad: {exc}") from exc
        if not payload:
            raise ValueError(
                "No questionnaire was found in the scratchpad. Save it with write_squad_scratchpad first."
            )
        return payload

    def _load_questions(self, json_file_path: str) -> List[dict]:
        relative = str(json_file_path).strip().lstrip("/")
        if relative.startswith("workspace/"):
            relative = relative[len("workspace/"):]

        if relative.lower() == QUESTIONS_SCRATCHPAD_SOURCE:
            data = self._questions_from_scratchpad()
        else:
            try:
                # Read from the same configured root that write_file uses, so an agent-written file is found.
                raw = file_backend.read_file(relative)["content"]
            except Exception:
                raise ValueError(f"Questions file '{json_file_path}' was not found in the workspace.") from None
            try:
                data = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Error parsing questions JSON file: {exc}") from exc

        items = data.get("questions", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        questions = []
        for index, item in enumerate(items):
            if isinstance(item, dict):
                questions.append({"id": str(item.get("id", index)), "question": str(item.get("question", item))})
            else:
                questions.append({"id": str(index), "question": str(item)})
        if not questions:
            raise ValueError("No questions found in the provided JSON file.")
        return questions

    def ask_user(self, json_file_path: str, timeout_seconds: float = 300.0) -> str:
        """Block until the user answers a questionnaire (or the wait times out). Returns a string."""
        try:
            questions = self._load_questions(json_file_path)
        except ValueError as exc:
            return f"Error: {exc}"

        try:
            wait = min(max(float(timeout_seconds), 1.0), 900.0)
        except (TypeError, ValueError):
            wait = 300.0

        record = questionnaire.create_request(questions)
        request_id = record["request_id"]
        completed = record["event"].wait(timeout=wait)
        request_data = questionnaire.resolve_request(request_id)  # always release the pending entry

        if not completed or request_data is None:
            return "Error: User questionnaire timed out waiting for input."

        answers = request_data.get("answers", {})
        return json.dumps(
            {
                "status": "COMPLETED",
                "qa_pairs": [
                    {
                        "id": q["id"],
                        "question": q["question"],
                        "answer": answers.get(q["id"], answers.get(str(i), "No answer provided")),
                    }
                    for i, q in enumerate(questions)
                ],
            },
            indent=2,
        )


class SandboxScriptFiles:
    """Save and open Python scripts for the editor inside the allowed sandbox folders."""

    MAX_FILE_BYTES = 500_000
    MAX_LISTED = 500
    MAX_FOLDERS = 300
    SKIP_DIRS = {"__pycache__", "venv", "node_modules"}
    _BAD_NAME_CHARS = set('<>:"/\\|?*')

    def _walk(self, root: Path, depth: int = 3):
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in self.SKIP_DIRS:
                continue
            yield entry
            if entry.is_dir() and depth > 1:
                yield from self._walk(entry, depth - 1)

    def listing(self) -> dict:
        sandbox_env.SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        folders, files = [], []
        for root in sandbox_paths.allowed_roots():
            folders.append(str(root))
            for entry in self._walk(root):
                if entry.is_dir():
                    if len(folders) < self.MAX_FOLDERS:
                        folders.append(str(entry))
                elif entry.suffix.lower() == ".py" and len(files) < self.MAX_LISTED:
                    files.append({"path": str(entry), "name": entry.name, "folder": str(entry.parent)})
        return {"default_folder": str(sandbox_env.SCRIPTS_DIR), "folders": folders, "files": files}

    def read(self, path: str) -> dict:
        try:
            target = sandbox_paths.resolve(path)
        except sandbox_paths.PathNotAllowed as exc:
            raise FileRejected(str(exc)) from exc
        if target.suffix.lower() != ".py" or not target.is_file():
            raise FileRejected("Only existing .py files can be opened.")
        if target.stat().st_size > self.MAX_FILE_BYTES:
            raise FileRejected("File is too large to open in the editor.")
        return {"path": str(target), "name": target.name, "content": target.read_text(encoding="utf-8", errors="replace")}

    def save(self, folder: str, name: str, content: str, overwrite: bool = False) -> dict:
        name = (name or "").strip()
        if not name.lower().endswith(".py"):
            name += ".py"
        if (
            name in {".py", "..py"} or any(ch in self._BAD_NAME_CHARS for ch in name)
            or any(ord(ch) < 32 for ch in name) or name.endswith((" .py", "..py"))
        ):
            raise FileRejected("Use a simple file name such as app.py (no folders or special characters).")
        if len(content.encode("utf-8")) > self.MAX_FILE_BYTES:
            raise FileRejected("File is too large to save.")
        try:
            directory = sandbox_paths.resolve(folder or str(sandbox_env.SCRIPTS_DIR))
        except sandbox_paths.PathNotAllowed as exc:
            raise FileRejected(str(exc)) from exc
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        if target.exists() and not overwrite:
            return {"saved": False, "exists": True, "path": str(target), "name": name}
        target.write_text(content, encoding="utf-8", newline="\n")
        return {"saved": True, "exists": False, "path": str(target), "name": name}

    def run_command(self, path: str, source: str) -> str:
        """Terminal command that runs a saved file; streamlit scripts use ``streamlit run``."""
        quoted = f'"{path}"' if " " in path else path
        imports = sandbox_env.imported_modules(source)
        return f"streamlit run {quoted}" if "streamlit" in imports else f"python {quoted}"


# Shared terminal for the CodeBuilder: runs one shell command at a time in the sandbox environment.
#
# A single session is shared by the docked panel and the pop-out window. Output is kept in a
# capped buffer addressed by absolute offsets so a newly opened window can catch up.
class TerminalSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer = ""
        self._base = 0  # absolute offset of _buffer[0]
        self._proc: Optional[subprocess.Popen] = None
        sandbox_env.SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        self.cwd = sandbox_env.SCRIPTS_DIR

    # ---- buffer ----
    def _append(self, text: str) -> None:
        with self._lock:
            self._buffer += text
            overflow = len(self._buffer) - MAX_BUFFER_CHARS
            if overflow > 0:
                self._buffer = self._buffer[overflow:]
                self._base += overflow

    def read(self, offset: int) -> tuple[str, int]:
        """Text after ``offset`` and the new end offset. Offsets older than the buffer are clamped."""
        with self._lock:
            start = max(offset, self._base)
            end = self._base + len(self._buffer)
            return self._buffer[start - self._base:], end

    def clear(self) -> None:
        with self._lock:
            self._base += len(self._buffer)
            self._buffer = ""

    # ---- state ----
    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def state(self) -> dict:
        return {
            "running": self.running,
            "cwd": str(self.cwd),
            "sandbox": sandbox_env.exists(),
        }

    # ---- environment ----
    def _environment(self) -> dict:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if sandbox_env.exists():
            scripts = str(sandbox_env.python_path().parent)
            env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = str(sandbox_env.SANDBOX_DIR)
            env.pop("PYTHONHOME", None)
        return env

    # ---- commands ----
    def _change_directory(self, target: str) -> None:
        target = (target or "").strip().strip('"')
        if not target:
            self._append(f"{self.cwd}\n")
            return
        try:
            path = sandbox_paths.resolve(target, self.cwd)
        except (sandbox_paths.PathNotAllowed, OSError) as exc:
            self._append(f"{exc}\n")
            return
        if not path.is_dir():
            self._append(f"The system cannot find the path specified: {target}\n")
            return
        self.cwd = path
        self._append(f"{self.cwd}\n")

    def _sandbox_command(self, action: str, argument: str) -> None:
        action = (action or "paths").lower()
        argument = (argument or "").strip()
        try:
            if action == "paths":
                self._append("Allowed folders:\n" + "".join(f"  {r}\n" for r in sandbox_paths.allowed_roots()))
                self._append(f"Packages: {sandbox_env.SANDBOX_DIR}\n")
            elif action == "add":
                self._append(f"Added: {sandbox_paths.add_root(argument)}\n")
            elif action == "remove":
                removed = sandbox_paths.remove_root(argument)
                self._append("Removed.\n" if removed else "That folder is not in the allowed list.\n")
                if not sandbox_paths.is_allowed(self.cwd):
                    self.cwd = sandbox_env.SCRIPTS_DIR
            elif action == "clear":
                sandbox_paths.clear_roots()
                self.cwd = sandbox_env.SCRIPTS_DIR
                self._append(f"Allowed folders reset to {sandbox_env.SCRIPTS_DIR}\n")
            else:
                self._append("Usage: sandbox paths | add <folder> | remove <folder> | clear\n")
        except ValueError as exc:
            self._append(f"{exc}\n")

    def run(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        if len(command) > MAX_COMMAND_CHARS:
            raise ValueError(f"Command is longer than {MAX_COMMAND_CHARS} characters.")
        with self._lock:
            if self.running:
                raise TerminalBusy("A command is already running. Stop it first.")

        self._append(f"{self.cwd}> {command}\n")
        match = _CD.match(command)
        if match:
            self._change_directory(match.group(1) or "")
            return

        sandbox_match = _SANDBOX.match(command)
        if sandbox_match:
            self._sandbox_command(sandbox_match.group(1), sandbox_match.group(2))
            return
        if not sandbox_env.exists():
            self._append("Creating the sandbox environment…\n")
            created = sandbox_env.ensure_venv()
            if not created["ok"]:
                self._append(f"{created['output']}\n")
                return

        self.cwd.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=str(self.cwd), env=self._environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **_popen_group_kwargs(),
            )
        except OSError as exc:
            self._append(f"Could not start command: {exc}\n")
            return
        self._proc = proc
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self._append(text)
        except (OSError, ValueError):
            pass
        proc.wait()
        self._append(f"\n[process exited with code {proc.returncode}]\n")

    def stop(self) -> bool:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        _kill_tree(proc)
        return True


codebuilder_service = CodeBuilderService()
script_files = SandboxScriptFiles()
terminal_session = TerminalSession()
