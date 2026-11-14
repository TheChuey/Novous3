"""Persistent, validated rule-based squad definitions."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_STEPS = 3


def clean_id(value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError("IDs must contain lowercase letters, digits, hyphens or underscores.")
    return value


def squad_path(squad_id: str) -> Path:
    path = ROOT / clean_id(squad_id)
    if path.is_symlink() or path.resolve().parent != ROOT.resolve():
        raise ValueError("Invalid squad folder.")
    return path


def read_squad(squad_id: str) -> dict:
    path = squad_path(squad_id) / "squad.yaml"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("Squad not found.")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != squad_id:
        raise ValueError("Invalid squad definition.")
    validate_flow(data)
    return data


def list_squads() -> list[dict]:
    return [read_squad(path.name) for path in sorted(ROOT.iterdir())
            if path.is_dir() and not path.is_symlink() and (path / "squad.yaml").is_file()]


def create_squad(squad_id: str, name: str) -> dict:
    squad_id = clean_id(squad_id)
    if not name.strip():
        raise ValueError("A squad name is required.")
    folder = squad_path(squad_id)
    folder.mkdir(exist_ok=False)
    data = {"id": squad_id, "name": name.strip(), "start": "", "agents": [], "routes": []}
    (folder / "squad.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (folder / "documentation.md").write_text(f"# {name.strip()}\n\n", encoding="utf-8")
    return data


def validate_flow(data: dict, runnable: bool = False) -> None:
    agents = data.get("agents")
    routes = data.get("routes")
    if not isinstance(agents, list) or not isinstance(routes, list):
        raise ValueError("Agents and routes must be lists.")
    ids = set()
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("kind") not in ("agent", "subagent"):
            raise ValueError("Each agent must have an ID and a valid kind.")
        agent_id = clean_id(agent.get("id", ""))
        if agent_id in ids or agent_id in ("squad", "documentation") or agent_id.startswith("skill-"):
            raise ValueError(f"Duplicate or reserved agent ID: {agent_id}")
        ids.add(agent_id)
    if len(ids) > 20 or len(routes) > 40:
        raise ValueError("A squad can have at most 20 agents and 40 routes.")
    start = data.get("start", "")
    if start and start not in ids:
        raise ValueError("The starting agent must belong to the squad.")
    if runnable and not start:
        raise ValueError("Choose a starting agent before running the squad.")
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or route.get("from") not in ids or route.get("to") not in ids:
            raise ValueError("Every route must connect two squad agents.")
        if route.get("on") not in ("success", "failure"):
            raise ValueError("Route outcomes must be success or failure.")
        key = (route["from"], route["on"])
        if key in seen:
            raise ValueError("An agent can have only one route for each outcome.")
        seen.add(key)


def save_flow(squad_id: str, start: str, routes: list[dict]) -> dict:
    data = read_squad(squad_id)
    data["start"], data["routes"] = start, routes
    validate_flow(data)
    (squad_path(squad_id) / "squad.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return data
