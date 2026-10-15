from pathlib import Path
from contextlib import contextmanager
import uuid
import unittest
from standalone_audio import prepare, export_text, load
from process_backup import save


@contextmanager
def temporary_workspace():
    folder = Path(__file__).resolve().parent.parent / ".test-work" / ("audio_" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    yield folder


class AudioJobs(unittest.TestCase):
    def test_resume_integrity_and_partial_output(self):
        with temporary_workspace() as temporary:
            base = Path(temporary)
            source = base / "sample.ogg"
            source.write_bytes(b"audio fixture")
            folder = prepare(base, source)
            self.assertEqual(folder, prepare(base, source))
            self.assertEqual(export_text(folder)["status"], "transcription_pending")
            job = load(folder / "audio_job.json")
            save(
                folder / "transcription_results.json",
                {job["audio_ref"]: {"status": "done", "sha256": job["sha256"], "text": "raw"}},
            )
            self.assertEqual(export_text(folder)["status"], "correction_pending")
            self.assertFalse((folder / "Readable_Transcript.txt").exists())
            save(
                folder / "correction_results.json",
                {
                    "m0_a0": {
                        "status": "done",
                        "audio_sha256": job["sha256"],
                        "corrected_text": "متن خوانا",
                        "uncertain_phrases": ["نام"],
                    }
                },
            )
            self.assertEqual(export_text(folder)["status"], "done")
            self.assertEqual(
                (folder / "Readable_Transcript.txt").read_text(encoding="utf-8-sig").strip(),
                "متن خوانا",
            )
            (folder / job["audio_ref"]).write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                prepare(base, source)

    def test_separate_audio_and_unsupported_input(self):
        with temporary_workspace() as temporary:
            base = Path(temporary)
            first = base / "one.mp3"
            first.write_bytes(b"first")
            second = base / "two.mp3"
            second.write_bytes(b"second")
            self.assertNotEqual(prepare(base, first), prepare(base, second))
            bad = base / "not_audio.txt"
            bad.write_text("text")
            with self.assertRaises(ValueError):
                prepare(base, bad)


if __name__ == "__main__":
    unittest.main()
