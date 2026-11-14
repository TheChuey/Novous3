"""Squad notes and orchestration-backed experiments."""

from squad_developement import agent_design, flow_design
from orchestration.logic_orchestration import run_service


def get_notes(squad_id: str) -> str:
    path = flow_design.squad_path(squad_id) / "documentation.md"
    flow_design.read_squad(squad_id)
    if path.is_symlink():
        raise ValueError("Documentation cannot be a symbolic link.")
    return path.read_text(encoding="utf-8")


def save_notes(squad_id: str, content: str) -> str:
    get_notes(squad_id)
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Notes must be at most 1 MB.")
    (flow_design.squad_path(squad_id) / "documentation.md").write_text(content, encoding="utf-8")
    return content


def start_experiment(squad_id: str, query: str) -> dict:
    if not query.strip():
        raise ValueError("A test request is required.")
    data = flow_design.read_squad(squad_id)
    flow_design.validate_flow(data, runnable=True)
    profiles = {item["id"]: agent_design.parse_agent(squad_id, item["id"]) for item in data["agents"]}
    return run_service.start_rule_flow(squad_id, query.strip(), data, profiles, flow_design.MAX_STEPS)
