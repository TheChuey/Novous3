"""Sandbox test-harness logic: the isolated virtualenv for untrusted code, the folder
allow-list for the sandbox terminal and script files, and the Tool Workbench that
tests, generates and promotes user-authored Python tools."""

import ast
import difflib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
from datetime import datetime, timezone
from pathlib import Path

from core_engine.logic_core_engine import Agent, tool_catalog, wrap_flat_arguments
from modelProfiles.logic_modelProfiles import model_profiles


class PathNotAllowed(ValueError):
    pass


class ModelCallError(RuntimeError):
    """The model runtime could not be reached or rejected the request."""


class PromotionTestFailed(ValueError):
    """The tool failed its re-test right before promotion."""

    def __init__(self, message: str, test_result: dict):
        super().__init__(message)
        self.test_result = test_result


class SandboxEnvManager:
    """Isolated virtualenv used to run untrusted test code (Tool Workbench and Code Builder).

    Its folder is ``sandbox_root`` in ``tools/allowed_paths.json``, else ``workspace/sandbox``.
    """

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    CONFIG_PATH = PROJECT_ROOT / "tools" / "allowed_paths.json"
    FALLBACK_ROOT = PROJECT_ROOT / "workspace" / "sandbox"
    LEGACY_VENV = PROJECT_ROOT / "workspace" / ".sandbox-venv"
    REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"
    VENV_TIMEOUT_SECONDS = 120
    INSTALL_TIMEOUT_SECONDS = 300
    MAX_OUTPUT_CHARS = 20000
    _PACKAGE_SPEC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}((==|>=)[A-Za-z0-9._!+-]{1,40})?$")
    _MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    # Import names that differ from their PyPI package name.
    IMPORT_TO_PACKAGE = {
        "cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn",
        "skimage": "scikit-image", "yaml": "pyyaml", "bs4": "beautifulsoup4",
        "dotenv": "python-dotenv", "dateutil": "python-dateutil", "serial": "pyserial",
        "docx": "python-docx", "fitz": "pymupdf", "jwt": "pyjwt", "attr": "attrs",
        "OpenSSL": "pyopenssl", "Crypto": "pycryptodome", "magic": "python-magic",
    }

    def __init__(self):
        self._lock = threading.Lock()
        self.SANDBOX_ROOT = self.sandbox_root()
        self.SANDBOX_DIR = self.SANDBOX_ROOT / "venv"
        self.SCRIPTS_DIR = self.SANDBOX_ROOT / "scripts"

    def read_config(self) -> dict:
        try:
            data = json.loads(self.CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_config(self, config: dict) -> None:
        self.CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def _usable_root(self, candidate: Path) -> bool:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def sandbox_root(self) -> Path:
        """Folder holding the sandbox venv and scripts: ``sandbox_root`` in allowed_paths.json, else inside the repo."""
        configured = str(self.read_config().get("sandbox_root") or "").strip()
        if configured:
            candidate = Path(os.path.expandvars(configured)).expanduser()
            if candidate.is_absolute() and self._usable_root(candidate):
                return candidate.resolve()
        self.FALLBACK_ROOT.mkdir(parents=True, exist_ok=True)
        return self.FALLBACK_ROOT

    def exists(self) -> bool:
        # A venv without pyvenv.cfg cannot start ("failed to locate pyvenv.cfg"); treat it as missing.
        return self.python_path().exists() and (self.SANDBOX_DIR / "pyvenv.cfg").is_file()

    def python_path(self) -> Path:
        scripts = "Scripts" if os.name == "nt" else "bin"
        return self.SANDBOX_DIR / scripts / ("python.exe" if os.name == "nt" else "python")

    def interpreter(self) -> str:
        """The sandbox Python when it exists, otherwise the server's own interpreter."""
        return str(self.python_path()) if self.exists() else sys.executable

    def _run(self, command: list[str], timeout: int) -> dict:
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=timeout, text=True,
                encoding="utf-8", errors="replace", **kwargs,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"Timed out after {timeout} seconds."}
        except OSError as exc:
            return {"ok": False, "output": f"Could not start process: {exc}"}
        output = (completed.stdout + completed.stderr).strip()
        return {"ok": completed.returncode == 0, "output": output[-self.MAX_OUTPUT_CHARS:]}

    def migrate_legacy(self) -> dict:
        """Move packages from the old in-repo venv into the new sandbox venv, then delete the old one."""
        legacy_python = self.LEGACY_VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        if not legacy_python.exists() or self.LEGACY_VENV.resolve() == self.SANDBOX_DIR.resolve():
            return {"ok": True, "migrated": False}
        frozen = self._run([str(legacy_python), "-m", "pip", "freeze", "--disable-pip-version-check"], 60)
        packages = [line.strip() for line in frozen["output"].splitlines() if line.strip() and not line.startswith(("-e", "#", "@"))] if frozen["ok"] else []
        created = self.ensure_venv()
        if not created["ok"]:
            return {"ok": False, "migrated": False, "output": created["output"]}
        if packages:
            installed = self._run(
                [str(self.python_path()), "-m", "pip", "install", "--disable-pip-version-check", *packages],
                self.INSTALL_TIMEOUT_SECONDS,
            )
            if not installed["ok"]:
                return {"ok": False, "migrated": False, "output": installed["output"]}
        shutil.rmtree(self.LEGACY_VENV, ignore_errors=True)
        return {"ok": True, "migrated": True, "packages": len(packages)}

    def ensure_venv(self) -> dict:
        with self._lock:
            if self.exists():
                return {"ok": True, "output": "Sandbox environment is ready."}
            self.SANDBOX_DIR.parent.mkdir(parents=True, exist_ok=True)
            self.SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
            return self._run([sys.executable, "-m", "venv", str(self.SANDBOX_DIR)], self.VENV_TIMEOUT_SECONDS)

    def validate_packages(self, packages: list[str]) -> list[str]:
        cleaned = []
        for package in packages:
            spec = (package or "").strip()
            if not self._PACKAGE_SPEC.fullmatch(spec):
                raise ValueError(f"Invalid package name: {spec!r}")
            cleaned.append(spec)
        if not cleaned:
            raise ValueError("Enter at least one package.")
        if len(cleaned) > 20:
            raise ValueError("Install at most 20 packages at a time.")
        return cleaned

    def install(self, packages: list[str]) -> dict:
        cleaned = self.validate_packages(packages)
        created = self.ensure_venv()
        if not created["ok"]:
            return {"ok": False, "packages": cleaned, "output": created["output"]}
        result = self._run(
            [str(self.python_path()), "-m", "pip", "install", "--disable-pip-version-check", *cleaned],
            self.INSTALL_TIMEOUT_SECONDS,
        )
        return {"ok": result["ok"], "packages": cleaned, "output": result["output"]}

    def reset(self) -> dict:
        with self._lock:
            if self.SANDBOX_DIR.exists():
                shutil.rmtree(self.SANDBOX_DIR, ignore_errors=True)
                if self.SANDBOX_DIR.exists():
                    return {"ok": False, "output": "Could not remove the sandbox; close programs using it and retry."}
        created = self.ensure_venv()
        return {"ok": created["ok"], "output": "Sandbox reset to an empty environment." if created["ok"] else created["output"]}

    def list_packages(self) -> list[dict]:
        if not self.exists():
            return []
        result = self._run([str(self.python_path()), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], 60)
        if not result["ok"]:
            return []
        try:
            return json.loads(result["output"])
        except json.JSONDecodeError:
            return []

    def imported_modules(self, source: str) -> list[str]:
        """Top-level module names imported by source, in order of first appearance."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.append(node.module.split(".")[0])
        return list(dict.fromkeys(name for name in names if self._MODULE_NAME.fullmatch(name)))

    def _is_local_module(self, name: str) -> bool:
        return (self.PROJECT_ROOT / name).is_dir() or (self.PROJECT_ROOT / f"{name}.py").is_file()

    def third_party_modules(self, source: str) -> list[str]:
        return [
            name for name in self.imported_modules(source)
            if name not in sys.stdlib_module_names and not self._is_local_module(name)
        ]

    def package_for_module(self, module: str) -> str:
        return self.IMPORT_TO_PACKAGE.get(module, module)

    def missing_modules(self, source: str, extra: list[str] | None = None) -> list[dict]:
        """Third-party modules the sandbox interpreter cannot import."""
        modules = list(dict.fromkeys(self.third_party_modules(source) + [m for m in (extra or []) if self._MODULE_NAME.fullmatch(m)]))
        if not modules:
            return []
        probe = (
            "import importlib.util, json, sys\n"
            "print(json.dumps([m for m in json.loads(sys.argv[1]) if importlib.util.find_spec(m) is None]))"
        )
        result = self._run([self.interpreter(), "-I", "-c", probe, json.dumps(modules)], 30)
        try:
            missing = json.loads(result["output"].splitlines()[-1]) if result["ok"] else modules
        except (json.JSONDecodeError, IndexError):
            missing = modules
        return [{"module": name, "package": self.package_for_module(name)} for name in missing]

    def _normalize(self, name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    def _requirement_names(self) -> set[str]:
        if not self.REQUIREMENTS_PATH.exists():
            return set()
        names = set()
        for line in self.REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if match and not line.lstrip().startswith("#"):
                names.add(self._normalize(match.group(1)))
        return names

    def add_to_requirements(self, source: str) -> list[str]:
        """Append third-party packages imported by source that requirements.txt lacks."""
        known = self._requirement_names()
        installed = {self._normalize(p["name"]): p["version"] for p in self.list_packages()}
        added = []
        for module in self.third_party_modules(source):
            package = self.package_for_module(module)
            key = self._normalize(package)
            if key in known:
                continue
            version = installed.get(key)
            added.append(f"{package}>={version}" if version else package)
            known.add(key)
        if added:
            existing = self.REQUIREMENTS_PATH.read_text(encoding="utf-8") if self.REQUIREMENTS_PATH.exists() else ""
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            with self.REQUIREMENTS_PATH.open("a", encoding="utf-8") as handle:
                handle.write(prefix + "\n".join(added) + "\n")
        return added


def _norm(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


class SandboxPathPolicy:
    """Allowed folders for the sandbox terminal and Code Builder file save/open.

    The list lives under ``sandbox_roots`` in tools/allowed_paths.json; the scripts
    folder is always allowed.
    """

    PathNotAllowed = PathNotAllowed

    def __init__(self, env: SandboxEnvManager):
        self.env = env

    def allowed_roots(self) -> list[Path]:
        configured = self.env.read_config().get("sandbox_roots")
        roots = [self.env.SCRIPTS_DIR]
        if isinstance(configured, list):
            for raw in configured:
                path = Path(os.path.expandvars(str(raw))).expanduser()
                if path.is_absolute() and path.is_dir():
                    roots.append(path)
        unique: dict[str, Path] = {}
        for root in roots:
            unique.setdefault(_norm(root), root.resolve())
        return list(unique.values())

    def is_allowed(self, path: Path | str) -> bool:
        target = _norm(path)
        for root in self.allowed_roots():
            base = _norm(root)
            if target == base or target.startswith(base + os.sep):
                return True
        return False

    def resolve(self, path: str | Path, base: Path | None = None) -> Path:
        """Resolve ``path`` (absolute, or relative to ``base``) and require it to be inside an allowed root."""
        candidate = Path(os.path.expandvars(str(path))).expanduser()
        if not candidate.is_absolute():
            candidate = (base or self.env.SCRIPTS_DIR) / candidate
        resolved = candidate.resolve()
        if not self.is_allowed(resolved):
            raise PathNotAllowed(f"'{resolved}' is outside the allowed sandbox paths. Use: sandbox add <folder>")
        return resolved

    def _save(self, roots: list[Path]) -> None:
        config = self.env.read_config()
        config["sandbox_roots"] = [str(root) for root in roots if _norm(root) != _norm(self.env.SCRIPTS_DIR)]
        config["sandbox_roots"].insert(0, str(self.env.SCRIPTS_DIR))
        self.env.write_config(config)

    def add_root(self, folder: str) -> Path:
        path = Path(os.path.expandvars(folder.strip().strip('"'))).expanduser()
        if not path.is_absolute():
            raise ValueError("Give a full folder path, for example: sandbox add D:\\projects")
        if not path.is_dir():
            raise ValueError(f"Folder does not exist: {path}")
        roots = self.allowed_roots()
        if not any(_norm(r) == _norm(path) for r in roots):
            roots.append(path.resolve())
            self._save(roots)
        return path.resolve()

    def remove_root(self, folder: str) -> bool:
        target = _norm(folder.strip().strip('"'))
        if target == _norm(self.env.SCRIPTS_DIR):
            raise ValueError("The scripts folder is always allowed and cannot be removed.")
        roots = self.allowed_roots()
        kept = [r for r in roots if _norm(r) != target]
        if len(kept) == len(roots):
            return False
        self._save(kept)
        return True

    def clear_roots(self) -> None:
        self._save([self.env.SCRIPTS_DIR])


class ToolWorkbench:
    """Local execution, LLM tool-call testing, generation and promotion of Python tools."""

    LOCAL_RUN_TIMEOUT_SECONDS = 5

    MAX_RUN_TIMEOUT_SECONDS = 600

    MAX_OUTPUT_BYTES = 64 * 1024

    _FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    _CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

    GENERATE_SYSTEM_PROMPT = (
        "You write standalone Python tool functions. Reply with exactly one fenced "
        "```python code block containing one public top-level synchronous function with "
        "type-hinted parameters (str, int, float, bool, list, dict), a docstring "
        "describing it, and a JSON-serializable return value. Put any imports at the top "
        "of the block. Do not include example calls, tests, or explanations."
    )

    HARNESS_START = "# --- Novous test call (edit the values; removed when adding to the library) ---"

    HARNESS_END = "# --- end Novous test call ---"

    _HARNESS_BLOCK = re.compile(
        r"\n*" + re.escape(HARNESS_START) + r".*?" + re.escape(HARNESS_END) + r"[ \t]*\n?",
        re.DOTALL,
    )

    _PLACEHOLDERS = {
        "string": '"test"', "integer": "1", "number": "1.0",
        "boolean": "True", "array": "[]", "object": "{}",
    }

    def __init__(self, env: SandboxEnvManager):
        self.env = env

    def get_function_definition(self, source: str) -> ast.FunctionDef:
        """Return the first public, top-level synchronous function in source."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError(f"Python syntax error: {exc}") from exc

        functions = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ]
        if not functions:
            raise ValueError("Define at least one public top-level function.")
        if isinstance(functions[0], ast.AsyncFunctionDef):
            raise ValueError("The primary tool function must be synchronous.")
        if not self._FUNCTION_NAME.fullmatch(functions[0].name):
            raise ValueError("The primary function name is not a valid tool name.")
        if (functions[0].args.posonlyargs or functions[0].args.kwarg or
                functions[0].args.vararg):
            raise ValueError(
                "The primary function cannot use positional-only or variadic arguments."
            )
        return functions[0]

    def _json_type(self, annotation: ast.expr | None) -> str:
        name = None
        if isinstance(annotation, ast.Name):
            name = annotation.id
        elif isinstance(annotation, ast.Attribute):
            name = annotation.attr
        if name:
            return {
                "str": "string",
                "int": "integer",
                "float": "number",
                "bool": "boolean",
                "list": "array",
                "List": "array",
                "dict": "object",
                "Dict": "object",
            }.get(name, "string")
        if isinstance(annotation, ast.Subscript):
            value = annotation.value
            base_name = value.id if isinstance(value, ast.Name) else (
                value.attr if isinstance(value, ast.Attribute) else ""
            )
            if base_name.lower() == "list":
                return "array"
            if base_name.lower() == "dict":
                return "object"
        return "string"

    def get_tool_schema(self, function: ast.FunctionDef, description: str | None = None) -> dict:
        """The schema an agent would send for this function, built by the same code as the agent.
        ``description`` overrides the wording; otherwise the saved wording is used, then the docstring."""
        positional_args = function.args.args
        positional_defaults = (
            [None] * (len(positional_args) - len(function.args.defaults))
            + list(function.args.defaults)
        )
        keyword_args = function.args.kwonlyargs
        keyword_defaults = function.args.kw_defaults
        parameters = {}
        for argument, default in (
            list(zip(positional_args, positional_defaults))
            + list(zip(keyword_args, keyword_defaults))
        ):
            parameters[argument.arg] = default is None
        if description is None:
            docstring = ast.get_docstring(function) or ""
            description = tool_catalog.prompt_text(function.name) or (docstring.splitlines()[0] if docstring else "")
        return Agent.tool_schema(function.name, description, parameters)

    def validate_arguments(self, function: ast.FunctionDef, arguments: dict) -> str | None:
        """Return an error message when arguments don't match the function signature."""
        schema = self.get_tool_schema(function)["function"]["parameters"]
        missing = [name for name in schema["required"] if name not in arguments]
        unknown = [name for name in arguments if name not in schema["properties"]]
        if missing:
            return f"Missing required argument(s): {', '.join(missing)}"
        if unknown:
            return f"Unknown argument(s): {', '.join(unknown)}"
        return None

    def extract_code(self, text: str) -> str:
        match = self._CODE_BLOCK.search(text or "")
        return (match.group(1) if match else (text or "")).strip()

    def generate_python_function(self, description: str, model: str, chat=None) -> dict:
        """Ask the LLM for a tool function and validate it; retry once on invalid output."""
        if chat is None:
            import ollama
            chat = ollama.chat
        messages = [
            {"role": "system", "content": self.GENERATE_SYSTEM_PROMPT},
            {"role": "user", "content": description},
        ]
        last_error = "No code returned."
        for _ in range(2):
            response = chat(model=model, messages=messages)
            text = (response.get("message") or {}).get("content", "")
            code = self.extract_code(text)
            try:
                function = self.get_function_definition(code)
                return {"code": code, "function_name": function.name}
            except ValueError as exc:
                last_error = str(exc)
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That code was invalid: {last_error} Fix it and reply with one python code block."},
                ]
        raise ValueError(last_error)

    def _local_environment(self, temp_dir: str) -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", ""), "TEMP": temp_dir, "TMP": temp_dir}
        for key in ("SYSTEMROOT", "WINDIR"):
            if value := os.environ.get(key):
                env[key] = value
        if os.name == "nt":
            env["USERPROFILE"] = temp_dir
        else:
            env["HOME"] = temp_dir
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _read_output(self, output_file) -> str:
        output_file.seek(0, os.SEEK_END)
        size = output_file.tell()
        output_file.seek(max(0, size - self.MAX_OUTPUT_BYTES))
        text = output_file.read(self.MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")
        if size > self.MAX_OUTPUT_BYTES:
            return f"[Earlier output truncated]\n{text}"
        return text

    def has_test_harness(self, source: str) -> bool:
        return self.HARNESS_START in source

    def strip_test_harness(self, source: str) -> str:
        return self._HARNESS_BLOCK.sub("\n", source).rstrip() + "\n" if self.has_test_harness(source) else source

    def build_test_harness(self, source: str) -> str:
        """Return a ready-to-edit call block for the first public function in source."""
        function = self.get_function_definition(self.strip_test_harness(source))
        args = function.args
        positional = args.args
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        params = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
        lines = [self.HARNESS_START]
        for argument, default in params:
            if default is not None:
                value = ast.unparse(default)
            else:
                value = self._PLACEHOLDERS[self._json_type(argument.annotation)]
            lines.append(f"{argument.arg} = {value}")
        call_args = ", ".join(f"{argument.arg}={argument.arg}" for argument, _ in params)
        lines += [f"result = {function.name}({call_args})", "print(result)", self.HARNESS_END]
        return "\n".join(lines) + "\n"

    def _run_locally(self, source: str, arguments: dict, function_name: str, script_mode: bool = False,
                     timeout: float | None = None) -> dict:
        timeout = min(timeout or self.LOCAL_RUN_TIMEOUT_SECONDS, self.MAX_RUN_TIMEOUT_SECONDS)
        marker = f"__NOVOUS2_RESULT_{secrets.token_hex(16)}__"
        wrapper = "" if script_mode else (
            "\n\nif __name__ == '__main__':\n"
            "    import json as __novous2_json\n"
            "    __novous2_arguments = __novous2_json.loads(input() or '{}')\n"
            f"    __novous2_result = {function_name}(**__novous2_arguments)\n"
            f"    print({marker!r} + __novous2_json.dumps("
            "__novous2_result, default=str))\n"
        )

        try:
            with tempfile.TemporaryDirectory(prefix="novous2-tool-test-") as temp_dir:
                script_path = Path(temp_dir) / "tool_test.py"
                script_path.write_text(source + wrapper, encoding="utf-8")
                with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                    process = subprocess.Popen(
                        [self.env.interpreter(), "-I", str(script_path)],
                        cwd=temp_dir,
                        env=self._local_environment(temp_dir),
                        stdin=subprocess.PIPE,
                        stdout=stdout_file,
                        stderr=stderr_file,
                    )
                    try:
                        process.communicate(
                            input=json.dumps(arguments).encode("utf-8"),
                            timeout=timeout,
                        )
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                        return {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status_code": 422,
                            "error_code": "ERR_TOOL_TIMEOUT",
                            "status": "ERROR",
                            "function_name": function_name,
                            "message": (
                                f"Local function test exceeded "
                                f"{timeout:g} seconds. Raise the time limit for GUI or long-running code."
                            ),
                            "stdout": self._read_output(stdout_file),
                            "stderr": self._read_output(stderr_file),
                            "exit_code": process.returncode,
                        }

                    stdout = self._read_output(stdout_file)
                    stderr = self._read_output(stderr_file)
                    marker_line = next(
                        (
                            line for line in reversed(stdout.splitlines())
                            if line.startswith(marker)
                        ),
                        None,
                    )
                    passed = process.returncode == 0 and (script_mode or marker_line is not None)
                    try:
                        output = json.loads(marker_line[len(marker):]) if passed and not script_mode else None
                    except json.JSONDecodeError:
                        passed = False
                        output = None
                    visible_stdout = "\n".join(
                        line for line in stdout.splitlines() if not line.startswith(marker)
                    )
                    return {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status_code": 200 if passed else 422,
                        "error_code": None if passed else "ERR_TOOL_EXECUTION",
                        "status": "SUCCESS" if passed else "ERROR",
                        "function_name": function_name,
                        "output": output,
                        "stdout": visible_stdout,
                        "stderr": stderr,
                        "exit_code": process.returncode,
                    }
        except OSError as exc:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status_code": 500,
                "error_code": "ERR_LOCAL_RUNNER",
                "status": "ERROR",
                "function_name": function_name,
                "message": f"Could not start local Python: {exc}",
            }

    def run_python_tool(self, source: str, arguments: dict, timeout: float | None = None) -> dict:
        if self.has_test_harness(source):
            # The edit-mode test call is the input: run the code exactly as written.
            try:
                function = self.get_function_definition(self.strip_test_harness(source))
            except ValueError as exc:
                return {"status": "ERROR", "status_code": 400,
                        "error_code": "ERR_INVALID_TOOL", "message": str(exc)}
            return self._run_locally(source, arguments, function.name, script_mode=True, timeout=timeout)
        try:
            function = self.get_function_definition(source)
        except ValueError as exc:
            return {
                "status": "ERROR",
                "status_code": 400,
                "error_code": "ERR_INVALID_TOOL",
                "message": str(exc),
            }

        return self._run_locally(source, arguments, function.name, timeout=timeout)

    def promote_python_tool(self, source: str, expected_name: str) -> dict:
        function = self.get_function_definition(source)
        if function.name != expected_name:
            raise ValueError("The primary function changed; run the test again before adding it.")
        from tools.logic_tools import tool_registry

        return tool_registry.register_tool(source, expected_name)

    def get_custom_tool_source(self, name: str) -> dict:
        if not self._FUNCTION_NAME.fullmatch(name):
            raise ValueError("The tool name is not valid.")
        from tools.logic_tools import tool_registry

        tool = tool_registry.get_tool_source(name)
        return {**tool, "test_source": self.strip_tool_decorator(tool["source"])}

    def strip_tool_decorator(self, source: str) -> str:
        """Drop @tool decorators so the code runs in the bare test interpreter."""
        source = textwrap.dedent(source)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source
        drop = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Name) and target.id == "tool":
                    drop.update(range(decorator.lineno, decorator.end_lineno + 1))
        lines = source.splitlines()
        return "\n".join(line for number, line in enumerate(lines, 1) if number not in drop).rstrip() + "\n"

    def delete_custom_tool(self, name: str) -> dict:
        if not self._FUNCTION_NAME.fullmatch(name):
            raise ValueError("The tool name is not valid.")
        from tools.logic_tools import tool_registry

        return tool_registry.remove_tool(name)

    def _text_tool_calls(self, content: str, tool_name: str) -> list[dict]:
        """Read calls a model wrote as JSON text instead of native tool_calls, the way the chat engine does."""
        text = (content or "").strip()
        decoder = json.JSONDecoder()
        calls = []
        index = 0
        while index < len(text):
            if text[index] not in "[{":
                index += 1
                continue
            try:
                parsed, end = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                index += 1
                continue
            items = parsed if isinstance(parsed, list) else [parsed]
            found = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = item.get("function") if isinstance(item.get("function"), dict) else item
                if source.get("name") != tool_name:
                    continue
                arguments = source.get("arguments") or source.get("parameters") or source.get("args") or {}
                calls.append({"function": {"name": tool_name, "arguments": arguments}})
                found = True
            index = end if found else index + 1
        return calls

    def test_with_llm(self, tool_code: str, model: str, user_prompt: str, timeout: float,
                      execute: bool = True, description: str | None = None) -> dict:
        """Let a model call the tool through the agent's schema, then run it with those arguments.
        ``execute=False`` (dry run) stops after checking the call, so the tool never runs."""
        try:
            function = self.get_function_definition(self.strip_test_harness(tool_code))
            schema = self.get_tool_schema(function, description)
        except ValueError:
            raise

        # Use the same context size the agent would use with this model.
        num_ctx = model_profiles.effective(model).get("num_ctx")
        try:
            import ollama
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[schema],
                options={"num_ctx": num_ctx} if num_ctx else None,
            )
        except Exception as exc:
            if "does not support tools" in str(exc).lower():
                return {
                    "status": "UNSUPPORTED",
                    "error_code": "ERR_MODEL_NO_TOOL_SUPPORT",
                    "message": f"{model} does not support tool calls, so it cannot be tested here.",
                    "llm_text_response": "",
                    "tool_calls_detected": [],
                    "function_input": None,
                    "tool_execution_result": None,
                }
            raise ModelCallError(f"LLM tool-call request failed: {exc}") from exc

        message = response.get("message", {})
        tool_calls = message.get("tool_calls") or []
        llm_text_response = message.get("content", "")
        origin = "native" if tool_calls else None

        def failure(code: str, text: str, arguments=None):
            return {
                "status": "ERROR",
                "error_code": code,
                "message": text,
                "llm_text_response": llm_text_response,
                "tool_calls_detected": tool_calls,
                "function_input": arguments,
                "origin": origin,
                "tool_execution_result": None,
            }

        if not tool_calls:
            tool_calls = self._text_tool_calls(llm_text_response, function.name)
            origin = "text_json" if tool_calls else None
        if not tool_calls:
            return failure("ERR_NO_TOOL_CALL", "The selected model did not return a tool call.")

        call = tool_calls[0].get("function", {})
        if call.get("name") != function.name:
            called = str(call.get("name") or "")
            near = difflib.get_close_matches(called, [function.name], n=1, cutoff=0.8)
            hint = (f" It looks like a misspelling of '{function.name}'; a shorter, simpler tool name "
                    "is easier for models to repeat." if near else "")
            return failure("ERR_UNEXPECTED_TOOL_CALL",
                           f"The model called '{called}' instead of '{function.name}'.{hint}")
        arguments = call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return failure("ERR_INVALID_TOOL_ARGUMENTS", "The model returned invalid JSON tool arguments.")
        if not isinstance(arguments, dict):
            return failure("ERR_INVALID_TOOL_ARGUMENTS", "The model's tool arguments must be a JSON object.")
        parameters = schema["function"]["parameters"]
        arguments = wrap_flat_arguments(list(parameters["properties"]), parameters["required"], arguments)
        problem = self.validate_arguments(function, arguments)
        if problem:
            return failure("ERR_INVALID_TOOL_ARGUMENTS", problem, arguments)

        if not execute:
            return {
                "status": "CALL_VALID",
                "error_code": None,
                "message": "The model made a valid call. The tool was not run (dry run).",
                "llm_text_response": llm_text_response,
                "tool_calls_detected": tool_calls,
                "function_input": arguments,
                "origin": origin,
                "tool_execution_result": None,
            }

        execution = self.run_python_tool(self.strip_test_harness(tool_code), arguments, timeout=timeout)
        return {
            "status": execution["status"],
            "error_code": execution["error_code"],
            "llm_text_response": llm_text_response,
            "tool_calls_detected": tool_calls,
            "function_input": arguments,
            "origin": origin,
            "tool_execution_result": execution,
        }

    def promote(self, tool_code: str, function_input: dict, timeout: float) -> dict:
        """Re-test the tool, then append it to the promoted tools and the requirements."""
        try:
            function = self.get_function_definition(self.strip_test_harness(tool_code))
        except ValueError:
            raise
        execution = self.run_python_tool(self.strip_test_harness(tool_code), function_input, timeout=timeout)
        if execution["status"] != "SUCCESS":
            raise PromotionTestFailed("The tool must pass its test before promotion.", execution)

        clean_code = self.strip_test_harness(tool_code)
        promoted = self.promote_python_tool(clean_code, function.name)
        requirements_added = self.env.add_to_requirements(clean_code)
        return {"promoted": promoted, "test_result": execution, "requirements_added": requirements_added}


sandbox_env = SandboxEnvManager()
sandbox_paths = SandboxPathPolicy(sandbox_env)
tool_workbench = ToolWorkbench(sandbox_env)
