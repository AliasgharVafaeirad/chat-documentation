"""One terminal entry point for capture, GPU handoff and resumable export."""

import json
from app_config import load_config
from report_state import describe
import subprocess
import sys
import os
from report_library import reports, organize, refresh, publish_excel, folder_name
from pathlib import Path

BASE = Path(__file__).resolve().parent


def call(python, script, *args):
    code = subprocess.call([str(python), "-u", str(BASE / script), *map(str, args)])
    if code:
        print("Stage stopped. Saved files are retained. Exit code:", code)
    try:
        organize(BASE)
    except (OSError, RuntimeError) as error:
        print("Report library update deferred:", error)


def choose_backup(multiple=False):
    all_folders = [folder / "messages.json" for folder in reports(BASE)]
    if not all_folders:
        print("No saved backups. Start a new capture first.")
        return None
    folders = [p for p in all_folders if (p.parent / "report.json").exists()] or all_folders
    labels_path = BASE / "report_labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf8")) if labels_path.exists() else {}
    while True:
        print("\nSAVED REPORTS", flush=True)
        for i, p in enumerate(folders, 1):
            folder = p.parent
            info = describe(folder)
            key = info.get("chat_key") or "UNVERIFIED"
            label = labels.get(key) or str(info.get("chat_title") or "")
            if not label or not label.isascii() or "legacy" in label.lower():
                label = key
            created = str(info.get("created_display", folder.name))
            if not created.isascii():
                created = folder.name
            print(f"{i}) {label} | {info['messages']} messages | {info.get('provider', 'unknown')}")
            print(f"   Created: {created} | Missing attachments: {info['incomplete']}")
            print(f"   Folder: {folder_name(folder)}")
            from report_flow import status

            state = status(folder)
            print(
                "   Source: "
                + ("Official export" if state["imported"] else "Browser")
                + " | Next: "
                + (
                    "Complete capture"
                    if state["capture_pending"]
                    else "Review / process saved data"
                )
            )
        if len(folders) != len(all_folders):
            print("A) Show all reports, including old attempts")
        answer = input(
            "Report numbers separated by commas (Enter to cancel): "
            if multiple
            else "Report number (Enter to cancel): "
        ).strip()
        if answer.lower() == "a":
            folders = all_folders
            continue
        break
    if not answer:
        return None
    if multiple:
        numbers = [int(n.strip()) for n in answer.split(",")]
        if len(set(numbers)) != len(numbers) or any(n < 1 or n > len(folders) for n in numbers):
            raise ValueError("Invalid report numbers")
        return [folders[n - 1].parent for n in numbers]
    number = int(answer)
    if not 1 <= number <= len(folders):
        raise ValueError("Invalid backup number")
    selected = folders[number - 1].parent
    print("Selected report:", folder_name(selected), flush=True)
    return selected


def run():
    from folder_setup import organize as organize_folders

    config = load_config(BASE)
    if config.get("manage_installed_folders", False):
        try:
            organize_folders(BASE)
        except (OSError, ValueError) as error:
            print("Folder organization deferred:", error)
    try:
        organize(BASE)
    except (OSError, RuntimeError) as error:
        print("Report library update deferred:", error)
    while True:
        config = load_config(BASE)
        print(
            "\nCHAT DOCUMENTATION\n1) Start a separate new report\n2) Continue processing a saved backup\n3) Import downloaded Colab results\n4) Rebuild Excel from saved results\n5) Set up ChatGPT text correction\n6) Complete capture of a selected report\n7) Consolidate reports from the same chat\n8) Run automated Colab for a saved report\n9) Repair a selected report\n10) Open reports library\n11) Open a report folder or Excel\n12) Audio to readable text (no chat required)\n0) Exit"
        )
        try:
            choice = input("Number: ").strip()
            if choice == "0":
                return
            if choice == "1":
                from report_flow import new_report

                new_report(BASE, config, call, sys.executable)
            elif choice == "2":
                folder = choose_backup()
                if folder:
                    from report_flow import continue_report

                    continue_report(folder, config, call, sys.executable)
            elif choice == "3":
                call(config["voice_python"], "finish_colab_results.py")
            elif choice == "4":
                folder = choose_backup()
                if folder:
                    call(
                        config["voice_python"],
                        "process_backup.py",
                        "--backup",
                        folder,
                        "--export-only",
                    )
            elif choice == "7":
                from report_merge import consolidate
                from datetime import datetime

                selected = choose_backup(multiple=True)
                if selected:
                    destination = selected[0].parent / (
                        datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_merged"
                    )
                    consolidate(selected, destination)
                    print("Combined report:", destination)
                    call(
                        config["voice_python"],
                        "process_backup.py",
                        "--backup",
                        destination,
                        "--export-only",
                    )
            elif choice == "6":
                folder = choose_backup()
                if folder:
                    metadata = (
                        json.loads((folder / "report.json").read_text(encoding="utf8"))
                        if (folder / "report.json").exists()
                        else {}
                    )
                    if metadata.get("source_kind") == "official_export":
                        print(
                            "This report came from a file. Import a more complete export as a separate report, or use option 2 to process existing data."
                        )
                    else:
                        call(sys.executable, "main.py", "--resume-report", folder)
            elif choice == "8":
                folder = choose_backup()
                if folder:
                    call(
                        config["voice_python"],
                        "process_backup.py",
                        "--backup",
                        folder,
                        "--auto-colab",
                    )
            elif choice == "5":
                from correction_setup import setup

                setup(BASE)
                config = load_config(BASE)
            elif choice == "9":
                folder = choose_backup()
                if folder:
                    call(config["voice_python"], "report_repair.py", folder)
            elif choice == "10":
                os.startfile(str(refresh(BASE)))
            elif choice == "11":
                folder = choose_backup()
                if folder:
                    print("1) Open final Excel\n2) Open report folder\n3) Open downloaded files")
                    action = input("Number: ").strip()
                    target = (
                        publish_excel(folder)
                        if action == "1"
                        else folder
                        if action == "2"
                        else folder / "downloads"
                        if action == "3"
                        else None
                    )
                    if target and target.exists():
                        os.startfile(str(target))
                    else:
                        print("This output is not available yet.")
            elif choice == "12":
                call(config["voice_python"], "standalone_audio.py")
            else:
                print("Choose 0 through 12.")
        except KeyboardInterrupt:
            print("Paused. Saved work is retained.")
        except Exception as error:
            print("Stage stopped:", error)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf8", errors="replace")
    run()
