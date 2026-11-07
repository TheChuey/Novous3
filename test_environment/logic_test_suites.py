"""Saved test suites: one JSON file per suite, grouped by category, plus run history.

Layout:
    workspace/saved_test_suites/<category>/<slug>.json          suite definition
    workspace/saved_test_suites/results/<category>/<slug>.json  recent runs (newest first)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SUITES_ROOT = REPO_ROOT / "workspace" / "saved_test_suites"
CATEGORIES = ("squads", "tool_usage", "prompts")
MAX_RUNS_KEPT = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")[:80].strip("-")
    if not slug:
        raise ValueError("Suite name must contain at least one letter or number.")
    return slug


def _check_category(category: str) -> str:
    if category not in CATEGORIES:
        raise ValueError(f"Category must be one of: {', '.join(CATEGORIES)}.")
    return category


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.stem}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class TestSuiteStore:
    def suite_path(self, category: str, slug: str) -> Path:
        return SUITES_ROOT / _check_category(category) / f"{slugify(slug)}.json"

    def results_path(self, category: str, slug: str) -> Path:
        return SUITES_ROOT / "results" / _check_category(category) / f"{slugify(slug)}.json"

    def list_suites(self, category: str | None = None) -> list[dict[str, Any]]:
        categories = [_check_category(category)] if category else list(CATEGORIES)
        suites: list[dict[str, Any]] = []
        for name in categories:
            folder = SUITES_ROOT / name
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    suites.append(_read_json(path))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(suites, key=lambda suite: (suite.get("category", ""), suite.get("name", "")))

    def get_suite(self, category: str, slug: str) -> dict[str, Any]:
        path = self.suite_path(category, slug)
        if not path.is_file():
            raise FileNotFoundError(f"No saved suite {category}/{slug}.")
        return _read_json(path)

    def save_suite(self, payload: dict[str, Any]) -> dict[str, Any]:
        category = _check_category(payload["category"])
        slug = slugify(payload["name"])
        path = self.suite_path(category, slug)
        now = _now()
        created = now
        if path.is_file():
            try:
                created = _read_json(path).get("created", now)
            except (OSError, json.JSONDecodeError):
                created = now
        suite = {
            "id": slug,
            "name": payload["name"].strip(),
            "category": category,
            "description": payload.get("description", "") or "",
            "agent_id": payload["agent_id"],
            "model": payload["model"],
            "steps": payload["steps"],
            "created": created,
            "updated": now,
        }
        _write_json_atomic(path, suite)
        return suite

    def delete_suite(self, category: str, slug: str) -> None:
        path = self.suite_path(category, slug)
        if not path.is_file():
            raise FileNotFoundError(f"No saved suite {category}/{slug}.")
        path.unlink()
        results = self.results_path(category, slug)
        if results.is_file():
            results.unlink()

    def get_results(self, category: str, slug: str) -> dict[str, Any]:
        path = self.results_path(category, slug)
        if not path.is_file():
            return {"runs": []}
        return _read_json(path)

    def append_result(self, category: str, slug: str, run: dict[str, Any]) -> dict[str, Any]:
        self.get_suite(category, slug)  # the suite must exist
        path = self.results_path(category, slug)
        runs = self.get_results(category, slug).get("runs", [])
        runs.insert(0, run)
        data = {"runs": runs[:MAX_RUNS_KEPT]}
        _write_json_atomic(path, data)
        return data


test_suite_store = TestSuiteStore()
