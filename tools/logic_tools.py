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

from core_engine.logic_core_engine import agent_library, current_agent_id, tool, tool_catalog
from editor.logic_editor import editor_files
from file_manager.logic_file_manager import file_backend
from ragMemory import interface_ragMemory as rag
from scratchpadMemory.logic_scratchpadMemory import code_scratchpad, scratchpad


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

@tool(provider="scratchpadMemory")
def read_agent_scratchpad(memory_type: str = "index", item: str = "", offset: str = "0", max_chars: str = "4000") -> str:
    """Recall what you have read or saved: call this when the user asks what you remember, to list or summarize saved files, or about earlier content; no arguments lists items, item=<path or id> reads one in pages.

    With no arguments, returns an index of saved items (id, kind, label, size, time, preview).
    With item set to a file path or id, returns one page of that item's text, at most max_chars
    long, starting at offset. Pass next_offset from the result as offset to read the next page.
    memory_type 'latest_read', 'latest_map' or 'latest_subagent_result' returns the newest of that kind.
    """
    return json.dumps(scratchpad.recall(memory_type, item, offset, max_chars), ensure_ascii=False)


@tool(provider="orchestration")
def call_subagent(subagent_id: str, task_prompt: str, parameters: str = "{}") -> str:
    """Dynamically invoke a background sub-agent from subAgentLibrary/ with a task prompt and parameters."""
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


@tool(provider="scratchpadMemory")
def write_agent_scratchpad(
    subagent_id: str = "subagent",
    task_type: str = "general",
    status: str = "success",
    payload: str = "",
) -> str:
    """Write structured sub-agent execution payloads into the shared scratchpad."""
    from orchestration.logic_orchestration import (
        TASK_TYPE_BY_AGENT,
        codebuilder_run_state,
        telemetry_bus,
    )

    # Inside a trace, the active code agent's identity wins over what the model passed.
    trace_context = telemetry_bus.current_context()
    if trace_context is not None:
        trace_id, active_agent_id = trace_context
        if active_agent_id in TASK_TYPE_BY_AGENT:
            subagent_id = active_agent_id
            task_type = TASK_TYPE_BY_AGENT[active_agent_id]

    parsed_payload = payload
    if isinstance(payload, str) and payload.strip().startswith(("[", "{")):
        try:
            parsed_payload = json.loads(payload)
        except Exception:
            parsed_payload = payload

    # An empty write used to report success with nothing stored; fail so the model retries.
    if parsed_payload is None or (isinstance(parsed_payload, str) and not parsed_payload.strip()) \
            or parsed_payload in ({}, []):
        raise ValueError(
            'payload is empty. Pass a JSON object with the results, for example '
            '{"answers": {}, "summary": "..."}; use an empty answers object if none were given.'
        )

    result = scratchpad.store_subagent_result(
        subagent_id=subagent_id,
        task_type=task_type,
        status=status,
        payload=parsed_payload,
    )
    if trace_context is not None:
        codebuilder_run_state.save_handoff(trace_id, subagent_id, task_type, status, parsed_payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


_CODE_SCRATCHPAD_TOOL_KINDS = {"note", "plan", "code", "test", "fix"}
_CODE_SCRATCHPAD_READ_LIMIT = 4_000


@tool(provider="scratchpadMemory")
def read_code_scratchpad(kind: str = "", limit: str = "10") -> str:
    """Read the coding agents' shared scratchpad, newest first.

    kind filters by entry type: plan, code, test, fix, summary, or note ('' for all).
    """
    try:
        count = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        count = 10
    compact = []
    for entry in code_scratchpad.entries(limit=count, kind=str(kind or "").strip()):
        content = entry.get("content", "")
        item = {key: entry.get(key) for key in ("author", "kind", "title", "run_id", "created_at")}
        item["content"] = content[:_CODE_SCRATCHPAD_READ_LIMIT]
        if len(content) > _CODE_SCRATCHPAD_READ_LIMIT:
            item["content_truncated"] = True
        compact.append(item)
    return json.dumps({"entries": compact}, ensure_ascii=False, indent=2)


@tool(provider="scratchpadMemory")
def post_code_scratchpad(title: str, content: str, kind: str = "note") -> str:
    """Post an entry to the coding agents' shared scratchpad so the code squad and other agents see it.

    kind is note (default), plan, code, test, or fix.
    """
    kind = str(kind or "note").strip().lower()
    if kind not in _CODE_SCRATCHPAD_TOOL_KINDS:
        raise ValueError("kind must be one of: " + ", ".join(sorted(_CODE_SCRATCHPAD_TOOL_KINDS)))
    if not str(content or "").strip():
        raise ValueError("content must not be empty.")
    entry = code_scratchpad.post(current_agent_id() or "agent", kind, title, content, run_id="chat")
    return json.dumps(
        {"posted": True, "id": entry["id"], "author": entry["author"], "kind": entry["kind"]},
        ensure_ascii=False,
    )


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
    return json.dumps(tree, ensure_ascii=False, indent=2)


map_files.display_output = True


@tool(provider="tools")
def read_file(path: str, request: str = "") -> str:
    """Read a UTF-8 text or Docling-supported document from the configured file-manager root.

    PDF, DOCX, PPTX, and ODT documents are text-extracted with Docling; OCR is enabled
    for PDFs to support scanned pages. Text files are limited to 1 MB; documents to
    25 MB and 1 MB of extracted text. Successful reads are saved to agent memory in full.
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
    page = scratchpad.window(content, 0, scratchpad.RECALL_DEFAULT_CHARS)
    if page["truncated"]:
        context = (
            f"Showing the first {page['returned_chars']} of {page['total_chars']} characters. "
            f"The full text is saved in the scratchpad. Call read_agent_scratchpad(item='{result['path']}', "
            f"offset='{page['next_offset']}') for the next page. Do not call read_file again for this file."
        )
    else:
        context = (
            f"Full text returned. Also saved in the scratchpad for later recall: "
            f"read_agent_scratchpad(item='{result['path']}')."
        )
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
