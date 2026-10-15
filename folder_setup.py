"""Organize the installed Windows app without breaking existing paths."""

import hashlib
import json
import os
import subprocess
from pathlib import Path


def hidden(path):
    subprocess.run(["attrib", "+H", str(path), "/L"], capture_output=True, check=True)


def junction(link, target):
    if link.exists():
        if link.resolve() != target.resolve():
            raise ValueError("Conflicting folder: " + str(link))
        return
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
    )
    if result.returncode:
        raise OSError("Cannot preserve folder path: " + str(link))


def organize(base):
    base = base.resolve()
    if os.name != "nt":
        return
    outputs = base / "Outputs"
    archive = base / "Archive"
    system = base / "System"
    for p in [
        outputs,
        archive / "Code_Backups",
        archive / "Old_Versions",
        archive / "Old_Launchers",
        archive / "Loose_Exports",
        base / "Guide",
        system,
    ]:
        p.mkdir(parents=True, exist_ok=True)
    moved = []

    def relocate(source, destination, compatibility=False):
        # Every computed source and target must stay inside this application.
        if not source.absolute().is_relative_to(base) or not destination.absolute().is_relative_to(
            base
        ):
            raise ValueError("Path outside application")
        if source.is_junction():
            return
        if not source.exists():
            return
        if destination.exists():
            if (
                compatibility
                and destination.is_junction()
                and destination.resolve() == source.resolve()
            ):
                return
            raise ValueError("Destination already exists; original retained: " + str(destination))
        digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
        try:
            source.rename(destination)
        except PermissionError:
            if compatibility and source.is_dir():
                junction(destination, source)
            else:
                hidden(source)
            return
        if compatibility:
            try:
                junction(source, destination)
            except Exception:
                destination.rename(source)
                raise
        if digest and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Moved file verification failed")
        moved.append(
            {"from": str(source.relative_to(base)), "to": str(destination.relative_to(base))}
        )

    for provider in ["telegram", "bale", "whatsapp"]:
        original = base / (provider + "_backups")
        actual = outputs / original.name
        if original.exists():
            relocate(original, actual, True)
            junction(
                outputs
                / ({"telegram": "Telegram", "bale": "Bale", "whatsapp": "WhatsApp"}[provider]),
                actual,
            )
            hidden(original)
            hidden(actual)
    for source in list(base.iterdir()):
        name = source.name
        if name.startswith("before-") or name == "source_backups":
            relocate(source, archive / "Old_Versions" / name)
        elif source.is_file() and (".before" in name or name == "report_naming_migration.json"):
            relocate(source, archive / "Code_Backups" / name)
        elif (
            source.is_file()
            and name.endswith(".cmd")
            and name not in ["Chat Documentation.cmd", "run_program.cmd"]
        ):
            relocate(source, archive / "Old_Launchers" / name)
        elif source.is_file() and name.endswith(".xlsx") and not name.startswith("~$"):
            relocate(source, archive / "Loose_Exports" / name)
        elif source.is_file() and name in [
            "README.md",
            "README_pipeline.md",
            "REPORT_WORKFLOW.txt",
            "LICENSE",
        ]:
            relocate(source, base / "Guide" / name)
        elif source.is_dir() and (
            name in [".venv-transcription", "transcription_models", "__pycache__"]
            or name.endswith("_user_data")
        ):
            relocate(source, system / name, True)
            hidden(source)
    # Keep active scripts at their established paths; hide implementation files.
    visible = {"Chat Documentation.cmd", "Reports.html", "START_HERE.txt"}
    for source in base.iterdir():
        if source.is_file() and source.name not in visible:
            hidden(source)
    (base / "START_HERE.txt").write_text(
        "CHAT DOCUMENTATION\n\n"
        "Start the app: Chat Documentation.cmd\n"
        "Find and open reports: Reports.html\n"
        "Browse saved files: Outputs > Telegram / Bale / WhatsApp\n\n"
        "Archive: old code copies and previous launchers; retained for recovery.\n"
        "System: Python, models and browser sessions; managed by the app.\n"
        "Some hidden compatibility paths are retained for existing reports.\n",
        encoding="ascii",
    )
    audit = system / "folder_organization.json"
    prior = json.loads(audit.read_text(encoding="utf8")) if audit.exists() else []
    audit.write_text(json.dumps(prior + moved, indent=2), encoding="utf8")
    return moved


if __name__ == "__main__":
    moved = organize(Path(__file__).resolve().parent)
    print("Organized entries:", len(moved or []))
