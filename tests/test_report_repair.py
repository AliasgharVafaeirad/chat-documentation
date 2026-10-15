import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from report_repair import (
    prepare,
    sender_plan,
    apply_senders,
    promote,
    read,
    undo,
    select_saved_repair,
    confirm_apply,
)
from report_state import write, stable_chat


class RepairTests(unittest.TestCase):
    def test_one_repair_does_not_ask_for_a_number(self):
        with patch("builtins.input", side_effect=AssertionError("Unexpected question")):
            self.assertEqual(select_saved_repair([Path("ready")]), Path("ready"))

    def test_apply_prompt_handles_wrong_input_without_stopping(self):
        with patch("builtins.input", side_effect=["wrong", "1"]):
            self.assertTrue(confirm_apply("senders", 181))
        with patch("builtins.input", return_value="0"):
            self.assertFalse(confirm_apply("senders", 181))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "bale_backups" / "report"
        self.folder.mkdir(parents=True)
        self.messages = [
            dict(
                id="a",
                text="hello",
                date="2026-10-01",
                time="10:00",
                sender="unknown",
                sender_id="",
                sender_status="unknown",
                attachment_results=["downloads/one.ogg"],
            )
        ]
        write(self.folder / "messages.json", self.messages)
        (self.folder / "downloads").mkdir()
        (self.folder / "downloads" / "one.ogg").write_bytes(b"original audio")
        write(
            self.folder / "transcription_results.json",
            {
                "downloads/one.ogg": {"status": "done", "text": "original transcript"},
                "downloads/two.ogg": {"status": "done", "text": "other transcript"},
            },
        )
        (self.folder / "chat_final.xlsx").write_bytes(b"original excel")

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, **kwargs):
        return dict(
            self.messages[0],
            sender="Alice",
            sender_id="7",
            sender_role="participant",
            sender_source="message_label",
            sender_status="displayed_name",
            **kwargs,
        )

    def test_sender_repair_changes_only_sender_fields(self):
        run, work = prepare(self.folder, "senders")
        changes, conflicts = sender_plan(self.messages, [self.candidate()])
        self.assertEqual(len(changes), 1)
        self.assertFalse(conflicts)
        write(work / "messages.json", apply_senders(self.messages, changes))
        promote(self.folder, run, "senders")
        self.assertEqual(read(self.folder / "messages.json")[0]["sender"], "Alice")
        self.assertEqual(read(run / "original" / "messages.json"), self.messages)
        self.assertEqual((self.folder / "downloads" / "one.ogg").read_bytes(), b"original audio")
        self.assertEqual((self.folder / "chat_final.xlsx").read_bytes(), b"original excel")
        self.assertEqual(
            read(self.folder / "transcription_results.json")["downloads/one.ogg"]["text"],
            "original transcript",
        )

    def test_conflicts_and_content_mismatch_never_apply(self):
        first = self.candidate()
        second = dict(first, sender="Bob")
        changes, conflicts = sender_plan(self.messages, [first, second])
        self.assertFalse(changes)
        self.assertEqual(conflicts, ["a"])
        changes, _ = sender_plan(self.messages, [dict(first, text="different message")])
        self.assertFalse(changes)

    def test_changed_client_id_matches_only_unique_exact_content(self):
        live = dict(self.candidate(), id="new-session-id")
        changes, _ = sender_plan(self.messages, [live])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["id"], "a")
        self.assertEqual(changes[0]["match"], "unique_date_time_content")
        changes, _ = sender_plan(self.messages, [live, dict(live, id="another-live-message")])
        self.assertFalse(changes)
        changes, _ = sender_plan(self.messages + [dict(self.messages[0], id="b")], [live])
        self.assertFalse(changes)

    def test_race_blocks_apply(self):
        run, work = prepare(self.folder, "senders")
        write(self.folder / "messages.json", [dict(self.messages[0], text="new capture")])
        with self.assertRaisesRegex(ValueError, "changed during repair"):
            promote(self.folder, run, "senders")
        self.assertEqual(read(self.folder / "messages.json")[0]["text"], "new capture")

    def test_excel_lock_is_not_report_content(self):
        lock = self.folder / "~$chat_final.xlsx"
        lock.write_bytes(b"owner lock")
        run, work = prepare(self.folder, "excel")
        self.assertFalse((run / "original" / lock.name).exists())
        lock.unlink()
        promote(self.folder, run, "excel")

    def test_scoped_asr_repair_does_not_modify_other_voice(self):
        run, work = prepare(self.folder, "asr")
        write(
            work / "transcription_results.json",
            {
                "downloads/one.ogg": {"status": "done", "text": "fixed"},
                "downloads/two.ogg": {"status": "done", "text": "unrelated mutation"},
            },
        )
        promote(self.folder, run, "asr", "downloads/one.ogg")
        result = read(self.folder / "transcription_results.json")
        self.assertEqual(result["downloads/one.ogg"]["text"], "fixed")
        self.assertEqual(result["downloads/two.ogg"]["text"], "other transcript")

    def test_failed_retry_preserves_good_transcript(self):
        run, work = prepare(self.folder, "asr")
        write(work / "transcription_results.json", {"downloads/one.ogg": {"status": "error"}})
        promote(self.folder, run, "asr", "downloads/one.ogg")
        self.assertEqual(
            read(self.folder / "transcription_results.json")["downloads/one.ogg"]["text"],
            "original transcript",
        )

    def test_snapshot_does_not_recursively_copy_old_repairs(self):
        first, _ = prepare(self.folder, "excel")
        second, _ = prepare(self.folder, "excel")
        self.assertFalse((second / "original" / "repairs").exists())
        self.assertTrue((first / "original" / "messages.json").exists())

    def test_bale_query_chat_identity(self):
        self.assertEqual(
            stable_chat("bale", {"url": "https://web.bale.ai/chat?uid=4848693586"}),
            "bale:4848693586",
        )
        self.assertIsNone(stable_chat("bale", {"url": "https://other.example/chat?uid=4848693586"}))

    def test_undo_restores_original_sender_without_touching_audio(self):
        run, work = prepare(self.folder, "senders")
        changes, _ = sender_plan(self.messages, [self.candidate()])
        write(work / "messages.json", apply_senders(self.messages, changes))
        promote(self.folder, run, "senders")
        undo(self.folder, run)
        self.assertEqual(read(self.folder / "messages.json"), self.messages)
        self.assertEqual((self.folder / "downloads" / "one.ogg").read_bytes(), b"original audio")


if __name__ == "__main__":
    unittest.main()
