# Stream Viewer

Локальный веб-сервер для просмотра камеры Xiongmai (RTSP) в браузере.

## Быстрый старт (Windows)

1) Установи Python 3.10+

2) В папке `stream_viewer/`:

```bat
py -m pip install -r requirements.txt
```

3) Скопируй `.env.example` -> `.env` и заполни параметры.

4) Запуск:

```bat
start.bat
```

Открой:

- `http://localhost:5050`

## Запуск в Docker (Mac mini как сервер)

Вся тяжёлая работа (RTSP-декодирование, JPEG, детектор, Telegram) идёт в контейнере
на Mac mini, а остальные устройства в сети только открывают страницу в браузере.

1) Установи Docker Desktop и включи в настройках *Start Docker Desktop when you sign in*.
   В macOS отключи сон (Настройки → Аккумулятор/Энергия → не переходить в режим сна).

2) Заполни `.env` (как для локального запуска). Для Docker важно:
   - `CAMERA_SCAN_SUBNET` — подсеть домашней сети (по умолчанию в compose `192.168.100`),
     иначе поиск камеры при смене её IP пойдёт по внутренней сети Docker;
   - `TZ` — часовой пояс для логов (по умолчанию `Asia/Almaty`);
   - `INSTALL_FFMPEG=1` — только если нужен `REMOTE_PUSH_URL`.

3) Сборка и запуск:

```bash
docker compose up -d --build
```

4) Открыть с любого устройства в сети: `http://<IP Mac mini>:5050`
   (на Mac mini: `ipconfig getifaddr en0`). IP Mac mini лучше закрепить в роутере.

Полезное:

```bash
docker compose logs -f
docker compose ps
docker compose down
```

- Контейнер перезапускается сам (`restart: unless-stopped`), логи ротируются (3×10 МБ).
- В образ попадают только runtime-файлы; `.env` подключается через `env_file` и в образ не копируется.
- Используется `opencv-python-headless` (`requirements-docker.txt`). Аппаратного
  декодирования Apple в Docker нет, декодирование идёт на CPU, для одной камеры это немного.

### Ноутбук как экран (Windows)

Python на ноутбуке не нужен. Скопируй `open_viewer.bat` и `open_browser.ps1` в одну папку и запусти:

```bat
open_viewer.bat
open_viewer.bat http://192.168.100.13:5050/
```

По умолчанию открывается `http://mac-mini.tail8eadef.ts.net:5050/` (Mac mini в Tailscale), так что адрес
не зависит от IP в локальной сети. Скрипт дождётся, пока сервер станет доступен, и откроет Edge/Chrome в kiosk-режиме.
Для автозапуска положи в автозагрузку Windows (`shell:startup`) ярлык на `open_viewer.bat` вместо `start.bat`.

## Переменные окружения

Смотри `stream_viewer/.env.example`.

Минимум:

- `CAMERA_IP`
- `CAMERA_USER`
- `CAMERA_PASS`

Поиск камеры:

- `CAMERA_SCAN_SUBNET` (пусто по умолчанию: подсеть берётся из IP машины; в Docker задавай явно, например `192.168.100`)

MJPEG-поток для браузера:

- `STREAM_MAX_WIDTH` / `STREAM_MAX_HEIGHT` (`1920` / `1080` по умолчанию) — сервер обрезает и уменьшает кадр
  в эту рамку с сохранением пропорций, браузер получает готовую картинку под экран. `0` — без ограничения.
- `STREAM_JPEG_QUALITY` (`80` по умолчанию) — качество JPEG в `/stream`.
- `/snapshot` и фото в Telegram по-прежнему в полном разрешении камеры.

Telegram (опционально):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Yellow-box Telegram alerts (опционально):

- `YELLOW_BOX_ALERT_ENABLED` (`0` по умолчанию, поставь `1` для включения)
- `YELLOW_BOX_CHECK_INTERVAL_SEC` (`1` по умолчанию, как часто проверять последний кадр)
- `YELLOW_BOX_ALERT_MIN_INTERVAL_SEC` (`3` по умолчанию, минимальная пауза между постановкой тревог в очередь)
- `YELLOW_BOX_MIN_CONFIDENCE` (`0.5` по умолчанию)
- `YELLOW_BOX_DETECTION_MAX_WIDTH` (`640` по умолчанию, уменьшение кадра перед детекцией для экономии CPU)
- `YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE` (`20` по умолчанию, лимит отправки Telegram)
- `YELLOW_BOX_TELEGRAM_QUEUE_SIZE` (`20` по умолчанию, bounded-очередь без Redis)

Remote push (опционально, для просмотра вне дома):

- `REMOTE_PUSH_URL` (пример: `rtmp://SERVER_IP/live/cam1`)
- `REMOTE_PUSH_TRANSPORT` (`tcp` по умолчанию)
- `FFMPEG_BIN` (`ffmpeg` по умолчанию)
- `REMOTE_PUSH_LOG_TO_CONSOLE` (`1` по умолчанию, лог ffmpeg в консоль сервера)
- `REMOTE_PUSH_CODEC` (`libx264` рекомендуется для стабильности)
- `REMOTE_PUSH_PRESET` (`veryfast` по умолчанию)
- `REMOTE_PUSH_TUNE` (`zerolatency` по умолчанию)
- `REMOTE_PUSH_FPS` (`12` по умолчанию)
- `REMOTE_PUSH_SCALE_HEIGHT` (`720` по умолчанию)
- `REMOTE_PUSH_STREAM_INDEX` (`1` по умолчанию, sub-stream)

Если `REMOTE_PUSH_URL` задан, сервер поднимает фоновый `ffmpeg` и пушит RTSP камеры на внешний сервер.
Логи ffmpeg будут видны прямо в окне, где запущен `start.bat` / `py server.py`.

Если `YELLOW_BOX_ALERT_ENABLED=1`, сервер раз в секунду проверяет последний кадр из памяти через `yellow_box_detector.py` и при найденной жёлтой рамке ставит JPEG в локальную Telegram-очередь. Очередь отправляет не чаще `YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE`, поэтому capture loop и Flask не ждут Telegram API.

## Приватность

- Файл `stream_viewer/.env` игнорируется git.

## Experiments / Research

Все ресерч/экспериментальные скрипты храним в `stream_viewer/experiments/`.

- Выходные файлы складываем в `stream_viewer/experiments/output/` (игнорируется git).
- Скрипты в `stream_viewer/experiments/` должны читать настройки из `stream_viewer/.env`.

## Настройка внешнего сервера (без авторизации)

Пример с MediaMTX на Ubuntu (VPS):

1) Установить Docker.

2) Запустить MediaMTX с RTMP ingest и HLS playback:

```bash
docker run -d --name mediamtx --restart unless-stopped -p 1935:1935 -p 8888:8888 -p 8889:8889 bluenviron/mediamtx:latest
```

3) На Windows-машине (в `stream_viewer/.env`) указать:

```env
REMOTE_PUSH_URL=rtmp://YOUR_SERVER_IP/live/cam1
REMOTE_PUSH_TRANSPORT=tcp
```

4) Запустить `start.bat`.

5) Смотреть поток извне через HLS:

- `http://YOUR_SERVER_IP:8888/live/cam1/index.m3u8`

Примечания:
- Порт `1935` нужен для входящего RTMP с твоей Windows-машины.
- Порт `8888` нужен клиентам для HLS-плеера.
- Для браузеров обычно лучше использовать `https` + reverse proxy (на следующем шаге).
