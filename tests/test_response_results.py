import json
import unittest
from response_results import complete_objects, validate_individual


class ResultsTests(unittest.TestCase):
    def test_two_closed_objects_survive_truncated_third(self):
        row = lambda key: dict(
            id=key,
            corrected_text="text",
            selected_context_ids=[],
            uncertain_phrases=[],
            sensitive_changes=[],
        )
        text = (
            '{"items":['
            + json.dumps(row("a"))
            + ","
            + json.dumps(row("b"))
            + ',{"id":"c","corrected_text":"unfinished'
        )
        accepted, errors = validate_individual(
            complete_objects(text), [{"id": key} for key in "abc"]
        )
        self.assertEqual([r["id"] for r in accepted], ["a", "b"])

    def test_invalid_and_duplicate_items_rejected(self):
        good = dict(
            id="a",
            corrected_text="text",
            selected_context_ids=[],
            uncertain_phrases=[],
            sensitive_changes=[],
        )
        accepted, errors = validate_individual(
            [good, dict(id=[]), good, {"id": "b"}], [{"id": "a"}, {"id": "b"}]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(set(errors), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
