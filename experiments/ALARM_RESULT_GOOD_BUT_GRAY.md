# Alarm Photos: Good Path (With Gray-Frame Caveat)

Date: 2026-02-15

This note captures the current "best known" way to export the last alarm images from this camera,
and the remaining caveat (some extracted frames may look partially gray/corrupted).

## What Works Well

We can reliably get "last N alarms" as *distinct* images, using camera's own alarm index:

- Query alarm files via DVRIP `OPFileQuery` with `Type=jpg` (paths like `/idea1/... .jpg`).
- Download each file using `OPPlayBack DownloadStart`.
- Treat the downloaded payload as a short video stream and extract a frame (OpenCV/ffmpeg via `cv2.VideoCapture`).

Script:

- `stream_viewer/experiments/export_last_alarm_photos.py`

Recommended command:

```bat
py experiments\export_last_alarm_photos.py --limit 5 --strict-jpg --download-timeout-sec 12 --download-retries 1
```

Example output run (known good):

- `stream_viewer/experiments/output/last_alarms_20260215_105821/`

## Known Caveats

1) Some `Type=jpg` entries are placeholders

- Download returns an empty stream (`1426 len=0`) or times out.
- In `--strict-jpg` mode we skip these and continue until we collect N real images.

2) Gray / corrupted-looking frames

Some extracted frames may look "mostly gray" or partially corrupted (e.g. large gray blocks),
even though the mobile app shows a normal picture.

Likely cause:

- The `/idea1/... .jpg` file is not a real JPEG; it is a short video stream.
- The stream may start mid-GOP or lacks a clean keyframe; decoding some frames yields artifacts.

Next investigation direction:

- Try extracting a *different frame* from the same downloaded stream (scan more frames,
  avoid low-information/gray frames) and/or force codec detection.

Pragmatic workaround (current best for "white bottom"):

- If the best frame from `/idea1/... .jpg` has a large corrupted/white bottom region,
  fall back to extracting a frame from the matching motion clip (`/idea0/... .h264`) covering the same time.

Experiment:

- `stream_viewer/experiments/research_idea1_hybrid_motion_fix.py`

See experiments:

- `stream_viewer/experiments/research_direct_alarm_jpg_download.py`
- `stream_viewer/experiments/research_idea1_frame_quality.py`
