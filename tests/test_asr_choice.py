import unittest
from unittest.mock import patch
from colab_transfer import CONFIG, reusable_transcript
from process_backup import choose_asr


class ASRChoiceTests(unittest.TestCase):
    def test_cpu_and_colab_reuse_same_quality(self):
        record = {
            "status": "done",
            "sha256": "abc",
            "text": "hello",
            "config": dict(CONFIG, device="cpu", compute_type="float32"),
        }
        self.assertTrue(reusable_transcript(record, "abc"))
        self.assertFalse(reusable_transcript(record, "changed"))
        self.assertFalse(
            reusable_transcript(dict(record, config=dict(CONFIG, vad_filter=True)), "abc")
        )

    def test_both_routes_and_pause(self):
        for value, expected in [("1", "colab"), ("2", "local"), ("0", None)]:
            with patch("builtins.input", return_value=value), patch("builtins.print"):
                self.assertEqual(choose_asr(), expected)


if __name__ == "__main__":
    unittest.main()
