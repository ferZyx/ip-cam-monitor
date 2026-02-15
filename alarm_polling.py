"""Alarm polling monitor.

This avoids relying on DVRIP realtime alarm callback which can be unreliable.

Workflow:

1) On startup, fetch recent alarm markers (Type=jpg, /idea1). Seed known set.
2) Every `poll_interval_sec`, fetch recent markers again.
3) For each new marker: extract photo via hybrid extractor and notify Telegram.

The caller owns logging and side-effects (saving files, store updates).
"""

from __future__ import annotations

import time
from datetime import datetime


def alarm_poll_loop(
    *,
    get_camera_ip,
    poll_interval_sec: int,
    store: dict,
    store_lock,
    executor,
    history_max: int,
    fetch_recent_alarm_markers,
    extract_alarm_photo_hybrid,
    save_alarm_photo,
    send_telegram,
    dvrip_port: int,
    username: str,
    password: str,
    debug_dir_root: str,
    debug: bool,
    want: int = 50,
    lookback_hours: int = 12,
):
    seeded = False

    while True:
        ip = get_camera_ip()
        if not ip:
            time.sleep(2)
            continue

        now = datetime.now()
        store["polling_active"] = True
        store["polling_last_run"] = now.isoformat()
        store["polling_last_error"] = None

        try:
            rows, meta = fetch_recent_alarm_markers(
                ip=ip,
                port=dvrip_port,
                user=username,
                password=password,
                end_dt=now,
                want=int(want),
                max_lookback_hours=int(lookback_hours),
            )

            if not seeded:
                # Seed known set; do not notify.
                with store_lock:
                    for r in rows:
                        fname = str(r.get("FileName", ""))
                        if fname:
                            store["known_files"].add(fname)
                    store["last_check"] = now.isoformat()
                seeded = True
                time.sleep(max(1, int(poll_interval_sec)))
                continue

            new_markers: list[dict] = []
            with store_lock:
                for r in rows:
                    fname = str(r.get("FileName", ""))
                    if not fname:
                        continue
                    if fname in store["known_files"]:
                        continue
                    store["known_files"].add(fname)
                    new_markers.append(r)

            # Process oldest -> newest (best UX in TG)
            new_markers.sort(key=lambda x: str(x.get("BeginTime", "")))

            for r in new_markers:
                bt_txt = str(r.get("BeginTime", ""))
                et_txt = str(r.get("EndTime", ""))
                fname = str(r.get("FileName", ""))

                alarm_entry = {
                    "time": bt_txt,
                    "end_time": et_txt,
                    "type": "Движение",
                    "type_code": "M",
                    "file": fname,
                    "size": 0,
                    "photo_file": None,
                    "source": "poll",
                }

                with store_lock:
                    store["alarms"] = ([alarm_entry] + store["alarms"])[
                        : int(history_max)
                    ]

                def job(entry=alarm_entry):
                    try:
                        dt_txt = str(entry.get("time", ""))
                        try:
                            bt = datetime.strptime(dt_txt, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            bt = None
                        if bt is None:
                            return

                        jpeg, meta2 = extract_alarm_photo_hybrid(
                            ip,
                            bt,
                            dvrip_port=dvrip_port,
                            username=username,
                            password=password,
                            debug_dir_root=debug_dir_root,
                            debug=debug,
                            timeout_sec=60,
                            download_retries=2,
                            bottom_white_threshold=0.25,
                        )

                        photo_file = None
                        if jpeg:
                            alarm_id = dt_txt.replace(":", "_").replace(" ", "_")
                            photo_file = save_alarm_photo(alarm_id, jpeg)

                        with store_lock:
                            for a in store["alarms"]:
                                if a.get("file") == entry.get("file"):
                                    a["photo_file"] = photo_file
                                    a["size"] = len(jpeg) if jpeg else 0
                                    a["photo_meta"] = meta2
                                    break

                        text = f"🚨 {entry.get('type', 'Событие')}\n🕐 {dt_txt}\n📼 {entry.get('file', '')}"
                        if not jpeg:
                            text += "\n⚠️ Фото не удалось достать"

                        send_telegram(text, jpeg if jpeg else None)
                    except Exception as e:
                        # do not raise out of executor
                        try:
                            store["polling_last_error"] = str(e)
                        except Exception:
                            pass

                executor.submit(job)

            with store_lock:
                store["last_check"] = now.isoformat()

        except Exception as e:
            store["polling_last_error"] = str(e)

        time.sleep(max(1, int(poll_interval_sec)))
