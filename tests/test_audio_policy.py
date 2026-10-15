import unittest
from audio_policy import is_voice, is_audio_file
from transcribe_voices import collect
from pathlib import Path


class AudioPolicyTests(unittest.TestCase):
    def test_metadata_outranks_filename(self):
        self.assertFalse(is_voice({"media": [{"kind": "audio"}]}, "downloads/Voice.ogg"))
        self.assertFalse(is_voice({"media": [{"kind": "document"}]}, "downloads/voice.mp3"))
        self.assertTrue(is_voice({"media": [{"kind": "voice"}]}, "downloads/recording.mp3"))
        self.assertTrue(is_audio_file({"media": [{"kind": "audio"}]}, "downloads/song.mp3"))

    def test_legacy_compatibility_and_ambiguous_media(self):
        self.assertTrue(is_voice({}, "downloads/old.ogg"))
        self.assertFalse(is_voice({}, "downloads/song.mp3"))
        self.assertFalse(
            is_voice({"media": [{"kind": "voice"}, {"kind": "audio"}]}, "downloads/one.ogg")
        )

    def test_bale_voice_documents_without_enabling_music(self):
        for name in ("voice.ogg", "voice_msg_-8692353053457950796.opus", "voice.mp4"):
            self.assertTrue(
                is_voice(
                    {"media": [{"kind": "document", "name": name}]}, "downloads/hash_001_" + name
                )
            )
        for name in ("song.ogg", "voice.mp3", "movie.mp4", "Radiohead.mp3"):
            self.assertFalse(
                is_voice({"media": [{"kind": "document", "name": name}]}, "downloads/" + name)
            )
        self.assertFalse(
            is_voice({"media": [{"kind": "audio", "name": "voice.ogg"}]}, "downloads/voice.ogg")
        )

    def test_local_collection_excludes_ordinary_audio(self):
        folder = Path.cwd()
        messages = [
            {"media": [{"kind": "audio"}], "attachment_results": ["downloads/song.mp3"]},
            {"media": [{"kind": "voice"}], "attachment_results": ["downloads/voice.ogg"]},
        ]
        self.assertEqual(collect(messages, folder), {"downloads/voice.ogg": [1]})


if __name__ == "__main__":
    unittest.main()
