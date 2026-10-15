import unittest
from unittest.mock import MagicMock, patch
import subprocess
from correction_runner import run_correction


class RunnerTests(unittest.TestCase):
    def test_heartbeat_and_retry(self):
        folder = MagicMock()
        logs = folder.__truediv__.return_value
        logs.__truediv__.return_value.open.return_value.__enter__.return_value = MagicMock()
        first = MagicMock()
        first.wait.side_effect = [subprocess.TimeoutExpired("request", 20), 1]
        first.poll.return_value = 1
        second = MagicMock()
        second.wait.return_value = 0
        second.poll.return_value = 0
        with (
            patch("correction_runner.subprocess.Popen", side_effect=[first, second]) as launch,
            patch("correction_runner.time.sleep"),
            patch("builtins.print") as output,
        ):
            self.assertEqual(run_correction(["python", "correct"], folder), 0)
            self.assertEqual(launch.call_count, 2)
            self.assertTrue(any("ChatGPT waiting:" in str(c) for c in output.call_args_list))


if __name__ == "__main__":
    unittest.main()
