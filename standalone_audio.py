"""Independent, resumable audio-to-readable-text entry point."""

import argparse
from datetime import datetime
import json
from app_config import load_config
import os
from pathlib import Path
import re
import shutil

from process_backup import AUDIO, digest, process, save

BASE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else {}


def prepare(base, source):
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in AUDIO:
        raise ValueError("Choose a supported audio file: " + ", ".join(sorted(AUDIO)))
    signature = digest(source)
    root = base / "Outputs" / "Audio"
    root.mkdir(parents=True, exist_ok=True)
    for marker in sorted(root.glob("*/audio_job.json")):
        if load(marker).get("sha256") == signature:
            folder = marker.parent
            job = load(marker)
            original = folder / job["audio_ref"]
            if not original.exists():
                original.parent.mkdir(exist_ok=True)
                shutil.copy2(source, original)
            if digest(original) != signature:
                raise ValueError("Saved audio integrity check failed. Original input was retained.")
            return folder
    title = re.sub(r"[^A-Za-z0-9_-]+", "_", source.stem).strip("_")[:60] or "Audio"
    folder = root / (title + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f"))
    (folder / "downloads").mkdir(parents=True)
    ref = "downloads/Original_Audio" + source.suffix.lower()
    shutil.copy2(source, folder / ref)
    if digest(folder / ref) != signature:
        raise ValueError("Audio copy integrity check failed")
    save(
        folder / "messages.json",
        [
            {
                "id": "audio_1",
                "text": "",
                "media": [{"kind": "voice"}],
                "attachment_results": [ref],
                "_media_done": True,
            }
        ],
    )
    save(
        folder / "audio_job.json",
        {
            "schema": 1,
            "source_name": source.name,
            "sha256": signature,
            "audio_ref": ref,
            "created": datetime.now().isoformat(),
        },
    )
    return folder


def export_text(folder):
    job = load(folder / "audio_job.json")
    raw = load(folder / "transcription_results.json").get(job["audio_ref"], {})
    corrected = load(folder / "correction_results.json").get("m0_a0", {})
    result = {"status": "transcription_pending", "source_name": job["source_name"]}
    if raw.get("status") == "done" and raw.get("sha256") == job["sha256"]:
        (folder / "Raw_Transcript.txt").write_text(raw.get("text", ""), encoding="utf-8-sig")
        result["status"] = "correction_pending"
        if (
            corrected.get("status") == "done"
            and corrected.get("audio_sha256") == job["sha256"]
            and corrected.get("corrected_text", "").strip()
        ):
            (folder / "Readable_Transcript.txt").write_text(
                corrected["corrected_text"].strip() + "\n", encoding="utf-8-sig"
            )
            result.update(
                status="done",
                uncertain_phrases=corrected.get("uncertain_phrases", []),
                sensitive_changes=corrected.get("sensitive_changes", []),
            )
    if result["status"] != "done" and (folder / "Readable_Transcript.txt").exists():
        (folder / "Readable_Transcript.txt").replace(folder / "Previous_Readable_Transcript.txt")
    save(folder / "Text_Status.json", result)
    (folder / "START_HERE.txt").write_text(
        "AUDIO TO TEXT\nStatus: " + result["status"] + "\n\n"
        "Readable_Transcript.txt: corrected readable text (only when complete).\n"
        "Raw_Transcript.txt: original speech recognition output.\n"
        "Text_Status.json: completion status and uncertain phrases to review.\n"
        "downloads/Original_Audio: unchanged source audio.\n"
        "To continue: choose Audio to readable text and select the same original file.\n"
        "Other files are saved processing checkpoints. Keep them to resume.\n",
        encoding="utf8",
    )
    return result


def pick_file():
    answer = input("Paste/drag an audio file path, or press Enter to browse: ").strip().strip('"')
    if answer:
        return Path(answer)
    import tkinter as tk
    from tkinter import filedialog

    window = tk.Tk()
    window.withdraw()
    try:
        answer = filedialog.askopenfilename(
            title="Select audio to transcribe",
            filetypes=[
                ("Audio files", " ".join("*" + suffix for suffix in sorted(AUDIO))),
                ("All files", "*.*"),
            ],
        )
    finally:
        window.destroy()
    return Path(answer) if answer else None


def run(source=None):
    print("\nAUDIO TO READABLE TEXT\nNo messenger or chat report required.", flush=True)
    source = source or pick_file()
    if source is None:
        return
    folder = prepare(BASE, source)
    print("Audio job: " + str(folder), flush=True)
    print("Completed stages will be reused. Choose the same input file to resume.", flush=True)
    config = load_config(BASE)
    config["correction_prompt"] = str(BASE / "standalone_audio_prompt.txt")
    try:
        process(folder, config)
    finally:
        state = export_text(folder)
        print("Audio text status: " + state["status"], flush=True)
        print("Output folder: " + str(folder), flush=True)
    if state["status"] == "done":
        print("Readable text: " + str(folder / "Readable_Transcript.txt"), flush=True)
        if state.get("uncertain_phrases"):
            print("Some phrases require review. See Text_Status.json.", flush=True)
    else:
        print("Work saved. Select the same audio again to continue pending stages.", flush=True)
    if os.name == "nt" and input("Open output folder? [y/N]: ").strip().lower() == "y":
        os.startfile(str(folder))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path)
    args = parser.parse_args()
    try:
        run(args.audio)
    except KeyboardInterrupt:
        print("Paused. Select the same audio file to resume.")
