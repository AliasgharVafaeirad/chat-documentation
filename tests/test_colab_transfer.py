import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import colab_transfer as transfer
from colab_worker import GPU_CONFIG, unpack_job
import process_backup


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / "downloads").mkdir()
        for name in ("one.ogg", "two.ogg"):
            (self.folder / "downloads" / name).write_bytes(name.encode())
        (self.folder / "downloads/private.pdf").write_bytes(b"private document")
        self.messages = [
            {
                "id": "m1",
                "text": "private chat text",
                "sender": "Private name",
                "attachment_results": [
                    "downloads/one.ogg",
                    "downloads/two.ogg",
                    "downloads/private.pdf",
                ],
            }
        ]
        transfer.save(self.folder / "messages.json", self.messages)

    def tearDown(self):
        self.temp.cleanup()

    def result(self, job, items):
        target = self.folder / "return.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(
                "results.json",
                json.dumps({"job_id": job["job_id"], "config": transfer.CONFIG, "items": items}),
            )
        return target

    def test_music_excluded_before_upload_even_if_ogg(self):
        (self.folder / "downloads/song.ogg").write_bytes(b"music")
        messages = [{"media": [{"kind": "audio"}], "attachment_results": ["downloads/song.ogg"]}]
        transfer.save(self.folder / "messages.json", messages)
        self.assertFalse(transfer.has_pending(self.folder))
        self.assertIsNone(transfer.prepare(self.folder))
        self.assertEqual(
            process_backup.tasks(
                messages,
                self.folder,
                {
                    "downloads/song.ogg": {
                        "status": "done",
                        "sha256": process_backup.digest(self.folder / "downloads/song.ogg"),
                        "text": "old music text",
                    }
                },
            ),
            [],
        )

    def test_voice_mp4_package_is_accepted_by_gpu_worker(self):
        (self.folder / "downloads/voice.mp4").write_bytes(b"audio-only voice container")
        transfer.save(
            self.folder / "messages.json",
            [
                {
                    "media": [{"kind": "document", "name": "voice.mp4"}],
                    "attachment_results": ["downloads/voice.mp4"],
                }
            ],
        )
        package = transfer.prepare(self.folder)
        job, folder = unpack_job(package, self.folder / "gpu")
        self.assertEqual(len(job["items"]), 1)
        self.assertTrue(job["items"][0]["file"].endswith(".mp4"))
        self.assertEqual(
            (folder / job["items"][0]["file"]).read_bytes(), b"audio-only voice container"
        )

    def test_partial_round_trip_only_audio_export_and_resume(self):
        package = transfer.prepare(self.folder)
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            self.assertNotIn("messages.json", archive.namelist())
            manifest_text = archive.read("job.json").decode()
            self.assertNotIn("private", manifest_text)
            self.assertNotIn("downloads/", manifest_text)
        job, unpacked = unpack_job(package, self.folder / "cloud")
        self.assertEqual(job["config"], GPU_CONFIG)
        one = job["items"][0]
        target = self.result(
            job,
            [
                dict(
                    one,
                    status="done",
                    text="GPU transcript",
                    segments=[{"start": 0, "end": 1, "text": "GPU transcript"}],
                )
            ],
        )
        transfer.import_results(self.folder, target)
        saved = json.loads((self.folder / "transcription_results.json").read_text())
        self.assertEqual(saved["downloads/one.ogg"]["text"], "GPU transcript")
        self.assertNotIn("downloads/two.ogg", saved)
        package = transfer.prepare(self.folder)
        with zipfile.ZipFile(package) as archive:
            pending = json.loads(archive.read("job.json"))
            self.assertEqual(len(pending["items"]), 1)
        self.assertEqual(json.loads((self.folder / "messages.json").read_text()), self.messages)

    def test_changed_audio_rejects_entire_result_without_cache_write(self):
        package = transfer.prepare(self.folder)
        job, _ = unpack_job(package, self.folder / "cloud")
        target = self.result(
            job, [dict(item, status="done", text="text", segments=[]) for item in job["items"]]
        )
        (self.folder / "downloads/two.ogg").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            transfer.import_results(self.folder, target)
        self.assertFalse((self.folder / "transcription_results.json").exists())

    def test_unexpected_archive_and_wrong_job_are_rejected(self):
        package = transfer.prepare(self.folder)
        with zipfile.ZipFile(package, "a") as archive:
            archive.writestr("../escape.txt", "bad")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            unpack_job(package, self.folder / "cloud")
        self.assertFalse((self.folder / "escape.txt").exists())
        target = self.result({"job_id": "not a job"}, [])
        with self.assertRaisesRegex(ValueError, "job ID"):
            transfer.import_results(self.folder, target)

    def test_pause_never_runs_local_whisper_or_chatgpt(self):
        config = {"asr_backend": "colab", "model": "gpt-5.6-sol"}
        with (
            patch("builtins.input", return_value=""),
            patch("process_backup.subprocess.call") as launch,
        ):
            process_backup.process(self.folder, config)
            launch.assert_not_called()
        self.assertTrue((self.folder / "chat_final.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
