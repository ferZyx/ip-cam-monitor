"""DVRIP alarm index helpers.

Goal: query recent alarms in a way that is robust to firmware truncation.

On this camera, alarms show up in OPFileQuery as `Type=jpg` entries with paths like:
  /idea1/YYYY-mm-dd/001/HH.MM.SS-HH.MM.SS[M][@..][..].jpg

We intentionally query in small windows going backwards from `end_dt` to avoid OPFileQuery
return caps. The caller can then compare against a known set and process new entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


try:
    from dvrip import DVRIPCam

    HAS_DVRIP = True
except Exception:
    DVRIPCam = None  # type: ignore
    HAS_DVRIP = False


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def opfilequery(
    cam, begin: str, end: str, *, event: str, ftype: str, stream: str = "Main"
) -> list[dict]:
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
    res = cam.send(1440, payload)
    if not res:
        return []
    data = res.get("OPFileQuery", res)
    if isinstance(data, dict):
        data = data.get("FileList", [])
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def normalize_rows(rows: list[dict], ftype: str) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        bt = _parse_dt(str(r.get("BeginTime", "")))
        if bt is None:
            continue
        rr = dict(r)
        rr["__dt"] = bt
        rr["__type"] = ftype
        out.append(rr)
    out.sort(key=lambda x: x["__dt"], reverse=True)
    return out


def fetch_recent_alarm_markers(
    *,
    ip: str,
    port: int,
    user: str,
    password: str,
    end_dt: datetime,
    want: int = 30,
    max_lookback_hours: int = 24,
    initial_chunk_minutes: int = 10,
    min_chunk_seconds: int = 60,
    cap_guard: int = 60,
) -> tuple[list[dict], dict]:
    """Fetch recent alarm markers (Type=jpg) in small backward windows.

    Returns (rows_sorted_desc, meta)
    """

    meta: dict = {
        "ok": False,
        "reason": "init",
        "chunks": [],
    }

    if (not HAS_DVRIP) or DVRIPCam is None:
        meta["reason"] = "python-dvr_not_available"
        return [], meta
    if not password:
        meta["reason"] = "empty_password"
        return [], meta

    cam = DVRIPCam(ip, port=int(port), user=user, password=password)
    if not cam.login():
        meta["reason"] = "dvrip_login_failed"
        return [], meta

    collected: list[dict] = []
    seen: set[str] = set()
    try:
        max_lookback = timedelta(hours=max(1, int(max_lookback_hours)))
        oldest_allowed = end_dt - max_lookback
        chunk = timedelta(minutes=max(1, int(initial_chunk_minutes)))
        min_chunk = timedelta(seconds=max(1, int(min_chunk_seconds)))

        cursor_end = end_dt
        while cursor_end > oldest_allowed and len(collected) < int(want):
            begin_dt = cursor_end - chunk
            if begin_dt < oldest_allowed:
                begin_dt = oldest_allowed

            begin = begin_dt.strftime("%Y-%m-%d %H:%M:%S")
            end = cursor_end.strftime("%Y-%m-%d %H:%M:%S")
            rows = opfilequery(cam, begin, end, event="*", ftype="jpg", stream="Main")
            norm = normalize_rows(rows, "jpg")

            meta["chunks"].append(
                {
                    "begin": begin,
                    "end": end,
                    "raw": len(rows),
                    "parsed": len(norm),
                    "chunk_sec": int(chunk.total_seconds()),
                }
            )

            # If we hit a cap, shrink the window and retry on the same end.
            if len(rows) >= int(cap_guard) and chunk > min_chunk:
                chunk = max(
                    min_chunk, timedelta(seconds=int(chunk.total_seconds() // 2))
                )
                continue

            for r in norm:
                key = f"{r.get('FileName', '')}|{r.get('BeginTime', '')}"
                if key in seen:
                    continue
                seen.add(key)
                collected.append(r)

            cursor_end = begin_dt
            if len(rows) < max(1, int(want) // 3):
                chunk = min(timedelta(hours=4), chunk * 2)

        collected.sort(key=lambda x: x["__dt"], reverse=True)
        meta["ok"] = True
        meta["reason"] = "ok"
        meta["count"] = len(collected)
        meta["max_time"] = (
            collected[0]["__dt"].strftime("%Y-%m-%d %H:%M:%S") if collected else None
        )
        return collected[: int(want)], meta
    finally:
        try:
            cam.close()
        except Exception:
            pass
