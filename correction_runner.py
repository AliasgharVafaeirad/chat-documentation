"""Bounded correction retries with a visible heartbeat and per-attempt logs."""

import subprocess
import time
from datetime import datetime


def run_correction(command, folder, attempts=3, heartbeat=20, timeout=900):
    logs = folder / "correction_logs"
    logs.mkdir(exist_ok=True)
    for attempt in range(1, attempts + 1):
        path = logs / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".log")
        print(f"ChatGPT request {attempt}/{attempts}. Waiting for response...", flush=True)
        with path.open("w", encoding="utf8") as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
            started = time.monotonic()
            try:
                while True:
                    try:
                        code = process.wait(timeout=heartbeat)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = int(time.monotonic() - started)
                        print(
                            f"ChatGPT waiting: {elapsed}s | attempt {attempt}/{attempts} | saved batches retained",
                            flush=True,
                        )
                        if elapsed >= timeout:
                            process.terminate()
                            process.wait(timeout=20)
                            code = 124
                            break
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        if code == 0:
            return 0
        if any(
            reason in path.read_text(encoding="utf8", errors="replace")
            for reason in ("Pilot input is too large", "content_filter")
        ):
            print(
                "Non-retryable request failure. Saved results retained; identical request will not be retried.",
                flush=True,
            )
            return code
        print(f"ChatGPT request failed (code {code}). Details: {path}", flush=True)
        if attempt < attempts:
            print("Retrying this batch in 5 seconds...", flush=True)
            time.sleep(5)
    return code


def failure_reason(folder):
    logs = sorted((folder / "correction_logs").glob("*.log"))
    text = logs[-1].read_text(encoding="utf8", errors="replace") if logs else ""
    for reason in (
        "content_filter",
        "Pilot input is too large",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectError",
        "stream_ended_without_completion",
    ):
        if reason in text:
            return reason
    return "ChatGPT correction did not complete; see correction_logs"
