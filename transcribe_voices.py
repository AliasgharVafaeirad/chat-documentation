"""Local, resumable Persian voice transcription for existing chat backups."""

from audio_policy import is_voice
import argparse
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIO_EXTENSIONS = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma", ".mp4"}


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_duration(path):
    import av

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("File has no audio stream")
        if container.duration is not None:
            return container.duration / av.time_base
        stream = container.streams.audio[0]
        return float(stream.duration * stream.time_base) if stream.duration else 0


def collect(messages, folder):
    refs = {}
    for index, message in enumerate(messages):
        for value in message.get("attachment_results", []):
            if not is_voice(message, value) or not value.startswith("downloads/"):
                continue
            path = (folder / value).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError("Attachment path is outside the backup folder")
            if path.suffix.lower() in AUDIO_EXTENSIONS:
                refs.setdefault(value, []).append(index)
    return refs


def safe_cell(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def stamp(seconds):
    total = int(round(seconds * 1000))
    hours, total = divmod(total, 3600000)
    minutes, total = divmod(total, 60000)
    seconds, millis = divmod(total, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_transcript(folder, key, result):
    directory = folder / "transcripts"
    directory.mkdir(exist_ok=True)
    (directory / (key + ".txt")).write_text(result["text"], encoding="utf-8")
    subtitles = "\n\n".join(
        f"{i + 1}\n{stamp(s['start'])} --> {stamp(s['end'])}\n{s['text']}"
        for i, s in enumerate(result["segments"])
    )
    (directory / (key + ".srt")).write_text(subtitles + "\n", encoding="utf-8")
    atomic_json(directory / (key + ".json"), result)


def export(messages, folder, results, refs):
    # Export copies; do not overwrite the original backup or its Excel file.
    enriched = json.loads(json.dumps(messages, ensure_ascii=False))
    for ref, indices in refs.items():
        result = results.get(ref) or {
            "status": "pending",
            "text": "",
            "segments": [],
            "review_needed": False,
        }
        for index in indices:
            enriched[index].setdefault("voice_transcripts", []).append(dict(result, audio_path=ref))
    atomic_json(folder / "messages_with_transcripts.json", enriched)
    import pandas as pd

    rows = []
    for index, message in enumerate(enriched, 1):
        transcripts = message.get("voice_transcripts", [])
        transcript = "\n\n".join(x.get("text", "") for x in transcripts)
        timing = "\n".join(
            f"[{stamp(s['start'])} - {stamp(s['end'])}] {s['text']}"
            for x in transcripts
            for s in x.get("segments", [])
        )
        text = message.get("text", "")
        parts = max(1, (max(len(text), len(transcript), len(timing)) + 31999) // 32000)
        for part in range(parts):
            start, end = part * 32000, (part + 1) * 32000
            rows.append(
                {
                    "Message": index,
                    "Part": part + 1,
                    "Date": safe_cell(message.get("date", "")),
                    "Sender": safe_cell(message.get("sender", "")),
                    "Time": safe_cell(message.get("time", "")),
                    "Message text": safe_cell(text[start:end]),
                    "Attachments": safe_cell("\n".join(message.get("attachment_results", []))),
                    "Voice transcript": safe_cell(transcript[start:end]),
                    "Timed transcript": safe_cell(timing[start:end]),
                    "Transcription status": safe_cell(
                        "; ".join(x.get("status", "") for x in transcripts)
                    ),
                    "Transcription error": safe_cell(
                        "; ".join(x.get("error", "") for x in transcripts if x.get("error"))
                    ),
                    "Review needed": any(x.get("review_needed", False) for x in transcripts),
                }
            )
    target = folder / "chat_with_transcripts.xlsx"
    temporary = folder / "chat_with_transcripts.tmp.xlsx"
    try:
        pd.DataFrame(rows).to_excel(temporary, index=False, engine="openpyxl")
        temporary.replace(target)
    except PermissionError:
        print(
            "Close chat_with_transcripts.xlsx and rerun to update Excel. JSON is saved.", flush=True
        )


def choose_backup():
    folders = sorted(
        (p.parent for p in BASE.glob("*_backups/*/messages.json")),
        key=lambda p: p.name,
        reverse=True,
    )
    if not folders:
        raise ValueError("No saved chat backups found")
    folders = folders[:20]
    print("Select an existing backup (newest first):")
    for index, folder in enumerate(folders, 1):
        print(f"{index}) {folder.parent.name}/{folder.name}")
    number = int(input("Number: "))
    if not 1 <= number <= len(folders):
        raise ValueError("Invalid backup number")
    return folders[number - 1]


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe saved voice files locally. Original backups are preserved."
    )
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--sample", type=int, help="Process only N shortest pending voice files")
    parser.add_argument("--only-file", help="Process only this report-relative attachment")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument(
        "--language", default="fa", help="fa for Persian; auto for language detection"
    )
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="cpu")
    parser.add_argument("--model-root", type=Path, default=BASE / "transcription_models")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Refresh outputs using saved transcripts without running the model",
    )
    args = parser.parse_args()
    folder = (args.backup or choose_backup()).resolve()
    messages = json.loads((folder / "messages.json").read_text(encoding="utf-8"))
    refs = collect(messages, folder)
    exclusion_path = folder / "voice_processing_exclusions.json"
    excluded = (
        set(json.loads(exclusion_path.read_text(encoding="utf8")))
        if exclusion_path.exists()
        else set()
    )
    refs = {ref: items for ref, items in refs.items() if ref not in excluded}
    if args.only_file:
        if args.only_file not in refs:
            raise ValueError("Selected voice is not in this report")
        refs = {args.only_file: refs[args.only_file]}
    print(f"Voice/audio files found: {len(refs)}", flush=True)
    if not refs:
        return
    cache_path = folder / "transcription_results.json"
    results = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    for result in results.values():
        duration = result.get("duration_seconds", 0)
        if result.get("status") == "done" and duration > 0:
            for segment in result.get("segments", []):
                segment["start"] = min(duration, max(0, segment["start"]))
                segment["end"] = min(duration, max(segment["start"], segment["end"]))
            if result.get("transcript_file"):
                write_transcript(folder, Path(result["transcript_file"]).stem, result)
    from colab_transfer import CONFIG, reusable_transcript

    config = dict(
        CONFIG, model=args.model, language=args.language, device=args.device, compute_type="float32"
    )
    pending = []
    for ref in refs:
        path = folder / ref
        if not path.is_file():
            results[ref] = {
                "status": "error",
                "error": "Audio file is missing",
                "review_needed": True,
            }
            continue
        digest = file_hash(path)
        previous = results.get(ref, {})
        if reusable_transcript(previous, digest, config):
            continue
        try:
            duration = audio_duration(path)
        except Exception as error:
            results[ref] = {"status": "error", "error": str(error), "review_needed": True}
            continue
        pending.append((duration, ref, digest))
    pending.sort()
    print(
        f"Pending: {len(pending)} | Total pending audio: {sum(x[0] for x in pending) / 60:.1f} minutes",
        flush=True,
    )
    if args.list_only:
        return
    if args.sample:
        if args.sample < 1:
            raise ValueError("Sample count must be positive")
        pending = pending[: args.sample]
    atomic_json(cache_path, results)
    export(messages, folder, results, refs)
    if args.export_only:
        print("Outputs refreshed using saved transcripts. No audio was reprocessed.", flush=True)
        return
    if not pending:
        print("All available voice files are already transcribed. Excel refreshed.", flush=True)
        return
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    import ctranslate2
    from faster_whisper import WhisperModel

    device = args.device
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
    print(
        f"Loading {args.model} on {device}. First run downloads the model; audio stays local.",
        flush=True,
    )
    local_model = args.model_root / (args.model + "-local")
    model_source = str(local_model) if (local_model / "model.bin").is_file() else args.model
    model = WhisperModel(
        model_source,
        device=device,
        compute_type="float32",
        download_root=str(args.model_root),
        cpu_threads=max(1, min(8, os.cpu_count() or 4)),
    )
    logger = logging.getLogger("voice_transcription")
    handler = logging.FileHandler(folder / "transcription.log", encoding="utf-8")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for number, (duration, ref, digest) in enumerate(pending, 1):
            print(
                f"Transcribing {number}/{len(pending)} | Audio duration: {duration:.1f}s",
                flush=True,
            )
            started = time.monotonic()
            try:
                segments, info = model.transcribe(
                    str(folder / ref),
                    language=None if args.language == "auto" else args.language,
                    beam_size=5,
                    temperature=0.0,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                saved = []
                for segment in segments:
                    saved.append(
                        {
                            "start": min(duration, max(0, segment.start))
                            if duration > 0
                            else segment.start,
                            "end": min(duration, max(segment.start, segment.end))
                            if duration > 0
                            else segment.end,
                            "text": segment.text.strip(),
                            "avg_logprob": segment.avg_logprob,
                            "no_speech_prob": segment.no_speech_prob,
                        }
                    )
                result = {
                    "status": "done",
                    "sha256": digest,
                    "config": config,
                    "duration_seconds": duration,
                    "processing_seconds": round(time.monotonic() - started, 2),
                    "language": info.language,
                    "text": "\n".join(s["text"] for s in saved),
                    "segments": saved,
                    "review_needed": not saved
                    or any(s["avg_logprob"] < -1 or s["no_speech_prob"] > 0.6 for s in saved),
                }
                key = hashlib.sha256((ref + digest).encode()).hexdigest()[:20]
                result["transcript_file"] = "transcripts/" + key + ".txt"
                write_transcript(folder, key, result)
                results[ref] = result
                logger.info(
                    "Done: %s; duration=%.1f; elapsed=%.1f; segments=%s",
                    ref,
                    duration,
                    result["processing_seconds"],
                    len(saved),
                )
                print(
                    f"Saved transcript | {len(saved)} segments | {result['processing_seconds']:.1f}s processing",
                    flush=True,
                )
            except Exception as error:
                results[ref] = {
                    "status": "error",
                    "sha256": digest,
                    "config": config,
                    "error": str(error),
                    "review_needed": True,
                }
                logger.exception("Failed: %s", ref)
                print(f"Transcription failed: {error}", flush=True)
            atomic_json(cache_path, results)
            export(messages, folder, results, refs)
    finally:
        atomic_json(cache_path, results)
        export(messages, folder, results, refs)
        logger.removeHandler(handler)
        handler.close()
    done = sum(results.get(ref, {}).get("status") == "done" for ref in refs)
    print(
        f"Finished: {done}/{len(refs)} files transcribed. Output: {folder / 'chat_with_transcripts.xlsx'}",
        flush=True,
    )
    print("Keep the original audio. Names and technical terms may need manual review.", flush=True)
    if any(results.get(ref, {}).get("status") == "error" for _, ref, _ in pending):
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped manually. Completed transcripts are saved; rerun to continue.")
    except Exception as error:
        print(f"Error: {error}", flush=True)
        raise SystemExit(1)
