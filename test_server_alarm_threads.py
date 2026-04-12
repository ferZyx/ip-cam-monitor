import re
import unittest
from pathlib import Path


class ServerAlarmThreadTests(unittest.TestCase):
    def test_main_starts_alarm_callback_thread(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertRegex(
            src,
            r"threading\.Thread\(\s*target=alarm_callback_loop,\s*daemon=True,\s*name=\"alarm_callback\"",
        )
        self.assertRegex(src, r"alarm_callback_thread\.start\(\)")


if __name__ == "__main__":
    unittest.main()
