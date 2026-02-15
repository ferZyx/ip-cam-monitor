"""Telegram notify helper.

This module intentionally does not read .env by itself; the caller supplies token/chat_id.
"""

from __future__ import annotations

import json
import urllib.request


def send_telegram(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    photo_bytes: bytes | None = None,
    timeout_sec: int = 10,
) -> tuple[bool, str | None]:
    """Send a Telegram message or photo.

    Returns (ok, error).
    """
    bot_token = (bot_token or "").strip()
    chat_id = (chat_id or "").strip()
    if not bot_token or not chat_id:
        return False, "missing_token_or_chat_id"

    try:
        if photo_bytes:
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            boundary = "----FormBoundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="caption"\r\n'
                    f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n"
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="photo"; filename="alarm.jpg"\r\n'
                    f"Content-Type: image/jpeg\r\n\r\n"
                ).encode("utf-8")
                + photo_bytes
                + f"\r\n--{boundary}--\r\n".encode("utf-8")
            )
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        else:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = json.dumps(
                {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )

        with urllib.request.urlopen(req, timeout=int(timeout_sec)) as resp:
            body = resp.read()

        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
            if isinstance(payload, dict) and payload.get("ok") is False:
                desc = payload.get("description")
                return False, str(desc) if desc else "telegram_api_ok_false"
        except Exception:
            # Not fatal; treat as success if HTTP request succeeded.
            pass

        return True, None
    except Exception as e:
        return False, str(e)
