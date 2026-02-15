# Experiments

All research / investigation scripts live here.

Guidelines:

- Put any "try / research / debug" scripts into `stream_viewer/experiments/`.
- Keep runnable scripts self-contained.
- Write outputs into `stream_viewer/experiments/output/` (this folder is git-ignored).

Notes:

- Load env from `stream_viewer/.env` (one level up from this folder).

Current experiments:

- `export_last_alarm_photos.py` - exports last N alarm images into `experiments/output/`.
- `realtime_alarm_last5.py` - listens to realtime DVRIP alarms and keeps last 5 snapshots.
- `research_direct_alarm_jpg_download.py` - investigates downloading `/idea1/... .jpg` alarm files and extracting a viewable frame.
- `research_idea1_frame_quality.py` - scans more frames and avoids gray-ish frames for better extraction.
- `research_idea1_frame_quality_v2.py` - adds bottom-white penalties and codec probe (h264/hevc) to reduce "white bottom" artifacts.
- `research_idea1_hybrid_motion_fix.py` - if idea1 frame has white bottom/corruption, tries a better frame from the matching motion clip.
- `research_alarm_callback_dump.py` - listens to DVRIP realtime alarm callback and dumps JSONL.
