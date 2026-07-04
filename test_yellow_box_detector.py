import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from yellow_box_detector import detect_yellow_box, detect_yellow_box_in_image


def make_scene(width=320, height=240):
    """Создает простую тестовую сцену без желтой рамки."""
    image = np.full((height, width, 3), (80, 120, 90), dtype=np.uint8)
    cv2.line(image, (0, height // 2), (width, height // 2), (140, 140, 140), 2)
    return image


def write_image(directory, image):
    """Сохраняет тестовое изображение во временную папку."""
    path = Path(directory) / "scene.jpg"
    self_check = cv2.imwrite(str(path), image)
    if not self_check:
        raise RuntimeError("Не удалось записать тестовое изображение")
    return path


class YellowBoxDetectorTests(unittest.TestCase):
    """Проверяет распознавание желтой прямоугольной рамки."""

    def test_detects_yellow_outline_rectangle(self):
        """Находит желтую рамку на изображении."""
        with tempfile.TemporaryDirectory() as directory:
            image = make_scene()
            cv2.rectangle(image, (85, 45), (180, 190), (0, 255, 255), 5)

            result = detect_yellow_box(write_image(directory, image))

        self.assertTrue(result["has_yellow_box"])
        self.assertGreater(result["confidence"], 0.5)
        self.assertGreaterEqual(len(result["boxes"]), 1)

    def test_detects_yellow_outline_rectangle_from_memory(self):
        """Находит жёлтую рамку без записи изображения на диск."""
        image = make_scene()
        cv2.rectangle(image, (85, 45), (180, 190), (0, 255, 255), 5)

        result = detect_yellow_box_in_image(image)

        self.assertTrue(result["has_yellow_box"])
        self.assertGreater(result["confidence"], 0.5)

    def test_rejects_image_without_yellow_pixels(self):
        """Не находит рамку на изображении без желтого цвета."""
        with tempfile.TemporaryDirectory() as directory:
            result = detect_yellow_box(write_image(directory, make_scene()))

        self.assertFalse(result["has_yellow_box"])
        self.assertEqual(result["boxes"], [])

    def test_rejects_solid_yellow_object(self):
        """Не принимает сплошной желтый объект за рамку."""
        with tempfile.TemporaryDirectory() as directory:
            image = make_scene()
            cv2.rectangle(image, (80, 50), (180, 190), (0, 255, 255), -1)

            result = detect_yellow_box(write_image(directory, image))

        self.assertFalse(result["has_yellow_box"])
        self.assertEqual(result["boxes"], [])


if __name__ == "__main__":
    unittest.main()
