import unittest
from documentation_excel import resolved_sender


class SenderExportTests(unittest.TestCase):
    def test_export_does_not_inject_any_account_name(self):
        original = {
            "sender": "نامشخص",
            "sender_role": "self",
            "sender_status": "unknown",
            "forwarded_from": "Forward source",
        }
        row = resolved_sender(original)
        self.assertEqual(row["sender"], "من")
        self.assertEqual(row["forwarded_from"], "Forward source")
        self.assertEqual(original["sender"], "نامشخص")
        for role in ("participant", "unknown", "self"):
            known = {"sender": "Actual name", "sender_role": role}
            self.assertEqual(resolved_sender(known), known)


if __name__ == "__main__":
    unittest.main()
