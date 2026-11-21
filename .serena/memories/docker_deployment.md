# Docker-деплой (с 2026-11-21)

Схема: сервер работает в Docker на Mac mini (M4, Docker Desktop, linux/arm64),
ноутбук Lenovo с Windows (SSH-профиль lenovo-laptop, репо в `C:/Users/Admin/Desktop/ip-cam-monitor`;
2026-11-21 его `.env` совпадал с `.env` на Mac mini) — только экран: браузер в kiosk-режиме через `open_viewer.bat`
(→ `open_browser.ps1 -Url http://mac-mini.tail8eadef.ts.net:5050/`, Mac mini в Tailscale; LAN-адрес тоже работает). Раньше всё шло на ноутбуке через `start.bat`, и он грелся.

Файлы:
- `Dockerfile` — python:3.12-slim, `requirements-docker.txt` (opencv-python-headless), non-root user,
  HEALTHCHECK по `/status`; build-arg `INSTALL_FFMPEG=1` нужен только для REMOTE_PUSH.
- `docker-compose.yml` — `env_file: .env`, порт 5050:5050, `restart: unless-stopped`, ротация логов 3×10m,
  `CAMERA_SCAN_SUBNET` (по умолчанию 192.168.100) и `TZ` (по умолчанию Asia/Almaty).
- `.dockerignore` — исключает .env, .venv, медиа, experiments, .bat/.ps1.

Нюансы:
- Внутри контейнера `get_local_ip()` возвращает IP сети Docker, поэтому подсеть для поиска камеры
  задаётся через `CAMERA_SCAN_SUBNET` (`resolve_scan_subnet()` в server.py, тест `test_camera_scan_subnet.py`).
- Аппаратного декодирования Apple (VideoToolbox) в Docker нет, всё идёт на CPU.
- `TZ` в `.env.example` закомментирован: на Windows значение `Asia/Almaty` сбивает локальное время.
- Камера отдаёт оба RTSP-потока в HEVC (H.265), 25 fps заявлено: main 2304x2592, sub 640x720.
  WebRTC/HLS H.265 в браузерах поддерживается ненадёжно, для видео в браузер нужен транскод в H.264.
- До 2026-11-21 MJPEG `/stream` шёл в полном разрешении main с JPEG q92: ~2.1 МБ на кадр, ~70–100 Мбит/с,
  из-за этого грелся ноутбук. Теперь сервер сам обрезает и уменьшает кадр для `/stream`
  (`build_stream_jpeg()`, кэш по кадру `CameraState.get_stream_jpeg()`, переменные
  `STREAM_MAX_WIDTH`/`STREAM_MAX_HEIGHT`/`STREAM_JPEG_QUALITY`, по умолчанию 1920x1080 q80).
  `/snapshot` и Telegram берут полноразмерный кадр. Если этого не хватит, следующий шаг —
  H.264 через MediaMTX/WebRTC с транскодом HEVC→H.264.
- Сеть на 2026-11-21: Mac mini 192.168.100.13, камера 192.168.100.9.

Обзор проекта: [[project_overview]].
