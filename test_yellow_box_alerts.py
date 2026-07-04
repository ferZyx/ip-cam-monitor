import unittest

import cv2
import numpy as np

from yellow_box_alerts import (
    FrameSnapshot,
    RateLimitedTelegramQueue,
    YellowBoxFrameMonitor,
)


class FakeClock:
    """Тестовые часы с ручным продвижением времени."""

    def __init__(self, value=100.0):
        """Создаёт часы с заданным начальным временем."""
        self.value = value
        self.sleeps = []

    def now(self):
        """Возвращает текущее тестовое время."""
        return self.value

    def sleep(self, seconds):
        """Запоминает паузу и продвигает тестовое время."""
        self.sleeps.append(seconds)
        self.value += seconds


def make_frame():
    """Создаёт тестовый BGR-кадр с жёлтой рамкой."""
    image = np.full((240, 320, 3), (80, 120, 90), dtype=np.uint8)
    cv2.rectangle(image, (85, 45), (180, 190), (0, 255, 255), 5)
    return image


class RateLimitedTelegramQueueTests(unittest.TestCase):
    """Проверяет асинхронную очередь Telegram с лимитом отправки."""

    def test_send_next_waits_between_messages(self):
        """Отправляет второе сообщение только после rate-limit паузы."""
        sent = []
        clock = FakeClock()
        queue = RateLimitedTelegramQueue(
            send_func=lambda text, photo: sent.append((text, photo)),
            rate_per_minute=20,
            max_queue_size=10,
            clock=clock.now,
            sleeper=clock.sleep,
        )

        queue.enqueue("one", b"1")
        queue.enqueue("two", b"2")

        self.assertTrue(queue.send_next(timeout=0))
        self.assertTrue(queue.send_next(timeout=0))

        self.assertEqual(sent, [("one", b"1"), ("two", b"2")])
        self.assertEqual(clock.sleeps, [3.0])

    def test_enqueue_drops_oldest_message_when_queue_is_full(self):
        """Оставляет свежий кадр вместо устаревшего при переполнении."""
        sent = []
        queue = RateLimitedTelegramQueue(
            send_func=lambda text, photo: sent.append((text, photo)),
            rate_per_minute=0,
            max_queue_size=1,
        )

        self.assertTrue(queue.enqueue("old", b"old"))
        self.assertTrue(queue.enqueue("new", b"new"))
        self.assertTrue(queue.send_next(timeout=0))

        self.assertEqual(sent, [("new", b"new")])


class YellowBoxFrameMonitorTests(unittest.TestCase):
    """Проверяет мониторинг кадров на наличие жёлтой рамки."""

    def test_monitor_enqueues_alert_for_detected_yellow_box(self):
        """Кладёт JPEG текущего кадра в очередь при положительной детекции."""
        enqueued = []
        snapshot = FrameSnapshot(
            frame_count=1,
            jpeg_bytes=b"jpeg",
            bgr_frame=make_frame(),
        )
        monitor = YellowBoxFrameMonitor(
            frame_provider=lambda: snapshot,
            alert_queue=enqueued.append,
            detector=lambda image, min_confidence: {
                "has_yellow_box": True,
                "confidence": 0.9,
                "boxes": [[1, 2, 3, 4]],
            },
            clock=lambda: 100.0,
            sleeper=lambda seconds: None,
        )

        self.assertTrue(monitor.run_once())

        self.assertEqual(len(enqueued), 1)
        self.assertEqual(enqueued[0].photo_bytes, b"jpeg")
        self.assertIn("Жёлтая рамка", enqueued[0].text)

    def test_monitor_skips_repeated_frame_and_respects_alert_interval(self):
        """Не спамит очередь одинаковым кадром и новым кадром раньше интервала."""
        enqueued = []
        clock = FakeClock()
        frames = [
            FrameSnapshot(1, b"one", make_frame()),
            FrameSnapshot(1, b"one-again", make_frame()),
            FrameSnapshot(2, b"two", make_frame()),
            FrameSnapshot(3, b"three", make_frame()),
        ]

        def next_frame():
            """Возвращает следующий тестовый кадр."""
            return frames.pop(0)

        monitor = YellowBoxFrameMonitor(
            frame_provider=next_frame,
            alert_queue=enqueued.append,
            detector=lambda image, min_confidence: {
                "has_yellow_box": True,
                "confidence": 1.0,
                "boxes": [],
            },
            alert_min_interval_sec=3.0,
            clock=clock.now,
            sleeper=clock.sleep,
        )

        self.assertTrue(monitor.run_once())
        clock.value += 1.0
        self.assertFalse(monitor.run_once())
        clock.value += 1.0
        self.assertFalse(monitor.run_once())
        clock.value += 1.1
        self.assertTrue(monitor.run_once())

        self.assertEqual([item.photo_bytes for item in enqueued], [b"one", b"three"])


if __name__ == "__main__":
    unittest.main()
