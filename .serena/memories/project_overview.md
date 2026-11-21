# ip-cam-monitor — обзор

Python-сервер для 24/7 просмотра IP-камеры Xiongmai в браузере плюс Telegram-тревоги.

- `server.py` — Flask на 0.0.0.0:5050: поиск камеры (DVRIP 34567), RTSP через OpenCV
  (сначала main 2304x2592 ~6.6fps, затем sub 640x720), fallback на DVRIP snapshot, раздача MJPEG `/stream`
  (`?cam=full|top|bottom` — обрезка кадра), `/snapshot`, `/status`.
- `yellow_box_detector.py` / `yellow_box_alerts.py` — детектор жёлтой рамки на последнем кадре +
  очередь Telegram с rate limit (`YELLOW_BOX_*`).
- `telegram_sender.py` — общий отправщик Telegram (SSL-настройки).
- `stream_push.py` — опциональный ffmpeg relay на внешний RTMP (`REMOTE_PUSH_*`).
- `index.html` — UI, MJPEG в `<img>`, опрос `/status`.
- Тесты: `python -m unittest discover -p "test_*.py"` (часть тестов проверяет исходники как текст).

Деплой: см. [[docker_deployment]].
