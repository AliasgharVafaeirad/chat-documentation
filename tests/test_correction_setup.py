import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from app_config import load_config
from correction_setup import catalog, setup

MODELS = [("model-a", "Model A"), ("model-b", "Model B")]


class SetupTests(unittest.TestCase):
    def test_first_use_logs_in_and_saves_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with (
                patch("correction_setup.catalog", side_effect=[([], "login"), (MODELS, None)]),
                patch("correction_setup.subprocess.call", return_value=0) as login,
                patch("builtins.input", return_value=""),
            ):
                setup(base)
            self.assertEqual(login.call_count, 1)
            saved = load_config(base)
            self.assertTrue(saved["correction_enabled"])
            self.assertEqual(saved["model"], "model-a")

    def test_connected_account_reused_and_other_settings_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "pipeline_config.json").write_text('{"asr_backend":"colab","custom":"keep"}')
            with (
                patch("correction_setup.catalog", return_value=(MODELS, None)),
                patch("correction_setup.subprocess.call") as login,
                patch("builtins.input", side_effect=["bad", "99", "2"]),
            ):
                setup(base)
            login.assert_not_called()
            saved = json.loads((base / "pipeline_config.json").read_text())
            self.assertEqual(saved["model"], "model-b")
            self.assertEqual(saved["asr_backend"], "colab")
            self.assertEqual(saved["custom"], "keep")

    def test_cancel_and_failed_login_preserve_settings(self):
        for responses, login_code, answer in [([(MODELS, None)], 0, "0"), ([([], "login")], 1, "")]:
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                with (
                    patch("correction_setup.catalog", side_effect=responses),
                    patch("correction_setup.subprocess.call", return_value=login_code),
                    patch("builtins.input", return_value=answer),
                ):
                    setup(base)
                self.assertFalse((base / "pipeline_config.json").exists())

    def test_disable_without_network_or_logout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "pipeline_config.json").write_text(
                '{"correction_enabled":true,"model":"model-b"}'
            )
            with (
                patch("correction_setup.catalog") as probe,
                patch("builtins.input", return_value="2"),
            ):
                setup(base)
            probe.assert_not_called()
            self.assertFalse(load_config(base)["correction_enabled"])
            self.assertEqual(load_config(base)["model"], "model-b")

    def test_network_timeout_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            p = base / "pipeline_config.json"
            p.write_text('{"correction_enabled":true,"model":"model-b"}')
            before = p.read_bytes()
            with (
                patch(
                    "correction_setup.catalog", side_effect=subprocess.TimeoutExpired("models", 90)
                ),
                patch("builtins.input", return_value="1"),
            ):
                setup(base)
            self.assertEqual(p.read_bytes(), before)

    def test_catalog_only_accepts_unique_model_rows(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            stdout="noise\nmodel-a | Model A\nmodel-a | duplicate\ninvalid id | bad\n",
            stderr="",
        )
        with patch("correction_setup.subprocess.run", return_value=result):
            models, error = catalog({"chatgpt_python": "python", "chatgpt_script": "connector.py"})
        self.assertEqual(models, [("model-a", "Model A")])
        self.assertIsNone(error)
