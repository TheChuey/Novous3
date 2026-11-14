"""Reusable Prompt Starter choices and templates shared across squads."""

import os
import re
import tempfile
import threading

import yaml

from squad_developement import flow_design

_lock = threading.Lock()
_templates_lock = threading.Lock()
_VARIABLE = re.compile(r"^[A-Z][A-Z0-9 _-]{0,63}$")
_TEMPLATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")


def _path():
    path = flow_design.ROOT / "prompt_starter_choices.yaml"
    if path.is_symlink():
        raise ValueError("Prompt Starter choices cannot be a symbolic link.")
    return path


def _validate(choices):
    if not isinstance(choices, dict) or len(choices) > 100:
        raise ValueError("Prompt Starter choices must contain at most 100 variables.")
    for key, values in choices.items():
        if not isinstance(key, str) or not _VARIABLE.fullmatch(key):
            raise ValueError("Variable names must use uppercase letters, digits, spaces, hyphens or underscores.")
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError(f"{key} must have at most 100 choices.")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 2000 for value in values):
            raise ValueError(f"Choices for {key} must be nonempty text of at most 2000 characters.")
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate choices for {key} are not allowed.")
    if len(yaml.safe_dump(choices, allow_unicode=True).encode("utf-8")) > 1_000_000:
        raise ValueError("Prompt Starter choices must be at most 1 MB.")
    return choices


def get_choices():
    path = _path()
    if not path.exists():
        return {}
    return _validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def add_choice(variable, value):
    if not isinstance(value, str):
        raise ValueError("Choice must be text.")
    value = value.strip()
    with _lock:
        choices = get_choices()
        values = choices.setdefault(variable, [])
        if value not in values:
            values.append(value)
        _validate(choices)
        path = _path()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
                temporary = handle.name
                yaml.safe_dump(choices, handle, sort_keys=True, allow_unicode=True)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return choices


# --- Templates ---

def _templates_path():
    path = flow_design.ROOT / "prompt_starter_templates.yaml"
    if path.is_symlink():
        raise ValueError("Prompt Starter templates cannot be a symbolic link.")
    return path


def _validate_templates(templates):
    if not isinstance(templates, list) or len(templates) > 50:
        raise ValueError("At most 50 templates are allowed.")
    names = set()
    for entry in templates:
        if not isinstance(entry, dict):
            raise ValueError("Each template must be a mapping.")
        name = entry.get("name", "")
        if not isinstance(name, str) or not _TEMPLATE_NAME.fullmatch(name):
            raise ValueError("Template names must start with a letter or digit and be at most 64 characters.")
        if name in names:
            raise ValueError(f"Duplicate template name: {name}")
        names.add(name)
        body = entry.get("body", "")
        if not isinstance(body, str) or not body.strip():
            raise ValueError(f"Template '{name}' must have nonempty body text.")
        if len(body.encode("utf-8")) > 500_000:
            raise ValueError(f"Template '{name}' must be at most 500 KB.")
    if len(yaml.safe_dump(templates, allow_unicode=True).encode("utf-8")) > 2_000_000:
        raise ValueError("Templates file must be at most 2 MB.")
    return templates


def _write_templates(templates):
    path = _templates_path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            yaml.safe_dump(templates, handle, sort_keys=False, allow_unicode=True)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def get_templates():
    path = _templates_path()
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return []
    return _validate_templates(data)


def save_template(name, body):
    if not isinstance(name, str) or not _TEMPLATE_NAME.fullmatch(name):
        raise ValueError("Template name must start with a letter or digit and be at most 64 characters.")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Template body must not be empty.")
    if len(body.encode("utf-8")) > 500_000:
        raise ValueError("Template body must be at most 500 KB.")
    with _templates_lock:
        templates = get_templates()
        existing = next((t for t in templates if t["name"] == name), None)
        if existing:
            existing["body"] = body
        else:
            templates.append({"name": name, "body": body})
        _validate_templates(templates)
        _write_templates(templates)
    return templates


def delete_template(name):
    with _templates_lock:
        templates = get_templates()
        before = len(templates)
        templates = [t for t in templates if t["name"] != name]
        if len(templates) == before:
            raise FileNotFoundError(f"Template '{name}' not found.")
        _write_templates(templates)
    return templates
