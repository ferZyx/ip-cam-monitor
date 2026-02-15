import hashlib
import json
import os
import socket
import struct
import time
from dataclasses import dataclass
from datetime import datetime

import cv2


# Legacy control header used by current project dvrip sender
_HDR_OLD = struct.Struct("BB2xII2xHI")

# DVRIP packet header (same as dvrip Packet struct)
_HDR_NEW = struct.Struct("<BBxxIIBBHI")


def _sofia_hash(password: str) -> str:
    md5 = hashlib.md5(password.encode("utf-8")).digest()
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    # legacy sender in this repo returns full 16 chars; keep same behavior
    return "".join(chars[(a + b) % 62] for a, b in zip(md5[0::2], md5[1::2]))


def _recvn(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def _send_old(sock: socket.socket, session: int, seq: int, msgid: int, obj: dict):
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    pkt = _HDR_OLD.pack(0xFF, 0x00, session, seq, msgid, len(payload) + 2)
    sock.sendall(pkt + payload + b"\x0a\x00")


def _recv_old_json(sock: socket.socket) -> dict:
    hdr = _recvn(sock, 20)
    _, _, sess, seq, msgid, dlen = _HDR_OLD.unpack(hdr)
    data = _recvn(sock, dlen)
    try:
        j = json.loads(data.rstrip(b"\x00\x0a"))
    except Exception:
        j = None
    return {
        "sess": sess,
        "seq": seq,
        "msgid": msgid,
        "len": dlen,
        "json": j,
        "raw": data,
    }


def _recv_new_packet(sock: socket.socket) -> dict:
    hdr = _recvn(sock, _HDR_NEW.size)
    magic, ver, sess, seq, b1, b2, msgid, dlen = _HDR_NEW.unpack(hdr)
    if magic != 0xFF:
        raise ValueError("invalid DVRIP magic")
    payload = _recvn(sock, dlen) if dlen else b""
    return {
        "ver": ver,
        "sess": sess,
        "seq": seq,
        "b1": b1,
        "b2": b2,
        "msgid": msgid,
        "len": dlen,
        "payload": payload,
    }


def _extract_media_from_1426(stream_1426: bytes) -> bytes:
    """
    Поток 1426 содержит заголовки фреймов/аудио/метаданных.
    Вычищаем служебные блоки и оставляем чистый H264 elementary stream.

    Форматы заголовков (наблюдено на этой камере):
      0x1FC: video frame with 12-byte header (BBBBII)
      0x1FD: audio (?) with 4-byte length
      0x1F9/0x1FA: aux blocks with 4-byte header (BBH)
    """
    cursor = 0
    remain = 0
    out = bytearray()
    while cursor < len(stream_1426):
        if remain == 0:
            if cursor + 4 > len(stream_1426):
                break
            dtype = struct.unpack(">I", stream_1426[cursor : cursor + 4])[0]
            cursor += 4

            if dtype in (0x1FC, 0x1FE):
                if cursor + 12 > len(stream_1426):
                    break
                _, _, _, _, _, remain = struct.unpack(
                    "BBBBII", stream_1426[cursor : cursor + 12]
                )
                cursor += 12
            elif dtype == 0x1FD:
                if cursor + 4 > len(stream_1426):
                    break
                (remain,) = struct.unpack("I", stream_1426[cursor : cursor + 4])
                cursor += 4
            elif dtype in (0x1FA, 0x1F9):
                if cursor + 4 > len(stream_1426):
                    break
                _, _, remain = struct.unpack("BBH", stream_1426[cursor : cursor + 4])
                cursor += 4
            elif dtype == 0xFFD8FFE0:
                # Rare: JPEG signature split into dword
                out.extend(b"\xff\xd8\xff\xe0")
                continue
            else:
                # resync by shifting 1 byte back (we already consumed 4)
                cursor -= 3
                continue

        take = min(remain, len(stream_1426) - cursor)
        if take <= 0:
            break
        out.extend(stream_1426[cursor : cursor + take])
        cursor += take
        remain -= take
    return bytes(out)


def _sharpness_score_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _contrast_score_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def _gray_ratio_bgr(frame) -> float:
    # low saturation pixels ratio
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    return float((s < 18).mean())


@dataclass
class ExtractResult:
    ok: bool
    reason: str
    jpeg_bytes: bytes | None
    debug_dir: str | None
    chosen_frame_index: int | None


def download_motion_file_h264(
    ip: str,
    port: int,
    username: str,
    password: str,
    filename: str,
    begin_time: str,
    end_time: str,
    debug_dir: str | None = None,
    timeout_sec: int = 12,
) -> bytes:
    """Скачивает motion-ролик через dual-socket OPPlayBack DownloadStart.

    Возвращает байты payload-ов 1426 (сырой поток, с заголовками).
    """
    ctl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ctl.settimeout(timeout_sec)
    ctl.connect((ip, port))

    login = {
        "EncryptType": "MD5",
        "LoginType": "DVRIP-Web",
        "PassWord": _sofia_hash(password),
        "UserName": username,
    }
    _send_old(ctl, 0, 0, 1000, login)
    lr = _recv_old_json(ctl)
    if not isinstance(lr.get("json"), dict) or lr["json"].get("Ret") not in (100, 515):
        ctl.close()
        raise RuntimeError(f"DVRIP login failed: {lr.get('json')}")
    session = int(lr["json"]["SessionID"], 16)

    pb = {
        "Name": "OPPlayBack",
        "SessionID": f"0x{session:08X}",
        "OPPlayBack": {
            "Action": "DownloadStart",
            "Parameter": {"FileName": filename, "TransMode": "TCP", "Value": 0},
            "StartTime": begin_time,
            "EndTime": end_time,
        },
    }

    data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    data_sock.settimeout(timeout_sec)
    data_sock.connect((ip, port))

    try:
        # data claim + control start
        _send_old(data_sock, session, 0, 1424, pb)
        _send_old(ctl, session, 2, 1420, pb)
        _ = _recv_old_json(ctl)  # control reply

        raw_1426 = bytearray()
        dbg_packets = []
        packets = 0

        while True:
            pkt = _recv_new_packet(data_sock)
            packets += 1
            if debug_dir and len(dbg_packets) < 50:
                dbg_packets.append({k: pkt[k] for k in ("msgid", "len", "b1", "b2")})

            if pkt["msgid"] == 1426 and pkt["payload"]:
                raw_1426.extend(pkt["payload"])
            if pkt["b2"] != 0:
                break

        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            with open(
                os.path.join(debug_dir, "download_debug.json"), "w", encoding="utf-8"
            ) as fh:
                json.dump(
                    {
                        "filename": filename,
                        "packets": packets,
                        "first_packets": dbg_packets,
                    },
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )

        return bytes(raw_1426)
    finally:
        try:
            data_sock.close()
        except Exception:
            pass
        try:
            ctl.close()
        except Exception:
            pass


def extract_best_jpeg_from_motion_h264(
    stream_1426: bytes,
    debug_dir: str | None = None,
    sample_frame_indexes: list[int] | None = None,
    score_mode: str = "sharpness",
) -> ExtractResult:
    """Преобразует 1426 stream -> media .h264 -> выбирает лучший кадр -> JPEG bytes."""
    if not stream_1426:
        return ExtractResult(False, "empty_1426_stream", None, debug_dir, None)

    media = _extract_media_from_1426(stream_1426)
    if not media:
        return ExtractResult(False, "failed_to_extract_h264", None, debug_dir, None)

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        media_path = os.path.join(debug_dir, "media_extracted.h264")
        with open(media_path, "wb") as fh:
            fh.write(media)
    else:
        # OpenCV VideoCapture needs a file path; use a timestamp temp file.
        tmp_name = f"alarm_media_{int(time.time() * 1000)}.h264"
        media_path = os.path.abspath(tmp_name)
        with open(media_path, "wb") as fh:
            fh.write(media)

    try:
        cap = cv2.VideoCapture(media_path)
        if not cap.isOpened():
            return ExtractResult(
                False, "opencv_failed_to_open_h264", None, debug_dir, None
            )

        # default frame picks; works reasonably for 10-25s motion clips
        if sample_frame_indexes is None:
            sample_frame_indexes = [0, 10, 30, 60]

        chosen = None
        chosen_idx = None
        chosen_score = -1.0

        idx = 0
        target_set = set(sample_frame_indexes)
        max_idx = max(sample_frame_indexes) if sample_frame_indexes else 0

        while idx <= max_idx:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if idx in target_set:
                if score_mode == "content":
                    sharp = _sharpness_score_bgr(frame)
                    cont = _contrast_score_bgr(frame)
                    gray_r = _gray_ratio_bgr(frame)
                    score = (sharp + cont * 5.0) * max(0.0, 1.0 - gray_r)
                else:
                    score = _sharpness_score_bgr(frame)
                if debug_dir:
                    out = os.path.join(
                        debug_dir, f"candidate_{idx:03d}_s{score:.1f}.jpg"
                    )
                    cv2.imwrite(out, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if score > chosen_score:
                    chosen_score = score
                    chosen = frame
                    chosen_idx = idx
            idx += 1

        cap.release()

        if chosen is None:
            return ExtractResult(False, "no_frames_decoded", None, debug_dir, None)

        ok, jpeg = cv2.imencode(".jpg", chosen, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return ExtractResult(
                False, "jpeg_encode_failed", None, debug_dir, chosen_idx
            )

        return ExtractResult(True, "ok", jpeg.tobytes(), debug_dir, chosen_idx)
    finally:
        if not debug_dir:
            try:
                os.remove(media_path)
            except Exception:
                pass
