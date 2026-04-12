import re
import unittest
from pathlib import Path


class ServerAlarmThreadTests(unittest.TestCase):
    def test_server_uses_history_poll_only(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertRegex(
            src,
            r"threading\.Thread\(\s*target=alarm_history_poll_loop,\s*daemon=True,\s*name=\"alarm_history\"",
        )
        self.assertNotRegex(src, r"def\s+alarm_callback_loop\(")
        self.assertNotRegex(src, r"setAlarm\(")

    def test_server_can_quiet_http_access_logs(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertRegex(
            src,
            r"QUIET_HTTP_ACCESS_LOGS\s*=\s*_env_bool\(\"QUIET_HTTP_ACCESS_LOGS\",\s*default=True\)",
        )
        self.assertIn('logging.getLogger("werkzeug").setLevel(logging.WARNING)', src)


if __name__ == "__main__":
    unittest.main()
