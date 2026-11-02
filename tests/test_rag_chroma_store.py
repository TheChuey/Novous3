import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragMemory import interface_ragMemory, logic_ragMemory, mcp_ragMemory

REPO_ROOT = Path(__file__).resolve().parent.parent


def fake_embed(model, input):
    """Deterministic 3-d vectors: 'alpha' texts point one way, everything else another."""
    texts = [input] if isinstance(input, str) else input
    return {"embeddings": [[1.0, 0.0, 0.0] if "alpha" in text else [0.0, 1.0, 0.0] for text in texts]}


class TestRagChromaStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name) / "home"
        self.folder = Path(self.temp_dir.name) / "docs"
        self.folder.mkdir()
        embed_patch = patch.object(logic_ragMemory.ollama, "embed", side_effect=fake_embed)
        embed_patch.start()
        self.addCleanup(embed_patch.stop)
        self.service = logic_ragMemory.create_rag_memory(self.home)

    def test_index_search_and_delete_round_trip(self):
        source = self.service.add_rag_base("Docs", str(self.folder))
        (self.folder / "a.md").write_text("alpha facts", encoding="utf-8")
        (self.folder / "b.md").write_text("beta facts", encoding="utf-8")

        result = self.service.indexer.scan_knowledge_base(base_id=source["id"])
        self.assertEqual(result["indexed"], 2)
        self.assertEqual(
            [item["chunks"] for item in self.service.list_documents(source["id"])], [1, 1]
        )

        hits = self.service.search_knowledge_base("alpha question", top_k=2, base_id=source["id"])
        self.assertEqual(Path(hits["results"][0]["path"]).name, "a.md")
        self.assertAlmostEqual(hits["results"][0]["score"], 1.0, places=5)
        self.assertEqual(hits["skipped_incompatible_chunks"], 0)

        document = self.service.get_document(hits["results"][0]["path"], source["id"])
        self.assertEqual(document["chunks_data"][0]["content"], "alpha facts")
        self.assertEqual(len(document["chunks_data"][0]["embedding"]), 3)

        (self.folder / "a.md").unlink()
        rescan = self.service.indexer.scan_knowledge_base(base_id=source["id"])
        self.assertEqual(len(rescan["removed"]), 1)
        hits = self.service.search_knowledge_base("alpha question", top_k=5, base_id=source["id"])
        self.assertEqual([Path(hit["path"]).name for hit in hits["results"]], ["b.md"])

        cleared = self.service.clear_rag_index(source["id"])
        self.assertEqual(cleared["removed"], 1)
        self.assertEqual(cleared["chunks"], 0)

    def test_factory_keeps_all_state_under_its_home(self):
        source = self.service.add_rag_base("Docs", str(self.folder))
        self.service.add_text_document("note", "alpha note", source["id"])

        self.assertTrue((self.home / "rag_config.json").is_file())
        self.assertTrue((self.home / "data" / "rag_indexes" / f"{source['id']}.sqlite3").is_file())
        self.assertTrue((self.home / "data" / "rag_indexes" / "chroma").is_dir())
        self.assertNotEqual(self.service.config.CONFIG_PATH, logic_ragMemory.rag_config.CONFIG_PATH)

    def test_deleting_a_source_removes_its_vectors(self):
        source = self.service.add_rag_base("Docs", str(self.folder))
        self.service.add_text_document("note", "alpha note", source["id"])
        self.service.delete_rag_base(source["id"])

        again = self.service.add_rag_base("Docs again", str(self.folder))
        with self.service.config.database_context(again["id"]):
            self.assertEqual(self.service.database.list_chunks(), [])
        self.assertFalse(
            (self.home / "data" / "rag_indexes" / f"{source['id']}.sqlite3").exists()
        )

    def test_legacy_json_chunks_migrate_into_chroma_without_embedding(self):
        source_id = "0123456789abcdef"
        self.home.mkdir(parents=True)
        (self.home / "rag_config.json").write_text(json.dumps({
            "knowledge_bases": [{
                "id": source_id, "name": "Old", "path": str(self.folder),
                "active": True, "legacy_index": False,
            }],
            "selected_knowledge_base": source_id,
        }), encoding="utf-8")
        manifest = self.home / "data" / "rag_indexes" / f"{source_id}.sqlite3"
        manifest.parent.mkdir(parents=True)
        connection = sqlite3.connect(manifest)
        connection.executescript(
            """
            CREATE TABLE documents (path TEXT PRIMARY KEY, modified_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL, indexed_at TEXT NOT NULL);
            CREATE TABLE chunks (path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL, content TEXT NOT NULL, embedding TEXT NOT NULL,
                PRIMARY KEY (path, chunk_index));
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO documents VALUES ('old.md', 1, 10, '2025-01-01T00:00:00+00:00');
            INSERT INTO chunks VALUES ('old.md', 0, 'alpha legacy', '[1.0,0.0,0.0]');
            INSERT INTO chunks VALUES ('old.md', 1, 'beta legacy', '[0.0,1.0,0.0]');
            """
        )
        connection.commit()
        connection.close()

        with patch.object(logic_ragMemory.ollama, "embed", side_effect=fake_embed) as embed:
            documents = self.service.list_documents(source_id)
            embed.assert_not_called()
        self.assertEqual(documents[0]["chunks"], 2)
        self.assertTrue(manifest.with_name(f"{manifest.name}.pre-chroma.bak").is_file())
        with sqlite3.connect(manifest) as check:
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("chunks", tables)

        hits = self.service.search_knowledge_base("alpha?", top_k=1)
        self.assertEqual(hits["results"][0]["content"], "alpha legacy")

    def test_interface_doorway_and_mcp_tools_use_the_configured_home(self):
        self.addCleanup(interface_ragMemory.configure, None)
        interface_ragMemory.configure(self.home)
        source = interface_ragMemory.add_source("Docs", str(self.folder))
        interface_ragMemory.add_text("note", "alpha note", source["id"])

        self.assertEqual(interface_ragMemory.selected_source_id(), source["id"])
        hits = interface_ragMemory.search_knowledge("alpha?", 3)
        self.assertEqual(hits["results"][0]["path"], "manual:note")

        listed = json.loads(mcp_ragMemory.list_knowledge_sources())
        self.assertEqual([(item["name"], item["chunks"]) for item in listed], [("Docs", 1)])
        found = json.loads(mcp_ragMemory.search_knowledge("alpha?", 1, source["id"]))
        self.assertEqual(found["results"][0]["content"], "alpha note")
        document = json.loads(mcp_ragMemory.read_document("manual:note"))
        self.assertEqual(document["content"], "alpha note")

        interface_ragMemory.configure(None)
        self.assertIs(interface_ragMemory.get_service(), logic_ragMemory.rag_memory)


class TestRagComponentBoundaries(unittest.TestCase):
    def test_package_imports_without_other_novous_components(self):
        code = (
            "import sys\n"
            "for name in ('editor', 'core_engine', 'tools', 'orchestration', 'scratchpadMemory'):\n"
            "    sys.modules[name] = None\n"
            "import ragMemory, ragMemory.mcp_ragMemory\n"
            "print(ragMemory.get_service().indexer.embedder.model)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_ragmemory_and_tests_import_the_logic_module(self):
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT)
            if relative.parts[0] in {"ragMemory", "tests", "venv", ".git"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ragMemory.logic_ragMemory" in text or "from ragMemory import logic_ragMemory" in text:
                offenders.append(relative.as_posix())
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
