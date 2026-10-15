"""Shared review and continuation for browser captures and official exports."""

import json
from audio_policy import is_voice, is_audio_file


def read(folder, name, default):
    p = folder / name
    return json.loads(p.read_text(encoding="utf8")) if p.exists() else default


def status(folder):
    meta = read(folder, "report.json", {})
    rows = read(folder, "messages.json", [])
    missing = sum(bool(r.get("media")) and not r.get("_media_done", False) for r in rows)
    imported = meta.get("source_kind") == "official_export"
    reason = (
        (folder / "stop_reason.txt").read_text(encoding="utf8")
        if (folder / "stop_reason.txt").exists()
        else ""
    )
    state = meta.get("capture_state") or (
        "section_complete"
        if reason.startswith("No new messages appeared at the end")
        else "interrupted"
    )
    voices = [
        ref for row in rows for ref in row.get("attachment_results", []) if is_voice(row, ref)
    ]
    return dict(
        imported=imported,
        capture_pending=not imported and (state != "section_complete" or missing > 0),
        missing=missing,
        voices=len(voices),
        audio=sum(
            is_audio_file(row, ref) for row in rows for ref in row.get("attachment_results", [])
        ),
        messages=len(rows),
        unknown=sum(r.get("sender_status") == "unknown" for r in rows),
        dates=sum(
            not r.get("date")
            or r.get("date") == "نامشخص"
            or r.get("date_source") == "telegram_day_header_partial"
            for r in rows
        ),
        first=rows[0].get("date", "unknown") if rows else "unknown",
        last=rows[-1].get("date", "unknown") if rows else "unknown",
    )


def review(folder):
    s = status(folder)
    print("\nREPORT CHECK", flush=True)
    print("Source: " + ("Official chat export" if s["imported"] else "Browser capture"))
    print(f"Messages: {s['messages']} | Voice files: {s['voices']} | Other audio: {s['audio']}")
    print(
        f"Missing attachments: {s['missing']} | Unknown senders: {s['unknown']} | Unverified dates: {s['dates']}"
    )
    first = s["first"] if s["first"].isascii() else "See report"
    last = s["last"] if s["last"].isascii() else "See report"
    print(f"Date range: {first} -> {last}")
    print("Full-chat coverage is not verified. Only the selected section/export is documented.")
    if s["imported"]:
        print("Export timestamps retain the source local time; timezone is not specified.")
    return s


def continue_report(folder, config, call, python):
    s = review(folder)
    if s["capture_pending"]:
        print("\nChat capture is unfinished. Audio processing has not started.")
        print(
            "1) Complete capture in the browser\n2) Process saved data and keep missing-data warnings\n0) Save and go back"
        )
        answer = input("Number: ").strip()
        if answer == "1":
            metadata = read(folder, "report.json", {})
            if not metadata.get("chat_key"):
                print(
                    "This old report has no verified chat ID. Use Repair or start a separate capture; it cannot be merged safely."
                )
                return
            call(python, "main.py", "--resume-report", folder, "--skip-voice-processing")
            s = review(folder)
            if s["capture_pending"]:
                print(
                    "Capture is still incomplete. Saved work retained. Continue this report later."
                )
                return
        elif answer != "2":
            return
    elif s["imported"] and s["missing"]:
        print(
            "Some media was not included in the export. Browser retries cannot recover it from this ZIP."
        )
        print(
            "1) Continue with explicit missing-media warnings\n0) Go back and obtain a more complete export"
        )
        if input("Number: ").strip() != "1":
            return
    print(
        "\n1) Process pending voices and create final Excel\n2) Create Excel now without processing voices\n0) Save and go back"
    )
    answer = input("Number: ").strip()
    if answer == "1":
        call(config["voice_python"], "process_backup.py", "--backup", folder)
    elif answer == "2":
        call(config["voice_python"], "process_backup.py", "--backup", folder, "--export-only")
    else:
        return
    from report_library import publish_excel

    result = publish_excel(folder.resolve())
    print("\nSAVED. Missing data and uncertain text remain marked in the report.")
    print("1) Open Excel\n2) Open report folder\n3) Repair this report\n0) Back to menu")
    answer = input("Number: ").strip()
    import os

    if answer == "1" and result:
        os.startfile(str(result))
    elif answer == "2":
        os.startfile(str(folder))
    elif answer == "3":
        call(config["voice_python"], "report_repair.py", folder)


def new_report(base, config, call, python):
    print("Select messenger: 1) Bale  2) Telegram  3) WhatsApp")
    provider = {"1": "bale", "2": "telegram", "3": "whatsapp"}.get(input("Number: ").strip())
    if not provider:
        return
    print("GET CHAT DATA\n1) Collect from browser\n2) Import an official chat export\n0) Back")
    answer = input("Number: ").strip()
    from report_library import reports

    if answer == "1":
        before = set(reports(base))
        call(python, "main.py", "--messenger", provider, "--skip-voice-processing")
        created = [
            p for p in reports(base) if p not in before and p.parent.name == provider + "_backups"
        ]
        if not created:
            print("No report data was captured. Check the capture diagnostics.")
            return
        folder = created[0]
    elif answer == "2":
        if provider == "bale":
            print("Official Bale export import is not supported. Choose browser capture for Bale.")
            return
        print("WhatsApp: Export chat WITH MEDIA, then select the ZIP.")
        print(
            "Telegram Desktop: Export chat history > JSON (machine-readable), with media. Select result.json beside its media folders, or a ZIP containing them."
        )
        path = input("Paste/drag the export path, or press Enter to browse: ").strip().strip('"')
        if not path:
            from tkinter import Tk, filedialog

            root = Tk()
            root.withdraw()
            try:
                path = filedialog.askopenfilename(
                    title="Select chat export", filetypes=[("Chat exports", "*.zip *.json *.txt")]
                )
            finally:
                root.destroy()
        if not path:
            return
        from chat_import import import_chat

        try:
            folder, created = import_chat(path, provider, base)
        except ValueError as error:
            if "Ambiguous dates" not in str(error):
                raise
            print("Date order is ambiguous. 1) Day/Month/Year  2) Month/Day/Year  0) Cancel")
            order = {"1": "DMY", "2": "MDY"}.get(input("Number: ").strip())
            if not order:
                return
            folder, created = import_chat(path, provider, base, date_order=order)
        if not created:
            print("This exact export was already imported.")
            print("1) Continue the existing report\n2) Create a separate new report\n0) Back")
            selected = input("Number: ").strip()
            if selected == "2":
                folder, _ = import_chat(path, provider, base, new_copy=True)
            elif selected != "1":
                return
        from report_library import organize

        organize(base)
        folder = folder.resolve()
    else:
        return
    continue_report(folder, config, call, python)
