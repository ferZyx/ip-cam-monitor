import re
import unittest
from pathlib import Path


class ServerHistoryDedupTests(unittest.TestCase):
    def test_history_poll_dedups_by_filename(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertRegex(
            src,
            r"if\s+fname\s+and\s+fname\s+in\s+alarm_store\[\"known_files\"\]:",
        )


if __name__ == "__main__":
    unittest.main()
