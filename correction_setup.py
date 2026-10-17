"""Interactive correction setup without editing configuration files by hand."""

import json
from pathlib import Path
import re
import subprocess
import getpass
import warnings

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


def choose_provider():
    print("Connection method:\n1) Sign in with ChatGPT account\n2) OpenAI API key\n0) Back")
    while True:
        answer = input("Number: ").strip()
        if answer in ("0", "1", "2"):
            return {"0": None, "1": "chatgpt", "2": "openai_api"}[answer]
        print("Choose 0, 1 or 2.")


def choose_model(models, current):
    default = next((i for i, (slug, _) in enumerate(models, 1) if slug == current), 1)
    for i, (slug, display) in enumerate(models, 1):
        print(f"{i}) {display} [{slug}]" + (" (default)" if i == default else ""))
    while True:
        answer = input(f"Model number (Enter = {default}, 0 = cancel): ").strip()
        if answer == "0":
            print("Cancelled. Settings unchanged.")
            return None
        answer = answer or str(default)
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(models):
            return models[int(answer) - 1][0]
        print("Choose one of the displayed model numbers.")


def setup_api(base, config):
    import api_connection as api

    print(
        "OPENAI API KEY\nAPI usage is billed separately by your API provider. No paid test generation is sent.",
        flush=True,
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass(
                "Paste API key (hidden; Enter = reuse saved key, 0 = cancel): "
            ).strip()
        if key == "0":
            return
        key = key or api.read_key(base)
        if len(key) < 16 or any(c.isspace() for c in key):
            print("Invalid key. Settings unchanged.")
            return
        print("Checking API access and available models...", flush=True)
        identifiers = api.text_models(api.models(key))
        if not identifiers:
            print("No candidate text models returned. Settings unchanged.")
            return
        selected = choose_model(
            [(identifier, identifier) for identifier in identifiers], config.get("model")
        )
        if selected is None:
            return
        path = api.key_file(base)
        previous = path.read_bytes() if path.exists() else None
        api.save_key(key, base)
        try:
            save_settings(
                base, correction_enabled=True, correction_provider="openai_api", model=selected
            )
        except Exception:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
            raise
        print("Ready! Text correction is ON via OpenAI API. Model: " + selected, flush=True)
    except getpass.GetPassWarning:
        print(
            "Hidden key entry is unavailable here. Open the app in Windows Terminal. Settings unchanged."
        )
    except RuntimeError as error:
        print(str(error) + " Settings unchanged.", flush=True)
    except OSError:
        print(
            "Could not save the connection. Check folder permissions. No key is printed.",
            flush=True,
        )


def setup(base):
    config = load_config(base)
    print("\nCHATGPT TEXT CORRECTION", flush=True)
    if config.get("correction_enabled"):
        print("ON | Connection: " + config["correction_provider"] + " | Model: " + config["model"])
        print("1) Change connection / model\n2) Turn off text correction\n0) Back")
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
    provider = choose_provider()
    if provider is None:
        return
    if provider == "openai_api":
        setup_api(base, config)
        return
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
    selected = choose_model(models, config.get("model"))
    if selected is None:
        return
    save_settings(base, correction_enabled=True, correction_provider="chatgpt", model=selected)
    print("Ready! ChatGPT text correction is ON. Model: " + selected, flush=True)
    print("Future processing uses this choice automatically. No file editing needed.", flush=True)
