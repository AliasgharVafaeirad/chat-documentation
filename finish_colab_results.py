"""Find the saved local job and finish GPU transcripts with the connected plan."""

from app_config import load_config
from pathlib import Path
from colab_transfer import load_result
from process_backup import process

BASE = Path(__file__).resolve().parent


def run():
    answer = (
        input("Paste or drag the downloaded whisper_colab_results.zip here: ").strip().strip('"')
    )
    result_path = Path(answer)
    result = load_result(result_path)
    job_id = result.get("job_id", "")
    import re

    if not re.fullmatch(r"[a-f0-9]{24}", job_id):
        raise ValueError("Invalid result job ID")
    candidates = sorted(
        {
            path.parent.parent.resolve()
            for path in BASE.glob(f"*_backups/*/colab_jobs/{job_id}.json")
        }
    )
    if not candidates:
        raise ValueError("No matching prepared backup found in this project")
    if len(candidates) == 1:
        folder = candidates[0]
    else:
        print("This audio job matches more than one saved backup:")
        for index, candidate in enumerate(candidates, 1):
            print(f"{index}) {candidate}")
        number = int(input("Select backup number: "))
        if not 1 <= number <= len(candidates):
            raise ValueError("Invalid backup number")
        folder = candidates[number - 1]
    print("Matched backup:", folder, flush=True)
    process(folder, load_config(BASE), colab_result=result_path)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("Paused. Completed results are saved. Run this launcher again to resume.")
    except Exception as error:
        print("Processing stopped:", error)
        raise SystemExit(1)
