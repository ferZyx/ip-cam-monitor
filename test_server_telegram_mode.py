import re
import unittest
from pathlib import Path


class ServerTelegramModeTests(unittest.TestCase):
    def test_send_telegram_uses_send_document_for_images(self):
        src = Path(__file__).with_name("server.py").read_text(encoding="utf-8")

        self.assertRegex(src, r"/sendDocument")
        self.assertIn('name="document"; filename="alarm.jpg"', src)
        self.assertNotRegex(src, r"/sendPhoto")


if __name__ == "__main__":
    unittest.main()
