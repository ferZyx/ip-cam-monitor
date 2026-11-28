import unittest
from unittest import mock

import cv2

import server


class RtspCaptureTimeoutTests(unittest.TestCase):
    """Проверяет, что таймауты RTSP передаются в OpenCV при открытии потока."""

    def test_read_timeout_follows_reconnect_threshold(self):
        """Таймаут чтения кадра равен порогу переподключения в секундах."""
        params = server.build_capture_params(5)
        pairs = dict(zip(params[::2], params[1::2]))
        self.assertEqual(pairs[cv2.CAP_PROP_READ_TIMEOUT_MSEC], 5000)
        self.assertEqual(pairs[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC], server.RTSP_OPEN_TIMEOUT_MSEC)

    def test_zero_threshold_does_not_disable_read_timeout(self):
        """Нулевой порог не превращается в бесконечное ожидание."""
        params = server.build_capture_params(0)
        pairs = dict(zip(params[::2], params[1::2]))
        self.assertEqual(pairs[cv2.CAP_PROP_READ_TIMEOUT_MSEC], 1000)

    def test_try_rtsp_passes_params_to_constructor(self):
        """try_rtsp открывает VideoCapture сразу с таймаутами (cap.set после открытия не работает)."""
        fake_cap = mock.Mock()
        fake_cap.isOpened.return_value = False
        with mock.patch.object(server.cv2, "VideoCapture", return_value=fake_cap) as ctor:
            self.assertIsNone(server.try_rtsp("192.0.2.1"))
        for call in ctor.call_args_list:
            self.assertEqual(call.args[1], cv2.CAP_FFMPEG)
            self.assertEqual(call.args[2], server.build_capture_params())


if __name__ == "__main__":
    unittest.main()
