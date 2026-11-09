"""Workspace domain logic: project state (``workspace/project.json``), system health,
squads and agent scaffolding in the central ``library/``."""

import json
import re
import shutil
import time
from pathlib import Path

from core_engine.logic_core_engine import AGENT_MD_TEMPLATE, agent_library, parse_yaml_frontmatter


class ProjectStateService:
    """Reads and persists the canonical project state and reports aggregated health."""

    PROJECT_FILE = Path(__file__).resolve().parent / "project.json"
    START_TIME = time.time()

    def _default_project(self) -> dict:
        return {
            "name": "Novous Workspace",
            "version": "1.0.0",
            "description": "",
            "default_model": "qwen2.5-coder:latest",
            "squad_root": "",
            "active_agent": "",
        }

    def get_project_state(self) -> dict:
        """Doorway function: read workspace/project.json."""
        if not self.PROJECT_FILE.is_file():
            return self._default_project()
        try:
            return json.loads(self.PROJECT_FILE.read_text(encoding="utf-8"))
        except Exception:
            return self._default_project()

    def save_project_state(self, state: dict) -> dict:
        """Doorway function: persist workspace/project.json."""
        current = self.get_project_state()
        current.update({k: v for k, v in state.items() if v is not None})
        self.PROJECT_FILE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        return current

    def get_health(self) -> dict:
        """Doorway function: aggregated system health."""
        ollama_ok = False
        ollama_detail = "unreachable"
        try:
            import ollama
            ollama.list()
            ollama_ok = True
            ollama_detail = "connected"
        except Exception as exc:
            ollama_detail = str(exc)

        return {
            "status": "ok" if ollama_ok else "degraded",
            "ollama": {"reachable": ollama_ok, "detail": ollama_detail},
            "uptime_seconds": round(time.time() - self.START_TIME, 1),
            "timestamp": time.time(),
        }


class SquadManager:
    """Squads are folders under ``workspace/squads`` with a small metadata file."""

    WORKSPACE_ROOT = Path(__file__).resolve().parent
    SQUAD_ROOT = WORKSPACE_ROOT / "squads"
    _NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$")

    def _slug(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

    def _meta_path(self, squad_name: str) -> Path:
        return self.SQUAD_ROOT / self._slug(squad_name) / "squad.json"

    def _validate(self, name: str) -> str:
        name = name.strip()
        if not self._NAME_PATTERN.match(name):
            raise ValueError("Squad name must be 1-64 chars (letters, digits, spaces, '-', '_').")
        return name

    def list_squads(self) -> list[dict]:
        squads = []
        if not self.SQUAD_ROOT.is_dir():
            return squads
        for child in sorted(self.SQUAD_ROOT.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            meta_file = child / "squad.json"
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            else:
                meta = {}
            squads.append({
                "id": child.name,
                "name": meta.get("name", child.name),
                "description": meta.get("description", ""),
                "path": f"squads/{child.name}",
            })
        return squads

    def create_squad(self, name: str, description: str = "") -> dict:
        name = self._validate(name)
        slug = self._slug(name)
        squad_dir = self.SQUAD_ROOT / slug
        if squad_dir.exists():
            raise ValueError(f"Squad already exists: {name}")
        squad_dir.mkdir(parents=True)
        meta = {"name": name, "description": description.strip(), "id": slug}
        (squad_dir / "squad.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return {"created": True, "squad": meta}

    def rename_squad(self, old_name: str, new_name: str) -> dict:
        new_name = self._validate(new_name)
        src = self.SQUAD_ROOT / self._slug(old_name)
        if not src.is_dir():
            raise FileNotFoundError(f"Squad not found: {old_name}")
        dst = self.SQUAD_ROOT / self._slug(new_name)
        if dst.exists():
            raise ValueError(f"Squad already exists: {new_name}")
        src.rename(dst)
        meta_file = dst / "squad.json"
        meta = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        meta["name"] = new_name
        meta["id"] = dst.name
        meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return {"renamed": True, "squad": meta}

    def delete_squad(self, name: str) -> dict:
        squad_dir = self.SQUAD_ROOT / self._slug(name)
        if not squad_dir.is_dir():
            raise FileNotFoundError(f"Squad not found: {name}")
        shutil.rmtree(squad_dir)
        return {"deleted": True, "id": squad_dir.name}


class WorkspaceWorkflow:
    """Agent scaffolding, metadata updates and squad routing for library agents."""

    _ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

    DEFAULT_TOOLS = [
        "calculator",
        "read_file",
        "write_file",
        "create_file",
        "list_directory",
        "project_status",
        "read_chat_scratchpad",
        "write_chat_scratchpad",
        "get_knowledge_catalog",
        "search_knowledge_branch",
        "list_knowledge_branch_documents",
        "read_knowledge_branch_document",
    ]

    CODEBUILDER_DEFAULT_TOOLS = [
        "calculator",
        "read_file",
        "write_file",
        "create_file",
        "list_directory",
        "run_python_code",
        "send_code_to_editor",
        "run_code_in_editor",
        "read_coding_scratchpad",
        "write_coding_scratchpad",
        "get_knowledge_catalog",
        "search_knowledge_branch",
        "list_knowledge_branch_documents",
        "read_knowledge_branch_document",
    ]

    def _validate_id(self, agent_id: str, environment: str = "workspace") -> str:
        agent_id = agent_id.strip().lower()
        if not self._ID_PATTERN.match(agent_id):
            raise ValueError(
                "agent_id must be lowercase letters, digits, '-' or '_', starting alphanumeric, max 64 chars.")
        if environment not in agent_library.AGENT_ENVIRONMENTS:
            raise ValueError(f"Unknown agent environment: {environment}")
        return agent_id

    def _agent_library_path(self, agent_id: str) -> Path:
        return agent_library.LIBRARY_ROOT / f"{agent_id}.md"

    def _render_frontmatter(self, meta: dict) -> str:
        lines = ["---"]
        for key in ["id", "name", "description", "mode", "model", "squad", "environment", "environment_name", "version"]:
            if key not in meta:
                continue
            value = meta[key]
            if value in (None, ""):
                continue
            if key == "tools":
                continue
            if isinstance(value, (bool, int, float)):
                lines.append(f"{key}: {value}")
            else:
                lines.append(f"{key}: {value}")
        if "tools" in meta and meta["tools"]:
            lines.append("tools:")
            for tool in meta["tools"]:
                lines.append(f"  - {tool}")
        if "skills" in meta and meta["skills"]:
            lines.append("skills:")
            for skill in meta["skills"]:
                if isinstance(skill, dict):
                    for skill_name, config in skill.items():
                        lines.append(f"  - {skill_name}:")
                        if isinstance(config, dict):
                            for nested_key, nested_value in config.items():
                                lines.append(f"      {nested_key}: {nested_value}")
                else:
                    lines.append(f"  - {skill}")
        lines.append("---")
        return "\n".join(lines) + "\n"

    def _read_agent_file(self, path: Path) -> tuple[dict, str]:
        if not path.exists():
            return {}, ""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}, text.strip("\n")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, text.strip("\n")
        end_index = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_index = idx
                break
        if end_index is None:
            return {}, text.strip("\n")
        frontmatter = parse_yaml_frontmatter(text)
        body = "\n".join(lines[end_index + 1:]).strip("\n")
        return frontmatter, body

    def scaffold_agent(self, agent_id: str, name: str, description: str = "",
                       mode: str = "chat", squad: str = "",
                       environment: str = "workspace",
                       environment_name: str = "") -> dict:
        """Create a single-file agent definition in library/{agent_id}.md."""
        agent_id = self._validate_id(agent_id, environment)
        environment_name = " ".join(str(environment_name or "").split())[:64]
        if not name.strip():
            raise ValueError("Agent name must not be empty.")

        library_path = self._agent_library_path(agent_id)
        if library_path.exists():
            raise ValueError(f"Agent already exists in library: {agent_id}")

        agent_library.LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)

        meta = {
            "id": agent_id,
            "name": name.strip(),
            "description": description.strip(),
            "mode": mode if mode in ("chat", "agent") else "chat",
            "model": "qwen2.5-coder:latest",
            "squad": squad.strip(),
            "tools": (
                list(self.CODEBUILDER_DEFAULT_TOOLS if environment == "codebuilder" else self.DEFAULT_TOOLS)
                if mode == "agent" else []
            ),
            "environment": environment,
            "environment_name": environment_name,
            "version": 1,
        }

        purpose = description.strip() or (
            f"Serve as the {name.strip()} for the {environment_name or environment} environment."
        )
        purpose += " Act strategically toward that goal and always deliver a clear, structured result."
        agent_template = AGENT_MD_TEMPLATE
        if environment == "codebuilder":
            agent_template = AGENT_MD_TEMPLATE.replace(
                "Novous workspace agent",
                "Novous CodeBuilder agent",
            )
            if mode == "agent":
                agent_template += """

    ## codebuilder editor workflow
    Whenever you provide or revise Python code, call `send_code_to_editor` with the complete code so it is placed in the active Monaco editor. Also include a fenced `python` code block in your reply so the user can review it and send it manually if needed. When the user asks to run the code, send any changed code first, then call `run_code_in_editor`.
    """

        markdown = agent_template.format(name=name.strip(), purpose=purpose)
        rendered = self._render_frontmatter(meta) + "\n" + markdown + "\n"
        library_path.write_text(rendered, encoding="utf-8")

        return {"created": True, "agent_id": agent_id,
                "path": f"library/{agent_id}.md",
                "environment": environment,
                "environment_name": environment_name,
                "agent": meta}

    def delete_agent(self, agent_id: str, environment: str = "workspace") -> dict:
        agent_id = self._validate_id(agent_id, environment)
        library_path = self._agent_library_path(agent_id)
        if library_path.is_file():
            metadata, _ = self._read_agent_file(library_path)
            if metadata.get("environment", "workspace") != environment:
                raise FileNotFoundError(f"Agent not found in {environment}: {agent_id}")
            library_path.unlink()
            return {"deleted": True, "agent_id": agent_id, "environment": environment, "path": f"library/{agent_id}.md"}
        raise FileNotFoundError(f"Agent not found in {environment}: {agent_id}")

    def save_agent_meta(self, agent_id: str, meta: dict) -> dict:
        """Persist edited agent metadata while preserving the markdown body."""
        agent_id = self._validate_id(agent_id)
        library_path = self._agent_library_path(agent_id)
        if not library_path.is_file():
            raise FileNotFoundError(f"Agent not found: {agent_id}")

        existing_data, body = self._read_agent_file(library_path)
        if existing_data.get("environment", "workspace") != "workspace":
            raise FileNotFoundError(f"Agent not found in workspace: {agent_id}")
        merged = dict(existing_data)
        merged.update(meta)
        merged["id"] = agent_id
        library_path.write_text(self._render_frontmatter(merged) + "\n" + body.strip("\n") + "\n", encoding="utf-8")
        return {"saved": True, "agent": merged, "path": f"library/{agent_id}.md"}

    def agents_for_squad(self, squad: str) -> list[dict]:
        """Squad routing: canonical agents filtered by their squad field."""
        agents = []
        library_root = agent_library.LIBRARY_ROOT
        if library_root.is_dir():
            for file in sorted(library_root.glob("*.md")):
                frontmatter, _ = self._read_agent_file(file)
                if frontmatter.get("environment", "workspace") == "workspace" and frontmatter.get("squad", "") == squad:
                    agents.append({**frontmatter, "path": file.relative_to(Path(__file__).resolve().parent.parent).as_posix()})
        return agents


project_state = ProjectStateService()
squad_manager = SquadManager()
workspace_workflow = WorkspaceWorkflow()
