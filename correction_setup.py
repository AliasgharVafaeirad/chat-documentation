"""Interactive correction setup without editing configuration files by hand."""

import json
from pathlib import Path
import re
import subprocess

from app_config import load_config


def catalog(config):
    result = subprocess.run(
        [config["chatgpt_python"], "-u", config["chatgpt_script"], "models"],
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
        timeout=90,
    )
    if result.returncode:
        # Do not print captured diagnostics: a replacement connector may log secrets.
        missing = any(
            reason in result.stdout
            for reason in (
                "Not connected",
                "Sign in again",
                "HTTP 401",
                "permission was removed",
            )
        )
        return [], "login" if missing else "unavailable"
    models = []
    seen = set()
    for line in result.stdout.splitlines():
        identifier, separator, label = line.partition(" | ")
        if (
            separator
            and re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", identifier)
            and identifier not in seen
        ):
            seen.add(identifier)
            display = "".join(c for c in label if c.isascii() and c.isprintable())[:100]
            models.append((identifier, display or identifier))
    return models, None if models else "unavailable"


def save_settings(base, **updates):
    path = Path(base) / "pipeline_config.json"
    config = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("Local configuration must be a JSON object")
    config.update(updates)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    temporary.replace(path)


def setup(base):
    config = load_config(base)
    print("\nCHATGPT TEXT CORRECTION", flush=True)
    if config.get("correction_enabled"):
        print("ON | Model: " + config["model"])
        print("1) Check connection / change model\n2) Turn off text correction\n0) Back")
        while True:
            answer = input("Number: ").strip()
            if answer == "0":
                return
            if answer == "2":
                save_settings(base, correction_enabled=False)
                print("Text correction is OFF. Audio transcription remains available.", flush=True)
                return
            if answer == "1":
                break
            print("Choose 0, 1 or 2.")
    print("Checking your ChatGPT connection...", flush=True)
    try:
        models, problem = catalog(config)
    except (OSError, subprocess.TimeoutExpired):
        models, problem = [], "unavailable"
    if problem == "login":
        print(
            "Sign in in the browser that opens. Then return here; setup continues automatically.",
            flush=True,
        )
        code = subprocess.call([config["chatgpt_python"], "-u", config["chatgpt_script"], "login"])
        if code:
            print(
                "Sign-in did not finish. Settings unchanged. Choose option 5 to retry.", flush=True
            )
            return
        try:
            models, problem = catalog(config)
        except (OSError, subprocess.TimeoutExpired):
            models, problem = [], "unavailable"
    if not models:
        print(
            "Could not verify the connection. Check your internet/VPN and try option 5 again. Settings unchanged.",
            flush=True,
        )
        return
    print("Connected. Choose a model for text correction:", flush=True)
    default = next((i for i, (slug, _) in enumerate(models, 1) if slug == config.get("model")), 1)
    for i, (slug, display) in enumerate(models, 1):
        print(f"{i}) {display} [{slug}]" + (" (default)" if i == default else ""))
    while True:
        answer = input(f"Model number (Enter = {default}, 0 = cancel): ").strip()
        if answer == "0":
            print("Cancelled. Settings unchanged.")
            return
        if not answer:
            answer = str(default)
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(models):
            selected = models[int(answer) - 1][0]
            break
        print("Choose one of the displayed model numbers.")
    save_settings(base, correction_enabled=True, model=selected)
    print("Ready! ChatGPT text correction is ON. Model: " + selected, flush=True)
    print("Future processing uses this choice automatically. No file editing needed.", flush=True)
