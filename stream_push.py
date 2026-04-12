import subprocess
import threading


def should_enable_push(target_url: str) -> bool:
    return bool(target_url and target_url.strip())


def _target_container(target_url: str) -> str:
    lowered = target_url.lower()
    if lowered.startswith("srt://"):
        return "mpegts"
    return "flv"


def build_ffmpeg_push_command(
    source_rtsp_url: str,
    target_url: str,
    transport: str = "tcp",
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    container = _target_container(target_url)
    return [
        ffmpeg_bin,
        "-nostdin",
        "-rtsp_transport",
        transport,
        "-i",
        source_rtsp_url,
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        container,
        target_url,
    ]


class RemotePushRelay:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", transport: str = "tcp"):
        self.ffmpeg_bin = ffmpeg_bin
        self.transport = transport
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._source_url: str | None = None
        self._target_url: str | None = None

    def ensure_running(self, source_rtsp_url: str, target_url: str) -> None:
        with self._lock:
            if not should_enable_push(target_url):
                self._stop_locked()
                return

            if (
                self._process is not None
                and self._process.poll() is None
                and self._source_url == source_rtsp_url
                and self._target_url == target_url
            ):
                return

            self._stop_locked()
            command = build_ffmpeg_push_command(
                source_rtsp_url=source_rtsp_url,
                target_url=target_url,
                transport=self.transport,
                ffmpeg_bin=self.ffmpeg_bin,
            )
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._source_url = source_rtsp_url
            self._target_url = target_url

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def status(self) -> dict:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {
                "enabled": should_enable_push(self._target_url or ""),
                "running": running,
                "target": self._target_url,
                "source": self._source_url,
            }

    def _stop_locked(self) -> None:
        if self._process is None:
            self._source_url = None
            self._target_url = None
            return

        process = self._process
        self._process = None
        self._source_url = None
        self._target_url = None

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
