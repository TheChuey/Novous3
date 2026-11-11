"""Data contracts for squad-folder management."""

from pydantic import BaseModel, Field


class SquadRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""


class SquadRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    new_name: str = Field(..., min_length=1, max_length=64)
