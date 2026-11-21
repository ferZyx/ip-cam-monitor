import unittest

from server import resolve_scan_subnet


class CameraScanSubnetTests(unittest.TestCase):
    """Проверяет выбор подсети для поиска камеры (важно для запуска в Docker)."""

    def test_uses_local_ip_when_override_is_empty(self):
        """Без CAMERA_SCAN_SUBNET подсеть берётся из IP машины."""
        self.assertEqual(resolve_scan_subnet("", "192.168.1.42"), "192.168.1")

    def test_override_wins_over_container_ip(self):
        """Подсеть из переменной важнее внутреннего IP контейнера."""
        self.assertEqual(
            resolve_scan_subnet("192.168.100", "172.17.0.2"),
            "192.168.100",
        )

    def test_accepts_cidr_and_trailing_dot_forms(self):
        """Поддерживает записи вида 192.168.100.0/24 и 192.168.100."""
        self.assertEqual(
            resolve_scan_subnet("192.168.100.0/24", "172.17.0.2"),
            "192.168.100",
        )
        self.assertEqual(
            resolve_scan_subnet(" 192.168.100. ", "172.17.0.2"),
            "192.168.100",
        )

    def test_invalid_override_falls_back_to_local_ip(self):
        """Некорректное значение не ломает поиск, а откатывается к IP машины."""
        with self.assertLogs("stream_viewer", level="WARNING"):
            self.assertEqual(
                resolve_scan_subnet("192.168.300", "10.0.0.5"),
                "10.0.0",
            )
        with self.assertLogs("stream_viewer", level="WARNING"):
            self.assertEqual(
                resolve_scan_subnet("camera-net", "10.0.0.5"),
                "10.0.0",
            )


if __name__ == "__main__":
    unittest.main()
