"""Shared test fixtures."""

import pytest

from orchestration.logic_orchestration import squad_registry
from modelProfiles.logic_modelProfiles import model_profiles
from scratchpadMemory.logic_scratchpadMemory import code_scratchpad, scratchpad


@pytest.fixture(autouse=True)
def isolated_code_scratchpad(tmp_path, monkeypatch):
    """Keep every test away from the real code scratchpad file."""
    path = tmp_path / "code_scratchpad.json"
    monkeypatch.setattr(code_scratchpad, "CODE_SCRATCHPAD_PATH", path)
    return path


@pytest.fixture(autouse=True)
def isolated_model_profiles(tmp_path, monkeypatch):
    """Keep tests off the real model profile file and away from a running Ollama."""
    monkeypatch.setattr(model_profiles, "PROFILES_PATH", tmp_path / "model_profiles.json")
    monkeypatch.setattr(model_profiles, "_fetch_facts", lambda model: None)
    model_profiles._facts.clear()


@pytest.fixture(autouse=True)
def isolated_agent_scratchpad(tmp_path, monkeypatch):
    """Keep every test away from the real agent scratchpad (reads, maps, sub-agent results)."""
    path = tmp_path / "scratchpad.json"
    monkeypatch.setattr(scratchpad, "SCRATCHPAD_PATH", path)
    return path


@pytest.fixture(autouse=True)
def isolated_squad_registry(tmp_path, monkeypatch):
    """Keep every test away from the real squad definitions and metrics files."""
    monkeypatch.setattr(squad_registry, "SQUADS_PATH", tmp_path / "orchestration_squads.json")
    monkeypatch.setattr(squad_registry, "METRICS_PATH", tmp_path / "orchestration_squad_metrics.json")
