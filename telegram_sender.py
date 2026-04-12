import json
import ssl
import urllib.request


def send_telegram_payload(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    photo_bytes: bytes | None = None,
    ssl_verify: bool = True,
    ca_bundle: str = "",
    as_document: bool = False,
) -> None:
    if not bot_token or not chat_id:
        return

    ssl_context = None
    if ssl_verify:
        if ca_bundle:
            ssl_context = ssl.create_default_context(cafile=ca_bundle)
        else:
            ssl_context = ssl.create_default_context()
    else:
        ssl_context = ssl._create_unverified_context()

    if photo_bytes:
        method = "sendDocument" if as_document else "sendPhoto"
        field_name = "document" if as_document else "photo"
        url = f"https://api.telegram.org/bot{bot_token}/{method}"
        boundary = "----FormBoundary"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n'
                f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="alarm.jpg"\r\n'
                f"Content-Type: image/jpeg\r\n\r\n"
            ).encode()
            + photo_bytes
            + f"\r\n--{boundary}--\r\n".encode()
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
        ).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )

    urllib.request.urlopen(req, timeout=10, context=ssl_context)
