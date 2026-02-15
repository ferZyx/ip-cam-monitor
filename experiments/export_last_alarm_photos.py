import argparse
import json
import os
import re
import hashlib
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Ensure we can import project modules from stream_viewer/ when running this script.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


try:
    from dvrip import DVRIPCam

    HAS_DVRIP = True
except Exception:
    DVRIPCam = None  # type: ignore
    HAS_DVRIP = False


try:
    # Reuse proven downloader + H264->JPEG extractor from main code.
    from alarm_photo_extractor import (
        download_motion_file_h264,
        extract_best_jpeg_from_motion_h264,
    )
except Exception as e:
    print("ERROR: failed to import alarm_photo_extractor from stream_viewer/:", e)
    raise


CAMERA_IP = os.getenv("CAMERA_IP", "192.168.100.9")
DVRIP_PORT = int(os.getenv("DVRIP_PORT", "34567"))
CAMERA_USER = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")

MSG_OP_FILE_QUERY = 1440


def _jpeg_decodable(jpeg_bytes: bytes) -> bool:
    try:
        import numpy as np
        import cv2

        img = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        return img is not None
    except Exception:
        return False


def download_dvrip_file_raw(
    *,
    ip: str,
    port: int,
    username: str,
    password: str,
    filename: str,
    begin_time: str,
    end_time: str,
    timeout_sec: int,
    retries: int = 2,
) -> bytes:
    """Download a file via OPPlayBack DownloadStart.

    Some firmwares occasionally close the socket; retry helps.
    """
    last_err: Exception | None = None
    tries = max(1, int(retries))

    bt = (begin_time or "").strip()
    et = (end_time or "").strip()
    if bt and (not et or et == bt):
        # Some firmwares are picky when StartTime==EndTime.
        dt = _parse_dt(bt)
        if dt is not None:
            et = (dt + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            et = bt

    for attempt in range(1, tries + 1):
        try:
            return download_motion_file_h264(
                ip=ip,
                port=port,
                username=username,
                password=password,
                filename=filename,
                begin_time=bt,
                end_time=et,
                debug_dir=None,
                timeout_sec=int(timeout_sec),
            )
        except Exception as e:
            last_err = e
            if attempt < tries:
                try:
                    import time

                    time.sleep(0.8)
                except Exception:
                    pass
                continue
            raise

    raise RuntimeError(str(last_err) if last_err else "download_failed")


def _parse_dt(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None

    # Common firmware format
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    # Variants
    for fmt in (
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y/%m/%d %H:%M:%S%z",
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue

    # Single-digit hour/min/sec without padding
    m = re.match(
        r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2}):(\d{1,2})$",
        s,
    )
    if m:
        try:
            y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
            return datetime(y, mo, d, hh, mm, ss)
        except Exception:
            return None

    return None


def _dt_from_filename(fname: str) -> datetime | None:
    """Best-effort parse timestamp from FileName path."""
    s = str(fname or "")
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/\d{3}/(\d{2})\.(\d{2})\.(\d{2})-", s)
    if not m:
        return None
    date_s, hh, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4)
    return _parse_dt(f"{date_s} {hh}:{mm}:{ss}")


def _sanitize_filename(s: str) -> str:
    s = s.strip()
    if not s:
        return "unknown"
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z._\-\[\]]+", "_", s)
    return s[:180]


def _label_name_for_file(fname: str) -> str:
    s = _sanitize_filename(fname)
    for ext in (".jpg", ".jpeg", ".h264", ".mp4"):
        if s.lower().endswith(ext):
            return s[: -len(ext)]
    return s


def opfilequery(
    cam, begin: str, end: str, event: str, ftype: str, stream: str = "Main"
) -> list[dict]:
    payload = {
        "Name": "OPFileQuery",
        "OPFileQuery": {
            "BeginTime": begin,
            "EndTime": end,
            "Channel": 0,
            "DriverTypeMask": "0x0000FFFF",
            "Event": event,
            "Type": ftype,
            "StreamType": stream,
        },
    }
    res = cam.send(MSG_OP_FILE_QUERY, payload)
    if not res:
        return []
    data = res.get("OPFileQuery", res)
    if isinstance(data, dict):
        data = data.get("FileList", [])
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for row in data:
        if isinstance(row, dict):
            out.append(row)
    return out


# The 1426 payload is wrapped into tagged blocks. For JPEG files we want to strip wrappers
# and then locate the JPEG SOI/EOI markers.
_HDR_NEW = struct.Struct("<BBxxIIBBHI")


def _extract_media_from_1426(stream_1426: bytes) -> bytes:
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


def _extract_largest_jpeg(blob: bytes) -> bytes | None:
    if not blob:
        return None
    best = None
    best_len = 0

    start = 0
    while True:
        i = blob.find(b"\xff\xd8", start)
        if i < 0:
            break
        j = blob.find(b"\xff\xd9", i + 2)
        if j < 0:
            break
        j += 2
        size = j - i
        if size > best_len:
            best = blob[i:j]
            best_len = size
        start = i + 2

    if best is not None and best_len > 1000:
        return best
    return None


@dataclass
class Extracted:
    ok: bool
    method: str
    reason: str
    jpeg: bytes | None


def extract_alarm_jpeg(file_type: str, raw_1426: bytes) -> Extracted:
    """Extract a viewable JPEG from a downloaded DVRIP file.

    Important finding for this camera:
    - Some "Type=jpg" alarm files (idea1/...) are NOT JPEGs. They contain a short H264-like stream.
      The mobile app shows them as a photo, but on disk they need video frame extraction.
    """

    # 1) Try treating the download as a motion clip (works for real h264 and for some idea1 "jpg" entries).
    res = extract_best_jpeg_from_motion_h264(raw_1426, debug_dir=None)
    if res.ok and res.jpeg_bytes:
        method = (
            "h264_best_frame"
            if file_type.lower() == "h264"
            else "jpg_as_h264_best_frame"
        )
        return Extracted(True, method, str(res.reason), res.jpeg_bytes)

    # 2) Try extracting a real JPEG by markers (some firmwares may store actual JPEG bytes).
    media = _extract_media_from_1426(raw_1426)
    jpeg = _extract_largest_jpeg(media)
    if jpeg and _jpeg_decodable(jpeg):
        return Extracted(True, "jpg_from_1426_media", "ok", jpeg)

    jpeg2 = _extract_largest_jpeg(raw_1426)
    if jpeg2 and _jpeg_decodable(jpeg2):
        return Extracted(True, "jpg_from_raw_1426", "ok", jpeg2)

    return Extracted(
        False,
        "extract",
        str(res.reason) or "no_decodable_jpeg_found",
        None,
    )


def choose_last_alarms(
    jpg_files: list[dict], h264_files: list[dict], limit: int
) -> list[dict]:
    # Prefer native jpg alarms if present.
    if len(jpg_files) >= limit:
        return jpg_files[:limit]

    picked: list[dict] = []
    seen_keys: set[str] = set()

    def add(rows: list[dict]):
        for r in rows:
            bt = str(r.get("BeginTime", ""))
            fn = str(r.get("FileName", ""))
            key = bt + "|" + fn
            if not bt and not fn:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            picked.append(r)
            if len(picked) >= limit:
                return

    add(jpg_files)
    if len(picked) < limit:
        add(h264_files)
    return picked[:limit]


def _find_closest_by_time(
    rows: list[dict], target: datetime, max_delta_sec: int
) -> dict | None:
    best = None
    best_key = None
    for r in rows:
        bt = r.get("__dt")
        if bt is None:
            bt = _parse_dt(str(r.get("BeginTime", "")))
        if bt is None:
            continue

        et = _parse_dt(str(r.get("EndTime", "")))
        in_range = False
        if et is not None:
            in_range = bt <= target <= et

        # Primary: candidates that contain the target time.
        # Secondary: closest by begin time.
        delta = abs((bt - target).total_seconds())
        if delta > max_delta_sec and not in_range:
            continue

        key = (
            0 if in_range else 1,
            delta,
            abs((et - target).total_seconds()) if et is not None else 10**9,
        )
        if best is None or (best_key is not None and key < best_key):
            best = r
            best_key = key
    return best


def _normalize_rows(rows: list[dict], file_type: str) -> tuple[list[dict], dict]:
    out: list[dict] = []
    unparsed_samples: list[dict] = []
    for r in rows:
        bt = str(r.get("BeginTime", ""))
        dt = _parse_dt(bt)
        if dt is None:
            dt = _dt_from_filename(str(r.get("FileName", "")))

        rr = dict(r)
        rr["__dt"] = dt
        rr["__type"] = file_type
        out.append(rr)

        if dt is None and len(unparsed_samples) < 5:
            unparsed_samples.append(
                {
                    "BeginTime": r.get("BeginTime"),
                    "EndTime": r.get("EndTime"),
                    "FileName": r.get("FileName"),
                }
            )

    out.sort(
        key=lambda x: (x.get("__dt") is not None, x.get("__dt") or datetime.min),
        reverse=True,
    )

    parsed = [x for x in out if x.get("__dt") is not None]
    meta = {
        "raw": len(rows),
        "parsed": len(parsed),
        "max_time": parsed[0]["__dt"].strftime("%Y-%m-%d %H:%M:%S") if parsed else None,
        "unparsed_samples": unparsed_samples,
    }
    return out, meta


def fetch_recent_files(
    cam,
    end_dt: datetime,
    *,
    event: str,
    ftype: str,
    stream: str,
    want: int,
    max_lookback_hours: int = 72,
    initial_chunk_minutes: int = 30,
    min_chunk_seconds: int = 120,
    cap_guard: int = 60,
) -> tuple[list[dict], list[dict]]:
    """Fetch most recent OPFileQuery entries by sliding windows backwards.

    Some firmwares truncate OPFileQuery results; this method avoids asking for huge windows.
    Returns (normalized_rows_sorted_desc, chunks_debug).
    """
    collected: list[dict] = []
    seen: set[str] = set()
    chunks: list[dict] = []

    max_lookback = timedelta(hours=max(1, int(max_lookback_hours)))
    oldest_allowed = end_dt - max_lookback

    chunk = timedelta(minutes=max(1, int(initial_chunk_minutes)))
    min_chunk = timedelta(seconds=max(1, int(min_chunk_seconds)))

    cursor_end = end_dt
    while cursor_end > oldest_allowed and len(collected) < want:
        begin_dt = cursor_end - chunk
        if begin_dt < oldest_allowed:
            begin_dt = oldest_allowed

        begin = begin_dt.strftime("%Y-%m-%d %H:%M:%S")
        end = cursor_end.strftime("%Y-%m-%d %H:%M:%S")
        rows = opfilequery(cam, begin, end, event=event, ftype=ftype, stream=stream)
        norm, meta = _normalize_rows(rows, ftype)

        chunks.append(
            {
                "begin": begin,
                "end": end,
                "raw": len(rows),
                "parsed": meta.get("parsed"),
                "max_time": meta.get("max_time"),
                "chunk_sec": int(chunk.total_seconds()),
            }
        )

        # If we hit a cap, shrink the window and retry on the same end.
        if len(rows) >= cap_guard and chunk > min_chunk:
            chunk = max(min_chunk, timedelta(seconds=int(chunk.total_seconds() // 2)))
            continue

        # Accept this chunk
        for r in norm:
            key = f"{r.get('BeginTime', '')}|{r.get('FileName', '')}|{r.get('__type', '')}"
            if key in seen:
                continue
            seen.add(key)
            collected.append(r)

        # Move window back
        cursor_end = begin_dt

        # If too few results, gently expand the chunk to speed up backfill.
        if len(rows) < max(1, want // 2):
            chunk = min(timedelta(hours=6), chunk * 2)

    collected.sort(
        key=lambda x: (x.get("__dt") is not None, x.get("__dt") or datetime.min),
        reverse=True,
    )
    return collected, chunks


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export last N alarm images (jpg or extracted from motion h264)"
    )
    ap.add_argument("--ip", default=CAMERA_IP)
    ap.add_argument("--port", type=int, default=DVRIP_PORT)
    ap.add_argument("--user", default=CAMERA_USER)
    ap.add_argument("--password", default=CAMERA_PASS)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--since-hours", type=int, default=0)
    ap.add_argument("--begin", default="")
    ap.add_argument("--end", default="")
    ap.add_argument(
        "--mode",
        choices=["latest", "window"],
        default="latest",
        help="latest = most recent alarms (works around OPFileQuery truncation), window = use --begin/--end",
    )
    ap.add_argument(
        "--prefer",
        choices=["jpg", "h264"],
        default="jpg",
        help="Prefer this alarm source when both exist",
    )
    ap.add_argument(
        "--download-timeout-sec",
        type=int,
        default=60,
        help="Socket timeout for DVRIP DownloadStart (per recv).",
    )
    ap.add_argument(
        "--download-retries",
        type=int,
        default=2,
        help="Retries for DownloadStart errors like 'socket closed'.",
    )
    ap.add_argument(
        "--strict-jpg",
        action="store_true",
        help="Only accept real Type=jpg downloads; skip placeholders and do not fallback to motion for jpg.",
    )
    args = ap.parse_args()

    if not HAS_DVRIP or DVRIPCam is None:
        print(
            "ERROR: dvrip module not available. Install: py -m pip install python-dvr"
        )
        return 2

    if not args.password:
        print("ERROR: CAMERA_PASS is empty. Set it in stream_viewer/.env")
        return 2

    now = datetime.now()

    mode = str(args.mode or "latest")
    if args.begin or args.end:
        mode = "window"

    if mode == "window":
        if args.begin:
            begin_dt = _parse_dt(args.begin)
            if not begin_dt:
                print("ERROR: invalid --begin format, expected YYYY-mm-dd HH:MM:SS")
                return 2
        elif args.since_hours and args.since_hours > 0:
            begin_dt = now - timedelta(hours=int(args.since_hours))
        else:
            # Default: from yesterday 00:00 (user said there were many alarms yesterday)
            y = now.date() - timedelta(days=1)
            begin_dt = datetime(y.year, y.month, y.day, 0, 0, 0)

        if args.end:
            end_dt = _parse_dt(args.end)
            if not end_dt:
                print("ERROR: invalid --end format, expected YYYY-mm-dd HH:MM:SS")
                return 2
        else:
            end_dt = now
    else:
        # In latest mode we ignore begin/end for querying (we use sliding windows).
        # Keep a small informational window in the report.
        end_dt = now
        begin_dt = now - timedelta(hours=6)

    begin = begin_dt.strftime("%Y-%m-%d %H:%M:%S")
    end = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    out_root = Path(__file__).resolve().parent / "output"
    run_dir = out_root / f"last_alarms_{now.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "camera": {"ip": args.ip, "port": args.port, "user": args.user},
        "mode": mode,
        "window": {"begin": begin, "end": end},
        "limit": int(args.limit),
        "queries": {},
        "chunks": {},
        "selected": [],
        "saved": [],
        "errors": [],
    }

    cam = DVRIPCam(args.ip, port=args.port, user=args.user, password=args.password)
    if not cam.login():
        print("ERROR: DVRIP login failed")
        return 3

    try:
        h264_event_used = "M"

        if mode == "latest":
            jpg_n, jpg_chunks = fetch_recent_files(
                cam,
                end_dt,
                event="*",
                ftype="jpg",
                stream="Main",
                want=int(args.limit),
            )

            h264_n, h264_chunks = fetch_recent_files(
                cam,
                end_dt,
                event=h264_event_used,
                ftype="h264",
                stream="Main",
                want=max(10, int(args.limit) * 3),
            )
            if not h264_n:
                h264_event_used = "*"
                h264_n, h264_chunks = fetch_recent_files(
                    cam,
                    end_dt,
                    event=h264_event_used,
                    ftype="h264",
                    stream="Main",
                    want=max(10, int(args.limit) * 3),
                )

            report["queries"]["jpg"] = {
                "event": "*",
                "raw": len(jpg_n),
                "parsed": len([x for x in jpg_n if x.get("__dt") is not None]),
                "max_time": jpg_n[0]["__dt"].strftime("%Y-%m-%d %H:%M:%S")
                if jpg_n and jpg_n[0].get("__dt")
                else None,
            }
            report["queries"]["h264"] = {
                "event": h264_event_used,
                "raw": len(h264_n),
                "parsed": len([x for x in h264_n if x.get("__dt") is not None]),
                "max_time": h264_n[0]["__dt"].strftime("%Y-%m-%d %H:%M:%S")
                if h264_n and h264_n[0].get("__dt")
                else None,
            }
            report["chunks"]["jpg"] = jpg_chunks
            report["chunks"]["h264"] = h264_chunks
        else:
            jpg_files = opfilequery(
                cam, begin, end, event="*", ftype="jpg", stream="Main"
            )
            h264_files = opfilequery(
                cam, begin, end, event=h264_event_used, ftype="h264", stream="Main"
            )
            if not h264_files:
                h264_event_used = "*"
                h264_files = opfilequery(
                    cam, begin, end, event=h264_event_used, ftype="h264", stream="Main"
                )

            jpg_n, jpg_meta = _normalize_rows(jpg_files, "jpg")
            h264_n, h264_meta = _normalize_rows(h264_files, "h264")
            report["queries"]["jpg"] = {"event": "*", **jpg_meta}
            report["queries"]["h264"] = {"event": h264_event_used, **h264_meta}

        limit = int(args.limit)

        # Optional: swap preference.
        if args.prefer == "h264":
            selected = choose_last_alarms(h264_n, jpg_n, limit)
        else:
            selected = choose_last_alarms(jpg_n, h264_n, limit)

        # Some cameras return placeholder jpg entries that cannot be downloaded.
        # In strict mode, try a larger set of jpg candidates and only keep those
        # that successfully download as real JPEG.
        if args.strict_jpg and args.prefer == "jpg":
            selected = jpg_n[: max(limit * 12, limit)]

        # Clean helper fields before report.
        report["selected"] = [
            {
                "type": str(r.get("__type", "")),
                "BeginTime": r.get("BeginTime"),
                "EndTime": r.get("EndTime"),
                "FileName": r.get("FileName"),
            }
            for r in selected
        ]

        if not selected:
            print("No alarm files found in selected window.")
            (run_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return 0

        print(f"Found jpg={len(jpg_n)} h264={len(h264_n)}. Candidates={len(selected)}")

        seen_sha1: dict[str, str] = {}
        saved_count = 0

        for r in selected:
            if saved_count >= limit:
                break

            idx = saved_count + 1
            ftype = str(r.get("__type", "")) or "h264"
            fname = str(r.get("FileName", ""))
            bt = str(r.get("BeginTime", ""))
            et = str(r.get("EndTime", ""))
            bt_dt = r.get("__dt")

            label = f"{idx:02d}_{ftype}_{_sanitize_filename(bt)}_{_label_name_for_file(fname)}"
            out_jpg = run_dir / f"{label}.jpg"

            row = {
                "index": idx,
                "type": ftype,
                "BeginTime": bt,
                "EndTime": et,
                "FileName": fname,
                "out": str(out_jpg.name),
            }

            try:
                ex = None

                # Note: DownloadStart works reliably for motion clips (Type=h264). For "Type=jpg" alarm files
                # some firmwares behave inconsistently. Prefer direct jpg download, then fallback to motion.
                if ftype.lower() == "jpg" and bt_dt is not None:
                    # 1) Direct download of the jpg file itself.
                    try:
                        raw_jpg = download_dvrip_file_raw(
                            ip=args.ip,
                            port=args.port,
                            username=args.user,
                            password=args.password,
                            filename=fname,
                            begin_time=bt,
                            end_time=et or bt,
                            timeout_sec=int(args.download_timeout_sec),
                            retries=int(args.download_retries),
                        )
                        exj = extract_alarm_jpeg("jpg", raw_jpg)
                        if exj.ok and exj.jpeg:
                            row["extract"] = {
                                "ok": exj.ok,
                                "method": exj.method,
                                "reason": exj.reason,
                                "from": "direct_jpg_downloadstart",
                            }
                            ex = exj
                    except Exception as e:
                        row["direct_jpg_error"] = str(e)

                    # 2) Fallback: align by time to closest motion clip.
                    if ex is None and (not args.strict_jpg):
                        closest = _find_closest_by_time(
                            h264_n, bt_dt, max_delta_sec=180
                        )
                        if closest is not None:
                            cfname = str(closest.get("FileName", ""))
                            cbt = str(closest.get("BeginTime", ""))
                            cet = str(closest.get("EndTime", ""))
                            raw2 = download_dvrip_file_raw(
                                ip=args.ip,
                                port=args.port,
                                username=args.user,
                                password=args.password,
                                filename=cfname,
                                begin_time=cbt,
                                end_time=cet or cbt,
                                timeout_sec=int(args.download_timeout_sec),
                                retries=int(args.download_retries),
                            )
                            ex2 = extract_alarm_jpeg("h264", raw2)
                            row["extract"] = {
                                "ok": ex2.ok,
                                "method": ex2.method,
                                "reason": ex2.reason,
                                "from": "jpg_marker_to_h264_motion",
                            }
                            row["motion_file"] = {
                                "FileName": cfname,
                                "BeginTime": cbt,
                                "EndTime": cet,
                            }
                            ex = ex2

                # Otherwise download the file entry itself.
                if ex is None:
                    raw_1426 = download_dvrip_file_raw(
                        ip=args.ip,
                        port=args.port,
                        username=args.user,
                        password=args.password,
                        filename=fname,
                        begin_time=bt,
                        end_time=et or bt,
                        timeout_sec=int(args.download_timeout_sec),
                        retries=int(args.download_retries),
                    )
                    ex = extract_alarm_jpeg(ftype, raw_1426)
                    row["extract"] = {
                        "ok": ex.ok,
                        "method": ex.method,
                        "reason": ex.reason,
                    }

                if not ex.ok or not ex.jpeg:
                    raise RuntimeError(f"extract_failed: {ex.reason}")

                sha1 = hashlib.sha1(ex.jpeg).hexdigest()
                row["sha1"] = sha1
                if sha1 in seen_sha1:
                    row["duplicate_of"] = seen_sha1[sha1]
                else:
                    seen_sha1[sha1] = row["out"]

                out_jpg.write_bytes(ex.jpeg)
                row["bytes"] = int(len(ex.jpeg))
                report["saved"].append(row)
                saved_count += 1
                print(f"  saved {out_jpg.name} ({len(ex.jpeg)} bytes) via {ex.method}")
            except Exception as e:
                row["error"] = str(e)
                report["errors"].append(row)
                print(f"  ERROR {idx}: {e}")

        report["saved_count"] = saved_count
        report["candidates"] = len(selected)

        (run_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print("Output:", str(run_dir))
        return 0
    finally:
        try:
            cam.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
