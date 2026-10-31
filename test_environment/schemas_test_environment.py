"""Sandbox test-harness data contracts: Tool Workbench and sandbox package requests."""

from typing import Any

from pydantic import BaseModel, Field


class ToolWorkbenchRequest(BaseModel):
    tool_code: str = Field(..., min_length=1, max_length=50000)
    function_input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(5, ge=1, le=600)


class ToolWorkbenchLLMRequest(ToolWorkbenchRequest):
    model: str = Field(..., min_length=1, max_length=120)
    user_prompt: str = Field(..., min_length=1, max_length=5000)


class ToolWorkbenchGenerateRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=5000)


class SandboxInstallRequest(BaseModel):
    packages: list[str] = Field(..., min_length=1, max_length=20)


class SandboxCheckRequest(BaseModel):
    source: str = Field("", max_length=50000)
    modules: list[str] = Field(default_factory=list, max_length=20)
