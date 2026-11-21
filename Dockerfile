# Образ сервера IP-камеры: захват RTSP, MJPEG-раздача, yellow-box тревоги в Telegram.
# Собирается под архитектуру хоста (на Mac mini M4 — linux/arm64).
FROM python:3.12-slim

# 1 — установить ffmpeg в образ (нужен только для REMOTE_PUSH_URL).
# Для RTSP-захвата он не нужен: OpenCV несёт свой ffmpeg внутри wheel.
ARG INSTALL_FFMPEG=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata — чтобы TZ из compose давал правильное локальное время в логах.
# libglib2.0-0 — системная зависимость cv2 на slim-образе.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata libglib2.0-0 \
    && if [ "$INSTALL_FFMPEG" = "1" ]; then apt-get install -y --no-install-recommends ffmpeg; fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — отдельный слой, чтобы правки кода не пересобирали pip install
COPY requirements-docker.txt .
RUN pip install -r requirements-docker.txt

# Только runtime-файлы; тесты, эксперименты и .env отсекает .dockerignore
COPY server.py stream_push.py telegram_sender.py yellow_box_alerts.py yellow_box_detector.py index.html ./

# Не запускаем сервер от root
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 5050

# Healthcheck: сервер отвечает на /status (не проверяет саму камеру — её статус внутри JSON)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/status', timeout=4)" || exit 1

CMD ["python", "server.py"]
