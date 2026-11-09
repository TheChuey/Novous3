"""Tools domain logic: the unified workspace file operations behind every agent file
tool, changed-line code previews, custom-tool promotion into ``tools/promoted_tools.py``,
catalog queries, and the agent-facing tool functions themselves (registered with the
core tool catalog).

Every file path goes through ``file_manager``'s ``FileBackendService``: paths are
validated against ``tools/allowed_paths.json`` and constrained to the active root.
"""

import ast
import inspect
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from core_engine.logic_core_engine import (
    agent_library, current_agent_id, current_session_id, tool, tool_catalog,
)
from editor.logic_editor import editor_files
from file_manager.logic_file_manager import file_backend
from ragMemory import interface_ragMemory as rag
from scratchpadMemory.logic_scratchpadMemory import (
    available_scratchpad_pairs, chat_scratchpad, code_scratchpad, match_scratchpad_entries,
    scratchpad, squad_scratchpad,
)


class FileReadLimitError(ValueError):
    """Raised when a bounded file read exceeds its configured byte limit."""


class ToolNotFoundError(FileNotFoundError):
    """Raised when a registered or promoted tool does not exist."""


class ToolConflictError(RuntimeError):
    """Raised when promoting a tool whose name is already registered."""


class ToolReadOnlyError(PermissionError):
    """Raised when code edits target a built-in tool, which only promoted tools may change."""


class WorkspaceFileTools:
    """File operations used by the agent tools, plus Docling-backed document reading."""

    MAX_READ_SIZE_BYTES = 1_000_000  # ~1 MB. Raise or lower this cap here.
    MAX_DOCUMENT_BYTES = 25_000_000
    MAX_EXTRACTED_TEXT_BYTES = 1_000_000
    _UTF8 = "utf-8"
    DOCLING_EXTENSIONS = {".pdf", ".docx", ".pptx", ".odt"}

    def __init__(self):
        self._doc_converter = None

    def map_files(self, path: str = "") -> dict:
        """Return the file-manager directory tree for a workspace-relative path."""
        return file_backend.directory_tree(self._workspace_relative(path))

    def _workspace_relative(self, path: str) -> str:
        """Map '', '.', '/', and absolute paths inside the workspace root to relative paths."""
        clean = (path or "").strip().replace("\\", "/")
        if clean in ("", ".", "./", "/"):
            return ""
        if clean.startswith("/") or (len(clean) > 1 and clean[1] == ":"):
            try:
                relative = Path(clean).resolve().relative_to(file_backend.workspace_root())
            except ValueError:
                return clean
            return relative.as_posix() if relative.parts else ""
        return clean[2:] if clean.startswith("./") else clean

    def list_directory(self, path: str = "") -> dict:
        """Return file-manager entries for a workspace-relative directory."""
        return file_backend.list_directory(path)

    def read_file(self, path: str) -> dict:
        """Read a file through the configured file-manager root."""
        return file_backend.read_file(path)

    def read_file_bytes(self, path: str, max_bytes: int) -> dict:
        """Read bounded binary contents through the configured file-manager root."""
        resolved = file_backend.resolve_workspace_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        with resolved.open("rb") as source:
            content = source.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise FileReadLimitError(
                f"File exceeds the {max_bytes}-byte read limit: {path}"
            )
        return {
            "path": resolved.relative_to(file_backend.workspace_root()).as_posix(),
            "resolved_path": resolved,
            "size_bytes": len(content),
            "content": content,
        }

    def write_file(self, path: str, content: str) -> dict:
        """Write a file through the configured file-manager root and publish its changed lines."""
        resolved = file_backend.resolve_workspace_path(path)
        previous_content = resolved.read_text(encoding="utf-8") if resolved.is_file() else ""
        result = file_backend.write_file(path, content)
        code_change_preview.record_applied(result["path"], previous_content, content)
        return result

    def delete_file(self, path: str) -> dict:
        """Delete a path through the configured file-manager root."""
        return file_backend.delete_path(path)

    def render_map_page(self,
        path: str,
        max_depth: int = 3,
        include_files: bool = True,
        offset: int = 0,
    ) -> dict:
        """Render a page of the active file-manager root's directory tree."""
        depth = max(1, min(int(max_depth), 6))
        start = max(0, int(offset))
        listing = self.map_files(path)
        lines = [f"📁 {listing['name']}"]
        entry_count = [0]

        def append_children(node: dict, level: int, prefix: str = "") -> None:
            if level <= 0:
                return
            children = node.get("children", [])
            visible = [
                child for child in children
                if child.get("type") == "directory" or include_files
            ]
            for index, child in enumerate(visible):
                if entry_count[0] >= 5000:
                    lines.append(f"{prefix}└── … scan limit of 5000 entries reached")
                    return
                entry_count[0] += 1
                last = index == len(visible) - 1
                connector = "└── " if last else "├── "
                if child.get("type") == "directory":
                    lines.append(f"{prefix}{connector}📁 {child['name']}/")
                    append_children(
                        child,
                        level - 1,
                        prefix + ("    " if last else "│   "),
                    )
                else:
                    lines.append(f"{prefix}{connector}📄 {child['name']}")

        append_children(listing, depth)
        page_size = 80
        page = lines[start:start + page_size]
        next_offset = start + len(page)
        remaining = max(0, len(lines) - next_offset)
        return {
            "text": "\n".join(page) if page else "(no more entries)",
            "next_offset": next_offset,
            "remaining": remaining,
            "total": len(lines),
        }

    def editor(self, action: str, path: str = "", content: str = "") -> dict:
        """Dispatch a file operation through the editor pillar."""
        if not isinstance(action, str):
            raise ValueError("Editor action must be a string.")
        action_clean = action.strip().lower()
        if action_clean == "read":
            return editor_files.read_file_content(path)
        if action_clean == "write":
            return editor_files.write_file_content(path, content)
        if action_clean == "tree":
            return editor_files.get_directory_tree(path)
        if action_clean == "delete":
            return editor_files.delete_path(path)
        raise ValueError(f"Unsupported editor action: '{action}'")

    def _get_docling_converter(self):
        if self._doc_converter is None:
            try:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption
            except ImportError as exc:
                raise RuntimeError(
                    "Docling is required to read PDF, DOCX, PPTX, and ODT files. "
                    "Install the project's dependencies and try again."
                ) from exc

            pdf_options = PdfPipelineOptions()
            pdf_options.do_ocr = True
            self._doc_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)
                }
            )
        return self._doc_converter

    def read_document(self, path: str) -> tuple[dict, str]:
        extension = Path(path).suffix.lower()
        is_document = extension in self.DOCLING_EXTENSIONS
        max_bytes = self.MAX_DOCUMENT_BYTES if is_document else self.MAX_READ_SIZE_BYTES

        try:
            source = self.read_file_bytes(path, max_bytes)
        except FileReadLimitError as exc:
            if is_document:
                raise ValueError(
                    f"Cannot read document '{path}': it exceeds the {self.MAX_DOCUMENT_BYTES}-byte "
                    f"limit ({self.MAX_DOCUMENT_BYTES // 1_000_000} MB). Split or compress the file "
                    "and try again."
                ) from exc
            raise ValueError(
                f"Cannot read '{path}': it exceeds the {self.MAX_READ_SIZE_BYTES}-byte "
                "text-file limit. Split the file or use a smaller file."
            ) from exc

        if is_document:
            converter = self._get_docling_converter()
            try:
                result = converter.convert(str(source["resolved_path"]))
                content = result.document.export_to_markdown()
            except Exception as exc:
                raise RuntimeError(
                    f"Docling could not extract text from '{path}'. The document may be "
                    "encrypted, damaged, unsupported, or image-only. Try an unprotected, "
                    "text-searchable PDF or another supported document."
                ) from exc
            if not content or not content.strip():
                raise ValueError(
                    f"Docling found no extractable text in '{path}'. It may be image-only, "
                    "empty, or unsupported."
                )
            extracted_size = len(content.encode(self._UTF8))
            if extracted_size > self.MAX_EXTRACTED_TEXT_BYTES:
                raise ValueError(
                    f"Extracted text from '{path}' is {extracted_size} bytes, exceeding the "
                    f"{self.MAX_EXTRACTED_TEXT_BYTES}-byte output limit. Split the document and "
                    "try again; no partial text was returned."
                )
            return source, content

        try:
            return source, source["content"].decode(self._UTF8)
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Cannot read '{path}' as UTF-8 text. Supported binary documents include "
                "PDF, DOCX, PPTX, and ODT."
            ) from exc


class CodeChangePreview:
    """Bounded changed-line diffs for code previews and applied workspace writes.

    Inside an orchestration trace the result is also published as telemetry so the
    editor can highlight the lines.
    """

    MAX_CONTENT_BYTES = 1_000_000
    MAX_HIGHLIGHTED_LINES = 120
    MAX_LINES = 5_000

    def changed_line_numbers(self, before: str, after: str, basis: str) -> list[int]:
        """Return bounded 1-based changed lines in either the old or proposed content."""
        if basis not in {"before", "after"}:
            raise ValueError("Diff basis must be 'before' or 'after'.")
        old_lines = before.splitlines()
        new_lines = after.splitlines()
        if max(len(old_lines), len(new_lines)) > self.MAX_LINES:
            raise ValueError("Code preview files cannot exceed 5,000 lines.")
        changed = set()
        for operation, old_start, old_end, new_start, new_end in SequenceMatcher(
            a=old_lines, b=new_lines, autojunk=False
        ).get_opcodes():
            if operation == "equal":
                continue
            lines, start, end = (
                (old_lines, old_start, old_end)
                if basis == "before"
                else (new_lines, new_start, new_end)
            )
            if start == end:
                anchor = start if basis == "before" else start + 1
                changed.add(max(1, min(anchor, len(lines))))
            else:
                changed.update(range(start + 1, end + 1))
            if len(changed) >= self.MAX_HIGHLIGHTED_LINES:
                break
        return sorted(changed)[:self.MAX_HIGHLIGHTED_LINES]

    def _read_current_file(self, path: str) -> tuple[str, str, bool]:
        if not isinstance(path, str) or not path.strip() or len(path) > 512:
            raise ValueError("Provide a workspace-relative file path of at most 512 characters.")
        resolved = file_backend.resolve_workspace_path(path.strip())
        relative_path = resolved.relative_to(file_backend.workspace_root()).as_posix()
        if resolved.exists() and not resolved.is_file():
            raise IsADirectoryError(f"Code preview target is not a file: {relative_path}")
        exists = resolved.is_file()
        if exists and resolved.stat().st_size > self.MAX_CONTENT_BYTES:
            raise ValueError("The existing file exceeds the 1 MB code preview limit.")
        current = resolved.read_text(encoding="utf-8") if exists else ""
        return relative_path, current, exists

    def _publish(self, event_type: str, relative_path: str, lines: list[int], file_exists: bool) -> None:
        from orchestration.logic_orchestration import telemetry_bus

        context = telemetry_bus.current_context()
        if context is None:
            return
        trace_id, agent_id = context
        telemetry_bus.log_event(
            trace_id, event_type, agent_id, "editor",
            {"path": relative_path, "changed_lines": lines, "file_exists": file_exists},
        )

    def preview(self, path: str, proposed_content: str) -> dict:
        """Compare proposed code with the current workspace file and notify the editor."""
        if not isinstance(proposed_content, str):
            raise ValueError("Proposed file content must be text.")
        if len(proposed_content.encode("utf-8")) > self.MAX_CONTENT_BYTES:
            raise ValueError("Code preview content exceeds the 1 MB code preview limit.")
        relative_path, current, exists = self._read_current_file(path)
        lines = self.changed_line_numbers(current, proposed_content, "before")
        self._publish("CODE_CHANGE_PREVIEW", relative_path, lines, exists)
        return {
            "status": "success" if lines else "nothing_found",
            "path": relative_path,
            "changed_lines": lines,
            "file_exists": exists,
        }

    def record_applied(self, relative_path: str, previous_content: str, written_content: str) -> None:
        """Publish changed line numbers after a workspace file write succeeds."""
        from orchestration.logic_orchestration import codebuilder_run_state, telemetry_bus

        lines = self.changed_line_numbers(previous_content, written_content, "after")
        context = telemetry_bus.current_context()
        if context is not None:
            codebuilder_run_state.save_workspace_source(context[0], relative_path, written_content)
        self._publish("CODE_CHANGE_APPLIED", relative_path, lines, True)


class ToolRegistryService:
    """Custom-tool promotion/removal and catalog views with custom-tool flags."""

    PROMOTED_MODULE_PATH = Path(__file__).resolve().parent / "promoted_tools.py"
    PROMOTED_MARKER = "# PROMOTION_MARKER: tools appended below will be registered as custom tools"
    REPO_ROOT = Path(__file__).resolve().parent.parent
    TOOL_IMPORT = "from core_engine.logic_core_engine import tool"
    LEGACY_TOOL_IMPORT = "from core_engine.tool_catalog import tool"
    _FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def _ensure_promoted_file(self) -> None:
        if not self.PROMOTED_MODULE_PATH.exists():
            self.PROMOTED_MODULE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.PROMOTED_MODULE_PATH.write_text(
                '"""Permanent storage for promoted custom tools."""\n'
                'from core_engine.logic_core_engine import tool\n\n'
                f"{self.PROMOTED_MARKER}\n\n",
                encoding="utf-8",
                newline="\n",
            )
        else:
            content = self.PROMOTED_MODULE_PATH.read_text(encoding="utf-8")
            if self.LEGACY_TOOL_IMPORT in content:
                content = content.replace(self.LEGACY_TOOL_IMPORT, self.TOOL_IMPORT)
                self.PROMOTED_MODULE_PATH.write_text(content, encoding="utf-8", newline="\n")
            if self.PROMOTED_MARKER not in content:
                sep = "\n\n" if not content.endswith("\n") else "\n"
                self.PROMOTED_MODULE_PATH.write_text(
                    content + sep + self.PROMOTED_MARKER + "\n\n",
                    encoding="utf-8",
                    newline="\n",
                )

    def list_promoted_tool_names(self) -> list[str]:
        """Return names of promoted custom tools defined after the marker in promoted_tools.py."""
        if not self.PROMOTED_MODULE_PATH.is_file():
            return []
        try:
            source = self.PROMOTED_MODULE_PATH.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            return []
        marker_line = 0
        for line_number, line in enumerate(source.splitlines(), start=1):
            if line.startswith("# PROMOTION_MARKER"):
                marker_line = line_number
                break
        names = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if node.lineno > marker_line:
                    names.append(node.name)
        return names

    def _decorated_source(self, source: str, function: ast.FunctionDef) -> str:
        """Ensure the primary function has the @tool decorator."""
        for decorator in function.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Name) and target.id == "tool":
                return source.rstrip() + "\n"

        lines = source.splitlines()
        start_line = min(
            (decorator.lineno for decorator in function.decorator_list),
            default=function.lineno,
        )
        lines.insert(start_line - 1, "@tool")
        return "\n".join(lines).rstrip() + "\n"

    def _function_span(self, source: str, name: str) -> tuple[int, int, ast.FunctionDef]:
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                start_line = min(
                    (decorator.lineno for decorator in node.decorator_list),
                    default=node.lineno,
                )
                return start_line - 1, node.end_lineno, node
        raise ToolNotFoundError(f"Custom tool '{name}' was not found in the promoted module.")

    def _function_source(self, source: str, name: str) -> str:
        start, end, _ = self._function_span(source, name)
        return "\n".join(source.splitlines()[start:end]).strip() + "\n"

    def _remove_function(self, source: str, name: str) -> str:
        start, end, _ = self._function_span(source, name)
        lines = source.splitlines(keepends=True)
        while end < len(lines) and lines[end].strip() == "":
            end += 1
        del lines[start:end]
        return "".join(lines)

    def register_tool(self, source: str, name: str | None = None) -> dict:
        """Register a custom tool by appending to promoted_tools.py and execing into that module."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError(f"Python syntax error: {exc}") from exc

        fn_node = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                fn_node = node
                break
        if fn_node is None:
            raise ValueError("Define at least one public top-level function.")

        func_name = fn_node.name
        if name and name != func_name:
            raise ValueError("The primary function changed; run the test again before adding it.")
        name = func_name

        self._ensure_promoted_file()
        tool_catalog.load_tools()
        if name in tool_catalog.registry:
            raise ToolConflictError(f"Tool '{name}' is already registered.")

        previous_content = self.PROMOTED_MODULE_PATH.read_text(encoding="utf-8")
        block = self._decorated_source(source, fn_node)
        sep = "\n" if not previous_content.endswith("\n\n") else ""
        new_content = previous_content + sep + block + "\n"
        self.PROMOTED_MODULE_PATH.write_text(new_content, encoding="utf-8", newline="\n")

        try:
            promoted_module = sys.modules.get("tools.promoted_tools")
            namespace = (
                promoted_module.__dict__
                if promoted_module is not None
                else {"tool": tool_catalog.tool, "__name__": "tools.promoted_tools"}
            )
            exec(block, namespace)
        except Exception as exc:
            self.PROMOTED_MODULE_PATH.write_text(previous_content, encoding="utf-8", newline="\n")
            tool_catalog.registry.pop(name, None)
            raise ValueError(f"Failed to execute tool '{name}': {exc}") from exc

        fn = tool_catalog.registry[name]["function"]
        tool_catalog.registry[name]["custom"] = True
        return {
            "name": name,
            "path": "tools/promoted_tools.py",
            "description": ast.get_docstring(fn_node) or "",
            "signature": str(inspect.signature(fn)),
        }

    def remove_tool(self, name: str) -> dict:
        """Remove a custom tool from promoted_tools.py and tool_catalog.registry."""
        tool_catalog.load_tools()
        if name not in tool_catalog.registry:
            raise ToolNotFoundError(f"Tool '{name}' not found.")

        self._ensure_promoted_file()
        previous_content = self.PROMOTED_MODULE_PATH.read_text(encoding="utf-8")
        new_content = self._remove_function(previous_content, name)

        try:
            self.PROMOTED_MODULE_PATH.write_text(new_content, encoding="utf-8", newline="\n")
        except Exception as exc:
            self.PROMOTED_MODULE_PATH.write_text(previous_content, encoding="utf-8", newline="\n")
            raise OSError(f"Could not update promoted module: {exc}") from exc

        tool_catalog.registry.pop(name, None)
        return {
            "name": name,
            "path": "tools/promoted_tools.py",
            "deleted": True,
        }

    def get_tool_source(self, name: str) -> dict:
        """Retrieve source code of a tool (either promoted or builtin)."""
        if not self._FUNCTION_NAME.fullmatch(name):
            raise ValueError("The tool name is not valid.")

        if self.PROMOTED_MODULE_PATH.is_file():
            full_source = self.PROMOTED_MODULE_PATH.read_text(encoding="utf-8")
            try:
                source = self._function_source(full_source, name)
                return {
                    "name": name,
                    "path": "tools/promoted_tools.py",
                    "source": source,
                }
            except FileNotFoundError:
                pass

        tool_catalog.load_tools()
        meta = tool_catalog.registry.get(name)
        if meta and "function" in meta:
            fn = meta["function"]
            try:
                source = inspect.getsource(fn)
                sourcefile = inspect.getsourcefile(fn)
                rel_path = (
                    str(Path(sourcefile).relative_to(self.REPO_ROOT).as_posix())
                    if sourcefile
                    else "tools"
                )
                return {
                    "name": name,
                    "path": rel_path,
                    "source": source,
                }
            except Exception:
                pass

        raise ToolNotFoundError(f"Custom tool '{name}' was not found.")

    def update_tool(self, name: str, source: str) -> dict:
        """Replace a promoted tool's function in promoted_tools.py and re-register it in place."""
        if not self._FUNCTION_NAME.fullmatch(name):
            raise ValueError("The tool name is not valid.")
        if name not in self.list_promoted_tool_names():
            raise ToolReadOnlyError(f"Tool '{name}' is built in; only custom tools can be edited.")

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError(f"Python syntax error: {exc}") from exc
        fn_node = next(
            (node for node in tree.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")),
            None,
        )
        if fn_node is None:
            raise ValueError("Define at least one public top-level function.")
        if fn_node.name != name:
            raise ValueError(f"The function must stay named '{name}'.")

        previous_content = self.PROMOTED_MODULE_PATH.read_text(encoding="utf-8")
        start, end, _ = self._function_span(previous_content, name)
        block = self._decorated_source(source, fn_node)
        lines = previous_content.splitlines(keepends=True)
        self.PROMOTED_MODULE_PATH.write_text(
            "".join(lines[:start]) + block + "".join(lines[end:]),
            encoding="utf-8",
            newline="\n",
        )

        previous_meta = tool_catalog.registry.pop(name, None)
        try:
            promoted_module = sys.modules.get("tools.promoted_tools")
            namespace = (
                promoted_module.__dict__
                if promoted_module is not None
                else {"tool": tool_catalog.tool, "__name__": "tools.promoted_tools"}
            )
            exec(block, namespace)
        except Exception as exc:
            self.PROMOTED_MODULE_PATH.write_text(previous_content, encoding="utf-8", newline="\n")
            if previous_meta is not None:
                tool_catalog.registry[name] = previous_meta
            raise ValueError(f"Failed to execute tool '{name}': {exc}") from exc

        fn = tool_catalog.registry[name]["function"]
        tool_catalog.registry[name]["custom"] = True
        return {
            "name": name,
            "path": "tools/promoted_tools.py",
            "description": ast.get_docstring(fn_node) or "",
            "signature": str(inspect.signature(fn)),
        }

    def get_tool_details(self, name: str) -> dict:
        """Source, docstring and the saved prompt wording for one tool, for the Tool Library editor."""
        tool_catalog.load_tools()
        meta = tool_catalog.registry.get(name)
        if meta is None:
            raise ToolNotFoundError(f"Tool '{name}' not found.")
        source = self.get_tool_source(name)
        docstring = " ".join(meta["description"]) if isinstance(meta["description"], list) else meta["description"]
        return {
            "name": name,
            "provider": meta["provider"],
            "signature": meta["signature"],
            "custom": name in self.list_promoted_tool_names() or meta.get("custom", False),
            "path": source["path"],
            "source": source["source"],
            "docstring": docstring,
            "prompt_description": tool_catalog.prompt_overrides().get(name, ""),
            "prompt_preview": tool_catalog.prompt_text(name) or docstring,
        }

    def set_prompt_description(self, name: str, text: str) -> dict:
        """Save the LLM-facing wording for a tool and rewrite agents that already list it."""
        if len(text) > 2000:
            raise ValueError("The prompt description is longer than 2000 characters.")
        tool_catalog.set_prompt_description(name, text)
        return {
            "name": name,
            "prompt_description": tool_catalog.prompt_overrides().get(name, ""),
            "prompt_preview": tool_catalog.prompt_text(name) or "",
            "refreshed_agents": agent_library.refresh_tool_sections(name),
        }

    def list_tools(self) -> list[dict]:
        """Return tool metadata with custom-tool flags."""
        tool_catalog.load_tools()
        promoted = set(self.list_promoted_tool_names())
        tools = []
        for meta in tool_catalog.registry.values():
            tools.append({
                "name": meta["name"],
                "provider": meta["provider"],
                "description": meta["description"],
                "signature": meta["signature"],
                "custom": meta["name"] in promoted or meta.get("custom", False),
            })
        return tools

    def list_tools_directory(self) -> list[dict]:
        """Return registered tools defined by modules in the tools package."""
        return [
            tool
            for tool in self.list_tools()
            if (
                (meta := tool_catalog.registry[tool["name"]])["function"].__module__.startswith(
                    "tools."
                )
                and meta["function"].__module__ != "tools.interface_tools"
            )
        ]

    def get_tool(self, name: str) -> Callable | None:
        """Resolve registered tool callable."""
        tool_catalog.load_tools()
        meta = tool_catalog.registry.get(name)
        return meta["function"] if meta else None

    def provider_bindings(self) -> dict[str, str]:
        """Return provider mappings."""
        return tool_catalog.get_provider_bindings()

    def catalog_report(self) -> dict:
        """Return catalog diagnostic report."""
        return tool_catalog.get_catalog_report()


workspace_files = WorkspaceFileTools()
code_change_preview = CodeChangePreview()
tool_registry = ToolRegistryService()


# --- Agent-facing tools ----------------------------------------------------------------

# Scratchpad tools: one write and one read for each store (coding, chat, squad). A write saves
# content under an agent name and a title. A read returns one entry picked by agent, title and
# index (0 = newest), so the model asks for something specific instead of the whole store.
_CODE_SCRATCHPAD_TOOL_KINDS = {"note", "plan", "code", "test", "fix"}
_SCRATCHPAD_READ_LIMIT = 8_000


def _scratchpad_writer(agent: str) -> str:
    return str(agent or "").strip() or current_agent_id() or "agent"


def _scratchpad_key(agent: str, title: str, index: str) -> tuple[str, str, int]:
    agent = str(agent or "").strip()
    title = str(title or "").strip()
    if not agent or not title:
        raise ValueError("agent and title are required: pass the agent name and the title the entry was saved under.")
    try:
        position = int(str(index or "0").strip() or "0")
    except ValueError as exc:
        raise ValueError("index must be a whole number: 0 is the newest match.") from exc
    if position < 0:
        raise ValueError("index must be 0 or greater: 0 is the newest match.")
    return agent, title, position


def _scratchpad_offset(offset: str) -> int:
    try:
        value = int(str(offset or "0").strip() or "0")
    except ValueError as exc:
        raise ValueError("offset must be a whole number of characters.") from exc
    if value < 0:
        raise ValueError("offset must be 0 or greater.")
    return value


def _scratchpad_write_args(agent: str, title: str, content: str) -> tuple[str, str]:
    title = str(title or "").strip()
    if not title or not str(content or "").strip():
        raise ValueError("title and content must not be empty.")
    return _scratchpad_writer(agent), title


def _scratchpad_recall(store: str, entries: list[dict], agent: str, title: str, index: int, offset: int) -> str:
    """One entry chosen by agent, title and index, returned as a page of its content."""
    matches = match_scratchpad_entries(entries, agent, title)
    if not matches:
        return json.dumps({
            "found": False, "store": store,
            "message": f"No {store} entry from '{agent}' titled '{title}'.",
            "available": available_scratchpad_pairs(entries),
        }, ensure_ascii=False, indent=2)
    if index >= len(matches):
        return json.dumps({
            "found": False, "store": store,
            "message": f"Only {len(matches)} matching {store} entries. Use an index from 0 to {len(matches) - 1}.",
            "available": available_scratchpad_pairs(entries),
        }, ensure_ascii=False, indent=2)
    entry = matches[index]
    content = str(entry.get("content", ""))
    page = content[offset: offset + _SCRATCHPAD_READ_LIMIT]
    result = {
        "found": True, "store": store, "agent": entry.get("author", ""), "title": entry.get("title", ""),
        "index": index, "total_matches": len(matches), "created_at": entry.get("created_at", ""),
        "offset": offset, "total_chars": len(content), "content": page,
    }
    next_offset = offset + len(page)
    if next_offset < len(content):
        result["next_offset"] = next_offset
        result["hint"] = (f"More content. Call read_{store}_scratchpad with agent='{agent}', "
                          f"title='{title}', index='{index}', offset='{next_offset}'.")
    return json.dumps(result, ensure_ascii=False, indent=2)


def _chat_session_id() -> str:
    session_id = current_session_id()
    if not session_id:
        raise ValueError("The chat scratchpad is only available inside a chat session.")
    return session_id


def _active_squad_scratchpad() -> tuple[str, str]:
    from orchestration.logic_orchestration import telemetry_bus

    context = telemetry_bus.current_context()
    if context is None:
        raise ValueError("This tool requires an active squad run.")
    run = squad_scratchpad.get(context[0])
    if run is None or run["status"] != "running":
        raise ValueError("This tool requires an active squad run.")
    return context


def _mirror_squad_handoff(trace_id: str, agent: str, title: str, content: str) -> None:
    """Squad and code pipelines read the newest handoff from the shared agent scratchpad; keep it current."""
    from orchestration.logic_orchestration import codebuilder_run_state

    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, (dict, list)) or not payload:
        payload = {"text": content}
    scratchpad.store_subagent_result(subagent_id=agent, task_type=title, status="success", payload=payload)
    codebuilder_run_state.save_handoff(trace_id, agent, title, "success", payload)


@tool(provider="scratchpadMemory")
def write_coding_scratchpad(agent: str, title: str, content: str) -> str:
    """Save content to the coding scratchpad under an agent name and a title. Read it back with read_coding_scratchpad using the same agent and title."""
    writer, title = _scratchpad_write_args(agent, title, content)
    entry = code_scratchpad.post(writer, "note", title, content, run_id="chat")
    return json.dumps({"saved": True, "store": "coding", "agent": writer, "title": title, "id": entry["id"]},
                      ensure_ascii=False)


@tool(provider="scratchpadMemory")
def read_coding_scratchpad(agent: str, title: str, index: str = "0", offset: str = "0") -> str:
    """Read one coding scratchpad entry by agent name and title. index 0 is the newest match; use offset to read the rest of long content. If nothing matches, the result lists the agent and title pairs that exist."""
    agent, title, position = _scratchpad_key(agent, title, index)
    entries = code_scratchpad.entries(limit=code_scratchpad.MAX_ENTRIES)
    return _scratchpad_recall("coding", entries, agent, title, position, _scratchpad_offset(offset))


@tool(provider="scratchpadMemory")
def write_chat_scratchpad(agent: str, title: str, content: str) -> str:
    """Save content to this chat's scratchpad under an agent name and a title. Read it back with read_chat_scratchpad using the same agent and title."""
    session_id = _chat_session_id()
    writer, title = _scratchpad_write_args(agent, title, content)
    entry = chat_scratchpad.post(session_id, writer, title, content)
    return json.dumps({"saved": True, "store": "chat", "agent": writer, "title": title, "id": entry["id"]},
                      ensure_ascii=False)


@tool(provider="scratchpadMemory")
def read_chat_scratchpad(agent: str, title: str, index: str = "0", offset: str = "0") -> str:
    """Read one entry from this chat's scratchpad by agent name and title. index 0 is the newest match; use offset to read the rest of long content. If nothing matches, the result lists the agent and title pairs that exist."""
    session_id = _chat_session_id()
    agent, title, position = _scratchpad_key(agent, title, index)
    entries = chat_scratchpad.entries(session_id)
    return _scratchpad_recall("chat", entries, agent, title, position, _scratchpad_offset(offset))


@tool(provider="scratchpadMemory")
def write_squad_scratchpad(agent: str, title: str, content: str) -> str:
    """Save content to the current squad run's scratchpad under an agent name and a title. Read it back with read_squad_scratchpad using the same agent and title."""
    trace_id, _ = _active_squad_scratchpad()
    writer, title = _scratchpad_write_args(agent, title, content)
    entry = squad_scratchpad.post(trace_id, writer, "note", title, content)
    _mirror_squad_handoff(trace_id, writer, title, content)
    return json.dumps({"saved": True, "store": "squad", "agent": writer, "title": title, "id": entry["id"]},
                      ensure_ascii=False)


@tool(provider="scratchpadMemory")
def read_squad_scratchpad(agent: str, title: str, index: str = "0", offset: str = "0") -> str:
    """Read one entry from the current squad run's scratchpad by agent name and title. index 0 is the newest match; use offset to read the rest of long content. If nothing matches, the result lists the agent and title pairs that exist."""
    trace_id, _ = _active_squad_scratchpad()
    agent, title, position = _scratchpad_key(agent, title, index)
    run = squad_scratchpad.get(trace_id)
    entries = run["entries"] if run else []
    return _scratchpad_recall("squad", entries, agent, title, position, _scratchpad_offset(offset))


@tool(provider="scratchpadMemory")
def clear_squad_scratchpad(kind: str = "", author: str = "") -> str:
    """Clear entries from the current squad run's scratchpad. Optionally limit by kind or author; leave both empty to clear everything. The run itself stays active."""
    trace_id, _ = _active_squad_scratchpad()
    kind = str(kind or "").strip().lower()
    if kind and kind not in _CODE_SCRATCHPAD_TOOL_KINDS:
        raise ValueError("kind must be one of: " + ", ".join(sorted(_CODE_SCRATCHPAD_TOOL_KINDS)))
    author = str(author or "").strip()
    removed = squad_scratchpad.clear_entries(trace_id, author=author, kind=kind)
    return json.dumps({"cleared": True, "removed": removed}, ensure_ascii=False)


def _run_subagent(subagent_id: str, task_prompt: str, parameters: str) -> str:
    from orchestration.logic_orchestration import RAG_RETRIEVER_ID, rag_retriever, subagent_runner

    # A caller whose prompt lists '## Sub-agents' may only reach those; no list means unrestricted.
    caller_id = current_agent_id()
    allowed = agent_library.allowed_subagents(caller_id) if caller_id else []
    if allowed and subagent_id not in allowed:
        return json.dumps(
            {"subagent_id": subagent_id, "error": f"Agent '{caller_id}' may only call: {', '.join(allowed)}"},
            ensure_ascii=False,
            indent=2,
        )

    if subagent_id == RAG_RETRIEVER_ID:
        parsed_params = parameters
        if isinstance(parameters, str):
            try:
                parsed_params = json.loads(parameters or "{}")
            except json.JSONDecodeError:
                pass
        return rag_retriever.run(task_prompt, parsed_params)

    parsed_params = {}
    if isinstance(parameters, dict):
        parsed_params = parameters
    elif isinstance(parameters, str) and parameters.strip().startswith("{"):
        parsed_params = json.loads(parameters)
    if not isinstance(parsed_params, dict):
        raise ValueError("Sub-agent parameters must be a JSON object.")

    full_prompt = f"Task: '{task_prompt}'. Parameters: {parsed_params}"
    result = subagent_runner.run_agent_turn(subagent_id, full_prompt)
    return json.dumps(
        {"subagent_id": subagent_id, "result": result}, ensure_ascii=False, indent=2
    )


@tool(provider="orchestration")
def call_subagent(subagent_id: str, task_prompt: str, parameters: str = "{}") -> str:
    """Dynamically invoke a background sub-agent from subAgentLibrary/ with a task prompt and parameters. In a chat, its output is saved to the chat scratchpad as agent=<subagent_id>, title=result."""
    before = scratchpad.read_memory("latest_subagent_result").get("latest_subagent_result")
    output = _run_subagent(subagent_id, task_prompt, parameters)
    session_id = current_session_id()
    if session_id and output:
        chat_scratchpad.post(session_id, subagent_id, "result", output)
    after = scratchpad.read_memory("latest_subagent_result").get("latest_subagent_result")
    already_stored = after is not None and after is not before and after != before
    if not already_stored:
        try:
            payload = json.loads(output) if isinstance(output, str) else output
        except (json.JSONDecodeError, TypeError):
            payload = {"result": output}
        if not isinstance(payload, dict) or not payload:
            payload = {"result": output}
        scratchpad.store_subagent_result(
            subagent_id=subagent_id, task_type="call_subagent", status="success", payload=payload
        )
    return output


@tool(provider="ragMemory")
def get_knowledge_catalog() -> str:
    """Get the live Knowledge TOC: exact branch names, IDs, status, and indexed document/chunk counts. Use this first to find a branch ID."""
    branches = [
        {
            "name": base["name"],
            "id": str(base["id"]),
            "status": "active" if base.get("active", True) else "archived",
            "selected": bool(base.get("selected")),
            "folder_available": bool(base.get("exists")),
            "indexed_documents": int(base.get("documents", 0)),
            "chunks": int(base.get("chunks", 0)),
        }
        for base in rag.list_knowledge_sources()
    ]
    return json.dumps(
        {"title": "Knowledge TOC", "branches": branches},
        ensure_ascii=False,
        indent=2,
    )


@tool(provider="ragMemory")
def search_knowledge_branch(
    query: str, top_k: int = 5, knowledge_branch_id: str = ""
) -> str:
    """Find relevant passages for a question in one knowledge branch. Get its ID from get_knowledge_catalog and pass it as knowledge_branch_id; results are source text for answering the user."""
    return json.dumps(
        rag.search_knowledge(query, top_k, source_id=knowledge_branch_id or None),
        ensure_ascii=False,
        indent=2,
    )


@tool(provider="ragMemory")
def list_knowledge_branch_documents(knowledge_branch_id: str = "") -> str:
    """List indexed document names, paths, and chunk counts in a branch. Use get_knowledge_catalog first and pass the matching branch ID; this lists metadata, not document contents."""
    return json.dumps(
        rag.list_documents(knowledge_branch_id or None),
        ensure_ascii=False,
        indent=2,
    )


@tool(provider="ragMemory")
def read_knowledge_branch_document(
    document_path: str, knowledge_branch_id: str = ""
) -> str:
    """Read the full indexed text of one document. Use get_knowledge_catalog and list_knowledge_branch_documents first, then pass the exact listed path and branch ID."""
    document = rag.read_document(document_path, knowledge_branch_id or None)
    return json.dumps(
        {
            "path": document["path"],
            "chunks": len(document["chunks_data"]),
            "content": "\n\n".join(
                chunk["content"] for chunk in document["chunks_data"]
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(provider="tools")
def delete_file(path: str) -> str:
    """Delete a file or directory from the workspace by relative path."""
    if not path or not path.strip():
        raise ValueError("A path is required.")
    result = workspace_files.delete_file(path)
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(provider="editor")
def editor(action: str, path: str = "", content: str = "") -> str:
    """Read, write, list a tree, or delete a path through the editor pillar."""
    result = workspace_files.editor(action, path=path, content=content)
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(provider="file_manager")
def map_files(path: str = "") -> str:
    """Return a directory tree and save a copy in the shared agent-memory scratchpad."""
    tree = workspace_files.map_files(path)
    scratchpad.store_map(tree, path)
    rendered = json.dumps(tree, ensure_ascii=False, indent=2)
    session_id = current_session_id()
    if session_id:
        chat_scratchpad.post(session_id, "map_files", path or "workspace", rendered)
    return rendered


map_files.display_output = True


@tool(provider="tools")
def read_file(path: str, request: str = "") -> str:
    """Read a UTF-8 text or Docling-supported document from the configured file-manager root.

    PDF, DOCX, PPTX, and ODT documents are text-extracted with Docling; OCR is enabled
    for PDFs to support scanned pages. Text files are limited to 1 MB; documents to
    25 MB and 1 MB of extracted text. Inside a chat, the full text is saved to the chat
    scratchpad as agent=read_file, title=<path>, and read_chat_scratchpad pages through it.
    Returns JSON with the request, path, the first page of content (with next_offset
    when the file is longer), and scratchpad context for fetching the rest.
    """
    if not path or not path.strip():
        raise ValueError("A file path is required.")

    try:
        result, content = workspace_files.read_document(path)
    except Exception as exc:
        raise RuntimeError(f"Could not read '{path}': {exc}") from exc

    scratchpad_result = scratchpad.store_read(result["path"], content, request)
    session_id = current_session_id()
    if session_id and content:
        chat_scratchpad.post(session_id, "read_file", result["path"], content)
    page = scratchpad.window(content, 0, scratchpad.RECALL_DEFAULT_CHARS)
    if page["truncated"] and session_id:
        context = (
            f"Showing the first {page['returned_chars']} of {page['total_chars']} characters. "
            f"The full text is saved in this chat's scratchpad. Call read_chat_scratchpad(agent='read_file', "
            f"title='{result['path']}', offset='{page['next_offset']}') for the next part. "
            "Do not call read_file again for this file."
        )
    elif page["truncated"]:
        context = (
            f"Showing the first {page['returned_chars']} of {page['total_chars']} characters. "
            "The rest is only kept inside a chat session, so ask for the part you need instead."
        )
    else:
        context = "Full text returned."
    payload = {
        "request": request,
        "path": result["path"],
        "size_bytes": result["size_bytes"],
        "content_type": "document_text" if Path(path).suffix.lower() in workspace_files.DOCLING_EXTENSIONS else "text",
        "content": page["content"],
        "truncated": page["truncated"],
        "total_chars": page["total_chars"],
        "next_offset": page["next_offset"],
        "remaining": page["remaining"],
        "scratchpad": {
            "path": scratchpad_result["path"],
            "updated_at": scratchpad_result["updated_at"],
            "context": context,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool(provider="tools")
def write_file(path: str, content: str) -> str:
    """Write UTF-8 content to a workspace file by relative path (bare filenames go in the root).

    Overwrites existing files and creates missing parent directories.
    Supports formats such as .txt, .md, .py, .json, .html, .css, and .js.
    Returns JSON with the resolved path and bytes written.
    """
    if not path or not path.strip():
        raise ValueError("A file path is required.")
    try:
        result = workspace_files.write_file(path, content)
    except Exception as exc:
        raise RuntimeError(f"Could not write '{path}': {exc}") from exc
    return json.dumps(result, ensure_ascii=False, indent=2)
