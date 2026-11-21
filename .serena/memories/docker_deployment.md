# Docker-деплой (с 2026-11-21)

Схема: сервер работает в Docker на Mac mini (M4, Docker Desktop, linux/arm64),
ноутбук Lenovo с Windows (SSH-профиль lenovo-laptop, репо в `C:/Users/Admin/Desktop/ip-cam-monitor`;
2026-11-21 его `.env` совпадал с `.env` на Mac mini) — только экран: `open_viewer.bat` показывает поток в mpv (подробности ниже).
Раньше всё шло на ноутбуке через `start.bat`, и он грелся.

Автозапуск на Lenovo (с 2026-11-21): `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\camera_viewer.bat`
→ свёрнутый `cmd` с `...\Desktop\ip-cam-monitor\open_viewer.bat`. Старый ярлык на `start.bat` (локальный сервер + браузер)
лежит в `C:\Users\Admin\startup_backup\` (`start - Ярлык.lnk`); вернуть его обратно — значит вернуть локальный режим.
Через SSH кириллица в именах файлов не читается, используй короткие имена (`dir /x`).

`open_viewer.bat`: адрес — сначала mDNS `http://Mac-mini-Vladislav.local:5050` (LAN напрямую, роутер не настраиваем;
Tailscale на Windows тратил ~16% ядра на шифрование потока), запасной — `http://mac-mini.tail8eadef.ts.net:5050`.
Плеер — mpv (`C:\Users\Admin\tools\mpv\mpv.exe`, сборка shinchiro 20261111, v0.42.0) fullscreen, `--demuxer-lavf-format=mpjpeg --untimed`,
перезапуск в цикле; без mpv открывается Edge kiosk через `open_browser.ps1`. `VIEWER_DRY_RUN=1` — проверка без запуска.
На Lenovo нет winget, а Windows `tar` не распаковывает 7z (нет LZMA): архив распаковывался на Mac, переупаковывался в zip и
копировался `scp lenovo:` (на Mac есть `Host lenovo` в ~/.ssh/config).

Облегчение Windows на Lenovo (2026-11-21): отключены WSearch, SysMain, DiagTrack, Spooler, CDPSvc, OneSyncSvc;
убран автозапуск OneDrive; политики Edge StartupBoost/BackgroundMode=0, DeliveryOptimization без P2P, Cortana/Bing off,
активные часы Windows Update 06–23, VisualFX «быстродействие». Откат: `C:\Users\Admin\startup_backup\restore_tweaks.bat`
(+ `hkcu_run_backup.reg`). Предел заряда батареи не выставлен: нет Lenovo Vantage/драйвера питания. AutoAdminLogon=0.
Chrome Remote Desktop (`remoting_network`) при активной сессии ест ~половину ядра.

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
