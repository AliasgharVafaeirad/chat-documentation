"""Report selection, verified chat binding and resumable attachment state."""

import json
import re
import uuid
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    temp.replace(path)


def load_messages(folder):
    data = json.loads((folder / "messages.json").read_text(encoding="utf8"))
    rows = list(data.values()) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("Invalid saved message collection")
    if any(not str(r.get("id", "")).strip() for r in rows):
        raise ValueError(
            "This report contains messages without stable IDs; automatic completion is unavailable."
        )
    if len({str(r["id"]) for r in rows}) != len(rows):
        raise ValueError("Duplicate message IDs in report")
    result = {}
    for index, row in enumerate(rows):
        row = dict(row)
        row.setdefault("_voice_task_prefix", f"m{index}")
        valid = []
        for ref in row.get("attachment_results", []):
            if not isinstance(ref, str) or not ref.startswith("downloads/"):
                continue
            path = (folder / ref).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError("Unsafe attachment path")
            if path.is_file() and path.stat().st_size > 0:
                valid.append(ref)
        previously_good = row.get("_media_done", False)
        old_files = [
            v
            for v in row.get("attachment_results", [])
            if isinstance(v, str) and v.startswith("downloads/")
        ]
        row["attachment_results"] = valid
        row["_media_done"] = not row.get("media") or (
            previously_good and len(valid) == len(old_files) and bool(valid)
        )
        row["_media_attempts"] = 1 if row["_media_done"] else 0
        row["_media_deferred"] = False
        result["id:" + str(row["id"])] = row
    return result


def stable_chat(provider, identity):
    peer = str(identity.get("peer") or "").strip()
    if provider == "telegram" and re.fullmatch(r"-?\d+", peer):
        return provider + ":" + peer
    if provider == "whatsapp" and re.fullmatch(r"[\w+.-]+@(?:c.us|g.us|s.whatsapp.net|lid)", peer):
        return provider + ":" + peer
    if provider == "bale":
        url = identity.get("url", "")
        parsed = urlparse(url)
        uid = parse_qs(parsed.query).get("uid", [])
        if (
            parsed.hostname == "web.bale.ai"
            and parsed.path.rstrip("/") == "/chat"
            and len(uid) == 1
            and re.fullmatch(r"\d+", uid[0])
        ):
            return provider + ":" + uid[0]
        match = re.search(r"/chat/([\w-]+)(?:[/?#]|$)|#(?:/chat/)?([\w-]{5,})$", url)
        if match:
            return provider + ":" + (match[1] or match[2])
    return None


def legacy_identity(folder):
    provider = folder.parent.name.removesuffix("_backups")
    log = folder / "run.log"
    text = log.read_text(encoding="utf8", errors="replace") if log.exists() else ""
    urls = re.findall(r"Selected page: (https?://[^;\s]+)", text)
    peers = {m[1] for url in urls if (m := re.search(r"web.telegram.org/a/#(-?\d+)$", url))}
    if provider == "telegram" and len(peers) == 1:
        peer = next(iter(peers))
        return {
            "provider": provider,
            "chat_key": provider + ":" + peer,
            "chat_title": "Legacy report (name unavailable)",
            "chat_identity": {"peer": peer, "url": urls[-1]},
        }
    return {"provider": provider, "chat_key": None, "chat_title": "Unverified legacy report"}


def describe(folder):
    path = folder / "report.json"
    meta = json.loads(path.read_text(encoding="utf8")) if path.exists() else legacy_identity(folder)
    data = json.loads((folder / "messages.json").read_text(encoding="utf8"))
    rows = list(data.values()) if isinstance(data, dict) else data
    dates = [str(r.get("date", "")) for r in rows if r.get("date") not in ("", None, "نامشخص")]
    if meta.get("created_at"):
        meta["created_display"] = (
            datetime.fromisoformat(meta["created_at"])
            .astimezone(timezone(timedelta(hours=3, minutes=30)))
            .strftime("%Y-%m-%d %H:%M Tehran")
        )
    else:
        meta["created_display"] = folder.name
    return dict(
        meta,
        folder=str(folder),
        messages=len(rows),
        incomplete=sum(bool(r.get("media")) and not r.get("_media_done", False) for r in rows),
        first_date=dates[0] if dates else "unknown",
        last_date=dates[-1] if dates else "unknown",
    )


def bind(folder, provider, identity, resume):
    current = stable_chat(provider, identity)
    path = folder / "report.json"
    prior = (
        json.loads(path.read_text(encoding="utf8")) if path.exists() else legacy_identity(folder)
    )
    if resume:
        if not prior.get("chat_key") or not current:
            raise ValueError(
                "Chat ID cannot be verified. Start a separate new report; this report was not merged."
            )
        if prior.get("provider") != provider or prior["chat_key"] != current:
            raise ValueError(
                "Selected chat does not match the chosen report. No messages were merged."
            )
    now = datetime.now(timezone.utc).isoformat()
    meta = dict(
        prior,
        report_id=prior.get("report_id") or uuid.uuid4().hex,
        created_at=prior.get("created_at") or now,
        updated_at=now,
        provider=provider,
        chat_key=current,
        chat_title=identity.get("title") or prior.get("chat_title") or "Unknown chat",
        chat_identity=identity,
    )
    write(path, meta)
    return meta


def ordered_keys(old, batch):
    result = list(old)
    for index, key in enumerate(batch):
        if key in result:
            continue
        right = next((k for k in batch[index + 1 :] if k in result), None)
        left = next((k for k in reversed(batch[:index]) if k in result), None)
        if right:
            result.insert(result.index(right), key)
        elif left:
            result.insert(result.index(left) + 1, key)
        else:
            result.append(key)
    return result
