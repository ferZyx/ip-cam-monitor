import json
import os
import threading
import time
from datetime import datetime
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


def main():
    if not PASSWORD:
        print("ERROR: set CAMERA_PASS in env")
        return

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (
        out_dir / f"alarm_callback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )

    lock = threading.Lock()

    def on_alarm(alarm_data, seq_number):
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "seq": seq_number,
            "data": alarm_data,
        }
        line = json.dumps(row, ensure_ascii=False)
        with lock:
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        print("ALARM", row["ts"], "seq", seq_number, "type", type(alarm_data).__name__)
        print("  ", str(alarm_data)[:400])

    cam = DVRIPCam(CAMERA_IP, port=DVRIP_PORT, user=USER, password=PASSWORD)
    if not cam.login():
        print("ERROR: login failed")
        return

    cam.setAlarm(on_alarm)
    print("Listening alarm callback. Move in front of camera to generate events.")
    print("Output:", out_path)

    # Start listener thread same way as server does
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
            if not cam.alarm.is_alive():
                print("Alarm thread stopped")
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            cam.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
