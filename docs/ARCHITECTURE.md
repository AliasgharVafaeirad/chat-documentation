# Code map

The code remains a flat Python application to preserve module imports and the
existing launch flow. Tests, optional integration code and documentation are
separate directories.

- `program.py`: terminal menu; `app_config.py`: portable local configuration.
- `main.py`, `messenger_adapters.py`, `sender_identity.js`, `account_identity.js`:
  browser capture and sender/date/media evidence.
- `chat_import.py`: official export parsing and import validation.
- `report_state.py`, `report_flow.py`, `report_library.py`: report identity,
  stage selection, status and readable report names.
- `report_merge.py`, `report_repair.py`: consolidation and staged repairs.
- `transcribe_voices.py`, `audio_policy.py`: local ASR and voice/audio classification.
- `colab_transfer.py`, `colab_worker.py`, `colab_bridge.py`, `colab_browser.py`:
  GPU job packaging, worker, checkpoints and interactive browser orchestration.
- `process_backup.py`, `correction_runner.py`, `correction_sizing.py`: resumable
  ASR/correction pipeline and bounded correction requests.
- `standalone_audio.py`: independent audio jobs using the shared pipeline.
- `documentation_excel.py`: workbook generation and evidence links.
- `integrations/chatgpt_plan/`: optional account-based text-correction connector.

Source `messages.json` and original media are retained. Transcript and correction
caches are separate files; repairs are staged and can preserve original state.
Cache reuse depends on content/settings hashes, not just filenames. Report IDs
are distinct from messenger chat identity; users can create separate reports
from the same conversation.

Browser scans depend on messenger DOM/component metadata. They do not constitute
a supported messenger API. Synthetic tests cover logic and selected structures,
not every live group, media type or login state.
