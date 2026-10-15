"""Read official WhatsApp Android TXT/ZIP and Telegram JSON exports locally."""

import hashlib
import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from report_state import write


def safe_name(value):
    value = str(value).replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError("Unsafe export file path")
    return str(path)


class Source:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.zip = zipfile.ZipFile(self.path) if self.path.suffix.lower() == ".zip" else None
        if self.zip:
            entries = [i for i in self.zip.infolist() if not i.is_dir()]
            names = [safe_name(i.filename) for i in entries]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate archive paths")
            if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in entries):
                raise ValueError("Linked files are not supported")
            self.members = dict(zip(names, entries))
        else:
            self.members = None

    def close(self):
        if self.zip:
            self.zip.close()

    def names(self):
        return list(self.members) if self.zip else [self.path.name]

    def read(self, name):
        name = safe_name(name)
        if self.zip:
            return self.zip.read(self.members[name])
        file = (self.path.parent / name).resolve()
        if not file.is_relative_to(self.path.parent):
            raise ValueError("Attachment escapes export directory")
        return file.read_bytes()

    def exists(self, name):
        name = safe_name(name)
        return name in self.members if self.zip else (self.path.parent / name).is_file()

    def copy(self, name, target):
        name = safe_name(name)
        with target.open("wb") as output:
            if self.zip:
                with self.zip.open(self.members[name]) as stream:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
            else:
                with (self.path.parent / name).open("rb") as stream:
                    shutil.copyfileobj(stream, output, 1024 * 1024)


def base_row(index, date, sender, text, source, system=False):
    return dict(
        id=str(index),
        date=date,
        time=date[11:] if len(date) > 10 else "",
        date_raw=date,
        date_source=source,
        sender=sender or "نامشخص",
        sender_id="",
        sender_role="system" if system else "participant",
        sender_source=source,
        sender_status="displayed_name" if sender else "unknown",
        sender_source_message_id=str(index),
        forwarded_from="",
        text=text,
        media=[],
        attachment_results=[],
        _media_done=True,
        _voice_task_prefix="import_" + str(index),
        id_source="export_record_index",
    )


def whatsapp(source, date_order=None):
    texts = [n for n in source.names() if n.lower().endswith(".txt")]
    if len(texts) != 1:
        raise ValueError("Select a ZIP with exactly one WhatsApp chat TXT, or the chat TXT itself")
    raw = source.read(texts[0]).decode("utf-8-sig")
    # Direction markers do not carry message content.
    text = re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", raw)
    pattern = re.compile(
        r"^\[?(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]?\s*(?:-\s*)?(.*)$"
    )
    blocks = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            blocks.append(list(match.groups()))
        elif blocks:
            blocks[-1][2] += "\n" + line
    if not blocks:
        raise ValueError("WhatsApp message format was not recognized")
    first = [int(re.split(r"[/.-]", b[0])[0]) for b in blocks]
    second = [int(re.split(r"[/.-]", b[0])[1]) for b in blocks]
    detected = (
        "DMY" if any(n > 12 for n in first) else "MDY" if any(n > 12 for n in second) else None
    )
    if date_order and detected and date_order != detected:
        raise ValueError("Selected date order conflicts with export dates")
    order = date_order or detected
    if order not in ("DMY", "MDY"):
        raise ValueError("Ambiguous dates. Choose DMY or MDY before importing")
    rows = []
    references = {}
    directory = str(PurePosixPath(texts[0]).parent)
    for index, (day, clock, body) in enumerate(blocks, 1):
        parts = re.split(r"[/.-]", day)
        year = int(parts[2])
        year = year + 2000 if year < 100 else year
        a, b = map(int, parts[:2])
        month, day = (b, a) if order == "DMY" else (a, b)
        clock = clock.strip().upper()
        time = (
            datetime.strptime(clock, "%I:%M:%S %p" if clock.count(":") == 2 else "%I:%M %p")
            if clock.endswith(("AM", "PM"))
            else datetime.strptime(clock, "%H:%M:%S" if clock.count(":") == 2 else "%H:%M")
        )
        stamp = datetime(year, month, day, time.hour, time.minute, time.second).isoformat(sep=" ")
        author = body.split(": ", 1) if ": " in body else None
        row = base_row(
            index,
            stamp,
            author[0] if author else "WhatsApp system",
            author[1] if author else body,
            "whatsapp_export_local_time",
            not author,
        )
        row["timezone_status"] = "Export does not specify a timezone"
        attached = re.search(r"(?:^|\n)([^\n]+?)\s*\(file attached\)", row["text"])
        if attached:
            name = attached[1].strip()
            ref = safe_name(str(PurePosixPath(directory) / name))
            suffix = Path(name).suffix.lower()
            kind = (
                "voice"
                if name.upper().startswith("PTT-")
                else "audio"
                if suffix in {".opus", ".ogg", ".mp3", ".m4a", ".wav"}
                else "photo"
                if suffix in {".jpg", ".jpeg", ".png", ".webp"}
                else "video"
                if suffix in {".mp4", ".mov"}
                else "document"
            )
            row["media"] = [dict(kind=kind, name=name, count=1, url="")]
            row["text"] = row["text"].replace(attached[0], "").strip()
            references[str(index)] = ref
        elif re.search(r"<Media omitted>|<attached:|media omitted", row["text"], re.I):
            row["media"] = [dict(kind="unknown", name="Media omitted from export", count=1, url="")]
            row["_media_done"] = False
        rows.append(row)
    return (
        Path(texts[0]).stem.removeprefix("WhatsApp Chat with "),
        None,
        rows,
        references,
        raw,
        order,
    )


def telegram(source):
    names = [n for n in source.names() if n.lower().endswith(".json")]
    selected = []
    for name in names:
        try:
            data = json.loads(source.read(name).decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            selected.append((name, data))
    if len(selected) != 1:
        raise ValueError(
            "Export ONE Telegram chat in JSON format with media. HTML and whole-account exports are not supported yet"
        )
    name, data = selected[0]
    directory = PurePosixPath(name).parent
    rows = []
    references = {}
    for item in data["messages"]:
        identity = str(item["id"])
        text = item.get("text", "")
        if isinstance(text, list):
            text = "".join(x if isinstance(x, str) else str(x.get("text", "")) for x in text)
        system = item.get("type") == "service"
        row = base_row(
            identity,
            str(item.get("date", "")).replace("T", " "),
            item.get("from") or item.get("actor") or ("Telegram system" if system else ""),
            text,
            "telegram_export_local_time",
            system,
        )
        row["sender_id"] = str(item.get("from_id") or item.get("actor_id") or "")
        row["id_source"] = "telegram_original_message_id"
        row["forwarded_from"] = str(item.get("forwarded_from") or "")
        row["timezone_status"] = "Export does not specify a timezone"
        if system and not text:
            row["text"] = str(item.get("action", "System event"))
        media = item.get("file") or item.get("photo")
        if media:
            kind = (
                "voice"
                if item.get("media_type") == "voice_message"
                else "audio"
                if item.get("media_type") == "audio_file"
                else "photo"
                if item.get("photo")
                else "video"
                if item.get("media_type") in ("video_file", "video_message")
                else "document"
            )
            row["media"] = [
                dict(
                    kind=kind, name=item.get("file_name") or Path(str(media)).name, count=1, url=""
                )
            ]
            if not str(media).startswith("("):
                references[identity] = safe_name(str(directory / str(media)))
            else:
                row["_media_done"] = False
        rows.append(row)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate Telegram message IDs")
    return (
        data.get("name") or "Telegram export",
        data.get("id"),
        rows,
        references,
        json.dumps(data, ensure_ascii=False),
        None,
    )


def import_chat(path, provider, base, date_order=None, new_copy=False):
    path = Path(path).resolve()
    base = Path(base)
    with path.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    from report_library import reports

    source = Source(path)
    try:
        if provider == "whatsapp":
            title, peer, rows, refs, raw, order = whatsapp(source, date_order)
        elif provider == "telegram":
            title, peer, rows, refs, raw, order = telegram(source)
        else:
            raise ValueError("Official export import currently supports Telegram and WhatsApp")
        if not rows:
            raise ValueError("No messages found; no report was created")
        if not source.zip:
            # A JSON/TXT export can stay unchanged while its sibling media changes.
            identity = hashlib.sha256(fingerprint.encode())
            for name in sorted(set(refs.values())):
                identity.update(name.encode())
                if source.exists(name):
                    with (path.parent / name).open("rb") as stream:
                        identity.update(hashlib.file_digest(stream, "sha256").digest())
                else:
                    identity.update(b"missing")
            fingerprint = identity.hexdigest()
        if not new_copy:
            for existing in reports(base):
                metadata = existing / "report.json"
                if (
                    metadata.exists()
                    and json.loads(metadata.read_text(encoding="utf8")).get("source_sha256")
                    == fingerprint
                ):
                    return existing, False
        folder = base / (provider + "_backups") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        (folder / "downloads").mkdir(parents=True)
        copied = {}
        for row in rows:
            name = refs.get(row["id"])
            if name:
                if source.exists(name):
                    if name not in copied:
                        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name).name)[:160]
                        target = (
                            folder
                            / "downloads"
                            / (hashlib.sha256(name.encode()).hexdigest()[:12] + "_" + filename)
                        )
                        source.copy(name, target)
                        copied[name] = "downloads/" + target.name
                    row["attachment_results"] = [copied[name]]
                    row["_media_done"] = (folder / copied[name]).stat().st_size > 0
                else:
                    row["_media_done"] = False
        now = datetime.now(timezone.utc).isoformat()
        write(
            folder / "report.json",
            dict(
                report_id=uuid.uuid4().hex,
                provider=provider,
                chat_title=title,
                chat_key=f"{provider}-export:{peer}" if peer is not None else None,
                created_at=now,
                updated_at=now,
                source_kind="official_export",
                source_sha256=fingerprint,
                source_path=str(path),
                date_order=order,
                capture_state="imported",
                scope="Only messages and media contained in this export; full chat not verified",
            ),
        )
        (
            folder / ("source_export.txt" if provider == "whatsapp" else "source_export.json")
        ).write_text(raw, encoding="utf8")
        write(folder / "messages.json", rows)
        return folder, True
    finally:
        source.close()
