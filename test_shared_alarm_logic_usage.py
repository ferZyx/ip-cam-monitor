import unittest
from pathlib import Path


class SharedAlarmLogicUsageTests(unittest.TestCase):
    def test_server_imports_shared_alarm_logic(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")
        self.assertIn(
            "from alarm_logic import alarm_row_dt, choose_best_alarm_events", src
        )

    def test_export_script_uses_shared_alarm_logic(self):
        src = (
            Path(__file__).with_name("experiments") / "export_last_alarm_photos.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from alarm_logic import choose_best_alarm_events", src)
        self.assertIn(
            "picked = choose_best_alarm_events(jpg_files, h264_files, cluster_gap_sec=30)",
            src,
        )


if __name__ == "__main__":
    unittest.main()
