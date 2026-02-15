# Alarm System Notes (XM / DVRIP)

This project interacts with camera alarms through two channels:

1) Realtime alarm callback (push)
2) History via OPFileQuery (pull)

## 1) Realtime (push): DVRIP alarm callback

Implemented in `stream_viewer/server.py` as `alarm_callback_loop()`.

High level:

- Connect via DVRIP, `setAlarm(callback)`
- Send `AlarmSet`
- Start `cam.alarm_thread` and wait for callback invocations

If this path is unreliable in practice, we still can reconstruct "last alarms" from history.

## 2) History (pull): OPFileQuery

The firmware provides an indexed list of files with timestamps:

- `Type=h264` + `Event=M` usually returns motion clips (most reliable to download)
- `Type=jpg` + `Event=*` returns many entries that look like alarm snapshots

Important practical finding:

- Downloading `Type=jpg` entries via the same `OPPlayBack DownloadStart` method may timeout / be inconsistent.
- Some `Type=jpg` entries are placeholders: download returns an empty 1426 payload (`len=0`).
- On this camera, many `Type=jpg` alarm files are NOT JPEG bytes. They often contain a short H264-like stream.
  To get a real image you must extract a frame (same approach as motion clips).
- Downloading motion clips (`Type=h264`) via `OPPlayBack DownloadStart` works reliably.
- To get a "picture of an alarm" from history we treat `Type=jpg` results as time markers and then:
  1. find the closest motion clip (`Type=h264`) by `BeginTime`
  2. download the motion clip
  3. extract the sharpest frame as JPEG

Newer experiments also confirmed that **some** `Type=jpg` files are directly downloadable as a real JPEG (tens of KB).
So the best strategy is:

1) try direct download of the `Type=jpg` file
2) if it is a placeholder / fails, either skip it (strict mode) or fallback to motion extraction

## Export last alarm images

Script: `stream_viewer/experiments/export_last_alarm_photos.py`

Default behavior:

- Queries yesterday 00:00 -> now
- Takes last 5 alarms
- If alarm list comes from `Type=jpg`, it converts them to images by extracting from closest motion clip (`Type=h264`)

Run:

```bat
py experiments\export_last_alarm_photos.py --limit 5
```

Output:

- `stream_viewer/experiments/output/last_alarms_YYYYmmdd_HHMMSS/`
- `report.json` inside that folder with query counts + per-file extraction metadata

Useful flags:

```bat
py experiments\export_last_alarm_photos.py --limit 5 --download-timeout-sec 60
py experiments\export_last_alarm_photos.py --begin "2026-02-14 00:00:00" --end "2026-02-15 23:59:59" --limit 5
py experiments\export_last_alarm_photos.py --prefer h264 --limit 5
```
