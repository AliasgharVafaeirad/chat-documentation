import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from chat_import import import_chat
from report_flow import status, continue_report
from unittest.mock import patch


class ImportTests(unittest.TestCase):
    def test_synthetic_whatsapp_export_and_duplicate_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "export.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "WhatsApp Chat with Example.txt",
                    "15/10/2026, 10:00 - Example Sender: Hello\n"
                    "15/10/2026, 10:01 - Other Sender: PTT-20261015-WA0001.opus (file attached)\n",
                )
                archive.writestr("PTT-20261015-WA0001.opus", b"synthetic audio")
            folder, created = import_chat(source, "whatsapp", base)
            self.assertTrue(created)
            rows = json.loads((folder / "messages.json").read_text(encoding="utf8"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["sender"], "Example Sender")
            self.assertEqual(status(folder)["voices"], 1)
            repeated, created = import_chat(source, "whatsapp", base)
            self.assertFalse(created)
            self.assertEqual(folder, repeated)

    def test_telegram_sender_forward_voice_and_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source.zip"
            data = {
                "name": "Test group",
                "id": 123,
                "messages": [
                    {
                        "id": 1,
                        "date": "2026-06-01T10:00:00",
                        "from": "Actual Sender",
                        "from_id": "user7",
                        "forwarded_from": "Original Author",
                        "text": [{"type": "plain", "text": "Caption"}],
                        "file": "voice/a.ogg",
                        "media_type": "voice_message",
                    },
                    {
                        "id": 2,
                        "date": "2026-06-01T11:00:00",
                        "from": "Other",
                        "text": "",
                        "photo": "photos/missing.jpg",
                    },
                ],
            }
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("export/result.json", json.dumps(data))
                z.writestr("export/voice/a.ogg", b"audio")
            folder, _ = import_chat(source, "telegram", base)
            rows = json.loads((folder / "messages.json").read_text(encoding="utf8"))
            self.assertEqual(rows[0]["sender"], "Actual Sender")
            self.assertEqual(rows[0]["forwarded_from"], "Original Author")
            self.assertEqual(rows[0]["text"], "Caption")
            self.assertEqual(status(folder)["voices"], 1)
            self.assertEqual(status(folder)["missing"], 1)

    def test_reject_archive_traversal_and_ambiguous_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "bad.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("../escape.txt", "no")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                import_chat(source, "whatsapp", base)
            source = base / "chat.txt"
            source.write_text("01/06/2026, 10:00 - Alice: Hello", encoding="utf8")
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                import_chat(source, "whatsapp", base)
            folder, _ = import_chat(source, "whatsapp", base, date_order="DMY")
            self.assertEqual(status(folder)["messages"], 1)

    def test_interrupted_capture_does_not_start_voice_processing(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / "messages.json").write_text("[]")
            (p / "report.json").write_text(
                json.dumps(
                    {
                        "source_kind": "browser_capture",
                        "capture_state": "interrupted",
                        "chat_key": "telegram:1",
                    }
                )
            )
            calls = []
            with patch("builtins.input", return_value="0"):
                continue_report(p, {"voice_python": "python"}, lambda *a: calls.append(a), "python")
            self.assertEqual(calls, [])
            self.assertTrue(status(p)["capture_pending"])


if __name__ == "__main__":
    unittest.main()
