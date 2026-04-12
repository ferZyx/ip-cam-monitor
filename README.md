# Stream Viewer

Локальный веб-сервер для просмотра камеры Xiongmai (RTSP) и получения тревог (DVRIP),
включая извлечение "фото тревоги" из архивного motion-ролика.

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

## Переменные окружения

Смотри `stream_viewer/.env.example`.

Минимум:

- `CAMERA_IP`
- `CAMERA_USER`
- `CAMERA_PASS`

Telegram (опционально):

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Remote push (опционально, для просмотра вне дома):

- `REMOTE_PUSH_URL` (пример: `rtmp://SERVER_IP/live/cam1`)
- `REMOTE_PUSH_TRANSPORT` (`tcp` по умолчанию)
- `FFMPEG_BIN` (`ffmpeg` по умолчанию)

Если `REMOTE_PUSH_URL` задан, сервер поднимает фоновый `ffmpeg` и пушит RTSP камеры на внешний сервер.

## Приватность

- Файл `stream_viewer/.env` игнорируется git.
- Папка `stream_viewer/alarm_photos/` игнорируется git.

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
