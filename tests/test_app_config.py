import tempfile
from pathlib import Path
import unittest
from app_config import load_config


class ConfigurationTests(unittest.TestCase):
    def test_clean_checkout_has_portable_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = load_config(base)
            self.assertFalse(config["correction_enabled"])
            self.assertFalse(config["manage_installed_folders"])
            self.assertTrue(Path(config["chatgpt_script"]).is_relative_to(base))
            self.assertFalse((base / "pipeline_config.json").exists())

    def test_enabled_correction_requires_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "pipeline_config.json").write_text('{"correction_enabled":true}')
            with self.assertRaises(ValueError):
                load_config(base)
