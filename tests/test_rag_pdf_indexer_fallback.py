import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fitz

from ragMemory import logic_ragMemory
from ragMemory.logic_ragMemory import document_indexer as indexer


class TestPdfIndexerFallback(unittest.TestCase):
    def test_uses_pymupdf_when_docling_pdf_backend_cannot_load(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "text.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "PDF text remains searchable")
            document.save(pdf_path)
            document.close()

            converter = Mock()
            converter.convert.side_effect = ImportError("native parser unavailable")
            with patch.object(indexer, "_doc_converter", converter):
                text = indexer._document_text(pdf_path)

        self.assertIn("PDF text remains searchable", text)

    def test_reports_when_fallback_cannot_extract_pdf_text(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "image-only.pdf"
            document = fitz.open()
            document.new_page()
            document.save(pdf_path)
            document.close()

            converter = Mock()
            converter.convert.side_effect = ImportError("native parser unavailable")
            with patch.object(indexer, "_doc_converter", converter):
                with self.assertRaisesRegex(RuntimeError, "scanned PDFs require"):
                    indexer._document_text(pdf_path)


class TestCloudPlaceholderSources(unittest.TestCase):
    def test_failed_cloud_download_reports_how_to_fix_it(self):
        error = OSError(22, "Invalid argument", "notes.docx", 362)
        with patch.object(indexer, "cloud_provider", return_value="OneDrive"), \
                patch.object(indexer, "_is_cloud_placeholder", return_value=True), \
                patch.object(Path, "read_bytes", side_effect=error):
            phases = []
            with self.assertRaisesRegex(
                RuntimeError, r"File not downloaded: OneDrive.*WinError 362.*Always keep on this device"
            ):
                indexer._document_text(Path("C:/OneDrive/notes.docx"), phase=phases.append)

        self.assertEqual(phases, ["downloading from OneDrive"])

    def test_stuck_cloud_download_times_out_with_a_clear_message(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with patch.object(indexer, "cloud_provider", return_value="Google Drive"), \
                patch.object(indexer, "_is_cloud_placeholder", return_value=False), \
                patch.object(indexer, "CLOUD_DOWNLOAD_TIMEOUT_SECONDS", 0.2), \
                patch.object(Path, "read_bytes", side_effect=lambda: release.wait(5) and b""):
            phases = []
            with self.assertRaisesRegex(
                RuntimeError, r"File not downloaded: Google Drive did not deliver it within 0.2 seconds.*Available offline"
            ):
                indexer._document_text(Path("G:/My Drive/notes.docx"), phase=phases.append)

        self.assertEqual(phases, ["reading from Google Drive"])

    def test_google_drive_volume_and_onedrive_folder_are_recognised(self):
        with patch.object(logic_ragMemory, "_volume_label", side_effect=lambda anchor: {
            "G:\\": "Google Drive"
        }.get(anchor, "Windows")), patch.dict(os.environ, {"OneDrive": r"C:\Users\me\OneDrive"}):
            self.assertEqual(indexer.cloud_provider(Path(r"G:\My Drive\Resume")), "Google Drive")
            self.assertEqual(indexer.cloud_provider(Path(r"C:\Users\me\OneDrive\Docs")), "OneDrive")
            self.assertIsNone(indexer.cloud_provider(Path(r"C:\Users\me\Desktop\missing")))

    def test_docling_receives_a_stream_of_the_downloaded_bytes(self):
        from docling.datamodel.base_models import DocumentStream

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.docx"
            path.write_bytes(b"docx bytes")
            converter = Mock()
            converter.convert.return_value.document.export_to_markdown.return_value = "# Notes"
            with patch.object(indexer, "_doc_converter", converter):
                text = indexer._document_text(path)

        source = converter.convert.call_args.args[0]
        self.assertIsInstance(source, DocumentStream)
        self.assertEqual(source.name, "notes.docx")
        self.assertEqual(source.stream.read(), b"docx bytes")
        self.assertEqual(text, "# Notes")

    def test_pdf_without_text_is_parsed_again_with_ocr(self):
        plain = Mock()
        plain.convert.return_value.document.export_to_markdown.return_value = "<!-- image -->"
        ocr = Mock()
        ocr.convert.return_value.document.export_to_markdown.return_value = "Scanned words"
        phases = []
        with patch.object(indexer, "PDF_OCR", "auto"), \
                patch.object(indexer, "_doc_converter", plain), \
                patch.object(indexer, "_ocr_converter", ocr):
            text = indexer._document_text(Path("scan.pdf"), b"%PDF", phases.append)

        self.assertEqual(text, "Scanned words")
        self.assertEqual(phases, ["parsing", "parsing with OCR"])


if __name__ == "__main__":
    unittest.main()
