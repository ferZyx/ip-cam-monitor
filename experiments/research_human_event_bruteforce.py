import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from dvrip import DVRIPCam

try:
    from dotenv import load_dotenv

    ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(ROOT / ".env")
except Exception:
    pass


CAMERA_IP = os.getenv("CAMERA_IP", "192.168.100.9")
DVRIP_PORT = int(os.getenv("DVRIP_PORT", "34567"))
USER = os.getenv("CAMERA_USER", "krua")
PASSWORD = os.getenv("CAMERA_PASS", "")

MSG_OP_FILE_QUERY = 1440


def parse_files(resp):
    if not resp or "OPFileQuery" not in resp:
        return []
    raw = resp["OPFileQuery"]
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("FileList", [])
    return []


def query(cam, begin, end, event, ftype, stream):
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
    return res, parse_files(res)


def main():
    if not PASSWORD:
        print("ERROR: set CAMERA_PASS in env")
        return

    now = datetime.now()
    begin = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")

    events = [
        "M",
        "H",
        "Human",
        "HumanDetect",
        "SMD",
        "SmartMotion",
        "MotionDetect",
        "AlarmLocal",
        "VideoLoss",
        "VideoBlind",
        "*",
    ]
    types = ["h264", "jpg"]
    streams = ["Main", "Extra"]

    cam = DVRIPCam(CAMERA_IP, port=DVRIP_PORT, user=USER, password=PASSWORD)
    if not cam.login():
        print("ERROR: login failed")
        return

    report = {
        "camera": CAMERA_IP,
        "begin": begin,
        "end": end,
        "results": [],
    }

    for ev in events:
        for tp in types:
            for st in streams:
                try:
                    _, files = query(cam, begin, end, ev, tp, st)
                    sample = files[0] if files else None
                    row = {
                        "event": ev,
                        "type": tp,
                        "stream": st,
                        "count": len(files),
                        "sample": sample,
                    }
                    report["results"].append(row)
                    if len(files):
                        sample_name = ""
                        if isinstance(sample, dict):
                            sample_name = sample.get("FileName", "")
                        print(
                            f"event={ev:12} type={tp:4} stream={st:5} -> {len(files)} | {sample_name}"
                        )
                except Exception as e:
                    report["results"].append(
                        {"event": ev, "type": tp, "stream": st, "error": str(e)}
                    )
                    print(f"event={ev} type={tp} stream={st} -> ERROR {e}")

    cam.close()

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"filequery_bruteforce_{now.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("Saved:", out_path)


if __name__ == "__main__":
    main()
