import re
import unittest
from pathlib import Path


class ServerTelegramModeTests(unittest.TestCase):
    """Проверяет Telegram-интеграции сервера."""

    def test_server_uses_shared_telegram_sender(self):
        """Проверяет, что сервер использует общий Telegram sender."""
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertIn("from telegram_sender import send_telegram_payload", src)
        self.assertIn("send_telegram_payload(", src)
        self.assertIn("as_document=False", src)

    def test_experiment_uses_same_shared_sender(self):
        """Проверяет, что диагностический скрипт использует общий sender."""
        src = (
            Path(__file__).with_name("experiments") / "telegram_send_probe.py"
        ).read_text(encoding="utf-8")

        self.assertIn("from telegram_sender import send_telegram_payload", src)

    def test_shared_sender_handles_ssl_config(self):
        """Проверяет поддержку SSL-настроек Telegram sender."""
        src = Path(__file__).with_name("telegram_sender.py").read_text(encoding="utf-8")

        self.assertIn("ssl.create_default_context", src)
        self.assertIn("ssl._create_unverified_context()", src)

    def test_server_starts_yellow_box_alerts_through_env_config(self):
        """Проверяет запуск yellow-box мониторинга через .env-переменные."""
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertIn("YELLOW_BOX_ALERT_ENABLED", src)
        self.assertIn("YELLOW_BOX_CHECK_INTERVAL_SEC", src)
        self.assertIn("YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE", src)
        self.assertIn("RateLimitedTelegramQueue", src)
        self.assertIn("YellowBoxFrameMonitor", src)
        self.assertIn("FrameSnapshot", src)
        self.assertIn("start_yellow_box_alerts()", src)


if __name__ == "__main__":
    unittest.main()
