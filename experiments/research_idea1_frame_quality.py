import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
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

from alarm_photo_extractor import (  # type: ignore
    download_motion_file_h264,
    _extract_media_from_1426,
)


CAMERA_IP = os.getenv("CAMERA_IP", "192.168.100.9")
DVRIP_PORT = int(os.getenv("DVRIP_PORT", "34567"))
CAMERA_USER = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")


@dataclass
class FrameScore:
    idx: int
    sharpness: float
    gray_ratio: float
    contrast: float
    score: float


def _sharpness_score_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _contrast_score_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def _gray_ratio_bgr(frame) -> float:
    """Heuristic: how much of the frame is low-saturation (gray-ish)."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    # low saturation threshold
    return float((s < 18).mean())


def pick_best_frame(
    media_bytes: bytes,
    *,
    max_frames: int,
    top_k: int,
    out_dir: Path,
) -> tuple[bytes | None, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # ffmpeg auto-detection depends on extension; many of these streams are H264-like.
    media_path_h264 = out_dir / "media_extracted.h264"
    media_path_hevc = out_dir / "media_extracted.hevc"
    media_path_h264.write_bytes(media_bytes)
    media_path_hevc.write_bytes(media_bytes)

    cap = None
    opened_as = None
    for path, label in ((media_path_h264, "h264"), (media_path_hevc, "hevc")):
        c = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        if c.isOpened():
            cap = c
            opened_as = label
            break
        c.release()

    if cap is None:
        return None, {
            "ok": False,
            "reason": "opencv_failed_to_open",
            "tried": ["h264", "hevc"],
        }

    # Decode sequentially; do not rely on seeking (many of these streams are not seekable).
    scored: list[FrameScore] = []
    candidates: list[tuple[FrameScore, bytes]] = []  # (score, jpeg_bytes)

    decoded_ok = 0
    consecutive_fail = 0
    read_calls = 0

    while decoded_ok < int(max_frames) and consecutive_fail < 80:
        ok, frame = cap.read()
        read_calls += 1
        if not ok or frame is None:
            consecutive_fail += 1
            continue
        consecutive_fail = 0

        idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        if idx < 0:
            idx = decoded_ok

        sharp = _sharpness_score_bgr(frame)
        gray_r = _gray_ratio_bgr(frame)
        cont = _contrast_score_bgr(frame)
        score = (sharp * 1.0 + cont * 5.0) * max(0.0, 1.0 - gray_r)

        fs = FrameScore(idx, sharp, gray_r, cont, score)
        scored.append(fs)
        decoded_ok += 1

        okj, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if okj:
            candidates.append((fs, jb.tobytes()))
            candidates.sort(key=lambda x: x[0].score, reverse=True)
            if len(candidates) > int(top_k):
                candidates = candidates[: int(top_k)]

    cap.release()

    if not scored:
        return None, {
            "ok": False,
            "reason": "no_frames_decoded",
            "opened_as": opened_as,
        }

    scored_sorted = sorted(scored, key=lambda x: x.score, reverse=True)
    best = scored_sorted[0]

    best_jpeg = None
    for fs, jb in candidates:
        if fs.idx == best.idx:
            best_jpeg = jb
            break
    if best_jpeg is None and candidates:
        best_jpeg = candidates[0][1]

    saved_candidates: list[dict] = []
    for rank, (fs, jb) in enumerate(
        sorted(candidates, key=lambda x: x[0].score, reverse=True), start=1
    ):
        name = (
            f"cand_{rank:02d}_idx{fs.idx:04d}_s{fs.score:.1f}_g{fs.gray_ratio:.3f}.jpg"
        )
        (out_dir / name).write_bytes(jb)
        saved_candidates.append(
            {
                "rank": rank,
                "idx": fs.idx,
                "sharpness": fs.sharpness,
                "gray_ratio": fs.gray_ratio,
                "contrast": fs.contrast,
                "score": fs.score,
                "file": name,
            }
        )

    meta = {
        "ok": True,
        "opened_as": opened_as,
        "best": {
            "idx": best.idx,
            "sharpness": best.sharpness,
            "gray_ratio": best.gray_ratio,
            "contrast": best.contrast,
            "score": best.score,
        },
        "read_calls": read_calls,
        "decoded_frames": decoded_ok,
        "candidates": saved_candidates,
    }
    return best_jpeg, meta


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Research: improve frame choice for /idea1 alarm files that look gray/corrupted"
    )
    ap.add_argument("--ip", default=CAMERA_IP)
    ap.add_argument("--port", type=int, default=DVRIP_PORT)
    ap.add_argument("--user", default=CAMERA_USER)
    ap.add_argument("--password", default=CAMERA_PASS)
    ap.add_argument(
        "--file", required=True, help="DVRIP FileName (e.g. /idea1/... .jpg)"
    )
    ap.add_argument("--begin", required=True, help="BeginTime YYYY-mm-dd HH:MM:SS")
    ap.add_argument("--end", default="", help="EndTime YYYY-mm-dd HH:MM:SS")
    ap.add_argument("--timeout-sec", type=int, default=30)
    ap.add_argument("--max-frames", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=12)
    args = ap.parse_args()

    if not args.password:
        print("ERROR: CAMERA_PASS is empty. Set it in stream_viewer/.env")
        return 2

    now = time.strftime("%Y%m%d_%H%M%S")
    out_root = Path(__file__).resolve().parent / "output"
    run_dir = out_root / f"idea1_frame_quality_{now}"
    run_dir.mkdir(parents=True, exist_ok=True)

    end_time = (args.end or args.begin).strip()
    raw_1426 = download_motion_file_h264(
        ip=args.ip,
        port=int(args.port),
        username=args.user,
        password=args.password,
        filename=str(args.file),
        begin_time=str(args.begin),
        end_time=end_time,
        debug_dir=str(run_dir / "download_debug"),
        timeout_sec=int(args.timeout_sec),
    )
    (run_dir / "raw_1426.bin").write_bytes(raw_1426)

    media = _extract_media_from_1426(raw_1426)
    (run_dir / "media_extracted.bin").write_bytes(media)

    jpeg, meta = pick_best_frame(
        media,
        max_frames=int(args.max_frames),
        top_k=int(args.top_k),
        out_dir=run_dir,
    )

    report = {
        "file": args.file,
        "begin": args.begin,
        "end": end_time,
        "raw_1426_bytes": len(raw_1426),
        "media_bytes": len(media),
        "result": meta,
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if jpeg:
        (run_dir / "best.jpg").write_bytes(jpeg)
        print("Saved:", str(run_dir / "best.jpg"))
    else:
        print("No best.jpg produced")

    print("Output:", str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
