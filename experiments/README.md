# Experiments

All research / investigation scripts live here.

Guidelines:

- Put any "try / research / debug" scripts into `stream_viewer/experiments/`.
- Keep runnable scripts self-contained.
- Write outputs into `stream_viewer/experiments/output/` (this folder is git-ignored).

Notes:

- Load env from `stream_viewer/.env` (one level up from this folder).

Current experiments:

- `telegram_send_probe.py` - sends one Telegram probe message using `.env` settings.
