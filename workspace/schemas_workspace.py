"""Workspace data contracts: project state, agent scaffolding and squad requests."""

from pydantic import BaseModel, Field


class ProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    default_model: str | None = None
    active_agent: str | None = None
    squad_root: str | None = None


class ScaffoldRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    mode: str = "chat"
    squad: str = ""


class SquadRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""


class SquadRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    new_name: str = Field(..., min_length=1, max_length=64)
