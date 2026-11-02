import json
import os
from contextlib import nullcontext
import threading
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ragMemory import logic_ragMemory
from ragMemory.logic_ragMemory import document_indexer as indexer
from ragMemory.logic_ragMemory import document_scanner as scanner
from ragMemory.logic_ragMemory import rag_config, rag_database, rag_memory
from scratchpadMemory.logic_scratchpadMemory import scratchpad

_chunk_text = indexer._chunk_text


class TestAgentMemory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)
        self.database_patch = patch.object(
            rag_database, "DATABASE_PATH", self.temp_path / "knowledge.sqlite3"
        )
        self.scratchpad_path_patch = patch.object(
            scratchpad, "SCRATCHPAD_PATH", self.temp_path / "scratchpad.json"
        )
        self.scratchpad_dir_patch = patch.object(
            scratchpad, "DATA_DIR", self.temp_path
        )
        self.rag_config_path_patch = patch.object(
            rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"
        )
        self.rag_data_dir_patch = patch.object(
            rag_config, "DATA_DIR", self.temp_path / "rag-data"
        )
        self.database_patch.start()
        self.scratchpad_path_patch.start()
        self.scratchpad_dir_patch.start()
        self.rag_config_path_patch.start()
        self.rag_data_dir_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.scratchpad_path_patch.stop)
        self.addCleanup(self.scratchpad_dir_patch.stop)
        self.addCleanup(self.rag_config_path_patch.stop)
        self.addCleanup(self.rag_data_dir_patch.stop)
        rag_database.initialize()
        knowledge_folder = self.temp_path / "knowledge"
        knowledge_folder.mkdir()
        rag_config._write_config({
            "knowledge_bases": [{
                "id": "test",
                "name": "Test Knowledge",
                "path": str(knowledge_folder),
                "active": True,
                "legacy_index": True,
            }],
            "selected_knowledge_base": "test",
        })

    def test_scratchpad_stores_latest_read_and_map(self):
        read = scratchpad.store_read("notes.txt", "remember this", "summarize")
        scratchpad.store_read("second.txt", "also remember this", "compare")
        mapped = scratchpad.store_map({"name": "workspace"}, "workspace")

        memory = scratchpad.read_memory()
        self.assertEqual(memory["latest_read"]["content"], "also remember this")
        self.assertEqual(memory["latest_read"]["request"], "compare")
        self.assertEqual(
            [entry["path"] for entry in memory["read_history"]],
            ["notes.txt", "second.txt"],
        )
        self.assertEqual(memory["latest_map"]["tree"], {"name": "workspace"})
        self.assertEqual(read["path"], mapped["path"])

    def test_read_history_is_bounded_and_clear_removes_saved_memory(self):
        for index in range(scratchpad.MAX_READ_HISTORY + 3):
            scratchpad.store_read(f"file-{index}.txt", str(index))

        history = scratchpad.read_memory()["read_history"]
        self.assertEqual(len(history), scratchpad.MAX_READ_HISTORY)
        self.assertEqual(history[0]["path"], "file-3.txt")
        self.assertEqual(history[-1]["path"], f"file-{scratchpad.MAX_READ_HISTORY + 2}.txt")

        self.assertTrue(scratchpad.clear_memory()["cleared"])
        self.assertEqual(scratchpad.read_memory(), {})

    def test_recall_index_lists_newest_first_with_ids_and_previews(self):
        self.assertEqual(scratchpad.recall(), {"items": [], "total_items": 0, "showing": 0,
                                               "hint": "Nothing is saved yet."})
        scratchpad.store_read("notes.txt", "remember this", "summarize")
        scratchpad.store_map({"name": "workspace"}, "workspace")
        scratchpad.store_read("second.txt", "also remember this", "compare")

        index = scratchpad.recall()
        self.assertEqual(index["total_items"], 3)
        self.assertEqual([row["label"] for row in index["items"]], ["second.txt", "workspace", "notes.txt"])
        self.assertEqual(index["items"][0]["request"], "compare")
        self.assertEqual(index["items"][0]["preview"], "also remember this")
        self.assertTrue(all(row["id"].startswith(f"{row['kind']}-") for row in index["items"]))
        self.assertEqual(scratchpad.recall("all"), index)

    def test_recall_pages_reassemble_the_exact_text(self):
        text = "\n".join(f"line {number} " + "x" * 60 for number in range(600))
        scratchpad.store_read("long.txt", text, "summarize")

        pages, offset = [], "0"
        while True:
            page = scratchpad.recall(item="long.txt", offset=offset, max_chars="4000")
            self.assertLessEqual(len(page["content"]), 4000)
            pages.append(page["content"])
            if not page["truncated"]:
                break
            offset = str(page["next_offset"])

        self.assertGreater(len(pages), 1)
        self.assertEqual("".join(pages), text)
        self.assertEqual(page["total_chars"], len(text))
        self.assertIsNone(page["next_offset"])

    def test_recall_item_by_path_is_newest_and_by_id_is_exact(self):
        first = scratchpad.store_read("notes.txt", "first version", "")
        scratchpad.store_read("notes.txt", "second version", "")

        self.assertEqual(scratchpad.recall(item="notes.txt")["content"], "second version")
        self.assertEqual(scratchpad.recall(item=first["id"])["content"], "first version")

    def test_recall_latest_subagent_keeps_legacy_shape_and_pages_when_large(self):
        scratchpad.store_subagent_result("rag-retriever", "retrieval", "success",
                                         {"results": [{"content": "small"}]})
        small = scratchpad.recall(memory_type="latest_subagent_result")["latest_subagent_result"]
        self.assertEqual(small["payload"], {"results": [{"content": "small"}]})

        big_payload = {"results": [{"content": "evidence " * 800}]}
        scratchpad.store_subagent_result("rag-retriever", "retrieval", "success", big_payload)
        paged = scratchpad.recall(memory_type="latest_subagent_result", max_chars="1000")["latest_subagent_result"]
        self.assertEqual(paged["subagent_id"], "rag-retriever")
        self.assertEqual(paged["payload_format"], "json_text")
        self.assertTrue(paged["truncated"])
        self.assertLessEqual(len(paged["payload"]), 1000)
        self.assertEqual(paged["total_chars"],
                         len(json.dumps(big_payload, ensure_ascii=False, separators=(",", ":"))))

    def test_recall_clamps_page_size_and_rejects_bad_arguments(self):
        scratchpad.store_read("notes.txt", "x" * 30000, "")
        self.assertEqual(len(scratchpad.recall(item="notes.txt", max_chars="10")["content"]), 500)
        self.assertEqual(len(scratchpad.recall(item="notes.txt", max_chars="99999")["content"]), 20000)

        with self.assertRaisesRegex(ValueError, "memory_type must be one of"):
            scratchpad.recall(memory_type="everything")
        with self.assertRaisesRegex(ValueError, "whole number"):
            scratchpad.recall(offset="abc")
        with self.assertRaisesRegex(ValueError, "offset must be 0 or greater"):
            scratchpad.recall(offset="-1")
        with self.assertRaisesRegex(ValueError, "No saved item matches"):
            scratchpad.recall(item="missing.txt")

    def test_document_crud_and_semantic_search(self):
        rag_database.replace_document(
            "workspace/Knowledge Base/guide.md",
            1,
            12,
            "2025-01-01T00:00:00+00:00",
            [
                ("cats and kittens", [1.0, 0.0]),
                ("space and planets", [0.0, 1.0]),
            ],
        )
        with patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.9, 0.1]]}):
            result = rag_memory.search_knowledge_base("cats", top_k=1)

        self.assertEqual(result["results"][0]["content"], "cats and kittens")
        self.assertEqual(rag_memory.list_documents()[0]["chunks"], 2)
        self.assertTrue(rag_memory.delete_document("workspace/Knowledge Base/guide.md")["deleted"])
        self.assertEqual(rag_memory.list_documents(), [])

    def test_stale_document_cleanup_normalizes_path_separators(self):
        rag_database.replace_document(
            "workspace\\Knowledge Base\\keep.md", 1, 1, "now", []
        )
        rag_database.replace_document(
            "workspace/Knowledge Base/remove.md", 1, 1, "now", []
        )

        removed = rag_database.delete_documents_under(
            "workspace/Knowledge Base",
            {"workspace/Knowledge Base/keep.md"},
        )

        self.assertEqual(removed, ["workspace/Knowledge Base/remove.md"])
        self.assertEqual(
            [item["path"] for item in rag_database.list_documents()],
            ["workspace\\Knowledge Base\\keep.md"],
        )

    def test_chunking_preserves_text_and_bounds_chunk_size(self):
        text = ("line of knowledge\n\n" * 120).strip()

        chunks = _chunk_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1400 for chunk in chunks))
        self.assertTrue(all(chunk in text for chunk in chunks))

    def test_configured_folder_is_scanned_and_root_change_purges_old_entries(self):

        first = self.temp_path / "first"
        second = self.temp_path / "second"
        first.mkdir()
        second.mkdir()
        (first / "a.md").write_text("alpha", encoding="utf-8")
        (second / "b.md").write_text("beta", encoding="utf-8")
        embed = {"embeddings": [[0.1, 0.2]]}
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"), \
                patch("ragMemory.logic_ragMemory.ollama.embed", return_value=embed):
            self.assertEqual(
                rag_memory.set_rag_folder(str(first))["path"], str(first.resolve())
            )
            result = indexer.scan_knowledge_base()
            self.assertEqual(result["indexed"], 1)
            self.assertEqual(
                [d["path"] for d in rag_memory.list_documents()],
                [(first / "a.md").resolve().as_posix()],
            )

            rag_memory.set_rag_folder(str(second))
            result = indexer.scan_knowledge_base()
            self.assertEqual(len(result["removed"]), 1)
            self.assertEqual(
                [d["path"] for d in rag_memory.list_documents()],
                [(second / "b.md").resolve().as_posix()],
            )

            cleared = rag_memory.clear_rag_index()
            self.assertEqual(cleared["removed"], 1)
            self.assertEqual(cleared["documents"], 0)

    def test_manual_text_is_added_listed_and_deleted(self):
        with patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}):
            result = rag_memory.add_text_document("  My   note ", "some knowledge")
        self.assertEqual(result["path"], "manual:My note")
        overview = rag_memory.get_rag_overview()
        self.assertEqual(overview["types"], {"manual": 1})
        self.assertEqual(overview["files"][0]["chunks"], 1)
        with self.assertRaises(ValueError):
            rag_memory.add_text_document("", "text")
        with self.assertRaises(ValueError):
            rag_memory.add_text_document("title", "   ")
        rag_memory.delete_document("manual:My note")
        self.assertEqual(rag_memory.get_rag_overview()["documents"], 0)

    def test_invalid_rag_folder_is_rejected_without_default_folder(self):
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"):
            with self.assertRaises(ValueError):
                rag_memory.set_rag_folder(str(self.temp_path / "missing"))
            with self.assertRaises(ValueError):
                rag_memory.set_rag_folder("relative/path")
            with self.assertRaisesRegex(ValueError, "required"):
                rag_memory.set_rag_folder("")

    def test_scanner_warns_and_retries_when_configured_folder_is_missing(self):
        stop_event = Mock()
        stop_event.is_set.side_effect = [False, True]

        with patch.object(
            scanner, "get_scan_job", return_value={"status": "idle"},
        ), patch.object(
            scanner, "start_scan_job",
            side_effect=FileNotFoundError("The RAG folder does not exist: missing"),
        ), patch.object(indexer, "stop_event", stop_event), \
                patch.object(logic_ragMemory.LOGGER, "warning") as warning, \
                patch.object(logic_ragMemory.LOGGER, "exception") as exception:
            scanner._scanner_loop()

        warning.assert_called_once()
        self.assertIn("Check the configured path", warning.call_args.args[0])
        self.assertEqual(
            str(warning.call_args.args[1]),
            "The RAG folder does not exist: missing",
        )
        stop_event.wait.assert_called_once_with(scanner.SCAN_INTERVAL_SECONDS)
        exception.assert_not_called()

    def test_named_knowledge_bases_keep_indexes_and_archived_records_separate(self):
        first_folder = self.temp_path / "first"
        second_folder = self.temp_path / "second"
        first_folder.mkdir()
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "rag_config.json"), \
                patch.object(rag_config, "DATA_DIR", self.temp_path / "rag-data"), \
                patch.object(scanner, "JOB_PATH", self.temp_path / "missing-folder-job.json"), \
                patch.object(scanner, "_job", {"status": "idle"}), \
                patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}), \
                patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}):
            first = rag_memory.add_rag_base("Business Decisions", str(first_folder))
            second = rag_memory.add_rag_base("Marketing", str(second_folder))
            rag_memory.add_text_document("Decision notes", "business knowledge", first["id"])
            rag_memory.add_text_document("Campaign notes", "marketing knowledge", second["id"])

            self.assertEqual(rag_memory.list_documents(first["id"])[0]["path"], "manual:Decision notes")
            self.assertEqual(rag_memory.list_documents(second["id"])[0]["path"], "manual:Campaign notes")
            self.assertEqual(rag_memory.get_rag_status(second["id"])["name"], "Marketing")
            self.assertFalse(second["exists"])
            self.assertEqual(
                rag_memory.search_knowledge_base("marketing", top_k=1)["results"][0]["content"],
                "marketing knowledge",
            )
            first_results = rag_memory.search_knowledge_base(
                "decision", top_k=1, base_id=first["id"]
            )["results"]
            self.assertEqual(first_results[0]["content"], "business knowledge")
            self.assertEqual(rag_memory.clear_rag_index(second["id"])["removed"], 1)
            self.assertEqual(len(rag_memory.list_documents(first["id"])), 1)
            self.assertEqual(rag_memory.list_documents(second["id"]), [])
            with self.assertRaises(FileNotFoundError):
                rag_memory.start_rag_scan(second["id"])
            self.assertEqual(rag_memory.get_rag_scan_job()["status"], "unavailable")

            rag_memory.archive_rag_base(first["id"])
            archived = next(
                item for item in rag_memory.list_rag_bases()
                if item["id"] == first["id"]
            )
            self.assertFalse(archived["active"])
            self.assertEqual(archived["path"], str(first_folder.resolve()))
            self.assertEqual(archived["documents"], 1)
            archived_results = rag_memory.search_knowledge_base(
                "decision", top_k=1, base_id=first["id"]
            )
            self.assertEqual(archived_results["knowledge_base_id"], first["id"])
            self.assertEqual(
                archived_results["results"][0]["content"],
                "business knowledge",
            )

    def test_default_configuration_migrates_to_a_named_legacy_branch(self):
        folder = self.temp_path / "OLC500"
        folder.mkdir()
        rag_config._write_config({
            "knowledge_base_path": str(folder),
            "knowledge_bases": [
                {
                    "id": "default",
                    "name": "Default Knowledge Base",
                    "path": str(folder),
                    "active": True,
                    "legacy_index": True,
                },
                {
                    "id": "named",
                    "name": "OLC500",
                    "path": str(folder),
                    "active": True,
                    "legacy_index": False,
                },
            ],
            "selected_knowledge_base": "named",
        })

        bases = rag_config.list_knowledge_bases()
        legacy = next(base for base in bases if base["id"] == "default")
        self.assertEqual(legacy["name"], "OLC500 (legacy index)")
        self.assertTrue(legacy["legacy_index"])
        self.assertNotIn("Default Knowledge Base", [base["name"] for base in bases])
        renamed = rag_config.rename_knowledge_base("default", "Historical OLC500")
        self.assertEqual(renamed["name"], "Historical OLC500")
        self.assertEqual(
            rag_config.get_knowledge_base("default")["name"],
            "Historical OLC500",
        )
        self.assertNotIn("knowledge_base_path", json.loads(
            rag_config.CONFIG_PATH.read_text(encoding="utf-8")
        ))

    def test_empty_configuration_has_no_implicit_default_branch(self):
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "empty_config.json"):
            rag_config._write_config({"knowledge_bases": []})
            self.assertEqual(rag_config.list_knowledge_bases(), [])
            with self.assertRaisesRegex(ValueError, "No knowledge source"):
                rag_config.selected_knowledge_base_id()

    def test_deleting_legacy_branch_removes_its_index_not_folder(self):
        folder = self.temp_path / "legacy-branch"
        folder.mkdir()
        source_file = folder / "keep.txt"
        source_file.write_text("Keep this source file.", encoding="utf-8")
        legacy_database = self.temp_path / "legacy-index.sqlite3"
        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "legacy_config.json"), \
                patch.object(rag_database, "DATABASE_PATH", legacy_database), \
                patch.object(scanner, "_job", {"status": "idle"}):
            rag_config._write_config({
                "knowledge_bases": [{
                    "id": "legacy",
                    "name": "Legacy source",
                    "path": str(folder),
                    "active": True,
                    "legacy_index": True,
                }],
                "selected_knowledge_base": "legacy",
            })
            rag_database.initialize()
            rag_database.replace_document(
                "manual:notes", 0, 1, "now", [("saved", [0.1, 0.2])]
            )

            result = rag_memory.delete_rag_base("legacy")

        self.assertEqual(result["documents_removed"], 1)
        self.assertFalse(legacy_database.exists())
        self.assertTrue(source_file.exists())

    def test_named_branch_can_be_renamed_and_deleted_without_deleting_folder(self):
        folder = self.temp_path / "branch-files"
        folder.mkdir()
        source_file = folder / "keep.txt"
        source_file.write_text("Do not delete this file.", encoding="utf-8")
        with patch("ragMemory.logic_ragMemory.ollama.embed", return_value={"embeddings": [[0.1, 0.2]]}):
            base = rag_memory.add_rag_base("Course branch", str(folder))
            rag_memory.add_text_document("notes", "indexed knowledge", base["id"])

        database_file = rag_config.database_path(base["id"])
        self.assertTrue(database_file.is_file())
        renamed = rag_memory.rename_rag_base(base["id"], "Leadership course")
        self.assertEqual(renamed["name"], "Leadership course")
        self.assertEqual(renamed["path"], str(folder.resolve()))
        result = rag_memory.delete_rag_base(base["id"])

        self.assertEqual(result["name"], "Leadership course")
        self.assertEqual(result["documents_removed"], 1)
        self.assertFalse(result["folder_deleted"])
        self.assertEqual(source_file.read_text(encoding="utf-8"), "Do not delete this file.")
        self.assertFalse(database_file.exists())
        self.assertNotIn(base["id"], {entry["id"] for entry in rag_memory.list_rag_bases()})

    def test_deleting_paused_branch_cancels_scheduled_resume(self):
        folder = self.temp_path / "paused-branch"
        folder.mkdir()
        base = rag_memory.add_rag_base("Paused source", str(folder))
        job = {
            "status": "paused",
            "base_id": base["id"],
            "resume_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        with patch.object(scanner, "JOB_PATH", self.temp_path / "paused_job.json"), \
                patch.object(scanner, "_job", job), \
                patch.object(scanner, "_job_thread", None), \
                patch.object(scanner, "_stop_requested", threading.Event()), \
                patch.object(scanner, "_pause_requested", threading.Event()), \
                patch.object(scanner, "_resume_event", threading.Event()), \
                patch.object(scanner, "_error_decision_event", threading.Event()):
            result = rag_memory.delete_rag_base(base["id"])

            self.assertTrue(result["deleted"])
            self.assertEqual(scanner.get_scan_job()["status"], "stopped")
            self.assertIsNone(scanner.get_scan_job()["resume_at"])
            self.assertNotIn(base["id"], scanner.get_scan_job()["stopped_base_ids"])

    def test_scan_error_choice_can_skip_one_file_or_skip_all(self):
        folder = self.temp_path / "error-policy"
        folder.mkdir()
        (folder / "first.md").write_text("first", encoding="utf-8")
        (folder / "second.md").write_text("second", encoding="utf-8")
        decisions = iter(["skip", "skip_all"])
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", side_effect=ValueError("parse failed")):
            result = indexer._scan_knowledge_base(
                False,
                "test",
                None,
                None,
                lambda path, error: next(decisions),
            )

        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertTrue(all(item["status"] == "skipped" for item in result["skipped"]))

    def test_scan_error_can_retry_same_file(self):
        folder = self.temp_path / "retry-error"
        folder.mkdir()
        (folder / "retry.md").write_text("retry", encoding="utf-8")
        decisions = iter(["retry"])
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(
                    indexer,
                    "index_file",
                    side_effect=[ValueError("temporary failure"), {"status": "indexed"}],
                ) as index_file:
            result = indexer._scan_knowledge_base(
                False,
                "test",
                None,
                None,
                lambda path, error: next(decisions),
            )

        self.assertEqual(index_file.call_count, 2)
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(result["errors"], [])

    def test_oversized_files_are_skipped_without_asking(self):
        folder = self.temp_path / "oversized"
        folder.mkdir()
        (folder / "big.md").write_text("x" * 20, encoding="utf-8")
        (folder / "small.md").write_text("small", encoding="utf-8")
        decider = Mock()
        with patch.object(indexer, "MAX_DOCUMENT_BYTES", 10), \
                patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", return_value={"status": "indexed"}) as index_file:
            result = indexer._scan_knowledge_base(False, "test", None, None, decider)

        decider.assert_not_called()
        self.assertEqual(index_file.call_count, 1)
        self.assertEqual(result["discovered"], 1)
        self.assertEqual([Path(item["path"]).name for item in result["skipped"]], ["big.md"])
        self.assertIn("indexing limit", result["skipped"][0]["error"])

    def test_google_docs_links_produce_a_warning_not_an_error(self):
        folder = self.temp_path / "google"
        folder.mkdir()
        (folder / "About me.gdoc").write_text('{"doc_id": "abc"}', encoding="utf-8")
        (folder / "Budget.gsheet").write_text('{"doc_id": "def"}', encoding="utf-8")
        (folder / "notes.md").write_text("notes", encoding="utf-8")
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", return_value={"status": "indexed"}) as index_file:
            result = indexer._scan_knowledge_base(False, "test", None, None, None)

        self.assertEqual(index_file.call_count, 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skipped"], [])
        [warning] = result["warnings"]
        self.assertIn("2 Google Docs/Sheets/Slides file(s) cannot be indexed", warning["message"])
        self.assertIn("File > Download", warning["message"])
        self.assertEqual(sorted(Path(path).name for path in warning["paths"]), ["About me.gdoc", "Budget.gsheet"])
        # Saved with the source, so its views show it after other sources are scanned.
        self.assertEqual(rag_memory.get_rag_status("test")["warnings"], result["warnings"])
        [base] = rag_memory.list_rag_bases()
        self.assertEqual(base["warnings"], result["warnings"])

    @unittest.skipUnless(os.name == "nt", "hidden file attributes are Windows-only")
    def test_hidden_system_files_are_not_indexed(self):
        import subprocess

        folder = self.temp_path / "hidden"
        folder.mkdir()
        (folder / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
        (folder / "notes.md").write_text("notes", encoding="utf-8")
        subprocess.run(["attrib", "+h", str(folder / "desktop.ini")], check=True)
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(indexer, "index_file", return_value={"status": "indexed"}) as index_file:
            result = indexer._scan_knowledge_base(False, "test", None, None, None)

        self.assertEqual(result["discovered"], 1)
        self.assertEqual(index_file.call_args.args[0].name, "notes.md")

    def test_scan_progress_reports_each_file_phase(self):
        folder = self.temp_path / "phases"
        folder.mkdir()
        (folder / "notes.md").write_text("phase text", encoding="utf-8")
        updates = []
        with patch.object(indexer, "knowledge_base_folder", return_value=folder), \
                patch.object(rag_config, "database_context", return_value=nullcontext()), \
                patch.object(rag_database, "initialize"), \
                patch.object(indexer, "_purge_previous_folder", return_value=[]), \
                patch.object(rag_database, "delete_documents_under", return_value=[]), \
                patch.object(rag_database, "document_metadata", return_value=None), \
                patch.object(rag_database, "replace_document"), \
                patch.object(indexer, "_embed", return_value=[[0.1, 0.2]]):
            result = indexer._scan_knowledge_base(
                False, "test", lambda **changes: updates.append(changes), None, None
            )

        self.assertEqual(result["indexed"], 1)
        phases = [update["phase"] for update in updates if update.get("phase")]
        self.assertEqual(phases, ["embedding"])
        self.assertEqual(updates[-1]["phase"], "")

    def test_scan_error_decision_endpoint_state_can_skip_all(self):
        decision_event = threading.Event()
        job = {
            "status": "awaiting_decision",
            "error_mode": "ask",
            "pending_error": {"path": "problem.pdf", "error": "parser failed"},
        }
        with patch.object(scanner, "_job", job), \
                patch.object(scanner, "_error_decision_event", decision_event):
            updated = scanner.decide_scan_error("skip_all")

        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["error_mode"], "skip_all")
        self.assertTrue(decision_event.is_set())
        with patch.object(scanner, "_job", {"status": "running"}):
            with self.assertRaisesRegex(ValueError, "no indexing error"):
                scanner.decide_scan_error("skip")

    def test_source_names_are_unique_across_archived_branches(self):
        one = self.temp_path / "one"
        two = self.temp_path / "two"
        one.mkdir()
        two.mkdir()
        first = rag_config.add_knowledge_base("First", str(one))
        rag_config.archive_knowledge_base(first["id"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            rag_config.add_knowledge_base("First", str(two))
        with self.assertRaisesRegex(ValueError, "reserved"):
            rag_config.add_knowledge_base("Default Knowledge Base", str(two))

    def test_scan_job_can_pause_schedule_resume_and_persist_progress(self):

        folder = self.temp_path / "scheduled"
        folder.mkdir()
        entered_scan = threading.Event()
        pause_gate = threading.Event()
        finished_scan = threading.Event()
        resume_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

        def controlled_scan(**kwargs):
            kwargs["progress"](discovered=2, completed=0, current="first.md")
            entered_scan.set()
            pause_gate.wait(2)
            kwargs["pause_point"]()
            kwargs["progress"](completed=1, indexed=1, current="")
            finished_scan.set()
            return {"errors": [], "removed": []}

        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "scan_config.json"), \
                patch.object(scanner, "JOB_PATH", self.temp_path / "rag_scan_job.json"), \
                patch.object(scanner, "_job", {"status": "idle"}), \
                patch.object(scanner, "_job_thread", None), \
                patch.object(indexer, "scan_knowledge_base", side_effect=controlled_scan), \
                patch.object(rag_config, "DATA_DIR", self.temp_path / "rag-data"):
            base = rag_config.add_knowledge_base("Scheduled", str(folder))
            base_id = base["id"]
            scanner.start_scan_job(base_id)
            self.assertTrue(entered_scan.wait(2))
            scanner.pause_scan_job()
            pause_gate.set()
            deadline = datetime.now() + timedelta(seconds=2)
            while scanner.get_scan_job()["status"] != "paused" and datetime.now() < deadline:
                threading.Event().wait(0.01)
            self.assertEqual(scanner.get_scan_job()["status"], "paused")

            scanner.schedule_scan_resume(resume_time)
            saved = json.loads((self.temp_path / "rag_scan_job.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["resume_at"], resume_time)
            scanner.resume_scan_job()
            self.assertTrue(finished_scan.wait(2))
            thread = scanner._job_thread
            if thread:
                thread.join(2)
            self.assertEqual(scanner.get_scan_job()["status"], "completed")
            self.assertIsNone(scanner.get_scan_job()["resume_at"])

    def test_stopping_scan_wakes_worker_and_blocks_automatic_restarts(self):
        folder = self.temp_path / "stopping"
        folder.mkdir()
        entered_scan = threading.Event()
        stop_gate = threading.Event()

        def controlled_scan(**kwargs):
            entered_scan.set()
            stop_gate.wait(2)
            kwargs["pause_point"]()
            self.fail("scan continued after it was stopped")

        with patch.object(rag_config, "CONFIG_PATH", self.temp_path / "stop_config.json"), \
                patch.object(scanner, "JOB_PATH", self.temp_path / "rag_scan_job.json"), \
                patch.object(scanner, "_job", {"status": "idle"}), \
                patch.object(scanner, "_job_thread", None), \
                patch.object(scanner, "_stop_requested", threading.Event()), \
                patch.object(scanner, "_pause_requested", threading.Event()), \
                patch.object(scanner, "_resume_event", threading.Event()), \
                patch.object(scanner, "_error_decision_event", threading.Event()), \
                patch.object(indexer, "scan_knowledge_base", side_effect=controlled_scan), \
                patch.object(rag_config, "DATA_DIR", self.temp_path / "rag-data"), \
                patch.object(rag_config, "list_knowledge_bases", return_value=[
                    {"id": "stopping", "active": True}
                ]):
            scanner._resume_event.set()
            base = rag_config.add_knowledge_base("Stopping", str(folder))
            scanner.start_scan_job(base["id"])
            self.assertTrue(entered_scan.wait(2))
            result = scanner.stop_scan_job()
            stop_gate.set()
            thread = scanner._job_thread
            if thread:
                thread.join(2)

            self.assertEqual(result["status"], "stopping")
            self.assertEqual(scanner.get_scan_job()["status"], "stopped")
            self.assertIn(base["id"], scanner.get_scan_job()["stopped_base_ids"])
            self.assertFalse(thread and thread.is_alive())
            deleted = rag_memory.delete_rag_base(base["id"])
            self.assertTrue(deleted["deleted"])
            self.assertNotIn(base["id"], scanner.get_scan_job()["stopped_base_ids"])

if __name__ == "__main__":
    unittest.main()
