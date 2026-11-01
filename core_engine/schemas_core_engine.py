"""Core-engine data contracts: agent profiles, chat/session requests and tool metadata."""

from dataclasses import dataclass, field
from typing import Callable, List, Literal, TypedDict

from pydantic import BaseModel, Field

AgentEnvironment = Literal["workspace", "codebuilder", "prompt-creation"]


@dataclass
class AgentProfile:
    id: str = ""
    name: str = ""
    description: str = ""
    mode: str = "chat"  # "agent" attaches tools; "chat" takes none
    system_prompt: str = ""
    role: str = ""
    purpose: str = ""
    boundaries: str = ""
    model: str = ""
    tools: List[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


class CreateAgentRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    mode: str = "chat"
    squad: str = ""
    environment: AgentEnvironment = "workspace"
    environment_name: str = Field("", max_length=64)


AgentDefinitionKind = Literal["prompt", "subagent"]


class SubagentUsage(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    usage: str = Field(default="", max_length=2000)


class AgentToolsRequest(BaseModel):
    tools: List[str] = Field(default_factory=list, max_length=200)
    mode: Literal["chat", "agent"] | None = None
    # None keeps the sub-agents the prompt already lists; [] clears them.
    subagents: List[SubagentUsage] | None = Field(default=None, max_length=50)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    agent_id: str
    model: str = "qwen2.5-coder:latest"
    session_id: str | None = None
    environment: AgentEnvironment = "workspace"


class ExportSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class AnswerSubmissionRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    answers: dict = Field(default_factory=dict)


class ToolMetadata(TypedDict, total=False):
    """One ``ToolCatalog.registry`` entry."""
    name: str
    function: Callable
    provider: str
    description: list[str] | str
    signature: str
    custom: bool
