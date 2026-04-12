import importlib.util
import unittest
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parent / "export_last_alarm_photos.py"
    spec = importlib.util.spec_from_file_location(
        "export_last_alarm_photos", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load export_last_alarm_photos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExportLastAlarmPhotosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_choose_last_alarms_collapses_same_alarm_to_best_quality(self):
        jpg_rows = [
            {
                "BeginTime": "2026-04-09 20:41:20",
                "EndTime": "2026-04-09 20:41:21",
                "FileName": "/idea1/2026-04-09/001/20.41.20-20.41.21[M][@55][0].jpg",
            },
            {
                "BeginTime": "2026-04-09 20:41:13",
                "EndTime": "2026-04-09 20:41:13",
                "FileName": "/idea1/2026-04-09/001/20.41.13-20.41.13[M][@54][0].jpg",
            },
        ]
        h264_rows = [
            {
                "BeginTime": "2026-04-09 20:41:13",
                "EndTime": "2026-04-09 20:41:30",
                "FileName": "/idea0/2026-04-09/001/20.41.13-20.41.30[M][@7d20][1].h264",
            }
        ]

        selected = self.mod.choose_last_alarms(jpg_rows, h264_rows, limit=5)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].get("FileName"), h264_rows[0]["FileName"])

    def test_choose_last_alarms_keeps_distinct_events(self):
        jpg_rows = [
            {
                "BeginTime": "2026-04-09 20:41:20",
                "EndTime": "2026-04-09 20:41:21",
                "FileName": "/idea1/2026-04-09/001/20.41.20-20.41.21[M][@55][0].jpg",
            }
        ]
        h264_rows = [
            {
                "BeginTime": "2026-04-09 20:35:00",
                "EndTime": "2026-04-09 20:35:10",
                "FileName": "/idea0/2026-04-09/001/20.35.00-20.35.10[M][@1111][1].h264",
            },
            {
                "BeginTime": "2026-04-09 19:20:00",
                "EndTime": "2026-04-09 19:20:08",
                "FileName": "/idea0/2026-04-09/001/19.20.00-19.20.08[M][@2222][1].h264",
            },
        ]

        selected = self.mod.choose_last_alarms(jpg_rows, h264_rows, limit=5)

        self.assertEqual(len(selected), 3)


if __name__ == "__main__":
    unittest.main()
