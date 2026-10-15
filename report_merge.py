"""Non-destructive consolidation of verified reports from the same chat."""

import hashlib
import json
from app_config import load_config
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
import report_state


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else {}


def consolidate(folders, destination):
    descriptions = [report_state.describe(p) for p in folders]
    keys = {d.get("chat_key") for d in descriptions}
    if len(folders) < 2 or len(keys) != 1 or None in keys:
        raise ValueError("Choose at least two reports with the same verified chat ID.")
    collections = [report_state.load_messages(p) for p in folders]
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "downloads").mkdir()
    merged = {}
    by_hash = {}
    whisper = {}
    source_tasks = []
    for folder, collection in zip(folders, collections):
        raw = read(folder / "transcription_results.json")
        corrected = read(folder / "correction_results.json")
        for row in collection.values():
            message_id = str(row["id"])
            target = merged.setdefault(
                message_id,
                dict(
                    row,
                    attachment_results=[],
                    _voice_task_prefix="m"
                    + hashlib.sha256(("id:" + message_id).encode()).hexdigest()[:16],
                ),
            )
            if row.get("text"):
                target["text"] = row["text"]
            if (
                row.get("date_source") == "telegram_original_time_tooltip"
                or target.get("date") == "نامشخص"
            ):
                for field in ("date", "date_raw", "date_source", "time"):
                    target[field] = row.get(field, target.get(field))
            for ref in row["attachment_results"]:
                file = (folder / ref).resolve()
                sha = digest(file)
                if sha not in by_hash:
                    name = sha[:20] + "_" + file.name
                    shutil.copy2(file, destination / "downloads" / name)
                    by_hash[sha] = "downloads/" + name
                new_ref = by_hash[sha]
                if new_ref not in target["attachment_results"]:
                    target["attachment_results"].append(new_ref)
                cache = raw.get(ref, {})
                if cache.get("sha256") == sha and cache.get("status") == "done":
                    whisper[new_ref] = cache
        from process_backup import tasks

        source_tasks.extend(
            (t, corrected.get(t["item"]["id"]))
            for t in tasks(list(collection.values()), folder, raw)
        )
    rows = list(merged.values())
    if descriptions[0]["provider"] == "telegram":
        rows.sort(key=lambda r: int(r["id"]))
    for row in rows:
        expected = sum(m.get("count", 1) for m in row.get("media", []))
        row["_media_done"] = not row.get("media") or len(row["attachment_results"]) >= expected
        row["_media_attempts"] = 1 if row["_media_done"] else 0
        if not row["_media_done"]:
            row["attachment_results"].append("Attachment incomplete after report consolidation")
    report_state.write(destination / "messages.json", rows)
    report_state.write(destination / "transcription_results.json", whisper)
    meta = dict(descriptions[0])
    meta.pop("folder", None)
    meta.update(
        report_id=uuid.uuid4().hex,
        created_at=datetime.now(timezone.utc).isoformat(),
        merged_from=[str(p) for p in folders],
    )
    report_state.write(destination / "report.json", meta)
    # Reuse a correction only if its audio and semantic input remain identical.
    from process_backup import tasks

    config = load_config(Path(__file__).parent)
    prompt = Path(__file__).with_name("correction_prompt.txt").read_text(encoding="utf8")
    version = hashlib.sha256((config["model"] + prompt).encode()).hexdigest()
    corrections = {}
    for task in tasks(rows, destination, whisper):
        for old, result in source_tasks:
            if not result or result.get("status") != "done" or old["sha256"] != task["sha256"]:
                continue
            old_hash = hashlib.sha256((old["input_hash"] + version).encode()).hexdigest()
            if result.get("input_hash") != old_hash:
                continue
            if {k: v for k, v in old["item"].items() if k != "id"} != {
                k: v for k, v in task["item"].items() if k != "id"
            }:
                continue
            result = dict(
                result,
                id=task["item"]["id"],
                input_hash=hashlib.sha256((task["input_hash"] + version).encode()).hexdigest(),
            )
            corrections[task["item"]["id"]] = result
            break
    report_state.write(destination / "correction_results.json", corrections)
    return destination
