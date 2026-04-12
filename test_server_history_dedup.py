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

    def test_history_poll_uses_last_hour_window_by_default(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertRegex(
            src,
            r"ALARM_POLL_LOOKBACK_SEC\s*=\s*_env_int\(\"ALARM_POLL_LOOKBACK_SEC\",\s*3600\)",
        )
        self.assertRegex(
            src,
            r"begin_dt\s*=\s*now\s*-\s*timedelta\(seconds=max\(60,\s*ALARM_POLL_LOOKBACK_SEC\)\)",
        )


if __name__ == "__main__":
    unittest.main()
