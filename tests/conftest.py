"""Shared test fixtures."""

import pytest

from orchestration.logic_orchestration import squad_registry
from scratchpadMemory.logic_scratchpadMemory import code_scratchpad


@pytest.fixture(autouse=True)
def isolated_code_scratchpad(tmp_path, monkeypatch):
    """Keep every test away from the real code scratchpad file."""
    path = tmp_path / "code_scratchpad.json"
    monkeypatch.setattr(code_scratchpad, "CODE_SCRATCHPAD_PATH", path)
    return path


@pytest.fixture(autouse=True)
def isolated_squad_registry(tmp_path, monkeypatch):
    """Keep every test away from the real squad definitions and metrics files."""
    monkeypatch.setattr(squad_registry, "SQUADS_PATH", tmp_path / "orchestration_squads.json")
    monkeypatch.setattr(squad_registry, "METRICS_PATH", tmp_path / "orchestration_squad_metrics.json")
