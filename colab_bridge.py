"""Embedded in automated notebooks; no browser/kernel download callback queue."""


def publish_checkpoint(checkpoint):
    import base64
    import io
    import json
    import zipfile
    from IPython.display import HTML, display

    result = json.loads(checkpoint.read_text(encoding="utf8"))
    name = f"whisper_colab_results_{result['job_id']}_{len(result['items']):06d}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(checkpoint, "results.json")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    display(
        HTML(
            f'<pre data-backup-result="{name}" '
            f'data-backup-payload="{payload}">Checkpoint ready: {len(result["items"])} items. '
            "The local program saves this automatically.</pre>"
        )
    )


def completed_checkpoint(checkpoint, expected):
    import json

    try:
        value = json.loads(checkpoint.read_text(encoding="utf8"))
        actual = {item["token"]: item for item in value["items"]}
        return (
            value["job_id"] == expected["job_id"]
            and value["config"] == expected["config"]
            and set(actual) == {item["token"] for item in expected["items"]}
            and all(
                actual[item["token"]].get("status") == "done"
                and actual[item["token"]].get("sha256") == item["sha256"]
                for item in expected["items"]
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run_parts(expected_jobs):
    # Imports are local so rerunning this cell never depends on the upload cell.
    import json
    import zipfile
    from pathlib import Path
    from google.colab import files

    for part_index, expected in enumerate(expected_jobs, 1):
        checkpoint = Path("/content/whisper_backup") / expected["job_id"] / "results.json"
        if completed_checkpoint(checkpoint, expected):
            print(f"Reusing completed part {part_index}/{len(expected_jobs)}", flush=True)
            publish_checkpoint(checkpoint)
            continue
        input_package = Path("/content/whisper_uploads") / (expected["job_id"] + ".zip")
        input_package.parent.mkdir(exist_ok=True)
        if not input_package.exists():
            print(f"Waiting for upload part {part_index}/{len(expected_jobs)}", flush=True)
            uploaded = files.upload()
            if len(uploaded) != 1:
                raise ValueError("Upload exactly one part")
            payload = next(iter(uploaded.values()))
            # Validate before retaining an upload for retries.
            import io

            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                if json.loads(archive.read("job.json")) != expected:
                    raise ValueError("Unexpected upload part; report protected")
            temporary = input_package.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(input_package)
            del uploaded, payload
        print(f"Upload received part {part_index}/{len(expected_jobs)}", flush=True)
        checkpoint = run_job(input_package)  # noqa: F821 - supplied by embedded GPU worker
        publish_checkpoint(checkpoint)
    print("All upload parts processed", flush=True)
