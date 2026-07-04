import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

try:
    from yellow_box_detector import DEFAULT_MIN_CONFIDENCE, detect_yellow_box_in_image
except ModuleNotFoundError:
    from stream_viewer.yellow_box_detector import (  # type: ignore
        DEFAULT_MIN_CONFIDENCE,
        detect_yellow_box_in_image,
    )


@dataclass(frozen=True)
class FrameSnapshot:
    """Снимок последнего кадра камеры для фоновой детекции."""

    frame_count: int
    jpeg_bytes: bytes
    bgr_frame: object | None = None


@dataclass(frozen=True)
class TelegramAlert:
    """Сообщение Telegram с текстом и JPEG-кадром."""

    text: str
    photo_bytes: bytes


class RateLimitedTelegramQueue:
    """Фоновая очередь Telegram-отправок с ограничением сообщений в минуту."""

    def __init__(
        self,
        send_func: Callable[[str, bytes | None], None],
        rate_per_minute: int = 20,
        max_queue_size: int = 20,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ):
        """Создаёт очередь с bounded-буфером и rate-limit отправкой."""
        self._send_func = send_func
        self._rate_per_minute = max(0, int(rate_per_minute))
        self._min_interval_sec = (
            60.0 / float(self._rate_per_minute) if self._rate_per_minute else 0.0
        )
        self._queue: queue.Queue[TelegramAlert] = queue.Queue(
            maxsize=max(1, int(max_queue_size))
        )
        self._clock = clock
        self._sleeper = sleeper
        self._logger = logger or logging.getLogger(__name__)
        self._last_sent_at: float | None = None
        self._dropped_count = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(self, text: str, photo_bytes: bytes | None = None) -> bool:
        """Кладёт текст и фото в очередь отправки."""
        return self.enqueue_alert(
            TelegramAlert(text=text, photo_bytes=photo_bytes or b"")
        )

    def enqueue_alert(self, alert: TelegramAlert) -> bool:
        """Кладёт готовую тревогу в очередь с удалением старого элемента."""
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._dropped_count += 1
            except queue.Empty:
                pass

        try:
            self._queue.put_nowait(alert)
            return True
        except queue.Full:
            self._dropped_count += 1
            return False

    def queued_count(self) -> int:
        """Возвращает текущий размер очереди."""
        return self._queue.qsize()

    def dropped_count(self) -> int:
        """Возвращает количество отброшенных устаревших сообщений."""
        return self._dropped_count

    def start(self, name: str = "telegram_alert_queue") -> None:
        """Запускает фоновый поток отправки Telegram-сообщений."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=name,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает фоновый поток отправки."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def send_next(self, timeout: float = 0.5) -> bool:
        """Отправляет один элемент очереди с учётом rate limit."""
        try:
            alert = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False

        try:
            self._wait_for_rate_limit()
            self._send_func(alert.text, alert.photo_bytes)
            self._last_sent_at = self._clock()
            return True
        except Exception as exc:
            self._logger.warning(f"Telegram queue send error: {exc}")
            return False

    def _wait_for_rate_limit(self) -> None:
        """Выдерживает минимальную паузу между Telegram-отправками."""
        if self._last_sent_at is None or self._min_interval_sec <= 0:
            return

        wait_sec = (self._last_sent_at + self._min_interval_sec) - self._clock()
        if wait_sec > 0:
            self._sleeper(wait_sec)

    def _worker_loop(self) -> None:
        """Постоянно отправляет сообщения из очереди в отдельном потоке."""
        while not self._stop_event.is_set():
            self.send_next(timeout=0.5)


def resize_for_detection(image, max_width: int):
    """Уменьшает кадр для дешёвой детекции, сохраняя пропорции."""
    if image is None or max_width <= 0:
        return image

    height, width = image.shape[:2]
    if width <= max_width:
        return image

    scale = max_width / float(width)
    target_size = (max_width, max(1, int(round(height * scale))))
    return cv2.resize(image, target_size, interpolation=cv2.INTER_AREA)


def decode_jpeg(jpeg_bytes: bytes):
    """Декодирует JPEG-байты в BGR-кадр OpenCV."""
    if not jpeg_bytes:
        return None

    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def snapshot_to_detection_image(snapshot: FrameSnapshot, max_width: int):
    """Возвращает BGR-кадр для детекции из BGR или JPEG-снимка."""
    image = snapshot.bgr_frame
    if image is None:
        image = decode_jpeg(snapshot.jpeg_bytes)
    return resize_for_detection(image, max_width=max_width)


def format_alert_text(result: dict) -> str:
    """Формирует короткую подпись Telegram для найденной жёлтой рамки."""
    confidence = float(result.get("confidence", 0.0))
    boxes = result.get("boxes") or []
    return f"Жёлтая рамка обнаружена. confidence={confidence:.3f}, boxes={boxes}"


class YellowBoxFrameMonitor:
    """Фоновый монитор последнего кадра камеры на наличие жёлтой рамки."""

    def __init__(
        self,
        frame_provider: Callable[[], FrameSnapshot | None],
        alert_queue: Callable[[TelegramAlert], object],
        detector: Callable[[object, float], dict] = detect_yellow_box_in_image,
        check_interval_sec: float = 1.0,
        alert_min_interval_sec: float = 3.0,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_detection_width: int = 640,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ):
        """Создаёт монитор с независимым интервалом проверки и отправки тревог."""
        self._frame_provider = frame_provider
        self._alert_queue = alert_queue
        self._detector = detector
        self._check_interval_sec = max(0.1, float(check_interval_sec))
        self._alert_min_interval_sec = max(0.0, float(alert_min_interval_sec))
        self._min_confidence = float(min_confidence)
        self._max_detection_width = int(max_detection_width)
        self._clock = clock
        self._sleeper = sleeper
        self._logger = logger or logging.getLogger(__name__)
        self._last_frame_count: int | None = None
        self._last_alert_at: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, name: str = "yellow_box_monitor") -> None:
        """Запускает фоновый поток мониторинга."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            daemon=True,
            name=name,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает фоновый поток мониторинга."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def run_forever(self) -> None:
        """Проверяет кадры до остановки фонового потока."""
        while not self._stop_event.is_set():
            started_at = self._clock()
            try:
                self.run_once()
            except Exception as exc:
                self._logger.warning(f"Yellow box monitor error: {exc}")

            # Интервал считаем от старта проверки, чтобы не запускать детекцию чаще заданного.
            elapsed = self._clock() - started_at
            delay = max(0.0, self._check_interval_sec - elapsed)
            self._sleeper(delay)

    def run_once(self) -> bool:
        """Проверяет один новый кадр и ставит тревогу в очередь при детекции."""
        snapshot = self._frame_provider()
        if snapshot is None:
            return False

        if snapshot.frame_count == self._last_frame_count:
            return False
        self._last_frame_count = snapshot.frame_count

        image = snapshot_to_detection_image(snapshot, self._max_detection_width)
        if image is None:
            return False

        result = self._detector(image, self._min_confidence)
        if not result.get("has_yellow_box"):
            return False

        now = self._clock()
        if (
            self._last_alert_at is not None
            and now - self._last_alert_at < self._alert_min_interval_sec
        ):
            return False

        alert = TelegramAlert(
            text=format_alert_text(result),
            photo_bytes=snapshot.jpeg_bytes,
        )
        queued = self._alert_queue(alert)
        if queued is not False:
            self._last_alert_at = now
            return True
        return False
