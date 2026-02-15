import argparse
import json
import os
import sys
import threading
import time
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


def _safe_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _keep_last_n(dir_path: Path, n: int) -> None:
    files = sorted(
        [p for p in dir_path.glob("*.jpg") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files[n:]:
        try:
            p.unlink()
        except Exception:
            pass


def _try_extract_from_motion(ip: str, dt: datetime) -> tuple[bytes | None, dict]:
    """Try to extract a good JPEG from the closest motion clip around dt."""
    meta: dict = {"ok": False, "reason": "init", "motion": None}
    # Motion file index can lag a bit.
    for attempt in range(1, 6):
        cam = None
        try:
            cam = DVRIPCam(ip, port=DVRIP_PORT, user=CAMERA_USER, password=CAMERA_PASS)
            if not cam.login():
                meta["reason"] = "login_failed"
                return None, meta

            begin = (dt - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
            end = (dt + timedelta(seconds=15)).strftime("%Y-%m-%d %H:%M:%S")
            query = {
                "Name": "OPFileQuery",
                "OPFileQuery": {
                    "BeginTime": begin,
                    "EndTime": end,
                    "Channel": 0,
                    "DriverTypeMask": "0x0000FFFF",
                    "Event": "M",
                    "Type": "h264",
                    "StreamType": "Main",
                },
            }
            res = cam.send(1440, query)
            data = None
            if isinstance(res, dict):
                data = res.get("OPFileQuery")
            if isinstance(data, dict):
                data = data.get("FileList")
            if not isinstance(data, list):
                data = []

            # choose closest by BeginTime
            best = None
            best_delta = None
            for r in data:
                if not isinstance(r, dict):
                    continue
                bt = str(r.get("BeginTime", ""))
                try:
                    bdt = datetime.strptime(bt, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                delta = abs((bdt - dt).total_seconds())
                if best is None or (best_delta is not None and delta < best_delta):
                    best = r
                    best_delta = delta

            if not best:
                meta["reason"] = f"no_motion_file_attempt_{attempt}"
                time.sleep(1.5)
                continue

            fname = str(best.get("FileName", ""))
            btime = str(best.get("BeginTime", ""))
            etime = str(best.get("EndTime", ""))
            meta["motion"] = {"FileName": fname, "BeginTime": btime, "EndTime": etime}

            raw = download_motion_file_h264(
                ip=ip,
                port=DVRIP_PORT,
                username=CAMERA_USER,
                password=CAMERA_PASS,
                filename=fname,
                begin_time=btime,
                end_time=etime or btime,
                debug_dir=None,
                timeout_sec=60,
            )
            res2 = extract_best_jpeg_from_motion_h264(raw, debug_dir=None)
            if res2.ok and res2.jpeg_bytes:
                meta["ok"] = True
                meta["reason"] = "ok"
                meta["chosen_frame_index"] = res2.chosen_frame_index
                return res2.jpeg_bytes, meta
            meta["reason"] = str(res2.reason)
            time.sleep(1.0)
        except Exception as e:
            meta["reason"] = f"exception: {e}"
            time.sleep(1.0)
        finally:
            if cam:
                try:
                    cam.close()
                except Exception:
                    pass

    return None, meta


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Listen for realtime DVRIP alarms and maintain last N alarm photos"
    )
    ap.add_argument("--ip", default=CAMERA_IP)
    ap.add_argument("--port", type=int, default=DVRIP_PORT)
    ap.add_argument("--user", default=CAMERA_USER)
    ap.add_argument("--password", default=CAMERA_PASS)
    ap.add_argument("--keep", type=int, default=5)
    ap.add_argument("--out", default="realtime_last5")
    ap.add_argument(
        "--duration-sec",
        type=int,
        default=0,
        help="0 = run forever",
    )
    ap.add_argument(
        "--also-extract-from-motion",
        action="store_true",
        help="After each alarm, try to extract best frame from motion clip too",
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

    out_dir = Path(__file__).resolve().parent / "output" / str(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "events.jsonl"

    lock = threading.Lock()
    start_ts = time.time()

    def on_alarm(alarm_data, seq_number):
        dt = datetime.now()
        ts = dt.strftime("%Y-%m-%d_%H-%M-%S")

        # Fast live snapshot
        jpeg = None
        try:
            snap = cam.snapshot(channel=0)
            if snap and len(snap) > 100 and snap[:2] == b"\xff\xd8":
                jpeg = bytes(snap)
        except Exception:
            jpeg = None

        row = {
            "ts": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "seq": seq_number,
            "alarm": alarm_data,
            "saved_live": False,
            "saved_motion": False,
        }

        if jpeg:
            name = f"{ts}_seq{seq_number}_live.jpg"
            _safe_write(out_dir / name, jpeg)
            row["saved_live"] = True

        if args.also_extract_from_motion:
            motion_jpeg, meta = _try_extract_from_motion(args.ip, dt)
            row["motion_meta"] = meta
            if motion_jpeg:
                name2 = f"{ts}_seq{seq_number}_motion.jpg"
                _safe_write(out_dir / name2, motion_jpeg)
                row["saved_motion"] = True

        with lock:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        _keep_last_n(out_dir, int(args.keep))
        print(
            "ALARM",
            row["ts"],
            "seq",
            seq_number,
            "live",
            row["saved_live"],
            "motion",
            row["saved_motion"],
        )

    cam = DVRIPCam(args.ip, port=args.port, user=args.user, password=args.password)
    if not cam.login():
        print("ERROR: DVRIP login failed")
        return 3

    cam.setAlarm(on_alarm)
    print("Listening realtime alarms...")
    print("Output:", str(out_dir))

    try:
        cam.send(
            cam.QCODES["AlarmSet"], {"Name": "", "SessionID": "0x%08X" % cam.session}
        )
    except Exception as e:
        print("AlarmSet error:", e)

    cam.alarm = threading.Thread(
        name="DVRAlarm%08X" % cam.session,
        target=cam.alarm_thread,
        args=[cam.busy],
        daemon=True,
    )
    cam.alarm.start()

    try:
        while True:
            time.sleep(1)
            if args.duration_sec and (time.time() - start_ts) >= int(args.duration_sec):
                break
            if not cam.alarm.is_alive():
                print("Alarm thread stopped")
                break
    finally:
        try:
            cam.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
