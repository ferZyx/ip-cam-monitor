import logging
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
    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        transport: str = "tcp",
        log_to_console: bool = False,
        logger: logging.Logger | None = None,
    ):
        self.ffmpeg_bin = ffmpeg_bin
        self.transport = transport
        self.log_to_console = log_to_console
        self.logger = logger
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._source_url: str | None = None
        self._target_url: str | None = None
        self._output_thread: threading.Thread | None = None

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
                stdout=(subprocess.PIPE if self.log_to_console else subprocess.DEVNULL),
                stderr=(
                    subprocess.STDOUT if self.log_to_console else subprocess.DEVNULL
                ),
                text=self.log_to_console,
                bufsize=(1 if self.log_to_console else -1),
            )
            self._source_url = source_rtsp_url
            self._target_url = target_url
            if self.log_to_console:
                self._output_thread = threading.Thread(
                    target=self._pipe_ffmpeg_output,
                    args=(self._process,),
                    daemon=True,
                    name="remote_push_logs",
                )
                self._output_thread.start()

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
            self._output_thread = None
            return

        process = self._process
        self._process = None
        self._source_url = None
        self._target_url = None
        self._output_thread = None

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _pipe_ffmpeg_output(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                if self.logger is not None:
                    self.logger.info(f"[remote_push] {line}")
                else:
                    print(f"[remote_push] {line}")
        except Exception:
            return
