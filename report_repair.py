"""Isolated, auditable report repairs. Capture never downloads attachments."""

import asyncio
import hashlib
import json
from app_config import load_config
import shutil
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter
from report_state import stable_chat, write

BASE = Path(__file__).resolve().parent
FIELDS = (
    "sender",
    "sender_id",
    "sender_role",
    "sender_source",
    "sender_status",
    "sender_source_message_id",
    "forwarded_from",
)
STAGE_NAMES = {
    "senders": "Fix message senders",
    "asr": "Convert voice to text",
    "correction": "Improve voice text",
    "excel": "Create Excel",
}


def select_saved_repair(runs):
    if len(runs) == 1:
        print("One saved repair found. Selected automatically.", flush=True)
        return runs[0]
    for index, run in enumerate(runs, 1):
        state = read(run / "repair.json")
        print(
            f"{index}) {STAGE_NAMES.get(state['stage'], state['stage'])} | Saved: {run.name[:8]} {run.name[9:15]}"
        )
    while True:
        answer = input(
            f"Type a repair NUMBER from 1 to {len(runs)}, or press Enter to go back: "
        ).strip()
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(runs):
            return runs[int(answer) - 1]
        print(
            "This step selects a repair. Type its number, for example 1. Confirmation comes next."
        )


def confirm_apply(stage, changes=None):
    print("\nREADY TO SAVE", flush=True)
    print("Change: " + STAGE_NAMES.get(stage, stage), flush=True)
    if changes is not None:
        print(f"Messages with a verified sender: {changes}", flush=True)
    print(
        "A safety copy is saved. Your previous Excel is kept. A NEW Excel will be created.",
        flush=True,
    )
    while True:
        print(
            "1) Save these changes and create the new Excel\n0) Keep the report unchanged and go back",
            flush=True,
        )
        answer = input("Type 1 to save, or 0 to go back: ").strip().lower()
        if answer in ("1", "y", "yes"):
            return True
        if answer in ("0", "", "n", "no"):
            return False
        print("Please type 1 or 0.")


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else default


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def prepare(folder, stage):
    folder = folder.resolve()
    if not (folder / "messages.json").is_file():
        raise ValueError("Invalid report")
    run = folder / "repairs" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run.mkdir(parents=True)
    snapshot = run / "original"
    shutil.copytree(folder, snapshot, ignore=shutil.ignore_patterns("repairs", "~$*"))
    manifest = {str(p.relative_to(snapshot)): digest(p) for p in snapshot.rglob("*") if p.is_file()}
    state = {"stage": stage, "status": "prepared", "source": str(folder), "files": manifest}
    write(run / "repair.json", state)
    work = run / "work"
    shutil.copytree(snapshot, work)
    print("Safety copy:", snapshot, flush=True)
    return run, work


def sender_plan(original, observed):
    counts = Counter(str(row.get("id", "")) for row in original)

    def content_key(row):
        text = str(row.get("text", "")).strip()
        media = tuple(
            (item.get("kind"), item.get("name"), item.get("count")) for item in row.get("media", [])
        )
        # Empty attachments and unknown dates cannot safely bridge changing IDs.
        if not text and not any(item[1] for item in media):
            return None
        if not row.get("date") or not row.get("time") or row.get("date") == "نامشخص":
            return None
        return (row["date"], row["time"], text, media)

    old_keys = {}
    live_keys = {}
    for row in original:
        key = content_key(row)
        if key is not None:
            old_keys.setdefault(key, set()).add(str(row.get("id", "")))
    for row in observed:
        key = content_key(row)
        if key is not None:
            live_keys.setdefault(key, set()).add(str(row.get("id", "")))
    candidates = {}
    conflicts = set()
    for row in observed:
        key = str(row.get("id", ""))
        if not key:
            continue
        match = "stable_message_id"
        if counts.get(key) != 1:
            signature = content_key(row)
            old = old_keys.get(signature, set())
            live = live_keys.get(signature, set())
            if len(old) != 1 or len(live) != 1:
                continue
            key = next(iter(old))
            match = "unique_date_time_content"
        row = dict(row)
        row["_repair_match"] = match
        if (
            row.get("sender_status") != "displayed_name"
            or not row.get("sender")
            or row.get("sender") in ("من", "مخاطب", "نامشخص")
        ):
            continue
        signature = (row["sender"], str(row.get("sender_id", "")))
        if key in candidates and signature != (
            candidates[key]["sender"],
            str(candidates[key].get("sender_id", "")),
        ):
            conflicts.add(key)
        candidates[key] = row
    changes = []
    for old in original:
        key = str(old.get("id", ""))
        new = candidates.get(key)
        if not new or key in conflicts:
            continue
        # A label on another message with a reused/local ID is not evidence.
        if any(
            str(old.get(field, "")) != str(new.get(field, "")) for field in ("text", "date", "time")
        ):
            continue
        if old.get("sender_status") == "displayed_name" and (
            old.get("sender") != new.get("sender")
            or old.get("sender_id")
            and old.get("sender_id") != new.get("sender_id")
        ):
            conflicts.add(key)
            continue
        values = {field: new.get(field, "") for field in FIELDS}
        if not values["forwarded_from"]:
            values["forwarded_from"] = old.get("forwarded_from", "")
        if any(old.get(field, "") != values[field] for field in FIELDS):
            changes.append(
                {
                    "id": key,
                    "match": new["_repair_match"],
                    "observed_id": new.get("id"),
                    "before": {field: old.get(field, "") for field in FIELDS},
                    "after": values,
                }
            )
    return changes, sorted(conflicts)


def apply_senders(messages, changes):
    result = [dict(row) for row in messages]
    by_id = {str(row.get("id", "")): row for row in result}
    if len(by_id) != len(result):
        raise ValueError("Duplicate message IDs; repair blocked")
    for change in changes:
        row = by_id[change["id"]]
        if {field: row.get(field, "") for field in FIELDS} != change["before"]:
            raise ValueError("Sender information changed since repair scan")
        if set(change["after"]) != set(FIELDS):
            raise ValueError("Invalid sender repair fields")
        row.update(change["after"])
    return result


def rebuild(folder, config, name):
    from process_backup import tasks, attachment, save
    from documentation_excel import write as excel

    messages = read(folder / "messages.json", [])
    whisper = read(folder / "transcription_results.json", {})
    corrections = read(folder / "correction_results.json", {})
    work = tasks(messages, folder, whisper)
    version = hashlib.sha256(
        (config["model"] + (BASE / "correction_prompt.txt").read_text(encoding="utf8")).encode()
    ).hexdigest()
    for task in work:
        task["input_hash"] = hashlib.sha256((task["input_hash"] + version).encode()).hexdigest()
    return excel(messages, folder, whisper, corrections, work, attachment, save, output_name=name)


async def scan_senders(folder, run):
    import main
    from playwright.async_api import async_playwright

    provider = folder.parent.name.removesuffix("_backups")
    config = main.adapters.PROVIDERS[provider]
    selector = config["selector"]
    metadata = read(folder / "report.json", {})
    expected = metadata.get("chat_key") or stable_chat(provider, metadata.get("chat_identity", {}))
    if not expected:
        raise ValueError("Saved chat identity cannot be verified. Sender repair was not applied.")
    original = read(folder / "messages.json", [])
    ids = {str(row.get("id", "")) for row in original}
    if "" in ids or len(ids) != len(original):
        raise ValueError("Stable unique message IDs are required")
    observed = []
    scanned = set()
    diagnostics = []
    async with async_playwright() as p:
        bundled = Path(p.chromium.executable_path).is_file()
        options = dict(headless=False, accept_downloads=False)
        if not bundled:
            options["channel"] = "chrome"
        profile = BASE / (
            f"{provider}_automation_user_data" if bundled else f"{provider}_user_data"
        )
        context = await p.chromium.launch_persistent_context(str(profile), **options)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            await page.evaluate(main.TRACK_PANEL)
            print(
                "Open the SAME group. Go to the original starting point and point at the messages.",
                flush=True,
            )
            await asyncio.to_thread(
                input, "When ready, press Enter. No attachments will be downloaded: "
            )
            identity = await page.evaluate(main.adapters.CHAT_IDENTITY, provider)
            if stable_chat(provider, identity) != expected:
                raise ValueError("Wrong chat selected. Report left unchanged.")
            if not (await page.evaluate(main.PIN_PANEL, selector))["ok"]:
                raise ValueError("Message panel is ambiguous")
            page._backup_selected_url = page.url
            page._backup_provider = provider
            page._backup_chat_identity = identity
            quiet = 0
            while quiet < 15:
                await main.assert_selection(page)
                if (
                    stable_chat(
                        provider, await page.evaluate(main.adapters.CHAT_IDENTITY, provider)
                    )
                    != expected
                ):
                    raise ValueError("Selected chat changed. Report left unchanged.")
                batch = (
                    await page.evaluate(main.SCAN, selector)
                    if provider == "bale"
                    else await page.evaluate(
                        main.adapters.SCAN, {"selector": selector, "provider": provider}
                    )
                )
                new = {str(row.get("id", "")) for row in batch} - scanned
                scanned.update(str(row.get("id", "")) for row in batch)
                observed.extend(batch)
                write(run / "observed_senders.json", observed)
                changes, conflicts = sender_plan(original, observed)
                print(
                    f"Checking senders | Verified changes: {len(changes)}/{len(ids)} | Conflicts left unchanged: {len(conflicts)}",
                    flush=True,
                )
                # Save only message DOM, not app state/cookies, for unsupported labels.
                if len(diagnostics) < 12:
                    unknown = [
                        row["token"]
                        for row in batch
                        if row.get("sender_status") != "displayed_name"
                    ][:2]
                    for token in unknown:
                        markup = await page.locator('[data-backup-token="' + token + '"]').evaluate(
                            "(el)=>el.outerHTML"
                        )
                        diagnostics.append(markup)
                        probe = await page.locator('[data-backup-token="' + token + '"]').evaluate("""el => {
                          let fiber=el[Object.keys(el).find(key=>key.startsWith('__reactFiber$'))];
                          for(let depth=0;fiber && depth<25;depth++,fiber=fiber.return){
                            const m=fiber.memoizedProps?.messageContainer || fiber.memoizedProps?.messageContent;
                            if(!m?.message)continue;
                            const fields=['id','uid','userId','senderUid','senderId','fromId','rid','seq','date','senderName','authorName','firstName','lastName','displayName','fullName','isOut'];
                            const pick=value=>Object.fromEntries(fields.filter(key=>['string','number','boolean'].includes(typeof value?.[key])).map(key=>[key,value[key]]));
                            return {keys:Object.keys(m),messageKeys:Object.keys(m.message),container:pick(m),message:pick(m.message),sender:pick(m.sender),fromUser:pick(m.fromUser)};
                          }
                          return {componentFound:false};
                        }""")
                        samples = read(run / "sender_component_samples.json", [])
                        samples.append({"token": token, "component": probe})
                        write(run / "sender_component_samples.json", samples)
                    write(run / "sender_dom_samples.json", diagnostics)
                status = await page.evaluate(main.SCROLL, selector)
                quiet = quiet + 1 if not new and status["bottom"] else 0
                await page.wait_for_timeout(1200)
            return sender_plan(original, observed)
        finally:
            await context.close()


def promote(folder, run, stage, only_file=None):
    state = read(run / "repair.json")
    work = run / "work"
    # Refuse to race another running capture/processor. Nothing has been changed yet.
    for relative, sha in state["files"].items():
        if Path(relative).name.startswith("~$"):
            continue  # Excel's transient owner/lock file, not report content.
        path = folder / relative
        if not path.exists() or digest(path) != sha:
            raise ValueError(
                "Report changed during repair. Staged results retained; no repair applied."
            )
    names = {
        "senders": ["messages.json"],
        "asr": ["transcription_results.json"],
        "correction": ["correction_results.json", "correction_parts.json"],
        "excel": [],
    }[stage]
    if stage == "senders":
        old = read(folder / "messages.json")
        new = read(work / "messages.json")
        if len(old) != len(new):
            raise ValueError("Message count changed")
        for a, b in zip(old, new):
            if {k: v for k, v in a.items() if k not in FIELDS} != {
                k: v for k, v in b.items() if k not in FIELDS
            }:
                raise ValueError("Repair tried to change message content")
    # Save a recovery plan before replacing any cache. Originals remain in snapshot.
    state.update(status="applying", changed_files=names)
    write(run / "repair.json", state)
    try:
        for name in names:
            if (work / name).exists():
                value = read(work / name)
                if only_file and stage in ("asr", "correction"):
                    original = read(folder / name, {})
                    if stage == "asr":
                        allowed = lambda key, row: key == only_file
                    else:
                        from process_backup import tasks

                        work_tasks = tasks(
                            read(work / "messages.json"),
                            work,
                            read(work / "transcription_results.json", {}),
                        )
                        ids = {
                            task["item"]["id"] for task in work_tasks if task["ref"] == only_file
                        }
                        allowed = lambda key, row: (
                            key in ids
                            or any(
                                str(row.get("id", "")).startswith(item + "__part_") for item in ids
                            )
                        )
                    original.update({key: row for key, row in value.items() if allowed(key, row)})
                    value = original
                if stage in ("asr", "correction"):
                    previous = read(folder / name, {})
                    # A failed retry cannot replace a previously successful result.
                    for key, row in previous.items():
                        if (
                            row.get("status") == "done"
                            and value.get(key, {}).get("status") != "done"
                        ):
                            value[key] = row
                write(folder / name, value)
    except Exception:
        for name in names:
            original = run / "original" / name
            if original.exists():
                write(folder / name, read(original))
            elif (folder / name).exists():
                write(folder / name, {})
        state["status"] = "rolled_back"
        write(run / "repair.json", state)
        raise
    state["applied_hashes"] = {
        name: digest(folder / name) for name in names if (folder / name).exists()
    }
    state["status"] = "applied"
    write(run / "repair.json", state)


def undo(folder, run):
    state = read(run / "repair.json", {})
    if state.get("status") != "applied":
        raise ValueError("Only completed repairs can be undone")
    for name, sha in state.get("applied_hashes", {}).items():
        if not (folder / name).exists() or digest(folder / name) != sha:
            raise ValueError(
                "Report changed after this repair. Automatic undo blocked to protect newer data."
            )
    for name in state.get("changed_files", []):
        source = run / "original" / name
        if source.exists():
            write(folder / name, read(source))
        elif name.endswith("_results.json") or name == "correction_parts.json":
            # New cache contents remain in this repair's work directory.
            write(folder / name, {})
    state["status"] = "undone"
    write(run / "repair.json", state)


def choose_voice(folder):
    from audio_policy import is_voice

    rows = read(folder / "messages.json", [])
    refs = sorted(
        {
            ref
            for row in rows
            for ref in row.get("attachment_results", [])
            if isinstance(ref, str) and is_voice(row, ref)
        }
    )
    print("0) All pending voices")
    for index, ref in enumerate(refs, 1):
        print(f"{index}) {Path(ref).name.encode('ascii', errors='backslashreplace').decode()}")
    answer = input("Voice number (Enter to cancel): ").strip()
    if not answer:
        return False
    number = int(answer)
    if number == 0:
        return None
    if not 1 <= number <= len(refs):
        raise ValueError("Invalid voice number")
    return refs[number - 1]


def menu(folder, config):
    print(
        "\nWHAT DO YOU WANT TO FIX?\n1) Who sent each message\n2) Convert voice files to text\n3) Improve the voice text\n4) Create a new Excel from saved results\n5) Show what is complete and what is missing\n6) Undo a previous repair\n7) Finish a saved repair without scanning again\n0) Go back"
    )
    choice = input("Type the NUMBER next to your choice: ").strip()
    if choice == "0":
        return
    if choice == "7":
        runs = [
            path.parent
            for path in sorted((folder / "repairs").glob("*/repair.json"), reverse=True)
            if read(path, {}).get("status") == "staged"
        ]
        if not runs:
            print("No staged repairs.")
            return
        run = select_saved_repair(runs)
        if run is None:
            return
        state = read(run / "repair.json")
        changes = (
            len(read(run / "sender_changes.json", {}).get("changes", []))
            if state["stage"] == "senders"
            else None
        )
        if not confirm_apply(state["stage"], changes):
            return
        promote(folder, run, state["stage"], state.get("only_file"))
        print("New Excel:", rebuild(folder, config, "chat_repaired_" + run.name + ".xlsx"))
        return
    if choice == "6":
        runs = [
            path.parent
            for path in sorted((folder / "repairs").glob("*/repair.json"), reverse=True)
            if read(path, {}).get("status") == "applied"
        ]
        if not runs:
            print("No completed repairs to undo.")
            return
        run = select_saved_repair(runs)
        if run is None:
            return
        if (
            input(
                "Type 1 to restore the data from before this repair, or Enter to go back: "
            ).strip()
            != "1"
        ):
            return
        undo(folder, run)
        print(
            "Previous data restored. Existing Excel files retained. Use Create a new Excel for a new view."
        )
        return
    if choice == "5":
        messages = read(folder / "messages.json", [])
        print(
            "Messages:",
            len(messages),
            "| Named senders:",
            sum(row.get("sender_status") == "displayed_name" for row in messages),
        )
        for name in ("transcription_results.json", "correction_results.json"):
            cache = read(folder / name, {})
            print(name, dict(Counter(row.get("status", "unknown") for row in cache.values())))
        return
    stages = {"1": "senders", "2": "asr", "3": "correction", "4": "excel"}
    if choice not in stages:
        raise ValueError("Invalid repair stage")
    stage = stages[choice]
    only_file = None
    if stage in ("asr", "correction"):
        only_file = choose_voice(folder)
        if only_file is False:
            return
    run, work = prepare(folder, stage)
    if stage == "senders":
        changes, conflicts = asyncio.run(scan_senders(folder, run))
        write(run / "sender_changes.json", {"changes": changes, "conflicts": conflicts})
        write(work / "messages.json", apply_senders(read(folder / "messages.json"), changes))
        print(
            f"Verified sender changes: {len(changes)} | Conflicts left unchanged: {len(conflicts)}"
        )
        if not changes:
            print("No verified sender repairs. Original report unchanged. Diagnostics:", run)
            return
    elif stage in ("asr", "correction"):
        from process_backup import process, tasks

        if (
            only_file
            and input("Reprocess the selected voice even if already completed? [y/N]: ")
            .strip()
            .lower()
            == "y"
        ):
            if stage == "asr":
                cache = read(work / "transcription_results.json", {})
                cache.pop(only_file, None)
                write(work / "transcription_results.json", cache)
            else:
                selected = {
                    task["item"]["id"]
                    for task in tasks(
                        read(work / "messages.json"),
                        work,
                        read(work / "transcription_results.json", {}),
                    )
                    if task["ref"] == only_file
                }
                cache = read(work / "correction_results.json", {})
                write(
                    work / "correction_results.json",
                    {key: row for key, row in cache.items() if key not in selected},
                )
                cache = read(work / "correction_parts.json", {})
                write(
                    work / "correction_parts.json",
                    {
                        key: row
                        for key, row in cache.items()
                        if not any(
                            str(row.get("id", "")).startswith(item + "__part_") for item in selected
                        )
                    },
                )
        # Completed results stay intact; only missing/failed items are retried.
        process(
            work,
            dict(config),
            skip_asr=stage == "correction",
            asr_only=stage == "asr",
            only_file=only_file,
        )
        for name in ("transcription_results.json", "correction_results.json"):
            cache = read(work / name, {})
            print(
                "Staged " + name + ":",
                dict(Counter(row.get("status", "unknown") for row in cache.values())),
            )
    state = read(run / "repair.json")
    state.update(status="staged", only_file=only_file)
    write(run / "repair.json", state)
    print("Repair results staged:", run)
    if not confirm_apply(stage, len(changes) if stage == "senders" else None):
        print("Original report unchanged. Staged results retained.")
        return
    promote(folder, run, stage, only_file)
    target = rebuild(folder, config, "chat_repaired_" + run.name + ".xlsx")
    print("Repair applied. Previous Excel and full safety copy retained. New Excel:", target)


if __name__ == "__main__":
    try:
        menu(Path(sys.argv[1]).resolve(), load_config(BASE))
    except KeyboardInterrupt:
        print("Repair paused. Original report and staged checkpoints retained.")
    except Exception as error:
        print("Repair stopped:", error)
        raise SystemExit(1)
