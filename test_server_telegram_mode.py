import re
import unittest
from pathlib import Path


class ServerTelegramModeTests(unittest.TestCase):
    def test_server_uses_shared_telegram_sender(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertIn("from telegram_sender import send_telegram_payload", src)
        self.assertIn("send_telegram_payload(", src)
        self.assertIn("as_document=False", src)

    def test_experiment_uses_same_shared_sender(self):
        src = (
            Path(__file__).with_name("experiments") / "telegram_send_probe.py"
        ).read_text(encoding="utf-8")

        self.assertIn("from telegram_sender import send_telegram_payload", src)

    def test_shared_sender_handles_ssl_config(self):
        src = Path(__file__).with_name("telegram_sender.py").read_text(encoding="utf-8")

        self.assertIn("ssl.create_default_context", src)
        self.assertIn("ssl._create_unverified_context()", src)


if __name__ == "__main__":
    unittest.main()
