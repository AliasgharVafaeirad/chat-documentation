"""Interactive Colab handoff: user login, automatic notebook/upload/results."""

import asyncio
import json
import re
import time
import uuid
import zipfile
import os
import subprocess
import base64
import hashlib
from pathlib import Path
from playwright.async_api import async_playwright
from colab_transfer import load_result, import_results, save, split_packages

BASE = Path(__file__).resolve().parent


class ColabQuotaError(RuntimeError):
    pass


def chrome_executable():
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Google Chrome is required for Colab. Install Chrome and try again.")


async def prepare_google_login(profile):
    # Authentication happens in ordinary Chrome, before browser automation starts.
    # Use an app-only profile; never copy cookies from the user's personal profile.
    profile.mkdir(exist_ok=True)
    subprocess.Popen(
        [
            str(chrome_executable()),
            "--user-data-dir=" + str(profile),
            "https://colab.research.google.com/notebooks/welcome.ipynb?hl=en",
        ]
    )
    print("Google login: normal Chrome opened with a separate application profile.", flush=True)
    print(
        "Sign in to Google there. Then CLOSE all windows of this application profile.", flush=True
    )
    print(
        "Your other Chrome windows can stay open. The program will reopen Colab after login.",
        flush=True,
    )
    await asyncio.to_thread(input, "After signing in and closing that window, press Enter: ")


def job_for(package):
    with zipfile.ZipFile(package) as z:
        return json.loads(z.read("job.json"))


def build_notebook(package, target, packages=None):
    job = job_for(package)
    notebook = json.loads((BASE / "Whisper_Colab_Backup.ipynb").read_text(encoding="utf8"))
    worker = (BASE / "colab_worker.py").read_text(encoding="utf8")
    bridge = (BASE / "colab_bridge.py").read_text(encoding="utf8")
    worker = worker.replace(
        "        save_checkpoint(checkpoint, result)",
        "        save_checkpoint(checkpoint, result)\n        if len(completed) % 5 == 0:\n            publish_checkpoint(checkpoint)",
    )
    worker += "\n" + bridge
    expected = [job_for(part) for part in (packages or [package])]
    for cell in notebook["cells"]:
        source = "".join(cell.get("source", []))
        if cell.get("cell_type") != "code":
            continue
        if "def run_job(" in source:
            cell["source"] = worker.splitlines(keepends=True)
        elif "uploaded = files.upload()" in source:
            cell["source"] = ["# Uploads are handled by the resumable processing cell.\n"]
        elif "checkpoint = run_job(input_package)" in source:
            cell["source"] = [f"run_parts({expected!r})\n"]
        elif "output_package = package_results(checkpoint)" in source:
            cell["source"] = ['print("All checkpoints offered to the local program.")\n']
        cell["outputs"] = []
        cell["execution_count"] = None
    name = "Whisper_Automated_" + job["job_id"] + ".ipynb"
    notebook["metadata"]["colab"]["name"] = name
    notebook["metadata"]["accelerator"] = "GPU"
    notebook["cells"].insert(
        0,
        {"cell_type": "markdown", "id": "automation-job", "metadata": {}, "source": ["# " + name]},
    )
    target.write_text(json.dumps(notebook, ensure_ascii=False), encoding="utf8")
    return job, name


def receive_result(folder, path, job):
    returned = load_result(path)
    if returned.get("job_id") != job["job_id"]:
        raise ValueError("Result belongs to another report job")
    import_results(folder, path)
    expected = {x["token"] for x in job["items"]}
    actual = {x["token"] for x in returned.get("items", [])}
    return actual == expected


async def click_text(page, names):
    for name in names:
        if name == "Run all":
            command = page.locator('[role="menuitem"][command="runall"]')
            visible = [
                command.nth(i)
                for i in range(await command.count())
                if await command.nth(i).is_visible()
            ]
            if len(visible) == 1:
                await visible[0].click(timeout=10000)
                return True
        entries = page.get_by_role("menu").get_by_text(name, exact=True)
        visible = [
            entries.nth(i)
            for i in range(await entries.count())
            if await entries.nth(i).is_visible()
        ]
        if len(visible) == 1:
            await visible[0].click(timeout=10000)
            return True
        for role in ("button", "menuitem", "tab"):
            locator = page.get_by_role(role, name=name, exact=True)
            if await locator.count() == 1 and await locator.is_visible():
                await locator.click(timeout=10000)
                return True
        locator = page.get_by_text(name, exact=True)
        if await locator.count() == 1 and await locator.is_visible():
            await locator.click(timeout=10000)
            return True
    return False


async def set_upload(page, path):
    for frame in page.frames:
        if not re.match(
            r"https://(?:colab\.research\.google\.com|[a-zA-Z0-9.-]+-colab\.googleusercontent\.com)(?:/|$)",
            frame.url,
        ):
            continue
        inputs = frame.locator("input[type=file]")
        for index in range(await inputs.count()):
            node = inputs.nth(index)
            if await node.get_attribute("data-backup-uploaded"):
                continue
            if await node.is_visible() and await node.is_enabled():
                await node.set_input_files(str(path), timeout=600000)
                await node.evaluate('(node) => node.setAttribute("data-backup-uploaded","1")')
                return True
    return False


def requested_part(text, packages):
    matches = re.findall(r"Waiting for upload part (\d+)/(\d+)", text)
    if not matches:
        return None
    number, total = map(int, matches[-1])
    if total != len(packages) or not 1 <= number <= total:
        return None
    return packages[number - 1]


async def collect_links(page, offered, receive):
    for frame in page.frames:
        if not re.match(r"https://[a-zA-Z0-9.-]+-colab\.googleusercontent\.com(?:/|$)", frame.url):
            continue
        links = frame.locator("[data-backup-result]")
        for index in range(await links.count()):
            link = links.nth(index)
            name = await link.get_attribute("data-backup-result")
            if not re.fullmatch(r"whisper_colab_results_[a-f0-9]{24}_\d{6}\.zip", name or ""):
                continue
            # Read the checkpoint already rendered by our notebook. Avoid
            # Chrome's download subsystem entirely (including data-URL clicks).
            encoded = await link.get_attribute("data-backup-payload")
            # Also recover checkpoints from the previous notebook version.
            href = (
                ("data:application/zip;base64," + encoded)
                if encoded
                else await link.get_attribute("href")
            )
            prefix = "data:application/zip;base64,"
            if not href or not href.startswith(prefix) or len(href) > 12000000:
                raise ValueError("Invalid checkpoint payload")
            fingerprint = (name, hashlib.sha256(href.encode("ascii")).hexdigest())
            if fingerprint in offered:
                continue
            payload = base64.b64decode(href[len(prefix) :], validate=True)
            for attempt in range(3):
                try:
                    await receive(name, payload)
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    print("Checkpoint save interrupted. Retrying local save...", flush=True)
                    await asyncio.sleep(1)
            offered.add(fingerprint)


def resumable_url(previous, jobs):
    url = previous.get("url", "")
    if (
        previous.get("job_id") in jobs
        and previous.get("stage") != "complete"
        and re.fullmatch(
            r"https://colab\.research\.google\.com/drive/[a-zA-Z0-9_-]+(?:\?hl=en)?", url
        )
    ):
        return url
    return None


async def resume_execution(page):
    if not await click_text(page, ["Runtime"]):
        raise RuntimeError("Runtime menu unavailable during recovery")
    interrupted = await click_text(page, ["Interrupt execution"])
    if not interrupted:
        # A failed cell may already be stopped; dismiss the open menu.
        await page.keyboard.press("Escape")
    await asyncio.sleep(3)
    if not await click_text(page, ["Runtime"]):
        raise RuntimeError("Runtime menu unavailable during recovery")
    if not await click_text(page, ["Run all"]):
        raise RuntimeError("Resume command unavailable during recovery")


async def run_once(folder, package):
    folder = Path(folder).resolve()
    package = Path(package).resolve()
    work = folder / "colab_browser"
    work.mkdir(exist_ok=True)
    state_path = work / "state.json"
    notebook_path = work / "Whisper_Automated.ipynb"
    packages = split_packages(folder, package)
    jobs = {job_for(part)["job_id"]: job_for(part) for part in packages}
    package = packages[0]
    job, name = build_notebook(package, notebook_path, packages)
    done_jobs = set()
    completed = asyncio.Event()
    received = []
    offered = set()
    try:
        previous = json.loads(state_path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        previous = {}
    resume_url = resumable_url(previous, jobs)
    state = {"job_id": job["job_id"], "stage": "login", "package": str(package)}
    if resume_url:
        state["url"] = resume_url
    save(state_path, state)
    profile = BASE / "colab_google_user_data"
    async with async_playwright() as p:
        options = dict(headless=False, channel="chrome", accept_downloads=False)
        while True:
            try:
                context = await p.chromium.launch_persistent_context(str(profile), **options)
                break
            except Exception as error:
                if "existing browser session" not in str(error):
                    raise
                await asyncio.to_thread(
                    input, "Close the previous Colab program window, then press Enter: "
                )
        page = context.pages[0] if context.pages else await context.new_page()

        async def receive_payload(name, payload):
            target = work / ("result_" + uuid.uuid4().hex + ".zip")
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
            returned = load_result(target)
            result_job = returned.get("job_id")
            if result_job not in jobs:
                raise ValueError("Unexpected result job")
            if receive_result(folder, target, jobs[result_job]):
                done_jobs.add(result_job)
            if len(done_jobs) == len(jobs):
                completed.set()
            received.append(target)
            state.update(stage="results_received", last_result=str(target))
            save(state_path, state)
            print(
                f"GPU checkpoint saved locally: {len(returned.get('items', []))} items | complete parts: {len(done_jobs)}/{len(jobs)}",
                flush=True,
            )

        failed = False
        quota = False
        try:
            await page.goto(
                resume_url or "https://colab.research.google.com/notebooks/welcome.ipynb?hl=en",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            print(
                "Reusing your Google session. The program will upload the voice ZIP to Colab.",
                flush=True,
            )
            await page.get_by_text("File", exact=True).wait_for(state="visible", timeout=30000)
            if await page.get_by_role("link", name="Sign in", exact=True).is_visible():
                await context.close()
                await prepare_google_login(profile)
                context = await p.chromium.launch_persistent_context(str(profile), **options)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    resume_url or "https://colab.research.google.com/notebooks/welcome.ipynb?hl=en",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await page.get_by_text("File", exact=True).wait_for(state="visible", timeout=30000)
                if await page.get_by_role("link", name="Sign in", exact=True).is_visible():
                    raise RuntimeError("Google login is incomplete. Close this window and retry.")
            print("Google session verified. No additional login required.", flush=True)
            if resume_url:
                print(
                    "Reopening saved Colab notebook. Reusing runtime checkpoints and uploaded parts.",
                    flush=True,
                )
                await collect_links(page, offered, receive_payload)
                if not completed.is_set():
                    await resume_execution(page)
            else:
                state["stage"] = "notebook_upload"
                save(state_path, state)
                if not await click_text(page, ["File"]):
                    raise RuntimeError(
                        "Colab File menu unavailable. Open Colab after login and try again."
                    )
                if not await click_text(page, ["Upload notebook"]):
                    raise RuntimeError("Upload notebook menu unavailable")
                await click_text(page, ["Upload"])
                if not await set_upload(page, notebook_path):
                    async with page.expect_file_chooser(timeout=15000) as chooser:
                        if not await click_text(page, ["Browse", "Choose file"]):
                            raise RuntimeError("Notebook file chooser unavailable")
                    await (await chooser.value).set_files(str(notebook_path))
                await page.get_by_role("heading", name=name, exact=True).wait_for(
                    state="visible", timeout=120000
                )
                state.update(stage="runtime", url=page.url)
                save(state_path, state)
                if await click_text(page, ["Runtime"]):
                    if await click_text(page, ["Change runtime type"]):
                        for label in ["T4 GPU", "GPU"]:
                            radio = page.get_by_role("radio", name=label, exact=True)
                            if await radio.count() == 1:
                                await radio.check()
                                break
                        await click_text(page, ["Save"])
                if not await click_text(page, ["Runtime"]):
                    raise RuntimeError("Runtime menu unavailable")
                if not await click_text(page, ["Run all"]):
                    raise RuntimeError("Run all command unavailable")
            state["stage"] = "running"
            save(state_path, state)
            uploaded = False
            started = time.monotonic()
            notice = 0
            seen_cell_errors = set()
            last_progress = ""
            upload_notice = False
            upload_activity = time.monotonic()
            upload_signature = ""
            recoveries = 0
            while not completed.is_set():
                if page.is_closed():
                    raise BrowserDisconnected("Colab browser disconnected; checkpoints retained.")
                await click_text(page, ["Run anyway"])
                await collect_links(page, offered, receive_payload)
                dialogs = page.get_by_role("dialog")
                for dialog in await dialogs.all():
                    if not await dialog.is_visible():
                        continue
                    detail = await dialog.inner_text()
                    if re.search(r"usage limits|GPU.*quota|quota.*GPU", detail, re.I):
                        raise ColabQuotaError(
                            "GPU usage limit reached. Choose local transcription or pause until GPU access returns."
                        )
                    if re.search(
                        r"too many sessions|active sessions|session limit|cannot connect|unable to connect|cannot.*GPU",
                        detail,
                        re.I,
                    ):
                        print("Colab needs attention:", detail[:1200], flush=True)
                        print(
                            "In Colab, open Runtime > Manage sessions and close only sessions you no longer need.",
                            flush=True,
                        )
                        await asyncio.to_thread(
                            input,
                            "Resolve the Colab session/GPU issue, then press Enter to retry: ",
                        )
                        await click_text(page, ["Cancel", "Close", "OK"])
                        if await click_text(page, ["Runtime"]):
                            await click_text(page, ["Run all"])
                for frame in page.frames:
                    if "-colab.googleusercontent.com/" not in frame.url:
                        continue
                    try:
                        output_text = await frame.locator("body").inner_text(timeout=1500)
                    except Exception:
                        continue
                    recoverable = (
                        (
                            "Traceback (most recent call last)" in output_text
                            and re.search(
                                r"NameError: name .files.|(?:Error|Exception):[^\n]*(?:network|connection|upload|timed? ?out)",
                                output_text,
                                re.I,
                            )
                        )
                        or "Upload widget is only available when the cell has been executed"
                        in output_text
                    )
                    if recoverable and output_text[-1800:] not in seen_cell_errors:
                        seen_cell_errors.add(output_text[-1800:])
                        if recoveries >= 2:
                            raise RuntimeError(
                                "Colab transfer failed after two recovery attempts. Saved results retained."
                            )
                        print(
                            "Colab upload/session error. Retrying execution with saved checkpoints...",
                            flush=True,
                        )
                        await resume_execution(page)
                        recoveries += 1
                        uploaded = False
                        upload_activity = time.monotonic()
                        upload_signature = ""
                        break
                    requested = requested_part(output_text, packages)
                    if requested is not None and requested != package:
                        package = requested
                        uploaded = False
                        upload_activity = time.monotonic()
                        upload_signature = ""
                        print("Colab requested next part. Sending:", package.name, flush=True)
                    # Watch the actual active upload widget, not old transcript
                    # output. A stalled upload is safely interrupted and resumed.
                    inputs = frame.locator("input[type=file]")
                    upload_tail = output_text.rsplit("Waiting for upload part ", 1)[-1]
                    active = (
                        "Waiting for upload part " in output_text
                        and "Upload received part " not in upload_tail
                        and any(
                            [await inputs.nth(i).is_visible() for i in range(await inputs.count())]
                        )
                    )
                    if active and uploaded:
                        signature = output_text[-800:]
                        if signature != upload_signature:
                            upload_signature = signature
                            upload_activity = time.monotonic()
                        if time.monotonic() - upload_activity > 300:
                            if recoveries >= 2:
                                raise RuntimeError(
                                    "Upload stalled after two automatic recovery attempts. Received results retained; retry this report later."
                                )
                            print(
                                "Upload stalled. Restarting execution; saved voices and uploaded parts will be reused.",
                                flush=True,
                            )
                            await resume_execution(page)
                            recoveries += 1
                            uploaded = False
                            upload_activity = time.monotonic()
                            upload_signature = ""
                            break
                    progress = re.findall(
                        r"(?:Transcribing \d+/\d+[^\n]*|Loading Whisper[^\n]*|GPU processing elapsed[^\n]*)",
                        output_text,
                    )
                    if progress and progress[-1] != last_progress:
                        last_progress = progress[-1]
                        print("Colab:", last_progress, flush=True)
                    if "Traceback (most recent call last)" in output_text:
                        detail = output_text[-1800:]
                        if detail not in seen_cell_errors:
                            seen_cell_errors.add(detail)
                            print("Colab cell error:", detail, flush=True)
                            print(
                                "Fix the cell in Colab. The program keeps receiving completed results; no terminal Enter or Run all is needed.",
                                flush=True,
                            )
                            state["stage"] = "cell_needs_attention"
                            save(state_path, state)
                if not uploaded and await set_upload(page, package):
                    uploaded = True
                    state["stage"] = "voices_uploaded"
                    save(state_path, state)
                    state["package"] = str(package)
                    save(state_path, state)
                    upload_activity = time.monotonic()
                    upload_signature = ""
                    print(
                        "Voice ZIP selected automatically. Colab is receiving/processing it; waiting for GPU results...",
                        flush=True,
                    )
                if not uploaded and not upload_notice and time.monotonic() - started > 600:
                    upload_notice = True
                    print(
                        "Colab has not requested the voice ZIP after 10 minutes. Check for a runtime/session prompt or a failed cell in the browser.",
                        flush=True,
                    )
                if time.monotonic() - notice > 30:
                    print(
                        "Colab running | ZIP uploaded:",
                        uploaded,
                        "| checkpoints:",
                        len(received),
                        flush=True,
                    )
                    notice = time.monotonic()
                if time.monotonic() - started > 14400:
                    raise RuntimeError(
                        "Colab stage reached the 4-hour waiting limit. Received checkpoints retained."
                    )
                await asyncio.sleep(2)
            state["stage"] = "complete"
            save(state_path, state)
            print("Colab results imported. Continuing local correction and Excel...", flush=True)
            return received[-1]
        except BaseException as error:
            failed = True
            quota = isinstance(error, ColabQuotaError)
            state.update(stage="paused", error=str(error), error_type=type(error).__name__)
            save(state_path, state)
            if quota:
                state["stage"] = "waiting_for_gpu"
                save(state_path, state)
            print("Colab stopped:", type(error).__name__, str(error), flush=True)
            if not page.is_closed():
                try:
                    await page.screenshot(path=str(work / "paused.png"))
                    (work / "paused.html").write_text(await page.content(), encoding="utf8")
                except Exception:
                    pass
            raise
        finally:
            if (
                failed
                and not quota
                and not page.is_closed()
                and not asyncio.current_task().cancelling()
                and not browser_disconnected(state.get("error", ""))
            ):
                print(
                    "Colab paused. Saved checkpoints retained. Inspect the browser, then close its window to return to the program.",
                    flush=True,
                )
                closed = asyncio.Event()
                context.on("close", lambda _: closed.set())
                await closed.wait()
            try:
                await context.close()
            except Exception:
                pass


class BrowserDisconnected(RuntimeError):
    pass


def browser_disconnected(error):
    return isinstance(error, BrowserDisconnected) or any(
        marker in str(error).lower()
        for marker in (
            "target page, context or browser has been closed",
            "browser disconnected",
            "browser has been closed",
            "browser closed",
        )
    )


async def run(folder, package):
    for attempt in range(3):
        try:
            return await run_once(folder, package)
        except Exception as error:
            if not browser_disconnected(error) or attempt == 2:
                raise
            print(
                f"Browser connection lost. Reopening saved notebook automatically ({attempt + 1}/2); local checkpoints retained.",
                flush=True,
            )
            await asyncio.sleep(5)


def automate(folder, package):
    return asyncio.run(run(folder, package))
