import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


import cv2
import numpy as np

from dvrip import DVRIPCam

from alarm_photo_extractor import (  # type: ignore
    download_motion_file_h264,
    _extract_media_from_1426,
)


CAMERA_IP = os.getenv("CAMERA_IP", "192.168.100.9")
DVRIP_PORT = int(os.getenv("DVRIP_PORT", "34567"))
CAMERA_USER = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _gray_ratio_bgr(frame) -> float:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    return float((s < 18).mean())


def _sharpness_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _contrast_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def _bottom_white_ratio_bgr(frame, bottom_frac: float = 0.35) -> float:
    h = int(frame.shape[0])
    cut = int(h * (1.0 - max(0.05, min(0.9, float(bottom_frac)))))
    gray = cv2.cvtColor(frame[cut:, :, :], cv2.COLOR_BGR2GRAY)
    return float((gray > 245).mean())


@dataclass
class Candidate:
    idx: int
    score: float
    sharpness: float
    contrast: float
    gray_ratio: float
    bottom_white_ratio: float


def _score_frame(frame) -> tuple[float, dict]:
    sharp = _sharpness_bgr(frame)
    cont = _contrast_bgr(frame)
    gray_r = _gray_ratio_bgr(frame)
    bw = _bottom_white_ratio_bgr(frame, bottom_frac=0.35)

    # Prefer content. Do NOT bake bottom-white penalty into the score here.
    # Bottom corruption is handled separately as a gating/fallback decision.
    score = (sharp + cont * 4.0) * max(0.0, 1.0 - gray_r)
    return float(score), {
        "sharpness": sharp,
        "contrast": cont,
        "gray_ratio": gray_r,
        "bottom_white_ratio": bw,
        "score": float(score),
    }


def extract_best_from_media(
    media: bytes, sample_indexes: list[int], out_dir: Path
) -> tuple[bytes | None, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    media_path = out_dir / "media.h264"
    media_path.write_bytes(media)

    cap = cv2.VideoCapture(str(media_path), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return None, {"ok": False, "reason": "opencv_failed_to_open"}

    best = None
    best_meta = None

    # Read sequentially up to max index (avoid seeking: these streams are flaky)
    targets = sorted(set(int(x) for x in sample_indexes if int(x) >= 0))
    if not targets:
        targets = [0]
    max_idx = targets[-1]
    tset = set(targets)

    idx = 0
    candidates: list[dict] = []
    while idx <= max_idx:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if idx in tset:
            s, m = _score_frame(frame)
            name = f"cand_{idx:04d}_score{s:.1f}_bw{m['bottom_white_ratio']:.3f}_g{m['gray_ratio']:.3f}.jpg"
            cv2.imwrite(str(out_dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            candidates.append({"idx": idx, "file": name, **m})
            if best is None or (
                best_meta is not None and s > float(best_meta["score"])
            ):
                okj, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if okj:
                    best = jb.tobytes()
                    best_meta = {"idx": idx, **m}
        idx += 1

    cap.release()
    if best is None:
        return None, {
            "ok": False,
            "reason": "no_frames_decoded",
            "candidates": candidates,
        }
    return best, {"ok": True, "best": best_meta, "candidates": candidates}


def opfilequery(cam, begin: str, end: str, event: str, ftype: str) -> list[dict]:
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
    return [r for r in data if isinstance(r, dict)]


def _download_with_retries(
    *,
    ip: str,
    port: int,
    username: str,
    password: str,
    filename: str,
    begin_time: str,
    end_time: str,
    debug_dir: str,
    timeout_sec: int,
    retries: int,
) -> bytes:
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
                begin_time=begin_time,
                end_time=end_time,
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


def pick_motion_file(rows: list[dict], target: datetime) -> dict | None:
    best = None
    best_key = None
    for r in rows:
        bt_s = str(r.get("BeginTime", ""))
        et_s = str(r.get("EndTime", ""))
        try:
            bt = _parse_dt(bt_s)
        except Exception:
            continue
        try:
            et = _parse_dt(et_s) if et_s else None
        except Exception:
            et = None
        in_range = False
        if et is not None:
            in_range = bt <= target <= et
        delta = abs((bt - target).total_seconds())
        key = (0 if in_range else 1, delta)
        if best is None or (best_key is not None and key < best_key):
            best = r
            best_key = key
    return best


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Hybrid research: decode /idea1 alarm file; if bottom is white/corrupted, try motion clip frame"
    )
    ap.add_argument("--ip", default=CAMERA_IP)
    ap.add_argument("--port", type=int, default=DVRIP_PORT)
    ap.add_argument("--user", default=CAMERA_USER)
    ap.add_argument("--password", default=CAMERA_PASS)
    ap.add_argument("--file", required=True, help="/idea1/... .jpg")
    ap.add_argument("--begin", required=True)
    ap.add_argument("--end", default="")
    ap.add_argument("--timeout-sec", type=int, default=30)
    ap.add_argument("--download-retries", type=int, default=2)
    ap.add_argument("--white-bottom-threshold", type=float, default=0.25)
    args = ap.parse_args()

    if not args.password:
        print("ERROR: CAMERA_PASS is empty. Set it in stream_viewer/.env")
        return 2

    now = time.strftime("%Y%m%d_%H%M%S")
    out_root = Path(__file__).resolve().parent / "output"
    run_dir = out_root / f"idea1_hybrid_{now}"
    run_dir.mkdir(parents=True, exist_ok=True)

    end_time = (args.end or args.begin).strip()
    alarm_dt = _parse_dt(args.begin)

    report: dict = {
        "idea1": {"file": args.file, "begin": args.begin, "end": end_time},
        "timeout_sec": int(args.timeout_sec),
        "white_bottom_threshold": float(args.white_bottom_threshold),
        "idea1_result": None,
        "motion": None,
        "motion_result": None,
        "chosen": None,
    }

    # 1) Download idea1 file and extract best frame (with candidate dumps)
    raw1 = _download_with_retries(
        ip=args.ip,
        port=int(args.port),
        username=args.user,
        password=args.password,
        filename=str(args.file),
        begin_time=str(args.begin),
        end_time=str(end_time),
        debug_dir=str(run_dir / "idea1_download_debug"),
        timeout_sec=int(args.timeout_sec),
        retries=int(args.download_retries),
    )
    (run_dir / "idea1_raw_1426.bin").write_bytes(raw1)
    media1 = _extract_media_from_1426(raw1)
    (run_dir / "idea1_media_extracted.bin").write_bytes(media1)

    # Wider sample set to increase chance of a good frame
    idea1_samples = [0, 1, 2, 3, 5, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32]
    idea1_jpeg, idea1_meta = extract_best_from_media(
        media1, idea1_samples, run_dir / "idea1_candidates"
    )
    report["idea1_result"] = idea1_meta

    if idea1_jpeg:
        (run_dir / "idea1_best.jpg").write_bytes(idea1_jpeg)

    # If idea1 best has big white bottom, try motion clip and pick a stable middle frame.
    need_motion = False
    if idea1_meta.get("ok") and idea1_meta.get("best"):
        bw = float(idea1_meta["best"].get("bottom_white_ratio", 0.0))
        if bw >= float(args.white_bottom_threshold):
            need_motion = True
    else:
        need_motion = True

    if need_motion:
        cam = DVRIPCam(
            args.ip, port=int(args.port), user=args.user, password=args.password
        )
        if not cam.login():
            report["motion"] = {"error": "dvrip_login_failed"}
        else:
            try:
                win_begin = (alarm_dt - timedelta(minutes=3)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                win_end = (alarm_dt + timedelta(minutes=3)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                rows = opfilequery(cam, win_begin, win_end, event="M", ftype="h264")
                picked = pick_motion_file(rows, alarm_dt)
                report["motion"] = {
                    "window": {"begin": win_begin, "end": win_end},
                    "count": len(rows),
                    "picked": picked,
                }

                if picked:
                    mf = str(picked.get("FileName", ""))
                    mb = str(picked.get("BeginTime", ""))
                    me = str(picked.get("EndTime", ""))
                    rawm = _download_with_retries(
                        ip=args.ip,
                        port=int(args.port),
                        username=args.user,
                        password=args.password,
                        filename=mf,
                        begin_time=mb,
                        end_time=me or mb,
                        debug_dir=str(run_dir / "motion_download_debug"),
                        timeout_sec=int(args.timeout_sec),
                        retries=int(args.download_retries),
                    )
                    (run_dir / "motion_raw_1426.bin").write_bytes(rawm)
                    mediam = _extract_media_from_1426(rawm)
                    (run_dir / "motion_media_extracted.bin").write_bytes(mediam)

                    # Sample later frames to reduce "missing refs" artifacts
                    motion_samples = [0, 10, 30, 60, 90, 120, 150, 180]
                    mjpeg, mmeta = extract_best_from_media(
                        mediam, motion_samples, run_dir / "motion_candidates"
                    )
                    report["motion_result"] = mmeta
                    if mjpeg:
                        (run_dir / "motion_best.jpg").write_bytes(mjpeg)
            finally:
                try:
                    cam.close()
                except Exception:
                    pass

    # Choose output
    chosen = None
    if idea1_jpeg and report.get("idea1_result", {}).get("best"):
        bw = float(report["idea1_result"]["best"].get("bottom_white_ratio", 0.0))
        if bw < float(args.white_bottom_threshold):
            chosen = "idea1"
        elif report.get("motion_result", {}).get("ok"):
            chosen = "motion"
        else:
            chosen = "idea1"
    elif report.get("motion_result", {}).get("ok"):
        chosen = "motion"

    report["chosen"] = chosen
    if chosen == "idea1" and idea1_jpeg:
        (run_dir / "chosen.jpg").write_bytes(idea1_jpeg)
    if chosen == "motion":
        try:
            mj = (run_dir / "motion_best.jpg").read_bytes()
            (run_dir / "chosen.jpg").write_bytes(mj)
        except Exception:
            pass

    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Output:", str(run_dir))
    print("Chosen:", chosen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
