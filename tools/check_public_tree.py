"""Supplementary publication guard. Never prints matching secret values."""

import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True)
if tracked.returncode or not (ROOT / ".git").is_dir():
    raise SystemExit("Initialize the dedicated release repository and stage files first.")
patterns = [
    re.compile(r"sk-" + r"(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(r"gsk_" + r"[A-Za-z0-9]{24,}"),
    re.compile(r"AIza" + r"[A-Za-z0-9_-]{25,}"),
    re.compile(r"AQ\." + r"[A-Za-z0-9_-]{30,}"),
    re.compile("C:" + r"[\\/]+Users[\\/]+work(?:[\\/]|$)", re.I),
]
forbidden_dirs = {
    ".private",
    "Outputs",
    "System",
    "Archive",
    "downloads",
    "transcription_models",
    "node_modules",
    "__pycache__",
}
forbidden_suffixes = {
    ".dpapi",
    ".pem",
    ".key",
    ".zip",
    ".rar",
    ".xlsx",
    ".ogg",
    ".opus",
    ".mp3",
    ".m4a",
    ".wav",
    ".mp4",
    ".bin",
    ".safetensors",
}
issues = []
names = [name for name in tracked.stdout.decode("utf8").split("\0") if name]
for name in names:
    path = ROOT / name
    if (
        set(path.relative_to(ROOT).parts) & forbidden_dirs
        or any(part.endswith(("_backups", "_user_data")) for part in path.relative_to(ROOT).parts)
        or path.suffix.lower() in forbidden_suffixes
        or path.name in {"pipeline_config.json", "report_labels.json", ".env"}
    ):
        issues.append(name + ": private/runtime file staged")
        continue
    if not path.is_file():
        continue
    if path.stat().st_size > 2_000_000:
        issues.append(name + ": unexpectedly large source file")
    try:
        text = path.read_text(encoding="utf8")
    except UnicodeDecodeError:
        issues.append(name + ": unexpected binary file")
        continue
    if any(pattern.search(text) for pattern in patterns):
        issues.append(name + ": credential-shaped text or personal machine path")
    if path.suffix == ".ipynb":
        import json

        book = json.loads(text)
        if any(
            cell.get("outputs") or cell.get("execution_count") is not None
            for cell in book.get("cells", [])
        ):
            issues.append(name + ": notebook contains execution output")
if issues:
    print("\n".join(issues))
    raise SystemExit(1)
print(f"Publication guard passed for {len(names)} staged/tracked source files.")
print("Also review git diff --cached; this check does not detect every possible secret.")
