"""Hybrid alarm photo extraction.

This camera exposes alarm-index entries as `/idea1/... .jpg`, but those files often are not
real JPEG bytes. In practice many of them are short H264-like streams.

Strategy:

1) Find closest `/idea1` alarm entry around the target timestamp.
2) Download it via OPPlayBack DownloadStart.
3) Extract a good frame as JPEG (treating payload as video stream).
4) If the extracted frame looks corrupted (notably: big white bottom), fall back to the
   matching motion clip `/idea0/... .h264` and extract from there.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta


try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None  # type: ignore
    np = None  # type: ignore


try:
    from dvrip import DVRIPCam

    HAS_DVRIP = True
except Exception:
    DVRIPCam = None  # type: ignore
    HAS_DVRIP = False


try:
    # When running as `py server.py` inside `stream_viewer/`
    from alarm_photo_extractor import (
        download_motion_file_h264,
        extract_best_jpeg_from_motion_h264,
    )
except ModuleNotFoundError:
    # When importing as a package
    from stream_viewer.alarm_photo_extractor import (  # type: ignore
        download_motion_file_h264,
        extract_best_jpeg_from_motion_h264,
    )


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _bottom_white_ratio(jpeg_bytes: bytes, bottom_frac: float = 0.35) -> float | None:
    if (cv2 is None) or (np is None):
        return None
    try:
        img = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h = int(img.shape[0])
        cut = int(h * (1.0 - max(0.05, min(0.9, float(bottom_frac)))))
        gray = cv2.cvtColor(img[cut:, :, :], cv2.COLOR_BGR2GRAY)
        return float((gray > 245).mean())
    except Exception:
        return None


def _opfilequery(cam, begin: str, end: str, *, event: str, ftype: str) -> list[dict]:
    payload = {
        "Name": "OPFileQuery",
        "OPFileQuery": {
            "BeginTime": begin,
            "EndTime": end,
            "Channel": 0,
            "DriverTypeMask": "0x0000FFFF",
            "Event": event,
            "Type": ftype,
            "StreamType": "Main",
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
    out: list[dict] = []
    for r in data:
        if isinstance(r, dict):
            out.append(r)
    return out


def _pick_closest_file(rows: list[dict], target: datetime) -> dict | None:
    best = None
    best_key = None
    for r in rows:
        bt = _parse_dt(str(r.get("BeginTime", "")))
        if bt is None:
            continue
        et = _parse_dt(str(r.get("EndTime", "")))
        in_range = False
        if et is not None:
            in_range = bt <= target <= et
        delta = abs((bt - target).total_seconds())
        key = (0 if in_range else 1, delta)
        if best is None or (best_key is not None and key < best_key):
            best = r
            best_key = key
    return best


def _download_with_retries(
    *,
    ip: str,
    port: int,
    username: str,
    password: str,
    filename: str,
    begin_time: str,
    end_time: str,
    timeout_sec: int,
    retries: int,
    debug_dir: str | None,
) -> bytes:
    bt = str(begin_time or "").strip()
    et = str(end_time or "").strip()
    if bt and (not et or et == bt):
        # Some firmwares misbehave when StartTime==EndTime.
        dt = _parse_dt(bt)
        if dt is not None:
            et = (dt + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            et = bt

    last_err: Exception | None = None
    tries = max(1, int(retries))
    for attempt in range(1, tries + 1):
        try:
            return download_motion_file_h264(
                ip=ip,
                port=port,
                username=username,
                password=password,
                filename=filename,
                begin_time=bt,
                end_time=et,
                debug_dir=debug_dir,
                timeout_sec=int(timeout_sec),
            )
        except Exception as e:
            last_err = e
            if attempt < tries:
                time.sleep(0.8)
                continue
            raise
    raise RuntimeError(str(last_err) if last_err else "download_failed")


def extract_alarm_photo_hybrid(
    ip: str,
    target_dt: datetime,
    *,
    dvrip_port: int,
    username: str,
    password: str,
    debug_dir_root: str | None = None,
    debug: bool = False,
    timeout_sec: int = 30,
    download_retries: int = 2,
    bottom_white_threshold: float = 0.25,
) -> tuple[bytes | None, dict]:
    """Hybrid extractor returning (jpeg|None, meta)."""

    meta: dict = {
        "ok": False,
        "reason": "init",
        "target": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "chosen": None,
        "idea1": None,
        "motion": None,
    }

    if (not HAS_DVRIP) or (DVRIPCam is None):
        meta["reason"] = "python-dvr_not_available"
        return None, meta
    if not password:
        meta["reason"] = "empty_password"
        return None, meta

    alarm_id = target_dt.strftime("%Y-%m-%d_%H_%M_%S")
    base_debug_dir = None
    if debug and debug_dir_root:
        base_debug_dir = os.path.join(debug_dir_root, f"debug_hybrid_{alarm_id}")
        try:
            os.makedirs(base_debug_dir, exist_ok=True)
        except Exception:
            base_debug_dir = None

    # 1) Try idea1 alarm file
    idea1_jpeg = None
    idea1_meta: dict = {}
    cam = None
    try:
        cam = DVRIPCam(ip, port=dvrip_port, user=username, password=password)
        if not cam.login():
            meta["reason"] = "dvrip_login_failed"
            return None, meta

        begin = (target_dt - timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S")
        end = (target_dt + timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S")
        rows = _opfilequery(cam, begin, end, event="*", ftype="jpg")
        picked = _pick_closest_file(rows, target_dt)
        idea1_meta = {
            "window": {"begin": begin, "end": end},
            "count": len(rows),
            "picked": picked,
        }

        if picked:
            fname = str(picked.get("FileName", ""))
            bt = str(picked.get("BeginTime", ""))
            et = str(picked.get("EndTime", "")) or bt

            dbg = None
            if base_debug_dir:
                dbg = os.path.join(base_debug_dir, "idea1")

            raw = _download_with_retries(
                ip=ip,
                port=dvrip_port,
                username=username,
                password=password,
                filename=fname,
                begin_time=bt,
                end_time=et,
                timeout_sec=timeout_sec,
                retries=download_retries,
                debug_dir=dbg,
            )

            # Treat as video stream, sample more frames.
            sample = [0, 1, 2, 3, 5, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32]
            res = extract_best_jpeg_from_motion_h264(
                raw, debug_dir=dbg, sample_frame_indexes=sample, score_mode="content"
            )
            if res.ok and res.jpeg_bytes:
                bw = _bottom_white_ratio(res.jpeg_bytes)
                idea1_jpeg = res.jpeg_bytes
                idea1_meta.update(
                    {
                        "download": {"ok": True, "bytes": len(raw)},
                        "extract": {
                            "ok": True,
                            "reason": res.reason,
                            "chosen_frame_index": res.chosen_frame_index,
                            "bottom_white_ratio": bw,
                        },
                    }
                )
            else:
                idea1_meta.update(
                    {
                        "download": {"ok": True, "bytes": len(raw)},
                        "extract": {"ok": False, "reason": res.reason},
                    }
                )
    except Exception as e:
        idea1_meta.update({"error": str(e)})
    finally:
        if cam:
            try:
                cam.close()
            except Exception:
                pass

    meta["idea1"] = idea1_meta

    # Decide if we accept idea1
    if idea1_jpeg is not None:
        bw = None
        try:
            bw = meta["idea1"].get("extract", {}).get("bottom_white_ratio")
        except Exception:
            bw = None
        if (bw is not None) and (float(bw) < float(bottom_white_threshold)):
            meta["ok"] = True
            meta["reason"] = "ok"
            meta["chosen"] = "idea1"
            return idea1_jpeg, meta

    # 2) Fallback to motion clip (idea0 h264)
    motion_jpeg = None
    motion_meta: dict = {}
    cam = None
    try:
        cam = DVRIPCam(ip, port=dvrip_port, user=username, password=password)
        if not cam.login():
            motion_meta["error"] = "dvrip_login_failed"
        else:
            begin = (target_dt - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
            end = (target_dt + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
            rows = _opfilequery(cam, begin, end, event="M", ftype="h264")
            picked = _pick_closest_file(rows, target_dt)
            motion_meta = {
                "window": {"begin": begin, "end": end},
                "count": len(rows),
                "picked": picked,
            }
            if picked:
                fname = str(picked.get("FileName", ""))
                bt = str(picked.get("BeginTime", ""))
                et = str(picked.get("EndTime", "")) or bt

                dbg = None
                if base_debug_dir:
                    dbg = os.path.join(base_debug_dir, "motion")

                raw = _download_with_retries(
                    ip=ip,
                    port=dvrip_port,
                    username=username,
                    password=password,
                    filename=fname,
                    begin_time=bt,
                    end_time=et,
                    timeout_sec=timeout_sec,
                    retries=download_retries,
                    debug_dir=dbg,
                )

                sample = [0, 10, 30, 60, 90, 120, 150, 180]
                res = extract_best_jpeg_from_motion_h264(
                    raw,
                    debug_dir=dbg,
                    sample_frame_indexes=sample,
                    score_mode="sharpness",
                )
                if res.ok and res.jpeg_bytes:
                    bw = _bottom_white_ratio(res.jpeg_bytes)
                    motion_jpeg = res.jpeg_bytes
                    motion_meta.update(
                        {
                            "download": {"ok": True, "bytes": len(raw)},
                            "extract": {
                                "ok": True,
                                "reason": res.reason,
                                "chosen_frame_index": res.chosen_frame_index,
                                "bottom_white_ratio": bw,
                            },
                        }
                    )
                else:
                    motion_meta.update(
                        {
                            "download": {"ok": True, "bytes": len(raw)},
                            "extract": {"ok": False, "reason": res.reason},
                        }
                    )
    except Exception as e:
        motion_meta.update({"error": str(e)})
    finally:
        if cam:
            try:
                cam.close()
            except Exception:
                pass

    meta["motion"] = motion_meta

    if motion_jpeg is not None:
        meta["ok"] = True
        meta["reason"] = "ok_fallback_motion"
        meta["chosen"] = "motion"
        return motion_jpeg, meta

    # Last resort: return idea1 if we at least got something
    if idea1_jpeg is not None:
        meta["ok"] = True
        meta["reason"] = "ok_but_bottom_white"
        meta["chosen"] = "idea1"
        return idea1_jpeg, meta

    meta["reason"] = "no_photo"
    return None, meta
