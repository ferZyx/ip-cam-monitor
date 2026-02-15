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

## Приватность

- Файл `stream_viewer/.env` игнорируется git.
- Папка `stream_viewer/alarm_photos/` игнорируется git.

## Experiments / Research

Все ресерч/экспериментальные скрипты храним в `stream_viewer/experiments/`.

- Выходные файлы складываем в `stream_viewer/experiments/output/` (игнорируется git).
- Скрипты в `stream_viewer/experiments/` должны читать настройки из `stream_viewer/.env`.
