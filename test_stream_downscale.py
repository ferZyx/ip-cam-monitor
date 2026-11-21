import unittest

import cv2
import numpy as np

from server import build_stream_jpeg, crop_frame


def _decode_shape(jpeg_bytes: bytes) -> tuple[int, int]:
    """Декодирует JPEG и возвращает (высота, ширина)."""
    img = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    return img.shape[:2]


def _make_frame(h: int, w: int):
    """Создаёт тестовый BGR-кадр с градиентом и его JPEG."""
    bgr = np.zeros((h, w, 3), dtype=np.uint8)
    bgr[:, :, 1] = np.linspace(0, 255, w, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return bgr, jpeg.tobytes()


class StreamDownscaleTests(unittest.TestCase):
    """Проверяет серверное уменьшение кадров MJPEG для браузера."""

    def setUp(self):
        """Готовит кадр в разрешении main-потока камеры (2304x2592)."""
        self.bgr, self.jpeg = _make_frame(2592, 2304)

    def test_full_frame_fits_into_box_keeping_aspect(self):
        """Полный кадр вписывается в 1920x1080 по высоте: 960x1080."""
        out = build_stream_jpeg(self.jpeg, self.bgr, "full", 1920, 1080, 80)
        self.assertEqual(_decode_shape(out), (1080, 960))
        self.assertLess(len(out), len(self.jpeg))

    def test_half_frame_is_cropped_before_scaling(self):
        """Половина кадра 2304x1296 сначала обрезается, затем уменьшается до 1920x1080."""
        for mode in ("top", "bottom"):
            out = build_stream_jpeg(self.jpeg, self.bgr, mode, 1920, 1080, 80)
            self.assertEqual(_decode_shape(out), (1080, 1920), mode)

    def test_small_frame_is_not_upscaled(self):
        """Кадр меньше рамки (sub-поток 640x720) не увеличивается."""
        bgr, jpeg = _make_frame(720, 640)
        out = build_stream_jpeg(jpeg, bgr, "full", 1920, 1080, 80)
        self.assertEqual(_decode_shape(out), (720, 640))

    def test_disabled_limits_return_original_for_full_mode(self):
        """При 0x0 полный кадр отдаётся как есть, без перекодирования."""
        out = build_stream_jpeg(self.jpeg, self.bgr, "full", 0, 0, 80)
        self.assertIs(out, self.jpeg)

    def test_decodes_jpeg_when_bgr_missing(self):
        """В DVRIP-режиме BGR нет — кадр декодируется из JPEG."""
        out = build_stream_jpeg(self.jpeg, None, "full", 1920, 1080, 80)
        self.assertEqual(_decode_shape(out), (1080, 960))

    def test_broken_jpeg_falls_back_to_original(self):
        """Битый JPEG без BGR не роняет поток — возвращаются исходные байты."""
        broken = b"not a jpeg"
        self.assertIs(build_stream_jpeg(broken, None, "top", 1920, 1080, 80), broken)

    def test_crop_frame_halves(self):
        """crop_frame делит кадр по высоте пополам."""
        self.assertEqual(crop_frame(self.bgr, "top").shape[:2], (1296, 2304))
        self.assertEqual(crop_frame(self.bgr, "bottom").shape[:2], (1296, 2304))
        self.assertIs(crop_frame(self.bgr, "full"), self.bgr)


if __name__ == "__main__":
    unittest.main()
