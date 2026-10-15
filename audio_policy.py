"""Only voice-message attachments enter ASR; ordinary audio stays as evidence."""

from pathlib import Path
import re

AUDIO = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma"}


def is_voice(message, ref):
    if not isinstance(ref, str) or Path(ref).suffix.lower() not in AUDIO | {".mp4"}:
        return False
    media = message.get("media", [])
    kinds = {
        item.get("kind") if isinstance(item, dict) else item
        for item in media
        if isinstance(item, (dict, str))
    }
    if kinds:
        # Metadata from the messenger outranks the extension or filename.
        if kinds == {"voice"}:
            return True
        if kinds == {"document"}:
            # Bale stores recorded voices as documents with generated voice names.
            names = [
                Path(str(item.get("name", ""))).name for item in media if isinstance(item, dict)
            ]
            return any(
                re.fullmatch(r"voice(?:_msg_-?\d+)?\.(?:ogg|opus|mp4)", name, re.I)
                for name in names
            ) and Path(ref).suffix.lower() in {".ogg", ".opus", ".mp4"}
        return False
    if media:
        return False
    # Compatibility only for old reports without a media classification.
    return Path(ref).suffix.lower() in {".ogg", ".opus"}


def is_audio_file(message, ref):
    return isinstance(ref, str) and Path(ref).suffix.lower() in AUDIO and not is_voice(message, ref)
