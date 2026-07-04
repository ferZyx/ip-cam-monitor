import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


YELLOW_LOWER = np.array([18, 130, 160], dtype=np.uint8)
YELLOW_UPPER = np.array([45, 255, 255], dtype=np.uint8)
DEFAULT_MIN_CONFIDENCE = 0.5


def make_kernel_size(image_shape, fraction, minimum=3, maximum=17):
    """Считает нечетный размер ядра относительно размера изображения."""
    height, width = image_shape[:2]
    size = int(round(min(height, width) * fraction))
    size = max(minimum, min(maximum, size))
    if size % 2 == 0:
        size += 1
    return size


def load_image(image_path):
    """Загружает изображение с диска и возвращает ошибку для плохого файла."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Не удалось прочитать изображение: {path}")
    return image


def build_yellow_mask(image):
    """Строит бинарную маску ярко-желтых участков изображения."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, YELLOW_LOWER, YELLOW_UPPER)

    # Соединяем небольшие разрывы линии, которые появляются из-за JPEG-сжатия.
    close_size = make_kernel_size(image.shape, 0.0035, minimum=3, maximum=11)
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (close_size, close_size),
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    # Чуть утолщаем линию, чтобы контур рамки был связанным на разных масштабах.
    dilate_size = make_kernel_size(image.shape, 0.0018, minimum=3, maximum=7)
    dilate_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (dilate_size, dilate_size),
    )
    return cv2.dilate(mask, dilate_kernel, iterations=1)


def count_mask_ratio(mask):
    """Возвращает долю ненулевых пикселей в маске."""
    if mask.size == 0:
        return 0.0
    return float(cv2.countNonZero(mask)) / float(mask.size)


def side_ratios(mask_roi, band):
    """Считает покрытие желтым цветом по четырем сторонам прямоугольника."""
    height, width = mask_roi.shape[:2]
    return {
        "top": count_mask_ratio(mask_roi[:band, :]),
        "bottom": count_mask_ratio(mask_roi[height - band :, :]),
        "left": count_mask_ratio(mask_roi[:, :band]),
        "right": count_mask_ratio(mask_roi[:, width - band :]),
    }


def score_rectangle_candidate(mask, rect, image_area):
    """Оценивает, похож ли желтый контур на полую прямоугольную рамку."""
    x, y, width, height = rect
    rect_area = width * height
    image_side = min(mask.shape[:2])
    min_side = max(8, int(round(image_side * 0.006)))

    if width < min_side or height < min_side:
        return 0.0
    if rect_area < max(64, int(image_area * 0.00004)):
        return 0.0
    if rect_area > image_area * 0.7:
        return 0.0

    aspect_ratio = width / float(height)
    if aspect_ratio < 0.08 or aspect_ratio > 12.0:
        return 0.0

    mask_roi = mask[y : y + height, x : x + width]
    yellow_density = count_mask_ratio(mask_roi)
    if yellow_density < 0.015 or yellow_density > 0.65:
        return 0.0

    # Для рамки важны желтые стороны и относительно пустая середина.
    band = max(2, int(round(min(width, height) * 0.14)))
    band = min(band, max(2, width // 2), max(2, height // 2))
    ratios = side_ratios(mask_roi, band)
    strong_sides = [value for value in ratios.values() if value >= 0.18]
    if len(strong_sides) < 3:
        return 0.0

    if width > band * 2 and height > band * 2:
        inner = mask_roi[band : height - band, band : width - band]
        inner_density = count_mask_ratio(inner)
    else:
        inner_density = 0.0

    if inner_density > 0.24:
        return 0.0

    side_score = min(sum(ratios.values()) / (4.0 * 0.45), 1.0)
    hollow_score = max(0.0, 1.0 - min(inner_density / 0.18, 1.0))
    density_score = min(yellow_density / 0.16, 1.0)
    return (side_score * 0.55) + (hollow_score * 0.3) + (density_score * 0.15)


def find_yellow_box_candidates(mask, min_confidence=DEFAULT_MIN_CONFIDENCE):
    """Ищет кандидатов желтых рамок в бинарной маске."""
    image_area = mask.shape[0] * mask.shape[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []

    # Каждый внешний контур превращаем в bounding box и проверяем геометрию рамки.
    for contour in contours:
        rect = cv2.boundingRect(contour)
        confidence = score_rectangle_candidate(mask, rect, image_area)
        if confidence >= min_confidence:
            x, y, width, height = rect
            candidates.append(
                {
                    "box": [int(x), int(y), int(width), int(height)],
                    "confidence": round(float(confidence), 3),
                }
            )

    return sorted(candidates, key=lambda item: item["confidence"], reverse=True)


def make_result(candidates):
    """Формирует стабильный результат для API и CLI."""
    confidence = candidates[0]["confidence"] if candidates else 0.0
    return {
        "has_yellow_box": bool(candidates),
        "confidence": confidence,
        "boxes": [candidate["box"] for candidate in candidates],
    }


def detect_yellow_box(image_path, min_confidence=DEFAULT_MIN_CONFIDENCE):
    """Проверяет, есть ли на изображении желтая прямоугольная рамка."""
    try:
        image = load_image(image_path)
    except (OSError, ValueError) as exc:
        return {
            "has_yellow_box": False,
            "confidence": 0.0,
            "boxes": [],
            "error": str(exc),
        }

    mask = build_yellow_mask(image)
    candidates = find_yellow_box_candidates(mask, min_confidence=min_confidence)
    return make_result(candidates)


def detect_yellow_box_in_image(image, min_confidence=DEFAULT_MIN_CONFIDENCE):
    """Проверяет BGR-изображение из памяти без записи кадра на диск."""
    if image is None or getattr(image, "size", 0) == 0:
        return {
            "has_yellow_box": False,
            "confidence": 0.0,
            "boxes": [],
            "error": "Пустое изображение",
        }

    # Используем тот же pipeline, что и CLI, чтобы результаты были одинаковыми.
    mask = build_yellow_mask(image)
    candidates = find_yellow_box_candidates(mask, min_confidence=min_confidence)
    return make_result(candidates)


def parse_args(argv):
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Определяет, есть ли на изображении желтая рамка камеры.",
    )
    parser.add_argument("image_path", help="Путь к JPG/PNG/другому изображению")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help="Минимальная уверенность для положительного результата",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Запускает CLI-режим детектора желтой рамки."""
    args = parse_args(argv)
    result = detect_yellow_box(
        args.image_path,
        min_confidence=args.min_confidence,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 2 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
