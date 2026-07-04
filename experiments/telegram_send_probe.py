import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from telegram_sender import send_telegram_payload


def main() -> int:
    """Отправляет тестовое Telegram-сообщение из переменных окружения."""
    ap = argparse.ArgumentParser(description="Send one Telegram probe message")
    ap.add_argument("--text", default="Probe from stream_viewer experiments")
    ap.add_argument("--photo", default="", help="Optional path to JPEG")
    ap.add_argument("--as-document", action="store_true")
    args = ap.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    ssl_verify = os.getenv("TELEGRAM_SSL_VERIFY", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ca_bundle = os.getenv("TELEGRAM_CA_BUNDLE", "").strip()

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are empty")
        return 2

    photo_bytes = None
    if args.photo:
        p = Path(args.photo)
        photo_bytes = p.read_bytes()

    send_telegram_payload(
        bot_token=token,
        chat_id=chat_id,
        text=args.text,
        photo_bytes=photo_bytes,
        ssl_verify=ssl_verify,
        ca_bundle=ca_bundle,
        as_document=bool(args.as_document),
    )
    print("OK: telegram probe sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
