"""Portable defaults; private machine settings stay outside version control."""

import json
from pathlib import Path
import sys


def load_config(base):
    base = Path(base).resolve()
    config = {
        "voice_python": sys.executable,
        "model_root": str(base / "transcription_models"),
        "chatgpt_python": sys.executable,
        "chatgpt_script": str(base / "integrations" / "chatgpt_plan" / "chatgpt_plan.py"),
        "model": "",
        "correction_enabled": False,
        "asr_backend": "local",
        "colab_browser": True,
        "choose_asr": True,
        "manage_installed_folders": False,
    }
    local = base / "pipeline_config.json"
    if local.exists():
        value = json.loads(local.read_text(encoding="utf8"))
        if not isinstance(value, dict):
            raise ValueError("pipeline_config.json must contain a JSON object")
        config.update(value)
    for key in ("voice_python", "chatgpt_python", "chatgpt_script", "model_root"):
        value = Path(config[key]).expanduser()
        if not value.is_absolute() and key not in ("voice_python", "chatgpt_python"):
            value = base / value
        config[key] = str(value)
    if config.get("correction_enabled") and not str(config.get("model", "")).strip():
        raise ValueError("Choose an available correction model in pipeline_config.json first")
    return config
