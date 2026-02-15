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
    bottom_white_ratio: float
    bottom_std: float
    score: float


def _sharpness_score_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _contrast_score_bgr(frame) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def _gray_ratio_bgr(frame) -> float:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    return float((s < 18).mean())


def _bottom_metrics_bgr(frame, bottom_frac: float) -> tuple[float, float]:
    h = int(frame.shape[0])
    cut = int(h * (1.0 - max(0.05, min(0.9, float(bottom_frac)))))
    roi = frame[cut:, :, :]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bottom_std = float(gray.std())
    bottom_white_ratio = float((gray > 245).mean())
    return bottom_white_ratio, bottom_std


def _score_frame(frame, idx: int, bottom_frac: float) -> FrameScore:
    sharp = _sharpness_score_bgr(frame)
    gray_r = _gray_ratio_bgr(frame)
    cont = _contrast_score_bgr(frame)
    bw, bstd = _bottom_metrics_bgr(frame, bottom_frac)

    # Penalize "white bottom" and "flat bottom" strongly.
    flat_penalty = 1.0 if bstd < 6.0 else 0.0
    score = sharp + cont * 5.0
    score *= max(0.0, 1.0 - gray_r)
    score -= bw * 900.0
    score -= flat_penalty * 250.0

    return FrameScore(idx, sharp, gray_r, cont, bw, bstd, float(score))


def _open_capture(
    media_bytes: bytes, out_dir: Path, codec: str
) -> tuple[cv2.VideoCapture | None, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if codec == "h264":
        path = out_dir / "media_extracted.h264"
    elif codec == "hevc":
        path = out_dir / "media_extracted.hevc"
    else:
        raise ValueError("codec must be h264 or hevc")
    path.write_bytes(media_bytes)
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return None, path
    return cap, path


def _scan_sequential(
    cap: cv2.VideoCapture,
    *,
    bottom_frac: float,
    max_frames: int,
    top_k: int,
) -> tuple[list[FrameScore], list[tuple[FrameScore, bytes]], dict]:
    scored: list[FrameScore] = []
    candidates: list[tuple[FrameScore, bytes]] = []

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

        fs = _score_frame(frame, idx, bottom_frac)
        scored.append(fs)
        decoded_ok += 1

        okj, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if okj:
            candidates.append((fs, jb.tobytes()))
            candidates.sort(key=lambda x: x[0].score, reverse=True)
            if len(candidates) > int(top_k):
                candidates = candidates[: int(top_k)]

    meta = {
        "read_calls": read_calls,
        "decoded_frames": decoded_ok,
        "scored_frames": len(scored),
    }
    return scored, candidates, meta


def pick_best_frame(
    media_bytes: bytes,
    *,
    out_dir: Path,
    bottom_frac: float,
    max_frames: int,
    top_k: int,
    force_codec: str,
    probe_frames: int,
) -> tuple[bytes | None, dict]:
    # Decide codec
    codecs = ["h264", "hevc"]
    if force_codec in ("h264", "hevc"):
        codecs = [force_codec]

    probe: dict[str, dict] = {}
    best_codec = None
    best_codec_score = None
    best_codec_meta = None
    best_candidates: list[tuple[FrameScore, bytes]] = []

    for c in codecs:
        cap, path = _open_capture(media_bytes, out_dir / f"probe_{c}", c)
        if cap is None:
            probe[c] = {"ok": False, "reason": "open_failed", "path": str(path)}
            continue

        scored, candidates, meta = _scan_sequential(
            cap,
            bottom_frac=bottom_frac,
            max_frames=int(probe_frames),
            top_k=max(5, int(top_k)),
        )
        cap.release()

        top_score = candidates[0][0].score if candidates else None
        probe[c] = {
            "ok": True,
            "path": str(path),
            "top_score": top_score,
            **meta,
        }

        if top_score is None:
            continue
        if best_codec is None or (
            best_codec_score is not None and top_score > best_codec_score
        ):
            best_codec = c
            best_codec_score = top_score
            best_codec_meta = probe[c]
            best_candidates = candidates

    if best_codec is None:
        return None, {"ok": False, "reason": "no_codec_opened", "probe": probe}

    # Full scan with chosen codec
    cap, full_path = _open_capture(
        media_bytes, out_dir / f"scan_{best_codec}", best_codec
    )
    if cap is None:
        return None, {"ok": False, "reason": "open_failed_after_probe", "probe": probe}

    scored, candidates, meta = _scan_sequential(
        cap,
        bottom_frac=bottom_frac,
        max_frames=int(max_frames),
        top_k=int(top_k),
    )
    cap.release()

    if not candidates:
        return None, {
            "ok": False,
            "reason": "no_candidates",
            "opened_as": best_codec,
            "probe": probe,
            "scan": meta,
        }

    # Save candidates for inspection
    saved_candidates: list[dict] = []
    for rank, (fs, jb) in enumerate(
        sorted(candidates, key=lambda x: x[0].score, reverse=True), start=1
    ):
        name = f"cand_{rank:02d}_idx{fs.idx:04d}_score{fs.score:.1f}_bw{fs.bottom_white_ratio:.3f}_g{fs.gray_ratio:.3f}.jpg"
        (out_dir / name).write_bytes(jb)
        saved_candidates.append(
            {
                "rank": rank,
                "idx": fs.idx,
                "sharpness": fs.sharpness,
                "gray_ratio": fs.gray_ratio,
                "contrast": fs.contrast,
                "bottom_white_ratio": fs.bottom_white_ratio,
                "bottom_std": fs.bottom_std,
                "score": fs.score,
                "file": name,
            }
        )

    best_fs, best_jb = sorted(candidates, key=lambda x: x[0].score, reverse=True)[0]
    result = {
        "ok": True,
        "opened_as": best_codec,
        "probe": probe,
        "scan": meta,
        "best": {
            "idx": best_fs.idx,
            "sharpness": best_fs.sharpness,
            "gray_ratio": best_fs.gray_ratio,
            "contrast": best_fs.contrast,
            "bottom_white_ratio": best_fs.bottom_white_ratio,
            "bottom_std": best_fs.bottom_std,
            "score": best_fs.score,
        },
        "candidates": saved_candidates,
        "full_path": str(full_path),
        "probe_choice": {
            "codec": best_codec,
            "top_score": best_codec_score,
            "meta": best_codec_meta,
        },
    }
    return best_jb, result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Research v2: choose less-gray/less-white-bottom frame from /idea1 alarm files"
    )
    ap.add_argument("--ip", default=CAMERA_IP)
    ap.add_argument("--port", type=int, default=DVRIP_PORT)
    ap.add_argument("--user", default=CAMERA_USER)
    ap.add_argument("--password", default=CAMERA_PASS)
    ap.add_argument("--file", required=True)
    ap.add_argument("--begin", required=True)
    ap.add_argument("--end", default="")
    ap.add_argument("--timeout-sec", type=int, default=30)
    ap.add_argument("--max-frames", type=int, default=320)
    ap.add_argument("--top-k", type=int, default=18)
    ap.add_argument("--bottom-frac", type=float, default=0.35)
    ap.add_argument("--force-codec", choices=["auto", "h264", "hevc"], default="auto")
    ap.add_argument("--probe-frames", type=int, default=60)
    args = ap.parse_args()

    if not args.password:
        print("ERROR: CAMERA_PASS is empty. Set it in stream_viewer/.env")
        return 2

    now = time.strftime("%Y%m%d_%H%M%S")
    out_root = Path(__file__).resolve().parent / "output"
    run_dir = out_root / f"idea1_frame_quality_v2_{now}"
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

    force = str(args.force_codec)
    if force == "auto":
        force = ""

    jpeg, meta = pick_best_frame(
        media,
        out_dir=run_dir,
        bottom_frac=float(args.bottom_frac),
        max_frames=int(args.max_frames),
        top_k=int(args.top_k),
        force_codec=force,
        probe_frames=int(args.probe_frames),
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
