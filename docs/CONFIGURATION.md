# Configuration

Defaults are defined in `app_config.py`. No configuration file is required to
capture/import reports or run raw transcription. All executable paths default to
the Python interpreter that launched the program. Start it from your virtualenv.

Copy `pipeline_config.example.json` to ignored `pipeline_config.json` to customize:

- `asr_backend`: `local` or `colab`.
- `choose_asr`: prompt before a pending ASR stage.
- `colab_browser`: automate the Colab handoff or use manual upload/result import.
- `model_root`: local model cache path, relative to this checkout or absolute.
- `correction_enabled`: false by default.
- `model`: exact model identifier available to your optional correction connector.
- `voice_python` / `chatgpt_python`: optional executable overrides.
- `chatgpt_script`: optional replacement connector script.
- `manage_installed_folders`: keep false in a source checkout. The legacy folder
  migration hides implementation files and moves documentation; it is retained
  only for old installed layouts, not normal Git development.

## Experimental text correction

The preserved `integrations/chatgpt_plan/` connector uses account login and writes
credentials under its ignored `.private/` directory using Windows DPAPI. It has
not been made portable to non-Windows systems. It is not an official SDK and its
authentication flow may be unavailable to a particular account or change.
There is no API-key fallback and no promise of free or unlimited account access.

To try it on a supported account, choose menu 5, then inspect available models:

```powershell
.\.venv\Scripts\python.exe integrations\chatgpt_plan\chatgpt_plan.py models
```

Set `model` to an exact identifier returned by your account and
`correction_enabled` to true in local configuration. Do not commit credentials
or account-specific model catalog files. If unavailable, leave correction off;
raw Whisper transcripts remain usable.

Correction transmits raw text and candidate neighboring chat text to the selected
service. Independent audio has no neighboring chat context. Audio files are not
sent by the correction connector. Cloud ASR in Colab does upload the selected
audio. Use local ASR if recordings must stay on your computer.

The original ASR text, corrected text and uncertainty metadata are retained. An
uncertainty note may be in `Text_Status.json` or workbook status rather than inline
in the readable text. Review it before sharing the transcript as documentation.

## Colab

`Whisper_Colab_Backup.ipynb` is generated from `colab_worker.py` by
`python build_colab_notebook.py`. GPU jobs bind settings and audio SHA256 hashes;
changing settings creates a different job and invalidates incompatible cache.
GPU mode requires CUDA 12 cuBLAS and cuDNN 9. The notebook must install/expose them
before launching inference. Colab's package environment can change independently.

If a runtime disconnects, retained local checkpoints are reused when compatible.
Runtime deletion loses files stored only in Colab. Do not close a runtime before
importing or downloading completed results. Browser automation is best-effort.
