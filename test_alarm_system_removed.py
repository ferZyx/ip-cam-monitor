import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class AlarmSystemRemovedTests(unittest.TestCase):
    """Проверяет, что старый camera-alarm pipeline удален из приложения."""

    def test_server_keeps_only_telegram_sender_wrapper(self):
        """Проверяет отсутствие runtime-логики тревог в Flask-сервере."""
        src = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("from telegram_sender import send_telegram_payload", src)
        self.assertIn("def send_telegram(", src)
        self.assertIn("send_telegram_payload(", src)

        forbidden_fragments = [
            "ALARM_",
            "alarm_store",
            "alarm_executor",
            "alarm_history_poll_loop",
            "query_alarms",
            "parse_alarm_event",
            "extract_alarm_photo",
            "save_alarm_photo",
            "get_alarm_photo_bytes",
            "@app.route(\"/alarms\")",
            "@app.route(\"/alarm_photo\")",
            "name=\"alarm_history\"",
            "alarm_photo_extractor",
            "alarm_hybrid_extractor",
            "alarm_logic",
        ]
        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, src)

    def test_index_has_no_alarm_ui_or_requests(self):
        """Проверяет, что браузерный UI больше не показывает старые тревоги."""
        src = (ROOT / "index.html").read_text(encoding="utf-8")

        forbidden_fragments = [
            "alarmPanel",
            "alarmList",
            "alarmCount",
            "alarmModal",
            "toggleAlarms",
            "loadAlarms",
            "showAlarmPhoto",
            "/alarms",
            "/alarm_photo",
            "Тревоги",
            "тревог",
        ]
        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, src)

    def test_alarm_modules_and_research_files_are_removed(self):
        """Проверяет, что tracked-файлы старой системы тревог удалены."""
        removed_paths = [
            ROOT / "alarm_logic.py",
            ROOT / "alarm_photo_extractor.py",
            ROOT / "alarm_hybrid_extractor.py",
            ROOT / "ALARM_TELEGRAM_NOTES.md",
            ROOT / "experiments" / "ALARM_RESEARCH.md",
            ROOT / "experiments" / "ALARM_RESULT_GOOD_BUT_GRAY.md",
            ROOT / "experiments" / "export_last_alarm_photos.py",
            ROOT / "experiments" / "realtime_alarm_last5.py",
            ROOT / "experiments" / "research_alarm_callback_dump.py",
            ROOT / "experiments" / "research_direct_alarm_jpg_download.py",
            ROOT / "experiments" / "research_human_event_bruteforce.py",
            ROOT / "experiments" / "research_idea1_frame_quality.py",
            ROOT / "experiments" / "research_idea1_frame_quality_v2.py",
            ROOT / "experiments" / "research_idea1_hybrid_motion_fix.py",
            ROOT / "experiments" / "test_export_last_alarm_photos.py",
        ]
        for path in removed_paths:
            with self.subTest(path=path.name):
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
