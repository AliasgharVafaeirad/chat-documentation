import json, unittest
from correction_sizing import split_text, units_for, batches, combine


class SizingTests(unittest.TestCase):
    def task(self, text):
        return {
            "input_hash": "hash",
            "item": {
                "id": "voice1",
                "alternatives": [{"engine": "Whisper", "text": text}],
                "chat_context": [{"source_id": "m1", "text": "زمینه " * 2000}],
            },
        }

    def test_complete_text_and_bounded_requests(self):
        original = ("این متن فارسی است.\n" * 1000) + "پایان"
        task = self.task(original)
        units = units_for(task)
        self.assertEqual("".join(u["item"]["alternatives"][0]["text"] for u in units), original)
        self.assertEqual(len({u["item"]["id"] for u in units}), len(units))
        for batch in batches(units):
            self.assertLessEqual(
                len(json.dumps({"items": [u["item"] for u in batch]}).encode()), 24000
            )

    def test_partial_cache_not_marked_complete(self):
        task = self.task("واژه " * 1000)
        units = units_for(task)
        cache = {}
        self.assertIsNone(combine(task, units, cache))
        for index, unit in enumerate(units):
            cache[unit["key"]] = {
                "status": "done",
                "corrected_text": str(index),
                "uncertain_phrases": ["phrase"],
            }
        result = combine(task, units, cache)
        self.assertEqual(result["id"], "voice1")
        self.assertEqual(result["corrected_text"], "\n".join(str(i) for i in range(len(units))))
        self.assertEqual(result["uncertain_phrases"], ["phrase"])
        changed = self.task("تغییر")
        self.assertNotEqual(units_for(changed)[0]["key"], units[0]["key"])

    def test_no_space_text_still_preserved(self):
        text = "الف" * 2000
        self.assertEqual("".join(split_text(text)), text)


if __name__ == "__main__":
    unittest.main()
