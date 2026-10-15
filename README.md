# Chat Documentation

A Windows terminal application for documenting selected conversations from Telegram,
WhatsApp and Bale, importing official chat exports, and transcribing standalone
Persian audio. Reports retain message order, sender evidence, dates, attachments,
raw transcripts and processing status.

**Status: experimental.** Browser interfaces change. A completed run is not proof
of complete chat coverage or a verbatim transcript. Review unknown senders, missing
attachments and uncertain speech before relying on a report.

[راهنمای فارسی](docs/README.fa.md) · [Configuration](docs/CONFIGURATION.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Privacy and security](SECURITY.md)

## What it does

- Captures a user-selected chat section using a dedicated browser profile.
- Imports WhatsApp Android `Export chat WITH MEDIA` ZIPs and Telegram Desktop
  **single-chat JSON** exports with their media folders or a containing ZIP.
- Produces an Excel workbook with message content, sender evidence, dates, linked
  attachments and explicit incomplete/review status.
- Keeps separate reports, resumes interrupted work, and stages targeted repairs.
- Transcribes voice messages and independent audio with Whisper large-v3, either
  locally or through Google Colab GPU. Original audio is retained.
- Supports an optional, experimental text-correction integration. It is disabled
  by default; capture, imports and raw ASR work without it.

## Install and run

The supported application platform is **Windows 10/11**, with **Python 3.13**.
Colab workers run on Linux. Local CPU transcription can be slow; the first model
download needs several GB of storage. GPU runtime availability in free Colab is
not guaranteed.

After cloning or downloading this repository, either run `setup.cmd`, then
`Chat Documentation.cmd`, or use PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe program.py
```

Run the application from your own writable checkout. Python dependencies and
browser binaries are downloaded at installation; model weights download when
transcription first runs. No model weights or private reports are distributed here.

## Main workflows

1. **New report**: choose a messenger, then browser capture or an official export.
   For capture, log in yourself, select the chat and starting point, then press Enter.
2. **Continue a saved report**: inspect the capture state and missing files before
   moving on to transcription. Completed compatible results are reused.
3. **Independent audio**: choose menu **12**, select an audio file, and choose local
   processing or Colab. Selecting the same file again resumes its saved job.
4. **Repair**: menu **9** stages sender/ASR/correction repairs separately before
   applying them; originals and rollback data are retained.

Supported standalone extensions: `.ogg`, `.opus`, `.mp3`, `.wav`, `.m4a`, `.aac`,
`.flac`, `.wma`, `.mp4` (must contain audio). The default recognition language is
Persian. Independent audio is copied unchanged into its job folder.

## Find outputs

Chat reports live in provider backup folders with readable names and a generated
`Reports.html` library. Original machine-readable records are `messages.json`;
the workbook is `chat_final.xlsx` or the readable final-report filename shown
by the application. Keep the entire report folder to retain attachment links.

Standalone audio jobs live in `Outputs/Audio/`. `Raw_Transcript.txt` is the ASR
output. `Readable_Transcript.txt` is written only after optional text correction
completes; `Text_Status.json` contains its processing state and uncertainty notes.
With correction disabled, raw text remains available and correction stays pending.
Checkpoints and support workbooks are retained for resuming the shared pipeline.

## Optional configuration

Copy `pipeline_config.example.json` to `pipeline_config.json` only if you need to
override defaults. This local configuration is ignored by Git. See
[configuration](docs/CONFIGURATION.md) for text correction, Colab, and model paths.

## Limitations

- Sender identities and dates are only as reliable as the available source
  metadata. Unknown identities are not evidence of a particular person.
- Forwarded authors and actual senders are distinct. Verify group reports.
- Telegram imports require JSON, not HTML. WhatsApp exports may omit older media.
- Browser capture can miss unloaded content, fail to download media, or stop when
  a web messenger changes its DOM. Use official exports when practical.
- Short, unclear Persian recordings can be transcribed incorrectly. Fluency after
  correction does not prove the wording is correct. No speaker diarization is
  provided for standalone recordings.
- Colab automation depends on login state, upload widgets, runtime limits and
  browser behavior; manual checkpoint import is available when automation fails.
- The optional account-based correction connector is experimental, Windows-only,
  and not an official SDK. Availability and account access are not guaranteed.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools\run_tests.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe tools\check_public_tree.py
```

Tests use synthetic messages and mocked service calls. They do not prove live
messenger compatibility or ASR accuracy and do not need your personal exports.
Never commit real screenshots, recordings, account profiles or report fixtures.

## License

Based on the original Bale Web Chat Backup project by Athar Naemi; the original
MIT copyright notice is preserved in [LICENSE](LICENSE).
Model weights and third-party dependencies retain
their own licenses. This project is not affiliated with the messenger providers,
Google, OpenAI, or the Whisper maintainers.
