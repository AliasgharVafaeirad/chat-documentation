import json
import tempfile
import unittest
from pathlib import Path
import report_state as state
import main


class ReportTests(unittest.TestCase):
    def test_identity_not_title_and_unknown_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            identity = {
                "peer": "123",
                "title": "Shared name",
                "url": "https://web.telegram.org/a/#123",
            }
            state.bind(folder, "telegram", identity, False)
            state.bind(folder, "telegram", dict(identity, title="Renamed"), True)
            before = (folder / "report.json").read_bytes()
            with self.assertRaises(ValueError):
                state.bind(folder, "telegram", dict(identity, peer="456"), True)
            with self.assertRaises(ValueError):
                state.bind(folder, "telegram", dict(identity, peer=""), True)
            self.assertEqual((folder / "report.json").read_bytes(), before)

    def test_same_chat_independent_new_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            identity = {"peer": "123", "title": "Chat"}
            self.assertNotEqual(
                state.bind(a, "telegram", identity, False)["report_id"],
                state.bind(b, "telegram", identity, False)["report_id"],
            )

    def test_resume_keeps_healthy_and_retries_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "downloads").mkdir()
            (folder / "downloads" / "good.ogg").write_bytes(b"voice")
            rows = [
                {
                    "id": "1",
                    "media": [{"kind": "voice"}],
                    "_media_done": True,
                    "attachment_results": ["downloads/good.ogg"],
                },
                {
                    "id": "2",
                    "media": [{"kind": "document"}],
                    "_media_done": True,
                    "attachment_results": ["downloads/missing.pdf"],
                },
            ]
            state.write(folder / "messages.json", rows)
            loaded = state.load_messages(folder)
            self.assertTrue(loaded["id:1"]["_media_done"])
            self.assertEqual(loaded["id:1"]["_media_attempts"], 1)
            self.assertFalse(loaded["id:2"]["_media_done"])
            self.assertEqual(loaded["id:2"]["_media_attempts"], 0)

    def test_overlap_merge_keeps_download_and_order(self):
        def row(i):
            return {
                "id": str(i),
                "date": "date",
                "sender": "A",
                "time": "12:00",
                "text": "same",
                "media": [],
                "token": "backup-0",
            }

        messages = {}
        main.merge(messages, [row(2), row(3)], [])
        messages["id:2"]["attachment_results"] = ["downloads/good.ogg"]
        messages["id:2"]["_media_done"] = True
        main.merge(messages, [row(1), row(2)], list(messages))
        self.assertEqual(list(messages), ["id:1", "id:2", "id:3"])
        self.assertEqual(messages["id:2"]["attachment_results"], ["downloads/good.ogg"])

    def test_legacy_identity_from_logged_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "telegram_backups" / "old"
            folder.mkdir(parents=True)
            (folder / "run.log").write_text(
                "Selected page: https://web.telegram.org/a/#123; panels: {}", encoding="utf8"
            )
            self.assertEqual(state.legacy_identity(folder)["chat_key"], "telegram:123")

    def test_consolidation_deduplicates_file_and_preserves_sources(self):
        from report_merge import consolidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folders = []
            for label in ("one", "two"):
                folder = root / "telegram_backups" / label
                (folder / "downloads").mkdir(parents=True)
                (folder / "downloads" / "voice.ogg").write_bytes(b"same-audio")
                state.bind(folder, "telegram", {"peer": "123", "title": "Chat"}, False)
                state.write(
                    folder / "messages.json",
                    [
                        {
                            "id": "1",
                            "text": "same",
                            "date": "unknown",
                            "time": "12:00",
                            "sender": "A",
                            "media": [{"kind": "voice", "count": 1}],
                            "_media_done": True,
                            "attachment_results": ["downloads/voice.ogg"],
                        }
                    ],
                )
                folders.append(folder)
            originals = [(p / "messages.json").read_bytes() for p in folders]
            out = root / "telegram_backups" / "merged"
            consolidate(folders, out)
            self.assertEqual(len(json.loads((out / "messages.json").read_text(encoding="utf8"))), 1)
            self.assertEqual(len(list((out / "downloads").iterdir())), 1)
            self.assertEqual([(p / "messages.json").read_bytes() for p in folders], originals)

    def test_telegram_nonoverlapping_earlier_range_order(self):
        def row(i):
            return {
                "id": str(i),
                "date": "date",
                "sender": "A",
                "time": "12:00",
                "text": "t",
                "media": [],
                "token": "backup-0",
            }

        messages = {}
        main.merge(messages, [row(20)], [], provider="telegram")
        main.merge(messages, [row(10)], list(messages), provider="telegram")
        self.assertEqual(list(messages), ["id:10", "id:20"])


if __name__ == "__main__":
    unittest.main()
