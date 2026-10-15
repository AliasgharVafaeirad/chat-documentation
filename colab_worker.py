"""Whisper GPU worker embedded in the Colab notebook; no account credentials."""

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

GPU_CONFIG = {
    "model": "large-v3",
    "language": "fa",
    "settings_version": 3,
    "device": "cuda",
    "compute_type": "float16",
    "beam_size": 5,
    "temperature": 0.0,
    "vad_filter": False,
    "condition_on_previous_text": False,
}


def save_checkpoint(path, value):
    temporary = path.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(path)


def unpack_job(package, workspace):
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if names.count("job.json") != 1 or archive.getinfo("job.json").file_size > 2000000:
            raise ValueError("Invalid job manifest")
        job = json.loads(archive.read("job.json"))
        if (
            job.get("schema") != 1
            or job.get("config") != GPU_CONFIG
            or not re.fullmatch(r"[a-f0-9]{24}", job.get("job_id", ""))
        ):
            raise ValueError("Unexpected job settings")
        items = job.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Empty audio job")
        wanted = ["job.json"]
        tokens = set()
        for item in items:
            token = item.get("token", "")
            name = item.get("file", "")
            if (
                not re.fullmatch(r"v\d{6}", token)
                or token in tokens
                or not re.fullmatch(
                    r"audio/" + token + r"\.(ogg|opus|mp3|wav|m4a|aac|flac|wma|mp4)", name
                )
                or not re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", ""))
            ):
                raise ValueError("Invalid audio manifest entry")
            tokens.add(token)
            wanted.append(name)
        if sorted(names) != sorted(wanted):
            raise ValueError("Archive contains unexpected or duplicate files")
        folder = workspace / job["job_id"]
        (folder / "audio").mkdir(parents=True, exist_ok=True)
        for item in items:
            audio = archive.read(item["file"])
            if hashlib.sha256(audio).hexdigest() != item["sha256"]:
                raise ValueError("Uploaded audio hash mismatch")
            (folder / item["file"]).write_bytes(audio)
    return job, folder


def run_job(package, workspace=Path("/content/whisper_backup")):
    import ctranslate2
    from faster_whisper import WhisperModel

    if (
        ctranslate2.get_cuda_device_count() < 1
        or "float16" not in ctranslate2.get_supported_compute_types("cuda")
    ):
        raise RuntimeError(
            "A supported GPU is required. Enable GPU in Runtime > Change runtime type."
        )
    job, folder = unpack_job(package, workspace)
    checkpoint = folder / "results.json"
    result = {"schema": 1, "job_id": job["job_id"], "config": GPU_CONFIG, "items": []}
    if checkpoint.exists():
        previous = json.loads(checkpoint.read_text(encoding="utf8"))
        if previous.get("job_id") == job["job_id"] and previous.get("config") == GPU_CONFIG:
            result = previous
    completed = {item["token"]: item for item in result["items"]}
    save_checkpoint(checkpoint, result)
    print(
        "Loading Whisper large-v3 on GPU. First model download can take several minutes.",
        flush=True,
    )
    model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    started_job = time.monotonic()
    for index, item in enumerate(job["items"], 1):
        old = completed.get(item["token"], {})
        if old.get("status") == "done" and old.get("sha256") == item["sha256"]:
            print(f"Already completed: {index}/{len(job['items'])}", flush=True)
            continue
        print(f"Transcribing {index}/{len(job['items'])}: {item['token']}", flush=True)
        started = time.monotonic()
        row = {"token": item["token"], "sha256": item["sha256"]}
        try:
            segments, info = model.transcribe(
                str(folder / item["file"]),
                language="fa",
                task="transcribe",
                beam_size=5,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=None,
                word_timestamps=False,
            )
            segments = list(segments)
            row.update(
                status="done",
                text="\n".join(segment.text.strip() for segment in segments),
                duration_seconds=info.duration,
                language=info.language,
                processing_seconds=round(time.monotonic() - started, 3),
                review_needed=not segments
                or any(s.avg_logprob < -1 or s.no_speech_prob > 0.6 for s in segments),
                segments=[
                    {
                        "start": max(0, min(info.duration, s.start)),
                        "end": max(0, min(info.duration, s.end)),
                        "text": s.text.strip(),
                        "avg_logprob": s.avg_logprob,
                        "no_speech_prob": s.no_speech_prob,
                    }
                    for s in segments
                ],
            )
            print(row["text"], flush=True)
        except Exception as error:
            row.update(status="error", error=str(error), text="", segments=[], review_needed=True)
            print("File failed:", error, flush=True)
        completed[item["token"]] = row
        result["items"] = list(completed.values())
        save_checkpoint(checkpoint, result)
    print(
        f"GPU processing elapsed: {time.monotonic() - started_job:.1f}s. Run the download cell.",
        flush=True,
    )
    return checkpoint


def package_results(checkpoint):
    result = json.loads(checkpoint.read_text(encoding="utf8"))
    target = (
        checkpoint.parent
        / f"whisper_colab_results_{result['job_id']}_{len(result['items']):06d}.zip"
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(checkpoint, "results.json")
    return target
