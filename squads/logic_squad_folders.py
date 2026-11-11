"""Metadata-folder management for squads in the Workspace tree."""

import json
import re
import shutil
from pathlib import Path


class SquadManager:
    """Manage squad metadata folders under ``workspace/squads``."""

    SQUAD_ROOT = Path(__file__).resolve().parents[1] / "workspace" / "squads"
    _NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$")

    def _slug(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

    def _validate(self, name: str) -> str:
        name = name.strip()
        if not self._NAME_PATTERN.match(name):
            raise ValueError("Squad name must be 1-64 chars (letters, digits, spaces, '-', '_').")
        return name

    def list_squads(self) -> list[dict]:
        squads = []
        if not self.SQUAD_ROOT.is_dir():
            return squads
        for child in sorted(self.SQUAD_ROOT.iterdir(), key=lambda path: path.name.lower()):
            if not child.is_dir():
                continue
            meta_file = child / "squad.json"
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
            else:
                meta = {}
            squads.append({
                "id": child.name,
                "name": meta.get("name", child.name),
                "description": meta.get("description", ""),
                "path": f"squads/{child.name}",
            })
        return squads

    def create_squad(self, name: str, description: str = "") -> dict:
        name = self._validate(name)
        slug = self._slug(name)
        squad_dir = self.SQUAD_ROOT / slug
        if squad_dir.exists():
            raise ValueError(f"Squad already exists: {name}")
        squad_dir.mkdir(parents=True)
        meta = {"name": name, "description": description.strip(), "id": slug}
        (squad_dir / "squad.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return {"created": True, "squad": meta}

    def rename_squad(self, old_name: str, new_name: str) -> dict:
        new_name = self._validate(new_name)
        src = self.SQUAD_ROOT / self._slug(old_name)
        if not src.is_dir():
            raise FileNotFoundError(f"Squad not found: {old_name}")
        dst = self.SQUAD_ROOT / self._slug(new_name)
        if dst.exists():
            raise ValueError(f"Squad already exists: {new_name}")
        src.rename(dst)
        meta_file = dst / "squad.json"
        meta = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        meta["name"] = new_name
        meta["id"] = dst.name
        meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return {"renamed": True, "squad": meta}

    def delete_squad(self, name: str) -> dict:
        squad_dir = self.SQUAD_ROOT / self._slug(name)
        if not squad_dir.is_dir():
            raise FileNotFoundError(f"Squad not found: {name}")
        shutil.rmtree(squad_dir)
        return {"deleted": True, "id": squad_dir.name}


squad_manager = SquadManager()
