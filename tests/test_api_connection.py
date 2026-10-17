import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx

import api_connection as api
from app_config import load_config
from correction_setup import setup

KEY = "test-credential-" + "x" * 24


class APITests(unittest.TestCase):
    def test_failed_settings_write_restores_previous_encrypted_key(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            key_path = api.key_file(base)
            key_path.parent.mkdir()
            key_path.write_bytes(b"previous encrypted key")

            def replace_key(value, folder):
                api.key_file(folder).write_bytes(b"new encrypted key")

            with (
                patch("correction_setup.choose_provider", return_value="openai_api"),
                patch("correction_setup.getpass.getpass", return_value=KEY),
                patch("api_connection.models", return_value=["model-a"]),
                patch("api_connection.save_key", side_effect=replace_key),
                patch("correction_setup.save_settings", side_effect=OSError("denied")),
                patch("builtins.input", return_value=""),
            ):
                setup(base)
            self.assertEqual(key_path.read_bytes(), b"previous encrypted key")

    def test_models_auth_and_errors_do_not_expose_credentials(self):
        with patch(
            "api_connection.httpx.get",
            return_value=httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]}),
        ) as request:
            self.assertEqual(api.models(KEY), ["model-a", "model-b"])
            self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer " + KEY)
        with patch("api_connection.httpx.get", return_value=httpx.Response(401, text=KEY)):
            with self.assertRaises(RuntimeError) as failure:
                api.models(KEY)
            self.assertNotIn(KEY, str(failure.exception))
        self.assertEqual(
            api.text_models(["model-a", "text-embedding-small", "whisper-1", "gpt-audio"]),
            ["model-a"],
        )

    @unittest.skipUnless(os.name == "nt", "Windows credential encryption")
    def test_encrypted_key_round_trip_and_not_in_config(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            base = Path(directory)
            api.save_key(KEY, base)
            self.assertNotIn(KEY.encode(), api.key_file(base).read_bytes())
            self.assertEqual(api.read_key(base), KEY)
            self.assertFalse((base / "pipeline_config.json").exists())

    def test_wizard_api_selection_saves_provider_and_routes_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with (
                patch("correction_setup.choose_provider", return_value="openai_api"),
                patch("correction_setup.getpass.getpass", return_value=KEY),
                patch("api_connection.models", return_value=["model-a"]),
                patch("api_connection.save_key") as store,
                patch("builtins.input", return_value=""),
            ):
                setup(base)
            config = load_config(base)
            self.assertEqual(config["correction_provider"], "openai_api")
            self.assertTrue(config["correction_enabled"])
            self.assertEqual(Path(config["correction_script"]).name, "api_connection.py")
            self.assertNotIn(KEY, (base / "pipeline_config.json").read_text())
            store.assert_called_once_with(KEY, base)

    def test_failed_or_cancelled_api_setup_preserves_account_configuration(self):
        for model_response in ([], ["model-a"]):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                target = base / "pipeline_config.json"
                target.write_text('{"correction_provider":"chatgpt","model":"old-model"}')
                before = target.read_bytes()
                with (
                    patch("correction_setup.choose_provider", return_value="openai_api"),
                    patch("correction_setup.getpass.getpass", return_value=KEY),
                    patch("api_connection.models", return_value=model_response),
                    patch("api_connection.save_key") as store,
                    patch("builtins.input", return_value="0"),
                ):
                    setup(base)
                self.assertEqual(target.read_bytes(), before)
                store.assert_not_called()

    def test_api_inference_uses_explicit_key_and_shared_result_validation(self):
        from integrations.chatgpt_plan import chatgpt_plan as plan

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input.json"
            output = folder / "output.json"
            prompt = folder / "prompt.txt"
            source.write_text(
                json.dumps(
                    {
                        "items": [
                            {"id": "one", "alternatives": [{"text": "raw"}], "chat_context": []}
                        ]
                    }
                )
            )
            prompt.write_text("Correct text faithfully")
            row = {
                "id": "one",
                "corrected_text": "readable",
                "selected_context_ids": [],
                "uncertain_phrases": [],
                "sensitive_changes": [],
            }
            response = MagicMock(status_code=200)
            response.iter_lines.return_value = [
                "data: "
                + json.dumps(
                    {"type": "response.output_text.delta", "delta": json.dumps({"items": [row]})}
                ),
                "data: " + json.dumps({"type": "response.completed"}),
            ]
            client = MagicMock()
            client.__enter__.return_value = client
            client.stream.return_value.__enter__.return_value = response
            with (
                patch("api_connection.read_key", return_value=KEY),
                patch("api_connection.models", return_value=["model-a"]),
                patch.object(
                    plan, "access", side_effect=AssertionError("Account login must not run")
                ),
                patch.object(plan.httpx, "Client", return_value=client),
            ):
                api.correct(source, "model-a", output, prompt, folder)
            self.assertEqual(
                client.stream.call_args.kwargs["headers"]["Authorization"], "Bearer " + KEY
            )
            saved = json.loads(output.read_text())
            self.assertEqual(saved["result"]["items"][0]["corrected_text"], "readable")
            self.assertNotIn(KEY, output.read_text())
