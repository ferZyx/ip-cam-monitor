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

import io
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

try:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        # .env support is optional; environment variables still work.
        pass

    # When running as `py server.py` inside `stream_viewer/`
    from alarm_photo_extractor import (
        download_motion_file_h264,
        extract_best_jpeg_from_motion_h264,
    )
except ModuleNotFoundError:
    # When running as `py -m stream_viewer.server` or importing as a package
    from stream_viewer.alarm_photo_extractor import (  # type: ignore
        download_motion_file_h264,
        extract_best_jpeg_from_motion_h264,
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
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
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
ALARM_POLL_INTERVAL = _env_int("ALARM_POLL_INTERVAL", 300)  # backup
ALARM_HISTORY_MAX = 200  # Макс тревог в памяти
ALARM_PHOTOS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "alarm_photos"
)
ALARM_COOLDOWN = _env_int("ALARM_COOLDOWN", 5)
ALARM_DEBUG_DUMP = _env_bool("ALARM_DEBUG_DUMP", default=False)

# ─── Логгирование ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stream_viewer")

# Принудительный TCP для RTSP (стабильнее UDP)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# ─── Flask ─────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=".", static_url_path="/static")


# ─── Глобальное состояние ──────────────────────────────────────────────────────


class CameraState:
    """Потокобезопасное состояние камеры."""

    def __init__(self):
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
        with self.lock:
            self.frame = jpeg_bytes
            self.frame_bgr = bgr_frame
            self.frame_count += 1
        self.frame_event.set()
        self.frame_event.clear()

    def get_frame(self) -> bytes | None:
        with self.lock:
            return self.frame

    def get_frame_bgr(self):
        with self.lock:
            return self.frame_bgr

    def set_status(self, status: str, error: str = ""):
        self.status = status
        self.error = error
        if status == "streaming" and self.uptime_start is None:
            self.uptime_start = time.time()
        elif status != "streaming":
            self.uptime_start = None

    def to_dict(self) -> dict:
        uptime = 0
        if self.uptime_start:
            uptime = int(time.time() - self.uptime_start)
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
        }


state = CameraState()


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


def rtsp_read_loop(cap: cv2.VideoCapture):
    """Читаем кадры из RTSP и кодируем в JPEG для раздачи."""
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    fps_counter = 0
    fps_timer = time.time()
    frame_interval = 1.0 / MAX_FPS if MAX_FPS > 0 else 0
    last_frame_time = 0
    last_good_frame_time = time.time()
    consecutive_errors = 0

    state.set_status("streaming")
    log.info(f"RTSP read loop started (max_fps={MAX_FPS}, quality={JPEG_QUALITY})")

    try:
        while True:
            ret, frame = cap.read()

            if not ret or frame is None:
                consecutive_errors += 1
                # Пустые чтения нормальны при медленном потоке.
                # Переподключаем только если >15 сек без единого кадра
                no_frame_sec = time.time() - last_good_frame_time
                if no_frame_sec > 15:
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
                rtsp_read_loop(cap)
            else:
                # 3. Fallback на DVRIP
                log.warning("RTSP недоступен — переход на DVRIP snapshot")
                dvrip_snapshot_loop(camera_ip)

        except Exception as e:
            log.error(f"Критическая ошибка: {e}")
            state.set_status("error", str(e))

        log.info(f"Переподключение через {RECONNECT_DELAY} сек...")
        time.sleep(RECONNECT_DELAY)


# ─── Тревоги (DVRIP OPFileQuery) ──────────────────────────────────────────────

alarm_store = {
    "alarms": [],  # список тревог [{time, end_time, type, file, size, photo_file}, ...]
    "last_check": None,
    "lock": threading.Lock(),
    "known_files": set(),  # уже виденные файлы, чтобы не дублировать
    "last_alarm_time": 0,  # timestamp последней тревоги (для cooldown)
    "callback_active": False,  # alarm callback запущен?
}

os.makedirs(ALARM_PHOTOS_DIR, exist_ok=True)


def send_telegram(text: str, photo_bytes: bytes | None = None):
    """Отправляет сообщение (и фото) в Telegram. Не падает при ошибках."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        if photo_bytes:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            boundary = "----FormBoundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{TELEGRAM_CHAT_ID}\r\n'
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="caption"\r\n\r\n{text}\r\n'
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="photo"; filename="alarm.jpg"\r\n'
                    f"Content-Type: image/jpeg\r\n\r\n"
                ).encode()
                + photo_bytes
                + f"\r\n--{boundary}--\r\n".encode()
            )
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = json.dumps(
                {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
            ).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
        urllib.request.urlopen(req, timeout=10)
        log.info(f"Telegram: отправлено")
    except Exception as e:
        log.warning(f"Telegram ошибка: {e}")


def query_alarms(cam, begin: str, end: str, file_type: str = "jpg") -> list:
    """Запрос тревог через DVRIP OPFileQuery."""
    query = {
        "Name": "OPFileQuery",
        "OPFileQuery": {
            "BeginTime": begin,
            "EndTime": end,
            "Channel": 0,
            "DriverTypeMask": "0x0000FFFF",
            "Event": "M" if file_type == "h264" else "*",
            "Type": file_type,
            "StreamType": "Main",
        },
    }
    try:
        res = cam.send(1440, query)
        if not res:
            return []
        data = res.get("OPFileQuery", res)
        if isinstance(data, dict) and "FileList" in data:
            data = data["FileList"]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        log.warning(f"OPFileQuery ошибка: {e}")
        return []


def parse_alarm_event(code: str) -> str:
    """Расшифровка кода тревоги."""
    mapping = {
        "M": "Движение",
        "H": "Человек",
        "V": "Маска камеры",
        "L": "Потеря видео",
        "A": "Локальная тревога",
        "*": "Событие",
    }
    return mapping.get(code, code)


def capture_alarm_snapshot(cam) -> bytes | None:
    """Делает OPSNAP снимок через DVRIP. Проверено: 100% надёжно, ~230мс, ~36КБ JPEG."""
    try:
        data = cam.snapshot(channel=0)
        if data and len(data) > 100 and data[:2] == b"\xff\xd8":
            return bytes(data)
    except Exception as e:
        log.warning(f"OPSNAP ошибка: {e}")
    return None


def dvrip_opsnap(ip: str) -> bytes | None:
    """Fallback: отдельный DVRIP логин и OPSNAP."""
    if (not HAS_DVRIP) or (DVRIPCam is None):
        return None
    cam = None
    try:
        cam = DVRIPCam(ip, port=DVRIP_PORT, user=CAMERA_USER, password=CAMERA_PASS)
        if not cam.login():
            return None
        return capture_alarm_snapshot(cam)
    except Exception as e:
        log.warning(f"OPSNAP fallback error: {e}")
        return None
    finally:
        if cam:
            try:
                cam.close()
            except Exception:
                pass


def capture_frame_from_buffer() -> bytes | None:
    """
    Берёт текущий кадр из RTSP буфера (уже в памяти).
    Полноразмерный 2304x2592 JPEG, задержка ~0мс.
    """
    frame = state.get_frame()
    if frame and len(frame) > 100:
        return frame
    return None


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _find_closest_motion_file(files: list[dict], target: datetime) -> dict | None:
    best = None
    best_delta = None
    for f in files:
        bt = _parse_dt(str(f.get("BeginTime", "")))
        if not bt:
            continue
        delta = abs((bt - target).total_seconds())
        if best is None or (best_delta is not None and delta < best_delta):
            best = f
            best_delta = delta
    return best


def extract_alarm_photo_from_motion(
    ip: str, target_dt: datetime, debug: bool = True
) -> tuple[bytes | None, dict]:
    """Новый подход: достаём фото из архивного motion-ролика (Event=M, Type=h264).

    Возвращает (jpeg_bytes|None, meta).
    """
    meta: dict = {
        "ok": False,
        "reason": "init",
        "file": None,
        "begin": None,
        "end": None,
        "chosen_frame_index": None,
    }

    if (not HAS_DVRIP) or (DVRIPCam is None):
        meta["reason"] = "python-dvr_not_available"
        return None, meta

    # Иногда запись появляется в файловом индексе с задержкой.
    for attempt in range(1, 6):
        cam = None
        try:
            cam = DVRIPCam(ip, port=DVRIP_PORT, user=CAMERA_USER, password=CAMERA_PASS)
            if not cam.login():
                meta["reason"] = "dvrip_login_failed"
                return None, meta

            begin = (target_dt - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")
            end = (target_dt + timedelta(seconds=15)).strftime("%Y-%m-%d %H:%M:%S")
            files = query_alarms(cam, begin, end, "h264")
            candidate = _find_closest_motion_file(files, target_dt)
            if not candidate:
                meta["reason"] = f"no_motion_file_found_attempt_{attempt}"
                time.sleep(1.5)
                continue

            fname = str(candidate.get("FileName", ""))
            meta["file"] = fname
            meta["begin"] = candidate.get("BeginTime")
            meta["end"] = candidate.get("EndTime")

            alarm_id = target_dt.strftime("%Y-%m-%d_%H_%M_%S")
            debug_dir = None
            if debug:
                debug_dir = os.path.join(ALARM_PHOTOS_DIR, f"debug_{alarm_id}")

            raw_1426 = download_motion_file_h264(
                ip=ip,
                port=DVRIP_PORT,
                username=CAMERA_USER,
                password=CAMERA_PASS,
                filename=fname,
                begin_time=str(candidate.get("BeginTime", "")),
                end_time=str(candidate.get("EndTime", "")),
                debug_dir=debug_dir,
            )
            res = extract_best_jpeg_from_motion_h264(raw_1426, debug_dir=debug_dir)
            meta["chosen_frame_index"] = res.chosen_frame_index
            meta["reason"] = res.reason
            meta["ok"] = bool(res.ok)
            return res.jpeg_bytes, meta
        except Exception as e:
            meta["reason"] = f"exception: {e}"
            time.sleep(1.0)
        finally:
            if cam:
                try:
                    cam.close()
                except Exception:
                    pass

    return None, meta


def save_alarm_photo(alarm_id: str, jpeg_bytes: bytes) -> str:
    """Сохраняет JPEG тревоги на диск. Возвращает имя файла."""
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", alarm_id)
    filename = f"{safe_id}.jpg"
    filepath = os.path.join(ALARM_PHOTOS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(jpeg_bytes)
    return filename


def get_alarm_photo_bytes(alarm_entry: dict) -> bytes | None:
    """Получает JPEG тревоги: с диска или текущий кадр стрима."""
    # 1. Сохранённое фото на диске
    photo_file = alarm_entry.get("photo_file")
    if photo_file:
        path = os.path.join(ALARM_PHOTOS_DIR, photo_file)
        if os.path.isfile(path) and os.path.getsize(path) > 100:
            with open(path, "rb") as f:
                return f.read()

    # 2. Текущий кадр стрима (fallback)
    return state.get_frame()


def on_alarm_callback(alarm_data, seq_number):
    """
    DVRIP alarm callback — вызывается МГНОВЕННО при тревоге.
    Сохраняет текущий кадр из RTSP буфера (уже в памяти, задержка ~0мс).
    """
    now = time.time()

    # Cooldown: не реагируем чаще чем раз в ALARM_COOLDOWN секунд
    if now - alarm_store["last_alarm_time"] < ALARM_COOLDOWN:
        return
    alarm_store["last_alarm_time"] = now

    dt_now = datetime.now()
    time_str = dt_now.strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"🚨 ТРЕВОГА (callback #{seq_number}): {alarm_data}")

    # Определяем тип тревоги из callback данных
    event_type = "Событие"
    event_code = "*"
    if isinstance(alarm_data, dict):
        channel = alarm_data.get("Channel", alarm_data.get("channel", 0))
        status = alarm_data.get("Status", alarm_data.get("Event", ""))
        if isinstance(status, str):
            status_lower = status.lower()
            if "motiondetect" in status_lower or "md" in status_lower:
                event_type = "Движение"
                event_code = "M"
            elif "human" in status_lower:
                event_type = "Человек"
                event_code = "H"
            elif "videoloss" in status_lower:
                event_type = "Потеря видео"
                event_code = "L"
            elif "videoblind" in status_lower or "mask" in status_lower:
                event_type = "Маска камеры"
                event_code = "V"
            else:
                event_type = status
    elif isinstance(alarm_data, list):
        for item in alarm_data:
            if isinstance(item, dict):
                status = item.get("Status", item.get("Event", ""))
                if isinstance(status, str) and status:
                    status_lower = status.lower()
                    if "motion" in status_lower:
                        event_type = "Движение"
                        event_code = "M"
                    else:
                        event_type = status
                    break

    # Новый подход: достаём фото ИЗ АРХИВНОГО M-РОЛИКА, а не из live-буфера.
    # Важно: делаем в отдельном потоке, чтобы не блокировать callback.
    def job():
        photo = None
        photo_meta = {}
        try:
            photo, photo_meta = extract_alarm_photo_from_motion(
                state.camera_ip or KNOWN_IP,
                dt_now,
                debug=ALARM_DEBUG_DUMP,
            )
            if not photo:
                # Fallback: если архивное фото не получилось, берём текущий кадр (лучше, чем ничего)
                photo = capture_frame_from_buffer() or dvrip_opsnap(
                    state.camera_ip or KNOWN_IP
                )
        except Exception as e:
            log.warning(f"alarm photo extraction failed: {e}")

        photo_file = None
        if photo:
            alarm_id = dt_now.strftime("%Y-%m-%d_%H_%M_%S")
            photo_file = save_alarm_photo(alarm_id, photo)
            log.info(
                f"📷 Фото тревоги (new): {photo_file} ({len(photo):,} байт) meta={photo_meta}"
            )

        alarm_entry = {
            "time": time_str,
            "end_time": time_str,
            "type": event_type,
            "type_code": event_code,
            "file": photo_meta.get("file") or f"callback_seq{seq_number}",
            "size": len(photo) if photo else 0,
            "photo_file": photo_file,
            "source": "realtime",
            "photo_meta": photo_meta,
        }

        with alarm_store["lock"]:
            alarm_store["alarms"] = ([alarm_entry] + alarm_store["alarms"])[
                :ALARM_HISTORY_MAX
            ]
            alarm_store["last_check"] = dt_now.isoformat()
            if alarm_entry["file"]:
                alarm_store["known_files"].add(alarm_entry["file"])

        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            text = f"🚨 {event_type}\n🕐 {time_str}"
            if photo_meta.get("file"):
                text += f"\n📼 {photo_meta.get('file')}"
            send_telegram(text, photo)

    threading.Thread(target=job, daemon=True).start()


def alarm_callback_loop():
    """
    Фоновый поток: держит DVRIP alarm callback соединение.
    При обрыве — переподключается.
    """
    if (not HAS_DVRIP) or (DVRIPCam is None):
        log.warning("DVRIP недоступен — мониторинг тревог отключён")
        return

    log.info("Мониторинг тревог: DVRIP alarm callback (реальное время)")

    while True:
        # Ждём пока камера будет найдена
        if not state.camera_ip:
            time.sleep(5)
            continue

        cam = None
        try:
            cam = DVRIPCam(
                state.camera_ip, port=DVRIP_PORT, user=CAMERA_USER, password=CAMERA_PASS
            )
            if not cam.login():
                log.warning("Alarm callback: DVRIP логин неуспешен")
                time.sleep(10)
                continue

            # Регистрируем callback
            cam.setAlarm(on_alarm_callback)

            # Запускаем alarm listener (отправляет AlarmSet + стартует thread)
            # Делаем thread daemon чтобы не блокировал выход
            log.info("🔔 Alarm callback: подключаюсь...")

            # Ручной запуск: AlarmSet команда
            try:
                cam.send(
                    cam.QCODES["AlarmSet"],
                    {"Name": "", "SessionID": "0x%08X" % cam.session},
                )
            except Exception as e:
                log.warning(f"AlarmSet ошибка: {e} — пробую alarmStart")

            # Запускаем alarm thread
            cam.alarm = threading.Thread(
                name="DVRAlarm%08X" % cam.session,
                target=cam.alarm_thread,
                args=[cam.busy],
                daemon=True,
            )
            cam.alarm.start()
            alarm_store["callback_active"] = True
            log.info("✅ Alarm callback активен — ожидаю тревоги в реальном времени")

            # Держим соединение живым, проверяем thread
            while cam.alarm.is_alive():
                time.sleep(5)

            log.warning("Alarm thread завершился — переподключение")
            alarm_store["callback_active"] = False

        except Exception as e:
            log.warning(f"Alarm callback ошибка: {e}")
            alarm_store["callback_active"] = False
        finally:
            if cam:
                try:
                    cam.close()
                except Exception:
                    pass

        time.sleep(RECONNECT_DELAY)


def alarm_history_poll_loop():
    """
    Backup: периодически опрашивает OPFileQuery для сбора истории тревог.
    Не делает фото (фото делает callback), только пополняет список.
    """
    if (not HAS_DVRIP) or (DVRIPCam is None):
        return

    # Даём время callback-у запуститься
    time.sleep(30)
    log.info(f"Backup: история тревог каждые {ALARM_POLL_INTERVAL}с")

    while True:
        if not state.camera_ip:
            time.sleep(10)
            continue

        cam = None
        try:
            cam = DVRIPCam(
                state.camera_ip, port=DVRIP_PORT, user=CAMERA_USER, password=CAMERA_PASS
            )
            if not cam.login():
                time.sleep(ALARM_POLL_INTERVAL)
                continue

            now = datetime.now()
            begin = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            end = now.strftime("%Y-%m-%d %H:%M:%S")

            # История тревог: именно motion-ролики (Event=M, Type=h264)
            files = query_alarms(cam, begin, end, "h264")

            new_count = 0
            for f in files:
                fname = f.get("FileName", "")
                if fname in alarm_store["known_files"]:
                    continue

                # В h264 motion-именах обычно есть [M]
                event_code = "M"

                alarm_entry = {
                    "time": f.get("BeginTime", ""),
                    "end_time": f.get("EndTime", ""),
                    "type": parse_alarm_event(event_code),
                    "type_code": event_code,
                    "file": fname,
                    "size": 0,
                    "photo_file": None,  # историческая тревога — фото нет
                    "source": "history",
                }

                alarm_store["known_files"].add(fname)
                with alarm_store["lock"]:
                    alarm_store["alarms"] = ([alarm_entry] + alarm_store["alarms"])[
                        :ALARM_HISTORY_MAX
                    ]
                new_count += 1

            if new_count > 0:
                log.info(f"История: +{new_count} тревог из OPFileQuery")

            with alarm_store["lock"]:
                alarm_store["last_check"] = now.isoformat()

        except Exception as e:
            log.warning(f"History poll ошибка: {e}")
        finally:
            if cam:
                try:
                    cam.close()
                except Exception:
                    pass

        time.sleep(ALARM_POLL_INTERVAL)


# ─── HTTP маршруты ─────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/stream")
def stream():
    """MJPEG-поток для <img> тега."""

    cam_mode = (request.args.get("cam", "full") or "full").lower()
    if cam_mode not in {"full", "top", "bottom"}:
        cam_mode = "full"

    def crop_bgr(bgr, mode: str):
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
        if bgr is None:
            return None
        ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return None
        return jpeg.tobytes()

    def generate():
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


@app.route("/alarms")
def api_alarms():
    """JSON со списком тревог."""
    limit = request.args.get("limit", 50, type=int)
    with alarm_store["lock"]:
        return jsonify(
            {
                "alarms": alarm_store["alarms"][:limit],
                "total": len(alarm_store["alarms"]),
                "last_check": alarm_store["last_check"],
            }
        )


@app.route("/alarm_photo")
def alarm_photo():
    """
    Фото тревоги. Приоритет:
      1. Сохранённый JPEG на диске (alarm_photos/)
      2. Извлечение из архивного motion-ролика (DVRIP DownloadStart + decode)
      3. Текущий кадр стрима (fallback)
    ?file=...&start=...&end=...
    """
    fname = request.args.get("file", "")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start:
        return "Нет данных", 400

    # Ищем тревогу в хранилище по имени файла
    alarm_entry = None
    with alarm_store["lock"]:
        for a in alarm_store["alarms"]:
            if a.get("file") == fname or a.get("time") == start:
                alarm_entry = dict(a)  # копия
                break

    if not alarm_entry:
        alarm_entry = {"file": fname, "time": start, "end_time": end or start}

    data = get_alarm_photo_bytes(alarm_entry)
    if data:
        return Response(
            data, mimetype="image/jpeg", headers={"Cache-Control": "max-age=3600"}
        )

    # Если фото нет, но есть file/time — пробуем вытянуть архивное фото на лету.
    try:
        t = _parse_dt(alarm_entry.get("time", ""))
        if t and state.camera_ip:
            jpeg, meta = extract_alarm_photo_from_motion(
                state.camera_ip, t, debug=False
            )
            if jpeg:
                alarm_id = t.strftime("%Y-%m-%d_%H_%M_%S")
                photo_file = save_alarm_photo(alarm_id, jpeg)
                # обновим запись в store
                with alarm_store["lock"]:
                    for a in alarm_store["alarms"]:
                        if a.get("file") == alarm_entry.get("file") or a.get(
                            "time"
                        ) == alarm_entry.get("time"):
                            a["photo_file"] = photo_file
                            a["size"] = len(jpeg)
                            a["photo_meta"] = meta
                            break
                return Response(
                    jpeg,
                    mimetype="image/jpeg",
                    headers={"Cache-Control": "max-age=3600"},
                )
    except Exception as e:
        log.warning(f"alarm_photo on-demand extraction failed: {e}")

    return "Фото недоступно", 404


# ─── Точка входа ──────────────────────────────────────────────────────────────


def main():
    log.info("=" * 50)
    log.info("  Stream Viewer — запуск")
    log.info("=" * 50)

    capture_thread = threading.Thread(target=capture_loop, daemon=True, name="capture")
    capture_thread.start()

    alarm_cb_thread = threading.Thread(
        target=alarm_callback_loop, daemon=True, name="alarm_callback"
    )
    alarm_cb_thread.start()

    alarm_hist_thread = threading.Thread(
        target=alarm_history_poll_loop, daemon=True, name="alarm_history"
    )
    alarm_hist_thread.start()

    log.info(f"Веб-интерфейс: http://localhost:{WEB_PORT}")
    log.info(f"MJPEG поток:   http://localhost:{WEB_PORT}/stream")
    log.info(f"Снимок:        http://localhost:{WEB_PORT}/snapshot")
    log.info(f"Статус JSON:   http://localhost:{WEB_PORT}/status")
    log.info(f"Тревоги JSON:  http://localhost:{WEB_PORT}/alarms")

    app.run(host=WEB_HOST, port=WEB_PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
