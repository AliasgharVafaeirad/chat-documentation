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

Choose menu **5: Set up ChatGPT text correction**. The program checks an existing
connection first. If sign-in is needed, it opens the sign-in flow and continues
automatically afterward. Select a model number (or press Enter for the displayed
default). The program saves settings and enables correction automatically.
No configuration-file editing or separate model-list command is required.
The connection check verifies access to the model catalog; it does not send a
test transcript or guarantee that a later generation request will succeed.

Choose menu 5 again to change the model, recheck the connection or turn correction
off. Cancelling or failing setup preserves existing settings. Do not commit
credentials or account-specific model catalog files. If unavailable, raw Whisper
transcripts remain usable.

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
