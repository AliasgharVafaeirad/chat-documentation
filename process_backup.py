"""Resumable local ASR, ChatGPT-plan correction and final documented Excel."""

from audio_policy import is_voice
import argparse
import hashlib
import json
from app_config import load_config
from pathlib import Path
import re
import subprocess
from correction_runner import run_correction, failure_reason

BASE = Path(__file__).resolve().parent
AUDIO = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma", ".mp4"}


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(path)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def attachment(folder, value):
    if not isinstance(value, str) or not value.startswith("downloads/"):
        return None
    path = (folder / value).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("Attachment path escapes backup folder")
    return path


def context(messages, index):
    # A bounded candidate window; model independently chooses relevance per voice.
    rows = []
    for neighbor in range(max(0, index - 4), min(len(messages), index + 5)):
        text = messages[neighbor].get("text", "").strip()
        if neighbor != index and text:
            rows.append(
                {
                    "relative_position": neighbor - index,
                    "text": text[:2000],
                    "message_id": str(messages[neighbor].get("id", neighbor)),
                }
            )
    return rows


def tasks(messages, folder, whisper):
    work = []
    exclusion_path = folder / "voice_processing_exclusions.json"
    excluded = (
        set(json.loads(exclusion_path.read_text(encoding="utf8")))
        if exclusion_path.exists()
        else set()
    )
    for index, message in enumerate(messages):
        for number, ref in enumerate(message.get("attachment_results", [])):
            if ref in excluded or not is_voice(message, ref):
                continue
            path = attachment(folder, ref)
            if path is None or path.suffix.lower() not in AUDIO or not path.is_file():
                continue
            sha = digest(path)
            raw = whisper.get(ref, {})
            if (
                raw.get("status") != "done"
                or raw.get("sha256") != sha
                or not raw.get("text", "").strip()
            ):
                continue
            alternatives = [{"engine": "Whisper-large-v3", "text": raw["text"]}]
            item = {
                "id": f"{message.get('_voice_task_prefix', f'm{index}')}_a{number}",
                "alternatives": alternatives,
                "chat_context": context(messages, index),
            }
            fingerprint = hashlib.sha256(
                json.dumps(item, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            work.append(
                {
                    "item": item,
                    "ref": ref,
                    "message_index": index,
                    "sha256": sha,
                    "input_hash": fingerprint,
                }
            )
    return work


def clean(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def export(messages, folder, whisper, corrections, work):
    from documentation_excel import write

    return write(messages, folder, whisper, corrections, work, attachment, save)


def choose_asr():
    print("Voice transcription: 1) Colab GPU  2) This computer (local CPU)  0) Pause", flush=True)
    while True:
        choice = input("Number: ").strip().translate(str.maketrans("۰۱۲", "012"))
        if choice in ("0", "1", "2"):
            return {"0": None, "1": "colab", "2": "local"}[choice]
        print("Choose 0, 1 or 2.", flush=True)


def process(
    folder,
    config,
    sample=None,
    export_only=False,
    skip_asr=False,
    colab_result=None,
    asr_only=False,
    only_file=None,
):
    folder = folder.resolve()
    print("Processing report: " + folder.name, flush=True)
    if not export_only and not skip_asr and not colab_result:
        print("Checking saved audio and completed transcripts...", flush=True)
        from colab_transfer import has_pending

        if not has_pending(folder, only_file=only_file):
            print("No pending audio transcription. Using saved results.", flush=True)
            skip_asr = True
        elif config.get("choose_asr", False):
            selected = choose_asr()
            if selected is None:
                return process(folder, config, export_only=True)
            config = dict(config, asr_backend=selected)
    if colab_result:
        from colab_transfer import import_results

        import_results(folder, colab_result)
        skip_asr = True
    if not export_only and not skip_asr and config.get("asr_backend") == "colab":
        from colab_transfer import prepare, import_results

        package = prepare(folder, only_file=only_file)
        if package:
            if config.get("colab_browser", False):
                from colab_browser import automate, ColabQuotaError

                try:
                    result_path = automate(folder, package)
                except ColabQuotaError:
                    print("Colab GPU quota unavailable. Completed results are saved.", flush=True)
                    selected = choose_asr()
                    if selected == "local":
                        return process(
                            folder,
                            dict(config, asr_backend="local", choose_asr=False),
                            sample,
                            asr_only=asr_only,
                            only_file=only_file,
                        )
                    print(
                        "GPU stage paused. Retry later using Continue processing a saved backup.",
                        flush=True,
                    )
                    return process(folder, config, export_only=True)
                except Exception:
                    process(folder, config, export_only=True)
                    raise
                import_results(folder, result_path)
            else:
                print(
                    "Open Whisper_Colab_Backup.ipynb in Google Colab, enable GPU, upload the package above, and run all cells.",
                    flush=True,
                )
                print("Notebook:", BASE / "Whisper_Colab_Backup.ipynb", flush=True)
                answer = (
                    input("Paste the downloaded whisper_colab_results.zip path (Enter to pause): ")
                    .strip()
                    .strip('"')
                )
                if not answer:
                    return process(folder, config, export_only=True)
                import_results(folder, Path(answer))
        skip_asr = True
    if not export_only and not skip_asr:
        command = [
            config["voice_python"],
            "-u",
            str(BASE / "transcribe_voices.py"),
            "--backup",
            str(folder),
        ]
        if config.get("model_root"):
            command += ["--model-root", config["model_root"]]
        if sample:
            command += ["--sample", str(sample)]
        if only_file:
            command += ["--only-file", only_file]
        code = subprocess.call(command)
        if code:
            print(
                "Some voice transcription failed. Completed files will still be exported.",
                flush=True,
            )
    messages = json.loads((folder / "messages.json").read_text(encoding="utf8"))
    cache = folder / "transcription_results.json"
    whisper = json.loads(cache.read_text(encoding="utf8")) if cache.exists() else {}
    work = tasks(messages, folder, whisper)
    correction_cache = folder / "correction_results.json"
    corrections = (
        json.loads(correction_cache.read_text(encoding="utf8")) if correction_cache.exists() else {}
    )
    prompt = Path(config.get("correction_prompt") or BASE / "correction_prompt.txt")
    pipeline_version = hashlib.sha256(
        (
            ("openai_api:" if config.get("correction_provider") == "openai_api" else "")
            + config["model"]
            + prompt.read_text(encoding="utf8")
        ).encode()
    ).hexdigest()
    for task in work:
        task["input_hash"] = hashlib.sha256(
            (task["input_hash"] + pipeline_version).encode()
        ).hexdigest()
    pending = [
        task
        for task in work
        if corrections.get(task["item"]["id"], {}).get("input_hash") != task["input_hash"]
        or corrections.get(task["item"]["id"], {}).get("status") != "done"
    ]
    if only_file:
        pending = [task for task in pending if task["ref"] == only_file]
    if sample:
        pending = pending[:sample]
    target = export(messages, folder, whisper, corrections, work)
    if not config.get("correction_enabled", True):
        print(
            "Text correction disabled. Raw transcripts are retained; corrected text remains pending.",
            flush=True,
        )
    if not export_only and not asr_only and config.get("correction_enabled", True):
        from correction_sizing import units_for, batches, combine

        part_cache_path = folder / "correction_parts.json"
        part_cache = (
            json.loads(part_cache_path.read_text(encoding="utf8"))
            if part_cache_path.exists()
            else {}
        )
        plans = [(task, units_for(task)) for task in pending]

        def commit_completed():
            for task, units in plans:
                result = combine(task, units, part_cache)
                if result is not None:
                    corrections[result["id"]] = dict(
                        result,
                        status="done",
                        input_hash=task["input_hash"],
                        audio_sha256=task["sha256"],
                        model=config["model"],
                    )
                else:
                    failed = [
                        part_cache.get(unit["key"], {})
                        for unit in units
                        if part_cache.get(unit["key"], {}).get("status") not in (None, "done")
                    ]
                    if failed:
                        reasons = list(
                            dict.fromkeys(
                                part.get("error", "incomplete_response") for part in failed
                            )
                        )
                        corrections[task["item"]["id"]] = {
                            "status": "blocked"
                            if any(part.get("status") == "blocked" for part in failed)
                            else "pending",
                            "error": "; ".join(reasons),
                            "input_hash": task["input_hash"],
                            "audio_sha256": task["sha256"],
                            "model": config["model"],
                        }
            save(correction_cache, corrections)

        commit_completed()
        remaining = [
            unit
            for _, units in plans
            for unit in units
            if part_cache.get(unit["key"], {}).get("status") not in ("done", "blocked")
        ]
        requests = list(batches(remaining))
        retried_empty = set()
        print(
            f"Pending voices: {len(pending)} | Text parts: {len(remaining)} | Requests: {len(requests)}",
            flush=True,
        )
        for request_number, batch in enumerate(requests, 1):
            source, output = folder / "correction_input.json", folder / "correction_batch.json"
            save(source, {"items": [task["item"] for task in batch]})
            output.unlink(missing_ok=True)  # Exact known temporary output, never cached results.
            print(
                f"Correcting request {request_number}/{len(requests)} | text parts: {len(batch)}",
                flush=True,
            )
            try:
                code = run_correction(
                    [
                        config["chatgpt_python"],
                        "-u",
                        config.get("correction_script", config["chatgpt_script"]),
                        "correct",
                        "--input",
                        str(source),
                        "--model",
                        config["model"],
                        "--output",
                        str(output),
                        "--instructions",
                        str(prompt),
                    ],
                    folder,
                )
                if code or not output.exists():
                    raise RuntimeError(failure_reason(folder))
                response = json.loads(output.read_text(encoding="utf8"))
                returned = response["result"]["items"]
                expected = {task["item"]["id"] for task in batch}
                mapped = {item["id"]: item for item in returned}
                missing = {item["id"]: item for item in response.get("pending", [])}
                if (
                    len(mapped) != len(returned)
                    or not set(mapped).issubset(expected)
                    or not set(missing).issubset(expected)
                    or set(mapped) & set(missing)
                ):
                    raise RuntimeError("Correction IDs mismatch")
                for task in batch:
                    key = task["item"]["id"]
                    if key not in mapped:
                        reason = (
                            missing.get(key, {}).get("error")
                            or response.get("stop_reason")
                            or "missing_or_invalid_item"
                        )
                        part_cache[task["key"]] = {
                            "id": key,
                            "status": "blocked" if reason == "content_filter" else "error",
                            "error": reason,
                        }
                        print("Correction pending: " + key + " | " + reason, flush=True)
                        continue
                    result = mapped[key]
                    if (
                        not isinstance(result.get("corrected_text"), str)
                        or not result["corrected_text"].strip()
                    ):
                        part_cache[task["key"]] = dict(
                            result, status="needs_review", error="Empty correction"
                        )
                        if task["key"] not in retried_empty:
                            retried_empty.add(task["key"])
                            requests.append([task])
                            print(
                                "Empty correction: scheduling one separate retry for "
                                + task["item"]["id"],
                                flush=True,
                            )
                        else:
                            print(
                                "Correction still empty: kept pending for review; continuing other voices.",
                                flush=True,
                            )
                        continue
                    part_cache[task["key"]] = dict(result, status="done")
                save(part_cache_path, part_cache)
                commit_completed()
            except Exception as error:
                print(
                    "Correction request failed:",
                    error,
                    "| continuing other requests; this part remains pending.",
                    flush=True,
                )
                for task in batch:
                    if part_cache.get(task["key"], {}).get("status") != "done":
                        part_cache[task["key"]] = {
                            "status": "blocked" if "content_filter" in str(error) else "error",
                            "error": str(error),
                            "id": task["item"]["id"],
                        }
                save(part_cache_path, part_cache)
                commit_completed()
                export(messages, folder, whisper, corrections, work)
                continue
            save(correction_cache, corrections)
            target = export(messages, folder, whisper, corrections, work)
        target = export(messages, folder, whisper, corrections, work)
        incomplete = sum(
            corrections.get(task["item"]["id"], {}).get("status") != "done"
            or corrections.get(task["item"]["id"], {}).get("input_hash") != task["input_hash"]
            for task in work
        )
        if incomplete:
            print(
                f"Correction stage finished with {incomplete} voices still pending/requiring review. Excel marks incomplete text. Continue later retries pending items; content-filter blocks require review.",
                flush=True,
            )
    print("Final Excel:", target, flush=True)
    print(
        "Original audio and raw transcripts retained. Check Status for incomplete data.", flush=True
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--sample", type=int)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument(
        "--skip-asr",
        action="store_true",
        help="Use existing raw transcripts and only run pending corrections",
    )
    parser.add_argument(
        "--import-colab", type=Path, help="Import GPU results and continue with ChatGPT correction"
    )
    parser.add_argument("--auto-colab", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(BASE)
        if args.auto_colab:
            config["colab_browser"] = True
            config["asr_backend"] = "colab"
            config["choose_asr"] = False
        process(
            args.backup, config, args.sample, args.export_only, args.skip_asr, args.import_colab
        )
    except KeyboardInterrupt:
        print("Stopped. Completed work is saved; rerun to resume.")
    except Exception as error:
        print("Processing stopped:", error)
        raise SystemExit(1)
