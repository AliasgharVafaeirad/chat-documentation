import ast, json, unittest, zipfile, inspect, types, sys
from unittest.mock import Mock, patch, AsyncMock
import asyncio
import colab_bridge
import colab_browser
import colab_worker
from test_colab_transfer import TransferTests
from colab_browser import build_notebook, receive_result, job_for, requested_part
from colab_bridge import completed_checkpoint
import colab_transfer as transfer


class BrowserJobTests(TransferTests):
    def test_browser_failure_reopens_only_twice(self):
        failure = RuntimeError("Target page, context or browser has been closed")
        runner = AsyncMock(side_effect=[failure, failure, "recovered"])
        with (
            patch.object(colab_browser, "run_once", runner),
            patch.object(colab_browser.asyncio, "sleep", AsyncMock()),
        ):
            self.assertEqual(
                asyncio.run(colab_browser.run(self.folder, "package.zip")), "recovered"
            )
        self.assertEqual(runner.await_count, 3)
        runner = AsyncMock(side_effect=ValueError("Unexpected result job"))
        with patch.object(colab_browser, "run_once", runner):
            with self.assertRaises(ValueError):
                asyncio.run(colab_browser.run(self.folder, "package.zip"))
        self.assertEqual(runner.await_count, 1)

    def test_resume_url_is_bound_to_the_same_job(self):
        url = "https://colab.research.google.com/drive/known_notebook?hl=en"
        state = {"job_id": "expected", "stage": "paused", "url": url}
        self.assertEqual(colab_browser.resumable_url(state, {"expected": {}}), url)
        self.assertIsNone(colab_browser.resumable_url(state, {"different": {}}))
        self.assertIsNone(
            colab_browser.resumable_url(dict(state, stage="complete"), {"expected": {}})
        )
        self.assertIsNone(
            colab_browser.resumable_url(
                dict(state, url="https://other.example/drive/x"), {"expected": {}}
            )
        )

    def test_partial_runtime_checkpoint_does_not_repeat_finished_voice(self):
        package = transfer.prepare(self.folder)
        job = job_for(package)
        runtime = self.folder / "runtime"
        checkpoint = runtime / job["job_id"] / "results.json"
        checkpoint.parent.mkdir(parents=True)
        old = dict(job["items"][0], status="done", text="saved text", segments=[])
        checkpoint.write_text(
            json.dumps(dict(schema=1, job_id=job["job_id"], config=job["config"], items=[old])),
            encoding="utf8",
        )
        model = Mock()
        segment = types.SimpleNamespace(
            text="new text", start=0, end=1, avg_logprob=0, no_speech_prob=0
        )
        model.transcribe.return_value = (
            [segment],
            types.SimpleNamespace(duration=1, language="fa"),
        )
        ct = types.ModuleType("ctranslate2")
        ct.get_cuda_device_count = lambda: 1
        ct.get_supported_compute_types = lambda device: {"float16"}
        fw = types.ModuleType("faster_whisper")
        fw.WhisperModel = Mock(return_value=model)
        with patch.dict(sys.modules, {"ctranslate2": ct, "faster_whisper": fw}):
            colab_worker.run_job(package, workspace=runtime)
        self.assertEqual(model.transcribe.call_count, 1)
        result = json.loads(checkpoint.read_text(encoding="utf8"))
        self.assertEqual(result["items"][0]["text"], "saved text")
        self.assertEqual(len(result["items"]), len(job["items"]))

    def test_resumed_multipart_skips_finished_uploads(self):
        package = transfer.prepare(self.folder)
        parts = transfer.split_packages(self.folder, package, limit=1)
        jobs = [job_for(part) for part in parts]
        root = self.folder / "runtime"
        root.mkdir()
        first = root / "whisper_backup" / jobs[0]["job_id"] / "results.json"
        first.parent.mkdir(parents=True)
        first.write_text(
            json.dumps(
                dict(jobs[0], items=[dict(item, status="done") for item in jobs[0]["items"]])
            ),
            encoding="utf8",
        )
        upload = Mock(return_value={"part.zip": parts[1].read_bytes()})
        google = types.ModuleType("google")
        google.__path__ = []
        module = types.ModuleType("google.colab")
        module.files = types.SimpleNamespace(upload=upload)
        google.colab = module

        def fake_run(path):
            job = job_for(path)
            checkpoint = root / "whisper_backup" / job["job_id"] / "results.json"
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(
                json.dumps(dict(job, items=[dict(item, status="done") for item in job["items"]])),
                encoding="utf8",
            )
            return checkpoint

        published = Mock()
        namespace = dict(
            completed_checkpoint=completed_checkpoint,
            publish_checkpoint=published,
            run_job=fake_run,
        )
        source = inspect.getsource(colab_bridge.run_parts).replace(
            "/content/", root.as_posix() + "/"
        )
        exec(source, namespace)
        with patch.dict(sys.modules, {"google": google, "google.colab": module}):
            namespace["run_parts"](jobs)
            self.assertEqual(upload.call_count, 1)
            namespace["run_parts"](jobs)
            self.assertEqual(upload.call_count, 1)
        self.assertEqual(published.call_count, 4)

    def test_next_part_does_not_require_previous_download(self):
        parts = ["first.zip", "second.zip"]
        self.assertEqual(
            requested_part(
                "Waiting for upload part 1/2\nTranscribing 48/48\nWaiting for upload part 2/2",
                parts,
            ),
            "second.zip",
        )
        self.assertIsNone(requested_part("Waiting for upload part 3/2", parts))
        self.assertIsNone(requested_part("Waiting for upload part 2/3", parts))

    def test_resume_checks_hash_status_and_config(self):
        job = job_for(transfer.prepare(self.folder))
        checkpoint = self.folder / "checkpoint.json"
        result = dict(job, items=[dict(item, status="done") for item in job["items"]])
        checkpoint.write_text(json.dumps(result), encoding="utf8")
        self.assertTrue(completed_checkpoint(checkpoint, job))
        result["items"][0]["sha256"] = "0" * 64
        checkpoint.write_text(json.dumps(result), encoding="utf8")
        self.assertFalse(completed_checkpoint(checkpoint, job))

    def test_automated_notebook_avoids_colab_download_callbacks(self):
        package = transfer.prepare(self.folder)
        build_notebook(package, self.folder / "auto.ipynb")
        notebook = json.loads((self.folder / "auto.ipynb").read_text(encoding="utf8"))
        source = "\n".join(
            "".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"
        )
        self.assertNotIn("files.download(", source)
        self.assertIn("data-backup-result", source)
        self.assertIn("from google.colab import files", source)

    def test_parts_keep_each_voice_exactly_once(self):
        package = transfer.prepare(self.folder)
        original = job_for(package)
        parts = transfer.split_packages(self.folder, package, limit=1)
        self.assertEqual(len(parts), len(original["items"]))
        combined = []
        with zipfile.ZipFile(package) as source:
            for part in parts:
                with zipfile.ZipFile(part) as archive:
                    for item in job_for(part)["items"]:
                        combined.append(item["token"])
                        self.assertEqual(archive.read(item["file"]), source.read(item["file"]))
        self.assertEqual(combined, [item["token"] for item in original["items"]])
        build_notebook(parts[0], self.folder / "parts.ipynb", parts)
        notebook = json.loads((self.folder / "parts.ipynb").read_text(encoding="utf8"))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code" and not "".join(cell["source"]).startswith("%"):
                ast.parse("".join(cell["source"]))
        for part in parts:
            job = job_for(part)
            target = self.result(
                job, [dict(item, status="done", text="text", segments=[]) for item in job["items"]]
            )
            self.assertTrue(receive_result(self.folder, target, job))
        cache = json.loads((self.folder / "transcription_results.json").read_text(encoding="utf8"))
        self.assertEqual(len(cache), len(original["items"]))

    def test_notebook_and_partial_results(self):
        package = transfer.prepare(self.folder)
        job, name = build_notebook(package, self.folder / "auto.ipynb")
        self.assertEqual(job, job_for(package))
        notebook = json.loads((self.folder / "auto.ipynb").read_text(encoding="utf8"))
        self.assertEqual(notebook["metadata"]["accelerator"], "GPU")
        for cell in notebook["cells"]:
            source = "".join(cell["source"])
            if cell["cell_type"] == "code" and not source.startswith("%"):
                ast.parse(source)
        self.assertIn(job["job_id"], name)
        self.assertNotIn("private chat text", json.dumps(notebook))
        target = self.result(job, [dict(job["items"][0], status="done", text="text", segments=[])])
        self.assertFalse(receive_result(self.folder, target, job))
        target = self.result(
            job, [dict(x, status="done", text="text", segments=[]) for x in job["items"]]
        )
        self.assertTrue(receive_result(self.folder, target, job))

    def test_wrong_job_does_not_touch_cache(self):
        job = job_for(transfer.prepare(self.folder))
        target = self.result({"job_id": "a" * 24}, [])
        with self.assertRaises(ValueError):
            receive_result(self.folder, target, job)
        self.assertFalse((self.folder / "transcription_results.json").exists())


if __name__ == "__main__":
    unittest.main()
