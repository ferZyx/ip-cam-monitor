import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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

from alarm_photo_extractor import (  # type: ignore
    download_motion_file_h264,
    extract_best_jpeg_from_motion_h264,
)


CAMERA_IP = os.getenv("CAMERA_IP", "192.168.100.9")
DVRIP_PORT = int(os.getenv("DVRIP_PORT", "34567"))
CAMERA_USER = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")


def _parse_dt(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _dt_from_filename(fname: str) -> datetime | None:
    s = str(fname or "")
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/\d{3}/(\d{2})\.(\d{2})\.(\d{2})-", s)
    if not m:
        return None
    date_s, hh, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4)
    return _parse_dt(f"{date_s} {hh}:{mm}:{ss}")


def _sanitize(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z._\-\[\]]+", "_", s)
    return s[:180] or "x"


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


def _nal_start_code_count(blob: bytes) -> int:
    # H264/H265 NAL start codes
    return blob.count(b"\x00\x00\x00\x01")


def opfilequery(cam, begin: str, end: str, event: str, ftype: str) -> list[dict]:
    payload = {
        "Name": "OPFileQuery",
        "OPFileQuery": {
            "BeginTime": begin,
            "EndTime": end,
            "Channel": 0,
            "DriverTypeMask": "0x0000FFFF",
            "Event": event,
            "Type": ftype,
            "StreamType": "Main",
        },
    }
    res = cam.send(1440, payload)
    if not res:
        return []
    data = res.get("OPFileQuery", res)
    if isinstance(data, dict):
        data = data.get("FileList", [])
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for r in data:
        if isinstance(r, dict):
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Research: try direct download of alarm Type=jpg files via DownloadStart"
    )
    ap.add_argument("--ip", default=CAMERA_IP)
    ap.add_argument("--port", type=int, default=DVRIP_PORT)
    ap.add_argument("--user", default=CAMERA_USER)
    ap.add_argument("--password", default=CAMERA_PASS)
    ap.add_argument("--begin", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--timeout-sec", type=int, default=60)
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
    if args.end:
        end_dt = _parse_dt(args.end)
        if end_dt is None:
            print("ERROR: invalid --end format")
            return 2
    else:
        end_dt = now

    if args.begin:
        begin_dt = _parse_dt(args.begin)
        if begin_dt is None:
            print("ERROR: invalid --begin format")
            return 2
    else:
        begin_dt = end_dt - timedelta(hours=6)

    begin = begin_dt.strftime("%Y-%m-%d %H:%M:%S")
    end = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    out_root = Path(__file__).resolve().parent / "output"
    run_dir = out_root / f"direct_jpg_{now.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "camera": {"ip": args.ip, "port": args.port, "user": args.user},
        "window": {"begin": begin, "end": end},
        "limit": int(args.limit),
        "results": [],
    }

    cam = DVRIPCam(args.ip, port=args.port, user=args.user, password=args.password)
    if not cam.login():
        print("ERROR: DVRIP login failed")
        return 3

    try:
        rows = opfilequery(cam, begin, end, event="*", ftype="jpg")
        enriched = []
        for r in rows:
            bt = _parse_dt(str(r.get("BeginTime", "")))
            if bt is None:
                bt = _dt_from_filename(str(r.get("FileName", "")))
            rr = dict(r)
            rr["__dt"] = bt
            enriched.append(rr)

        enriched.sort(
            key=lambda x: (x.get("__dt") is not None, x.get("__dt") or datetime.min),
            reverse=True,
        )
        enriched = enriched[: int(args.limit)]

        print(f"OPFileQuery jpg: got {len(rows)}, taking {len(enriched)}")
        for i, r in enumerate(enriched, start=1):
            bt = str(r.get("BeginTime", ""))
            et = str(r.get("EndTime", ""))
            fname = str(r.get("FileName", ""))
            label = f"{i:02d}_{_sanitize(bt)}_{_sanitize(fname)}"

            row = {
                "index": i,
                "BeginTime": bt,
                "EndTime": et,
                "FileName": fname,
                "ok": False,
                "bytes_raw": 0,
                "bytes_jpeg": 0,
                "sha1": None,
                "error": None,
            }

            debug_dir = run_dir / f"debug_{i:02d}"
            raw = b""
            try:
                bt2 = bt
                et2 = et or bt
                if bt2 and et2 == bt2:
                    dtp = _parse_dt(bt2)
                    if dtp is not None:
                        et2 = (dtp + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
                raw = download_motion_file_h264(
                    ip=args.ip,
                    port=args.port,
                    username=args.user,
                    password=args.password,
                    filename=fname,
                    begin_time=bt2,
                    end_time=et2,
                    debug_dir=str(debug_dir),
                    timeout_sec=int(args.timeout_sec),
                )
                row["bytes_raw"] = int(len(raw))

                # Save raw payload for analysis
                (debug_dir / "raw_1426.bin").write_bytes(raw)

                # First: try to treat it as a short motion clip and extract a frame.
                res = extract_best_jpeg_from_motion_h264(raw, debug_dir=None)
                if res.ok and res.jpeg_bytes:
                    out_path = run_dir / f"{label}.jpg"
                    out_path.write_bytes(res.jpeg_bytes)
                    row["ok"] = True
                    row["mode"] = "as_h264"
                    row["bytes_jpeg"] = int(len(res.jpeg_bytes))
                    row["sha1"] = hashlib.sha1(res.jpeg_bytes).hexdigest()
                    print(
                        f"  OK  {i:02d} {out_path.name} ({len(res.jpeg_bytes)} bytes) via as_h264"
                    )
                    report["results"].append(row)
                    continue

                # Try to locate JPEG markers in raw payload (many firmwares embed JPEG directly)
                jpeg = _extract_largest_jpeg(raw)
                if jpeg is None:
                    # Try to locate markers in the extracted media file if present
                    media_path = debug_dir / "media_extracted.h264"
                    if media_path.exists():
                        jpeg = _extract_largest_jpeg(media_path.read_bytes())

                if jpeg is None:
                    raise RuntimeError("jpeg_markers_not_found_in_stream")

                out_path = run_dir / f"{label}.jpg"
                out_path.write_bytes(jpeg)
                row["ok"] = True
                row["mode"] = "jpeg_markers"
                row["bytes_jpeg"] = int(len(jpeg))
                row["sha1"] = hashlib.sha1(jpeg).hexdigest()
                print(f"  OK  {i:02d} {out_path.name} ({len(jpeg)} bytes)")
            except Exception as e:
                row["error"] = str(e)
                print(f"  ERR {i:02d} {e}")

            try:
                # light heuristic: if this looks like video, note it
                row["nal_start_codes"] = (
                    _nal_start_code_count(raw) if row.get("bytes_raw") else 0
                )
            except Exception:
                row["nal_start_codes"] = None

            report["results"].append(row)

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
