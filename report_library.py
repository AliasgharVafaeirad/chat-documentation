"""Human-readable report folders, with legacy paths kept as compatibility links."""

import html
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from report_state import describe


def reports(base):
    return sorted(
        {p.parent.resolve() for p in base.glob("*_backups/*/messages.json")},
        key=lambda p: (p / "messages.json").stat().st_mtime,
        reverse=True,
    )


def label(folder):
    info = describe(folder)
    root = folder.parent.parent
    if root.name == "Outputs":
        root = root.parent
    path = root / "report_labels.json"
    labels = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    title = labels.get(info.get("chat_key")) or info.get("chat_title") or "Unknown_chat"
    if not title.isascii():
        title = (
            "Chat_"
            + str(
                info.get("chat_key") or info.get("chat_identity", {}).get("peer") or "Unverified"
            ).split(":")[-1]
        )
    if "legacy" in title.lower():
        title = "Legacy_" + str(info.get("chat_key") or "Unverified").split(":")[-1]
    return re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")[:65] or "Unknown_chat"


def folder_name(folder):
    info = describe(folder)
    created = info.get("created_at")
    if created:
        date = (
            datetime.fromisoformat(created)
            .astimezone(timezone(timedelta(hours=3, minutes=30)))
            .strftime("%Y-%m-%d_%H-%M")
        )
    else:
        match = re.search(r"(\d{8})_(\d{6})", folder.name)
        date = (
            datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").strftime("%Y-%m-%d_%H-%M")
            if match
            else "Unknown_date"
        )
    return f"{info.get('provider', folder.parent.name.removesuffix('_backups')).title()}_{label(folder)}_{date}"


def latest_excel(folder):
    choices = [
        p
        for p in folder.glob("*.xlsx")
        if p.name.startswith(("chat_final", "chat_repaired")) and ".tmp." not in p.name
    ]
    return max(choices, key=lambda p: p.stat().st_mtime) if choices else None


def publish_excel(folder, source=None):
    source = source or latest_excel(folder)
    if not source:
        return None
    target = folder / ("Final_Report_" + label(folder) + ".xlsx")
    if source == target:
        return target
    if not target.exists() or source.stat().st_mtime_ns > target.stat().st_mtime_ns:
        temporary = target.with_suffix(".publishing.xlsx")
        try:
            shutil.copy2(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def organize(base):
    """Rename only report directories; preserve every existing file and old path."""
    if os.name != "nt":
        raise RuntimeError("Report folder migration requires Windows junctions")
    for folder in reports(base):
        if not re.fullmatch(r"\d{8}_\d{6}_\d+(?:_merged)?", folder.name):
            continue
        target = folder.with_name(folder_name(folder))
        if target.is_junction() and target.resolve() == folder.resolve():
            continue
        if target.exists():
            target = target.with_name(target.name + "_" + folder.name[-6:])
        parent = folder.parent.resolve()
        if folder.resolve().parent != parent or target.resolve().parent != parent:
            raise ValueError("Report path outside its messenger directory")
        try:
            folder.rename(target)
        except PermissionError:
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(target), str(folder)], capture_output=True
            )
            if result.returncode:
                raise OSError("Cannot create readable report path: " + str(target))
            subprocess.run(["attrib", "+H", str(folder), "/L"], capture_output=True)
            continue
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(folder), str(target)], capture_output=True
        )
        if result.returncode:
            target.rename(folder)
            raise RuntimeError("Could not preserve the old report path; rename rolled back")
        # Hide the compatibility link itself, leaving the readable real folder visible.
        subprocess.run(["attrib", "+H", str(folder), "/L"], capture_output=True)
    return refresh(base)


def refresh(base):
    entries = []
    for folder in reports(base):
        info = describe(folder)
        try:
            excel = publish_excel(folder)
        except PermissionError:
            excel = latest_excel(folder)
        rows = json.loads((folder / "messages.json").read_text(encoding="utf8"))
        if isinstance(rows, dict):
            rows = list(rows.values())
        unknown = sum(x.get("sender_status") != "displayed_name" for x in rows)
        title = info.get("chat_title") or label(folder)

        def link(path, text):
            return f'<a href="{html.escape(path.as_uri(), quote=True)}">{html.escape(text)}</a>'

        actions = link(excel, "باز کردن اکسل") if excel else "اکسل هنوز ساخته نشده"
        actions += " · " + link(folder, "پوشه گزارش")
        if (folder / "downloads").exists():
            actions += " · " + link(folder / "downloads", "فایل‌ها و ویس‌ها")
        entries.append(
            f"<article><h2>{html.escape(str(title))}</h2><p>{html.escape(folder_name(folder))}</p><p>{info['messages']} پیام · {info['incomplete']} پیوست ناقص · {unknown} فرستنده تأییدنشده</p><p>{actions}</p></article>"
        )
    page = base / "Reports.html"
    page.write_text(
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>گزارش‌های گفتگو</title><style>body{font:17px Tahoma;background:#f3f5f7;max-width:1000px;margin:40px auto;padding:20px}article{background:white;padding:20px;margin:16px 0;border-radius:12px}a{color:#0759ac}h2{font-size:22px}</style><h1>گزارش‌های گفتگو</h1><p>برای مشاهده نتیجه، اکسل گزارش موردنظر را باز کنید. فایل‌های اصلی در پوشه گزارش محفوظ‌اند.</p>'
        + "".join(entries)
        + "</html>",
        encoding="utf8",
    )
    return page


if __name__ == "__main__":
    print(organize(Path(__file__).resolve().parent))
