@echo off
setlocal EnableExtensions
:: Лаунчер ноутбука-«экрана». Сервер камеры работает на Mac mini в Docker,
:: здесь только показываем MJPEG-поток: mpv на весь экран (лёгкий, один процесс),
:: а если mpv не найден — Edge/Chrome в kiosk-режиме через open_browser.ps1.
::
:: Использование:
::   open_viewer.bat                         — адрес выбирается автоматически
::   open_viewer.bat http://IP:5050          — свой адрес сервера (без /stream)
:: Переменные окружения (необязательно):
::   MPV_EXE      — путь к mpv.exe (по умолчанию C:\Users\Admin\tools\mpv\mpv.exe или mpv из PATH)
::   VIEWER_CAM   — режим кадра: full | top | bottom (по умолчанию full)
::   VIEWER_DRY_RUN=1 — только показать выбранный адрес и плеер, ничего не запускать
title Camera Viewer
cd /d "%~dp0"

:: Кандидаты адреса по приоритету:
:: 1) mDNS-имя Mac mini в локальной сети — прямой путь, без шифрования Tailscale
::    и без настройки роутера: имя само следует за IP;
:: 2) MagicDNS-имя в Tailscale — запасной вариант, если mDNS не ответил.
set "URL_LAN=http://Mac-mini-Vladislav.local:5050"
set "URL_TS=http://mac-mini.tail8eadef.ts.net:5050"
if not "%~1"=="" set "URL_LAN=%~1"
if not defined VIEWER_CAM set "VIEWER_CAM=full"

:: Ищем mpv: явный путь, затем папка по умолчанию, затем PATH
if not defined MPV_EXE set "MPV_EXE=C:\Users\Admin\tools\mpv\mpv.exe"
if not exist "%MPV_EXE%" (
  for /f "delims=" %%m in ('where mpv.exe 2^>nul') do if not defined MPV_FOUND set "MPV_FOUND=%%m"
)
if defined MPV_FOUND set "MPV_EXE=%MPV_FOUND%"

:pick_url
:: Ждём, пока сервер ответит хотя бы по одному адресу (после загрузки сеть и
:: Tailscale поднимаются не сразу)
set "BASE_URL="
curl.exe -s -o NUL --max-time 3 "%URL_LAN%/status" && set "BASE_URL=%URL_LAN%"
if not defined BASE_URL curl.exe -s -o NUL --max-time 4 "%URL_TS%/status" && set "BASE_URL=%URL_TS%"
if not defined BASE_URL (
  echo Server not reachable yet, retry in 3s...
  timeout /t 3 /nobreak >nul
  goto pick_url
)
echo Server: %BASE_URL%

:: Режим проверки (например, по SSH): показать выбор и выйти, ничего не запуская
if "%VIEWER_DRY_RUN%"=="1" (
  echo DRY RUN: player="%MPV_EXE%" url="%BASE_URL%/stream?cam=%VIEWER_CAM%"
  if exist "%MPV_EXE%" (echo DRY RUN: mpv found) else (echo DRY RUN: mpv missing, would open browser)
  goto :eof
)

if not exist "%MPV_EXE%" goto browser

echo Starting mpv: %BASE_URL%/stream?cam=%VIEWER_CAM%
:: --untimed: у MJPEG нет таймстемпов, показываем кадр сразу как пришёл.
:: --network-timeout: зависшее соединение завершает mpv, и цикл переподключается.
:: --cursor-autohide=always: курсор не висит поверх картинки.
:: mpv сам не даёт экрану погаснуть, пока идёт видео (stop-screensaver по умолчанию).
start "" /wait "%MPV_EXE%" --no-config --fs --no-border --no-osc --osd-level=0 ^
  --cursor-autohide=always --force-window=immediate --title="Camera" ^
  --demuxer-lavf-format=mpjpeg --profile=low-latency --untimed --cache=no ^
  --hwdec=no --network-timeout=10 --keep-open=no --idle=no --really-quiet ^
  "%BASE_URL%/stream?cam=%VIEWER_CAM%"
:: mpv закрылся (обрыв потока, рестарт сервера или Esc/q) — пауза и заново.
:: Адрес выбираем повторно: вдруг mDNS или Tailscale отвалились.
echo mpv exited, reconnecting in 3s...
timeout /t 3 /nobreak >nul
goto pick_url

:browser
:: Запасной путь без mpv: браузер в kiosk-режиме (open_browser.ps1 ждёт сервер до 90 сек)
echo mpv not found, opening browser: %BASE_URL%/
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0open_browser.ps1" -Url "%BASE_URL%/" -TimeoutSeconds 90
endlocal
