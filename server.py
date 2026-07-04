"""
Stream Viewer — Python-бэкенд для 24/7 просмотра IP-камеры Xiongmai в браузере.

Архитектура:
  1. Автоматически находит камеру в локальной сети (сканирует порт 34567 DVRIP)
  2. Подключается к RTSP-потоку через OpenCV (пароль DVRIP!)
  3. Раздаёт MJPEG-поток по HTTP (Flask)
  4. Самовосстановление: при потере соединения автоматически переподключается
  5. Fallback на DVRIP snapshot если RTSP недоступен

Тесты (14.02.2026):
  RTSP main:     2304x2592, ~6.6 fps  ← основной метод
  RTSP sub:      640x720              ← экономный метод
  DVRIP snapshot: ~4.1 snap/s, 33KB   ← fallback

Запуск:
  py server.py
  Открыть http://localhost:5050
"""

import json
import logging
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # .env опционален: сервер должен работать и с системными переменными окружения.
    pass

try:
    # When running as `py server.py` inside `stream_viewer/`
    from telegram_sender import send_telegram_payload
except ModuleNotFoundError:
    from stream_viewer.telegram_sender import send_telegram_payload  # type: ignore

try:
    # When running as `py server.py` inside `stream_viewer/`
    from stream_push import RemotePushRelay, should_enable_push
except ModuleNotFoundError:
    from stream_viewer.stream_push import (  # type: ignore
        RemotePushRelay,
        should_enable_push,
    )

try:
    # When running as `py server.py` inside `stream_viewer/`
    from yellow_box_alerts import (
        FrameSnapshot,
        RateLimitedTelegramQueue,
        YellowBoxFrameMonitor,
    )
except ModuleNotFoundError:
    from stream_viewer.yellow_box_alerts import (  # type: ignore
        FrameSnapshot,
        RateLimitedTelegramQueue,
        YellowBoxFrameMonitor,
    )

try:
    import cv2
except ImportError:
    print("opencv-python не установлен. Выполните: pip install opencv-python")
    sys.exit(1)

try:
    from flask import Flask, Response, jsonify, send_from_directory, request
except ImportError:
    print("flask не установлен. Выполните: pip install flask")
    sys.exit(1)

try:
    from dvrip import DVRIPCam

    HAS_DVRIP = True
except ImportError:
    DVRIPCam = None  # type: ignore
    HAS_DVRIP = False


# ─── Конфигурация ─────────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    """Читает целочисленную переменную окружения с безопасным значением по умолчанию."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    """Читает дробную переменную окружения с безопасным значением по умолчанию."""
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Читает булеву переменную окружения в человекочитаемых форматах."""
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


# ВАЖНО: Камера Xiongmai использует DVRIP-пароль для RTSP (не RTSP-специфичный)
KNOWN_IP = os.getenv("CAMERA_IP", "192.168.100.9")
CAMERA_USER = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")
DVRIP_PORT = _env_int("DVRIP_PORT", 34567)
RTSP_PORT = _env_int("RTSP_PORT", 554)

WEB_HOST = "0.0.0.0"
WEB_PORT = 5050

JPEG_QUALITY = 92  # Качество MJPEG (1-100), 92 = почти без потерь
RECONNECT_DELAY = 3  # Секунд между переподключениями
SCAN_TIMEOUT = 0.3  # Таймаут порт-сканирования
MAX_FPS = 7  # Камера даёт ~6.6fps

# Telegram (оставить пустым чтобы отключить)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_SSL_VERIFY = _env_bool("TELEGRAM_SSL_VERIFY", default=True)
TELEGRAM_CA_BUNDLE = os.getenv("TELEGRAM_CA_BUNDLE", "").strip()

# Yellow-box Telegram alerts (optional)
YELLOW_BOX_ALERT_ENABLED = _env_bool("YELLOW_BOX_ALERT_ENABLED", default=False)
YELLOW_BOX_CHECK_INTERVAL_SEC = _env_float("YELLOW_BOX_CHECK_INTERVAL_SEC", 1.0)
YELLOW_BOX_ALERT_MIN_INTERVAL_SEC = _env_float(
    "YELLOW_BOX_ALERT_MIN_INTERVAL_SEC",
    3.0,
)
YELLOW_BOX_MIN_CONFIDENCE = _env_float("YELLOW_BOX_MIN_CONFIDENCE", 0.5)
YELLOW_BOX_DETECTION_MAX_WIDTH = _env_int("YELLOW_BOX_DETECTION_MAX_WIDTH", 640)
YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE = _env_int(
    "YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE",
    20,
)
YELLOW_BOX_TELEGRAM_QUEUE_SIZE = _env_int("YELLOW_BOX_TELEGRAM_QUEUE_SIZE", 20)

# RTSP stability
RTSP_NO_FRAME_TIMEOUT_SEC = _env_int("RTSP_NO_FRAME_TIMEOUT_SEC", 15)

# Remote upstream push (optional)
REMOTE_PUSH_URL = os.getenv("REMOTE_PUSH_URL", "").strip()
REMOTE_PUSH_TRANSPORT = (
    os.getenv("REMOTE_PUSH_TRANSPORT", "tcp").strip() or "tcp"
).lower()
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
REMOTE_PUSH_LOG_TO_CONSOLE = _env_bool("REMOTE_PUSH_LOG_TO_CONSOLE", default=True)
REMOTE_PUSH_CODEC = os.getenv("REMOTE_PUSH_CODEC", "libx264").strip() or "libx264"
REMOTE_PUSH_PRESET = os.getenv("REMOTE_PUSH_PRESET", "veryfast").strip() or "veryfast"
REMOTE_PUSH_TUNE = os.getenv("REMOTE_PUSH_TUNE", "zerolatency").strip() or "zerolatency"
REMOTE_PUSH_FPS = _env_int("REMOTE_PUSH_FPS", 12)
REMOTE_PUSH_SCALE_HEIGHT = _env_int("REMOTE_PUSH_SCALE_HEIGHT", 720)
REMOTE_PUSH_STREAM_INDEX = _env_int("REMOTE_PUSH_STREAM_INDEX", 1)

QUIET_HTTP_ACCESS_LOGS = _env_bool("QUIET_HTTP_ACCESS_LOGS", default=True)


# ─── Логгирование ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stream_viewer")
if QUIET_HTTP_ACCESS_LOGS:
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Принудительный TCP для RTSP (стабильнее UDP)
# + таймауты на уровне ffmpeg (уменьшает зависания при потере пакетов)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|stimeout;5000000|max_delay;500000"
)
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

# ─── Flask ─────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=".", static_url_path="/static")


# ─── Глобальное состояние ──────────────────────────────────────────────────────


class CameraState:
    """Потокобезопасное состояние камеры."""

    def __init__(self):
        """Создаёт пустое состояние камеры и счётчики потока."""
        self.lock = threading.Lock()
        self.frame: bytes | None = None  # JPEG bytes
        self.frame_bgr = None  # last BGR frame (optional)
        self.frame_event = threading.Event()
        self.camera_ip: str | None = None
        self.status: str = "init"  # init | scanning | connecting | streaming | error
        self.error: str = ""
        self.mode: str = ""  # rtsp_main | rtsp_sub | dvrip_snap
        self.fps: float = 0.0
        self.resolution: str = ""
        self.uptime_start: float | None = None
        self.frame_count: int = 0
        self.clients: int = 0

    def set_frame(self, jpeg_bytes: bytes, bgr_frame=None):
        """Сохраняет последний кадр и будит клиентов MJPEG-потока."""
        with self.lock:
            self.frame = jpeg_bytes
            self.frame_bgr = bgr_frame
            self.frame_count += 1
        self.frame_event.set()
        self.frame_event.clear()

    def get_frame(self) -> bytes | None:
        """Возвращает последний JPEG-кадр."""
        with self.lock:
            return self.frame

    def get_frame_bgr(self):
        """Возвращает последний BGR-кадр, если он доступен."""
        with self.lock:
            return self.frame_bgr

    def set_status(self, status: str, error: str = ""):
        """Обновляет статус камеры и время аптайма трансляции."""
        self.status = status
        self.error = error
        if status == "streaming" and self.uptime_start is None:
            self.uptime_start = time.time()
        elif status != "streaming":
            self.uptime_start = None

    def to_dict(self) -> dict:
        """Возвращает публичный JSON-словарь состояния камеры."""
        uptime = 0
        if self.uptime_start:
            uptime = int(time.time() - self.uptime_start)
        relay_status = remote_relay.status()
        return {
            "status": self.status,
            "error": self.error,
            "camera_ip": self.camera_ip,
            "mode": self.mode,
            "fps": round(self.fps, 1),
            "resolution": self.resolution,
            "uptime_seconds": uptime,
            "frame_count": self.frame_count,
            "clients": self.clients,
            "remote_push_enabled": relay_status["enabled"],
            "remote_push_running": relay_status["running"],
            "remote_push_target": relay_status["target"],
        }


state = CameraState()
remote_relay = RemotePushRelay(
    ffmpeg_bin=FFMPEG_BIN,
    transport=REMOTE_PUSH_TRANSPORT,
    log_to_console=REMOTE_PUSH_LOG_TO_CONSOLE,
    logger=log,
    video_codec=REMOTE_PUSH_CODEC,
    preset=REMOTE_PUSH_PRESET,
    tune=REMOTE_PUSH_TUNE,
    fps=REMOTE_PUSH_FPS,
    scale_height=REMOTE_PUSH_SCALE_HEIGHT,
)


# ─── Обнаружение камеры ───────────────────────────────────────────────────────


def get_local_ip() -> str:
    """Определяет IP текущей машины в локальной сети."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.100.1"


def check_port(ip: str, port: int, timeout: float = SCAN_TIMEOUT) -> bool:
    """Проверяет открыт ли порт."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex((ip, port))
        s.close()
        return result == 0
    except Exception:
        return False


def discover_camera() -> str | None:
    """
    Находит камеру в локальной сети.
    1. Сначала проверяет последний известный IP
    2. Если не найден — сканирует подсеть по порту 34567 (DVRIP — уникален для Xiongmai)
    """
    log.info(f"Проверяю известный IP: {KNOWN_IP}...")
    if check_port(KNOWN_IP, DVRIP_PORT, timeout=1.0):
        log.info(f"✓ Камера найдена: {KNOWN_IP}")
        return KNOWN_IP

    local_ip = get_local_ip()
    subnet = ".".join(local_ip.split(".")[:3])
    log.info(f"Камера не на {KNOWN_IP}. Сканирую {subnet}.0/24 (порт {DVRIP_PORT})...")
    state.set_status("scanning")

    def scan_ip(ip: str) -> str | None:
        """Возвращает IP, если на нём открыт DVRIP-порт камеры."""
        return ip if check_port(ip, DVRIP_PORT) else None

    ips = [f"{subnet}.{i}" for i in range(1, 255) if f"{subnet}.{i}" != local_ip]

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(scan_ip, ip): ip for ip in ips}
        for future in as_completed(futures):
            result = future.result()
            if result:
                log.info(f"✓ Камера найдена: {result}")
                executor.shutdown(wait=False, cancel_futures=True)
                return result

    log.warning(f"✗ Камера не найдена в {subnet}.0/24")
    return None


# ─── RTSP URL ─────────────────────────────────────────────────────────────────


def build_rtsp_url(ip: str, stream: int = 0) -> str:
    """
    RTSP URL для Xiongmai.
    stream=0 → Main (2304x2592, ~6.6fps)
    stream=1 → Sub  (640x720, быстрее)
    Пароль = DVRIP пароль (проверено тестами).
    """
    return (
        f"rtsp://{ip}:{RTSP_PORT}/"
        f"user={CAMERA_USER}_password={CAMERA_PASS}_channel=0_stream={stream}.sdp"
    )


# ─── Захват: RTSP ─────────────────────────────────────────────────────────────


def try_rtsp(ip: str) -> cv2.VideoCapture | None:
    """Пробует подключиться к RTSP. Main-stream первый — максимальное качество."""
    for stream, label in [(0, "main"), (1, "sub")]:
        url = build_rtsp_url(ip, stream)
        log.info(f"RTSP {label}: {url}")
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 10)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 15000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 15000)

        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                state.mode = f"rtsp_{label}"
                state.resolution = f"{w}x{h}"
                log.info(f"✓ RTSP {label} работает: {w}x{h}")
                return cap
            cap.release()
            log.warning(f"  RTSP {label}: открылся, но кадр не читается")
        else:
            cap.release()
            log.warning(f"  RTSP {label}: не удалось открыть")

    return None


# ─── Захват: DVRIP snapshot (fallback) ────────────────────────────────────────


def dvrip_snapshot_loop(ip: str):
    """
    Fallback: получаем кадры через DVRIP cam.snapshot().
    ~4 fps, JPEG ~33KB. Работает всегда, когда камера доступна.
    """
    if (not HAS_DVRIP) or (DVRIPCam is None):
        log.error("python-dvr не установлен — DVRIP fallback невозможен")
        state.set_status("error", "python-dvr не установлен, RTSP тоже не работает")
        return

    log.info("Fallback: DVRIP snapshot режим")
    cam = DVRIPCam(ip, port=DVRIP_PORT, user=CAMERA_USER, password=CAMERA_PASS)

    if not cam.login():
        log.error("DVRIP: не удалось залогиниться")
        state.set_status("error", "DVRIP логин неуспешен")
        return

    log.info("✓ DVRIP подключено, режим snapshot")
    state.mode = "dvrip_snap"
    state.set_status("streaming")

    fps_counter = 0
    fps_timer = time.time()
    consecutive_errors = 0

    try:
        while True:
            try:
                snap = cam.snapshot()
            except Exception as e:
                log.warning(f"DVRIP snapshot error: {e}")
                consecutive_errors += 1
                if consecutive_errors >= 10:
                    log.error("DVRIP: 10 ошибок подряд — переподключение")
                    state.set_status("error", "DVRIP потерял связь")
                    break
                time.sleep(0.5)
                continue

            if not snap:
                consecutive_errors += 1
                if consecutive_errors >= 10:
                    break
                time.sleep(0.3)
                continue

            consecutive_errors = 0
            state.set_frame(snap)  # snapshot() уже возвращает JPEG

            if not state.resolution:
                try:
                    # JPEG SOF0 marker parsing для определения размера
                    idx = snap.find(b"\xff\xc0")
                    if idx > 0:
                        h = int.from_bytes(snap[idx + 5 : idx + 7], "big")
                        w = int.from_bytes(snap[idx + 7 : idx + 9], "big")
                        state.resolution = f"{w}x{h}"
                        log.info(f"DVRIP разрешение: {state.resolution}")
                except Exception:
                    state.resolution = "?"

            fps_counter += 1
            now = time.time()
            elapsed = now - fps_timer
            if elapsed >= 2.0:
                state.fps = fps_counter / elapsed
                fps_counter = 0
                fps_timer = now
    finally:
        try:
            cam.close()
        except Exception:
            pass


# ─── Захват: RTSP цикл чтения ────────────────────────────────────────────────


def rtsp_read_loop(cap: cv2.VideoCapture, remote_source_url: str | None = None):
    """Читаем кадры из RTSP и кодируем в JPEG для раздачи."""
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    fps_counter = 0
    fps_timer = time.time()
    frame_interval = 1.0 / MAX_FPS if MAX_FPS > 0 else 0
    last_frame_time = 0
    last_good_frame_time = time.time()
    consecutive_errors = 0
    last_push_check = 0.0

    state.set_status("streaming")
    log.info(f"RTSP read loop started (max_fps={MAX_FPS}, quality={JPEG_QUALITY})")

    try:
        while True:
            ret, frame = cap.read()

            if not ret or frame is None:
                consecutive_errors += 1
                # Пустые чтения нормальны при медленном потоке.
                # Переподключаем только если слишком долго без единого кадра
                no_frame_sec = time.time() - last_good_frame_time
                if no_frame_sec > RTSP_NO_FRAME_TIMEOUT_SEC:
                    log.warning(
                        f"RTSP: {no_frame_sec:.0f}с без кадров — переподключение"
                    )
                    state.set_status("error", "Потеря RTSP-потока")
                    break
                time.sleep(0.02)
                continue

            consecutive_errors = 0
            last_good_frame_time = time.time()

            now = time.time()

            if should_enable_push(REMOTE_PUSH_URL) and remote_source_url:
                if now - last_push_check >= 3.0:
                    last_push_check = now
                    try:
                        remote_relay.ensure_running(remote_source_url, REMOTE_PUSH_URL)
                    except Exception as push_error:
                        log.warning(f"Remote push monitor failed: {push_error}")

            if now - last_frame_time < frame_interval:
                continue
            last_frame_time = now

            ok, jpeg = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue

            state.set_frame(jpeg.tobytes(), bgr_frame=frame)

            if not state.resolution:
                h, w = frame.shape[:2]
                state.resolution = f"{w}x{h}"
                log.info(f"Разрешение потока: {w}x{h}")

            fps_counter += 1
            elapsed = now - fps_timer
            if elapsed >= 2.0:
                state.fps = fps_counter / elapsed
                fps_counter = 0
                fps_timer = now
    finally:
        cap.release()


# ─── Главный цикл захвата ─────────────────────────────────────────────────────


def capture_loop():
    """
    Самовосстанавливающийся цикл:
    1. Найти камеру
    2. Попробовать RTSP (main → sub)
    3. Если RTSP не работает → fallback на DVRIP snapshot
    4. При любой ошибке → переподключение через 3 сек
    """
    while True:
        try:
            # 1. Найти камеру
            state.set_status("scanning")
            camera_ip = discover_camera()

            if not camera_ip:
                state.set_status("error", "Камера не найдена в сети")
                time.sleep(RECONNECT_DELAY)
                continue

            state.camera_ip = camera_ip
            state.set_status("connecting")
            state.resolution = ""  # Сбросим для нового подключения

            # 2. Попробовать RTSP
            cap = try_rtsp(camera_ip)

            if cap is not None:
                remote_source_rtsp_url = None
                if should_enable_push(REMOTE_PUSH_URL):
                    stream_idx = 1 if REMOTE_PUSH_STREAM_INDEX == 1 else 0
                    remote_source_rtsp_url = build_rtsp_url(camera_ip, stream_idx)
                    try:
                        remote_relay.ensure_running(
                            remote_source_rtsp_url, REMOTE_PUSH_URL
                        )
                        log.info(f"Remote push enabled -> {REMOTE_PUSH_URL}")
                    except FileNotFoundError:
                        log.error(
                            f"Remote push: ffmpeg not found ('{FFMPEG_BIN}'). Install ffmpeg or set FFMPEG_BIN"
                        )
                    except Exception as push_error:
                        log.warning(f"Remote push start failed: {push_error}")
                rtsp_read_loop(cap, remote_source_url=remote_source_rtsp_url)
            else:
                # 3. Fallback на DVRIP
                remote_relay.stop()
                log.warning("RTSP недоступен — переход на DVRIP snapshot")
                dvrip_snapshot_loop(camera_ip)

        except Exception as e:
            log.error(f"Критическая ошибка: {e}")
            state.set_status("error", str(e))
            remote_relay.stop()

        log.info(f"Переподключение через {RECONNECT_DELAY} сек...")
        time.sleep(RECONNECT_DELAY)


# ─── Telegram ─────────────────────────────────────────────────────────────────


def send_telegram(text: str, photo_bytes: bytes | None = None):
    """Отправляет сообщение (и фото) в Telegram. Не падает при ошибках."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        send_telegram_payload(
            bot_token=TELEGRAM_BOT_TOKEN,
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            photo_bytes=photo_bytes,
            ssl_verify=TELEGRAM_SSL_VERIFY,
            ca_bundle=TELEGRAM_CA_BUNDLE,
            as_document=False,
        )
        log.info(f"Telegram: отправлено")
    except Exception as e:
        log.warning(f"Telegram ошибка: {e}")


# ─── Yellow-box Telegram alerts ───────────────────────────────────────────────


def get_yellow_box_frame_snapshot() -> FrameSnapshot | None:
    """Возвращает последний кадр камеры для фоновой yellow-box детекции."""
    with state.lock:
        if state.frame is None:
            return None

        # Берём JPEG, BGR и номер кадра под одним lock, чтобы монитор не смешивал кадры.
        return FrameSnapshot(
            frame_count=state.frame_count,
            jpeg_bytes=state.frame,
            bgr_frame=state.frame_bgr,
        )


def should_enable_yellow_box_alerts() -> bool:
    """Проверяет, можно ли запускать yellow-box Telegram alerts."""
    return bool(
        YELLOW_BOX_ALERT_ENABLED
        and TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def start_yellow_box_alerts():
    """Запускает очередь Telegram и фоновую yellow-box детекцию, если они включены."""
    if not YELLOW_BOX_ALERT_ENABLED:
        return None

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning(
            "Yellow box alerts enabled, but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are empty"
        )
        return None

    telegram_queue = RateLimitedTelegramQueue(
        send_func=send_telegram,
        rate_per_minute=YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE,
        max_queue_size=YELLOW_BOX_TELEGRAM_QUEUE_SIZE,
        logger=log,
    )
    telegram_queue.start()

    monitor = YellowBoxFrameMonitor(
        frame_provider=get_yellow_box_frame_snapshot,
        alert_queue=telegram_queue.enqueue_alert,
        check_interval_sec=YELLOW_BOX_CHECK_INTERVAL_SEC,
        alert_min_interval_sec=YELLOW_BOX_ALERT_MIN_INTERVAL_SEC,
        min_confidence=YELLOW_BOX_MIN_CONFIDENCE,
        max_detection_width=YELLOW_BOX_DETECTION_MAX_WIDTH,
        logger=log,
    )
    monitor.start()

    log.info(
        "Yellow box alerts enabled "
        f"(check={YELLOW_BOX_CHECK_INTERVAL_SEC}s, "
        f"rate={YELLOW_BOX_TELEGRAM_RATE_PER_MINUTE}/min, "
        f"queue={YELLOW_BOX_TELEGRAM_QUEUE_SIZE})"
    )
    return telegram_queue, monitor


# ─── HTTP маршруты ─────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """Отдаёт основной HTML-интерфейс."""
    return send_from_directory(".", "index.html")


@app.route("/stream")
def stream():
    """MJPEG-поток для <img> тега."""

    cam_mode = (request.args.get("cam", "full") or "full").lower()
    if cam_mode not in {"full", "top", "bottom"}:
        cam_mode = "full"

    def crop_bgr(bgr, mode: str):
        """Обрезает BGR-кадр под выбранный режим просмотра."""
        if mode == "full" or bgr is None:
            return bgr
        h = int(bgr.shape[0])
        if h < 2:
            return bgr
        mid = h // 2
        if mode == "top":
            return bgr[:mid, :, :]
        if mode == "bottom":
            return bgr[mid:, :, :]
        return bgr

    def to_jpeg_bytes(bgr) -> bytes | None:
        """Кодирует BGR-кадр в JPEG-байты."""
        if bgr is None:
            return None
        ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return None
        return jpeg.tobytes()

    def generate():
        """Генерирует multipart MJPEG-ответ для браузера."""
        state.clients += 1
        log.info(f"Клиент подключился (всего: {state.clients})")
        try:
            while True:
                state.frame_event.wait(timeout=2.0)
                frame_jpeg = state.get_frame()
                if frame_jpeg is None:
                    continue

                out_jpeg = frame_jpeg
                if cam_mode != "full":
                    bgr = state.get_frame_bgr()
                    if bgr is None:
                        try:
                            import numpy as np

                            arr = np.frombuffer(frame_jpeg, dtype=np.uint8)
                            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        except Exception:
                            bgr = None

                    out_bgr = crop_bgr(bgr, cam_mode)
                    maybe = to_jpeg_bytes(out_bgr)
                    if maybe:
                        out_jpeg = maybe

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(out_jpeg)).encode() + b"\r\n"
                    b"\r\n" + out_jpeg + b"\r\n"
                )
        except GeneratorExit:
            pass
        finally:
            state.clients -= 1
            log.info(f"Клиент отключился (всего: {state.clients})")

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
        },
    )


@app.route("/snapshot")
def snapshot():
    """Текущий кадр как JPEG."""
    cam_mode = (request.args.get("cam", "full") or "full").lower()
    if cam_mode not in {"full", "top", "bottom"}:
        cam_mode = "full"

    frame_jpeg = state.get_frame()
    if frame_jpeg is None:
        return "Нет кадра", 503

    if cam_mode == "full":
        return Response(
            frame_jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-cache"}
        )

    bgr = state.get_frame_bgr()
    if bgr is None:
        try:
            import numpy as np

            arr = np.frombuffer(frame_jpeg, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            bgr = None

    if bgr is None:
        return Response(
            frame_jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-cache"}
        )

    h = int(bgr.shape[0])
    mid = h // 2 if h >= 2 else 0
    if cam_mode == "top":
        bgr = bgr[:mid, :, :]
    elif cam_mode == "bottom":
        bgr = bgr[mid:, :, :]

    ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        return Response(
            frame_jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-cache"}
        )
    return Response(
        jpeg.tobytes(), mimetype="image/jpeg", headers={"Cache-Control": "no-cache"}
    )


@app.route("/status")
def api_status():
    """JSON со статусом камеры."""
    return jsonify(state.to_dict())


# ─── Точка входа ──────────────────────────────────────────────────────────────


def main():
    """Запускает фоновый захват камеры и Flask-сервер."""
    log.info("=" * 50)
    log.info("  Stream Viewer — запуск")
    log.info("=" * 50)

    _yellow_box_alert_workers = start_yellow_box_alerts()

    capture_thread = threading.Thread(target=capture_loop, daemon=True, name="capture")
    capture_thread.start()

    log.info(f"Веб-интерфейс: http://localhost:{WEB_PORT}")
    log.info(f"MJPEG поток:   http://localhost:{WEB_PORT}/stream")
    log.info(f"Снимок:        http://localhost:{WEB_PORT}/snapshot")
    log.info(f"Статус JSON:   http://localhost:{WEB_PORT}/status")
    if should_enable_push(REMOTE_PUSH_URL):
        log.info(
            f"Remote push:   {REMOTE_PUSH_URL} (ffmpeg={FFMPEG_BIN}, codec={REMOTE_PUSH_CODEC}, stream={REMOTE_PUSH_STREAM_INDEX})"
        )

    app.run(host=WEB_HOST, port=WEB_PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
