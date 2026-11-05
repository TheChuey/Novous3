"""Tools data contracts: catalog entries and tool-registry request bodies."""

from typing import TypedDict

from pydantic import BaseModel, Field


class ToolDefinition(TypedDict):
    """Public catalog entry returned by /api/tools and /api/tools/{name}."""
    name: str
    provider: str
    description: list[str] | str
    signature: str
    custom: bool


class RegisterToolRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=50000)
    name: str | None = None


class UpdateToolRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=50000)


class ToolPromptRequest(BaseModel):
    prompt_description: str = Field("", max_length=2000)


class MapPageRequest(BaseModel):
    path: str
    max_depth: int = 3
    include_files: bool = True
    offset: int = 0
