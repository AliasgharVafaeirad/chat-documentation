"""File-bound GPU transcription jobs; never transports ChatGPT credentials."""

from audio_policy import is_voice
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

CONFIG = {
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
AUDIO = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma", ".mp4"}


def reusable_transcript(record, sha, config=CONFIG):
    quality = (
        "model",
        "language",
        "settings_version",
        "beam_size",
        "temperature",
        "vad_filter",
        "condition_on_previous_text",
    )
    old = record.get("config", {})
    return (
        record.get("status") == "done"
        and record.get("sha256") == sha
        and bool(record.get("text", "").strip())
        and all(old.get(k) == config.get(k) for k in quality)
    )


def has_pending(folder, only_file=None):
    excluded_path = folder / "voice_processing_exclusions.json"
    excluded = (
        set(json.loads(excluded_path.read_text(encoding="utf8")))
        if excluded_path.exists()
        else set()
    )
    cache_path = folder / "transcription_results.json"
    cache = json.loads(cache_path.read_text(encoding="utf8")) if cache_path.exists() else {}
    messages = json.loads((folder / "messages.json").read_text(encoding="utf8"))
    for message in messages:
        for ref in message.get("attachment_results", []):
            if only_file and ref != only_file:
                continue
            if (
                not is_voice(message, ref)
                or not isinstance(ref, str)
                or ref in excluded
                or not ref.startswith("downloads/")
                or Path(ref).suffix.lower() not in AUDIO
            ):
                continue
            path = local_audio(folder, ref)
            if path.is_file() and not reusable_transcript(cache.get(ref, {}), digest(path)):
                return True
    return False


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(path)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def local_audio(folder, ref):
    if not isinstance(ref, str) or not ref.startswith("downloads/"):
        raise ValueError("Invalid audio reference")
    path = (folder / ref).resolve()
    if not path.is_relative_to(folder.resolve()) or path.suffix.lower() not in AUDIO:
        raise ValueError("Invalid audio location")
    return path


def prepare(folder, only_file=None):
    folder = folder.resolve()
    messages = json.loads((folder / "messages.json").read_text(encoding="utf8"))
    cache_path = folder / "transcription_results.json"
    cache = json.loads(cache_path.read_text(encoding="utf8")) if cache_path.exists() else {}
    selected, seen = [], set()
    exclusion_path = folder / "voice_processing_exclusions.json"
    excluded = (
        set(json.loads(exclusion_path.read_text(encoding="utf8")))
        if exclusion_path.exists()
        else set()
    )
    for message in messages:
        for ref in message.get("attachment_results", []):
            if only_file and ref != only_file:
                continue
            if ref in excluded or not is_voice(message, ref):
                continue
            if (
                not isinstance(ref, str)
                or not ref.startswith("downloads/")
                or Path(ref).suffix.lower() not in AUDIO
                or ref in seen
            ):
                continue
            seen.add(ref)
            path = local_audio(folder, ref)
            if not path.is_file():
                continue
            sha = digest(path)
            old = cache.get(ref, {})
            if reusable_transcript(old, sha):
                continue
            selected.append(
                {
                    "token": f"v{len(selected) + 1:06d}",
                    "ref": ref,
                    "sha256": sha,
                    "file": f"audio/v{len(selected) + 1:06d}{path.suffix.lower()}",
                }
            )
    if not selected:
        print("No pending GPU transcription files. Existing results will be used.", flush=True)
        return None
    job_id = hashlib.sha256(
        json.dumps({"items": selected, "config": CONFIG}, sort_keys=True).encode()
    ).hexdigest()[:24]
    manifest = {"schema": 1, "job_id": job_id, "config": CONFIG, "items": selected}
    directory = folder / "colab_jobs"
    directory.mkdir(exist_ok=True)
    save(directory / (job_id + ".json"), manifest)
    target = folder / "colab_voices.zip"
    temporary = target.with_suffix(".tmp.zip")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as archive:
        # Cloud needs only audio, neutral tokens and hashes; original chat stays here.
        cloud_manifest = dict(
            manifest, items=[{k: item[k] for k in ("token", "file", "sha256")} for item in selected]
        )
        archive.writestr("job.json", json.dumps(cloud_manifest, indent=2))
        for item in selected:
            archive.write(local_audio(folder, item["ref"]), item["file"])
    temporary.replace(target)
    print(
        f"Colab package: {target}\nPending voices: {len(selected)} | Package: {target.stat().st_size / 1048576:.1f} MB",
        flush=True,
    )
    return target


def split_packages(folder, package, limit=16 * 1024 * 1024):
    """Partition whole audio entries. Never cut audio bytes or duplicate a token."""
    with zipfile.ZipFile(package) as source:
        original = json.loads(source.read("job.json"))
        manifest = json.loads(
            (folder / "colab_jobs" / (original["job_id"] + ".json")).read_text(encoding="utf8")
        )
        groups = []
        current = []
        size = 0
        for item in manifest["items"]:
            amount = source.getinfo(item["file"]).file_size
            if current and size + amount > limit:
                groups.append(current)
                current = []
                size = 0
            current.append(item)
            size += amount
        if current:
            groups.append(current)
        if len(groups) <= 1:
            return [package]
        directory = folder / "colab_parts"
        directory.mkdir(exist_ok=True)
        packages = []
        for index, items in enumerate(groups, 1):
            job = dict(manifest, items=items)
            job["job_id"] = hashlib.sha256(
                json.dumps({"items": items, "config": CONFIG}, sort_keys=True).encode()
            ).hexdigest()[:24]
            save(folder / "colab_jobs" / (job["job_id"] + ".json"), job)
            path = directory / (job["job_id"] + ".zip")
            temporary = path.with_suffix(".tmp")
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as output:
                cloud = dict(
                    job, items=[{k: item[k] for k in ("token", "file", "sha256")} for item in items]
                )
                output.writestr("job.json", json.dumps(cloud))
                for item in items:
                    output.writestr(item["file"], source.read(item["file"]))
            temporary.replace(path)
            packages.append(path)
            print(
                f"Upload part {index}/{len(groups)}: {len(items)} whole voices | {path.stat().st_size / 1048576:.1f} MiB",
                flush=True,
            )
        assert [item["token"] for group in groups for item in group] == [
            item["token"] for item in manifest["items"]
        ]
        return packages


def load_result(path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if names != ["results.json"] or archive.getinfo("results.json").file_size > 50000000:
                raise ValueError("Unexpected result archive structure")
            return json.loads(archive.read("results.json"))
    if path.stat().st_size > 50000000:
        raise ValueError("Result file is too large")
    return json.loads(path.read_text(encoding="utf8"))


def import_results(folder, result_path):
    folder = folder.resolve()
    returned = load_result(result_path)
    job_id = returned.get("job_id", "")
    if not re.fullmatch(r"[a-f0-9]{24}", job_id):
        raise ValueError("Invalid result job ID")
    manifest = json.loads((folder / "colab_jobs" / (job_id + ".json")).read_text(encoding="utf8"))
    if returned.get("config") != manifest["config"] or manifest["config"] != CONFIG:
        raise ValueError("Transcription settings mismatch")
    originals = {item["token"]: item for item in manifest["items"]}
    cache_path = folder / "transcription_results.json"
    cache = json.loads(cache_path.read_text(encoding="utf8")) if cache_path.exists() else {}
    updates, seen = {}, set()
    rows = returned.get("items")
    if not isinstance(rows, list) or not rows:
        raise ValueError("No transcription results found")
    for result in rows:
        token = result.get("token")
        if token not in originals or token in seen:
            raise ValueError("Unknown or duplicated voice token")
        seen.add(token)
        original = originals[token]
        audio = local_audio(folder, original["ref"])
        if result.get("sha256") != original["sha256"] or digest(audio) != original["sha256"]:
            raise ValueError("Audio hash mismatch; nothing was imported")
        status = result.get("status")
        if status not in ("done", "error"):
            raise ValueError("Unexpected transcription status")
        text = result.get("text", "")
        if not isinstance(text, str) or len(text) > 2000000:
            raise ValueError("Invalid transcription text")
        segments = result.get("segments", [])
        if not isinstance(segments, list):
            raise ValueError("Invalid segment list")
        for segment in segments:
            if (
                not isinstance(segment, dict)
                or not isinstance(segment.get("text"), str)
                or not all(
                    isinstance(segment.get(k), (int, float)) and 0 <= segment[k] < 10000000
                    for k in ("start", "end")
                )
                or segment["end"] < segment["start"]
            ):
                raise ValueError("Invalid transcript timing")
        record = {
            key: result[key]
            for key in (
                "text",
                "segments",
                "duration_seconds",
                "processing_seconds",
                "language",
                "review_needed",
                "error",
            )
            if key in result
        }
        record.update(status=status, sha256=original["sha256"], config=CONFIG, colab_job_id=job_id)
        if status == "done" and not text.strip():
            record.update(status="error", error="No speech transcript produced", review_needed=True)
        # A failed retry must not erase an already completed matching transcript.
        old = cache.get(original["ref"], {})
        if (
            record["status"] == "error"
            and old.get("status") == "done"
            and old.get("sha256") == original["sha256"]
        ):
            continue
        updates[original["ref"]] = record
    # Validate the entire package before modifying the cache.
    cache.update(updates)
    save(cache_path, cache)
    directory = folder / "transcripts"
    directory.mkdir(exist_ok=True)
    for ref, record in updates.items():
        key = hashlib.sha256((ref + record["sha256"]).encode()).hexdigest()[:20]
        (directory / (key + ".txt")).write_text(record.get("text", ""), encoding="utf8")
        save(directory / (key + ".json"), record)
    print(
        f"GPU results imported: {sum(r['status'] == 'done' for r in updates.values())} done; {sum(r['status'] == 'error' for r in updates.values())} failed. Unreturned voices remain pending.",
        flush=True,
    )
    return updates


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        import_results(args.backup, args.result) if args.result else prepare(args.backup)
    except Exception as error:
        print("Colab transfer stopped:", error)
        raise SystemExit(1)
