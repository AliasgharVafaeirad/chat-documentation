# Contributing

Use Python 3.13 and a virtual environment. Install `requirements-dev.txt` and
Playwright Chromium. Run `python tools/run_tests.py`, `python -m ruff check .`,
`python -m ruff format --check .`, and `python tools/check_public_tree.py`.

Add synthetic, minimal fixtures for sender/date/media fixes. Never copy real
exports, DOM snapshots, screenshots, voices or account sessions into tests.
Describe which messenger and flow you exercised and whether validation was
synthetic or live. Do not claim complete coverage based only on a successful scan.

Preserve originals and resumability. Missing sender/date/media evidence must stay
explicit rather than be guessed. Do not silently treat music as a voice message,
replace incomplete transcripts with invented content, or discard completed items
because another item in the same batch failed.

Please keep pull requests focused and explain behavior changes and validation.
