import re
from datetime import datetime


def parse_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def alarm_row_dt(row: dict) -> datetime | None:
    dt = parse_dt(str(row.get("BeginTime", "")))
    if dt is not None:
        return dt
    fname = str(row.get("FileName", ""))
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/\d{3}/(\d{2})\.(\d{2})\.(\d{2})-", fname)
    if not m:
        return None
    date_s, hh, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4)
    return parse_dt(f"{date_s} {hh}:{mm}:{ss}")


def _alarm_duration_sec(row: dict) -> int:
    bt = alarm_row_dt(row)
    et = parse_dt(str(row.get("EndTime", "")))
    if bt is None or et is None:
        return 0
    return max(0, int((et - bt).total_seconds()))


def _alarm_best_score(row: dict) -> tuple[int, int, int, str]:
    ftype = str(row.get("__type", "") or "").lower()
    type_score = 1 if ftype == "h264" else 0
    duration_score = _alarm_duration_sec(row)
    raw_size = row.get("CstSize", 0)
    try:
        size_score = int(raw_size)
    except Exception:
        size_score = 0
    return (type_score, duration_score, size_score, str(row.get("FileName", "")))


def choose_best_alarm_events(
    jpg_files: list[dict], h264_files: list[dict], cluster_gap_sec: int = 30
) -> list[dict]:
    rows = []
    for r in jpg_files:
        rr = dict(r)
        rr["__type"] = "jpg"
        rows.append(rr)
    for r in h264_files:
        rr = dict(r)
        rr["__type"] = "h264"
        rows.append(rr)

    rows = [r for r in rows if alarm_row_dt(r) is not None]
    rows.sort(key=lambda x: alarm_row_dt(x) or datetime.min, reverse=True)
    if not rows:
        return []

    gap = max(1, int(cluster_gap_sec))
    clusters: list[list[dict]] = []
    for row in rows:
        row_dt = alarm_row_dt(row)
        if row_dt is None:
            continue
        if not clusters:
            clusters.append([row])
            continue
        prev = clusters[-1][-1]
        prev_dt = alarm_row_dt(prev)
        if prev_dt is None:
            clusters[-1].append(row)
            continue
        if abs(int((prev_dt - row_dt).total_seconds())) <= gap:
            clusters[-1].append(row)
        else:
            clusters.append([row])

    picked = [max(cluster, key=_alarm_best_score) for cluster in clusters if cluster]
    picked.sort(key=lambda x: alarm_row_dt(x) or datetime.min, reverse=True)
    return picked
