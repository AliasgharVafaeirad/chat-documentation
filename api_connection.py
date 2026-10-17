"""Explicit OpenAI API-key route; never falls back from account authentication."""

import argparse
import os
from pathlib import Path
import re

import httpx

BASE = Path(__file__).resolve().parent
API = "https://api.openai.com/v1"


def key_file(base=BASE):
    return Path(base) / ".private" / "openai_api_key.dpapi"


def read_key(base=BASE):
    path = key_file(base)
    if not path.exists():
        value = os.environ.get("OPENAI_API_KEY", "").strip()
        if value:
            return value
        raise RuntimeError("API key not configured. Choose option 5 > OpenAI API key.")
    from integrations.chatgpt_plan.chatgpt_plan import protect

    try:
        return protect(path.read_bytes(), decrypt=True).decode("utf8")
    except Exception:
        raise RuntimeError(
            "Saved API key could not be opened. Enter it again using option 5."
        ) from None


def save_key(value, base=BASE):
    if not value or len(value) < 16 or any(c.isspace() for c in value):
        raise ValueError("Enter a valid API key")
    from integrations.chatgpt_plan.chatgpt_plan import protect, atomic

    atomic(key_file(base), protect(value.encode("utf8")))


def models(key=None, base=BASE):
    key = key or read_key(base)
    try:
        response = httpx.get(
            API + "/models", headers={"Authorization": "Bearer " + key}, timeout=30
        )
    except httpx.HTTPError:
        raise RuntimeError("API connection failed. Check your internet/VPN and retry.") from None
    if response.status_code >= 400:
        hint = {
            401: "API key rejected.",
            403: "API access denied.",
            429: "API rate limit or quota reached.",
        }.get(response.status_code, "API service request failed.")
        raise RuntimeError(hint + " HTTP " + str(response.status_code))
    try:
        records = response.json()["data"]
        ids = {
            item["id"]
            for item in records
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", item["id"])
        }
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Unexpected API model-list response.") from None
    return sorted(ids)


def text_models(identifiers):
    excluded = (
        "embedding",
        "whisper",
        "tts",
        "audio",
        "transcribe",
        "realtime",
        "image",
        "dall-e",
        "moderation",
        "sora",
    )
    return [
        identifier
        for identifier in identifiers
        if not any(word in identifier.lower() for word in excluded)
    ]


def correct(input_path, model, output_path, instructions_path=None, base=BASE):
    from integrations.chatgpt_plan import chatgpt_plan as plan

    key = read_key(base)
    plan.correct(
        input_path,
        model,
        output_path,
        instructions_path,
        token_provider=lambda: key,
        catalog_provider=lambda: [{"slug": identifier} for identifier in models(key)],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["correct"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--instructions")
    args = parser.parse_args()
    try:
        correct(args.input, args.model, args.output, args.instructions)
    except Exception as error:
        # Neither credentials nor arbitrary exception bodies belong in logs.
        print(
            "API correction stopped:",
            str(error) if isinstance(error, RuntimeError) else type(error).__name__,
            flush=True,
        )
        raise SystemExit(1)
