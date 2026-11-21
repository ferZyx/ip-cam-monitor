@echo off
:: Лаунчер для ноутбука-«экрана»: сервер камеры работает на Mac mini в Docker,
:: здесь только открываем браузер в kiosk-режиме. Python на ноутбуке не нужен.
title Camera Viewer
cd /d "%~dp0"

:: Адрес сервера. Можно передать первым аргументом: open_viewer.bat http://IP:5050/
:: По умолчанию — MagicDNS-имя Mac mini в Tailscale: не зависит от смены IP в локальной сети.
:: Если Tailscale ещё не поднялся при старте системы, open_browser.ps1 подождёт до 90 сек.
set "VIEWER_URL=http://mac-mini.tail8eadef.ts.net:5050/"
if not "%~1"=="" set "VIEWER_URL=%~1"

:: echo на латинице: cmd без chcp 65001 искажает UTF-8 кириллицу
echo Opening %VIEWER_URL%
:: open_browser.ps1 ждёт, пока сервер станет доступен (до 90 сек), затем запускает Edge/Chrome
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0open_browser.ps1" -Url "%VIEWER_URL%" -TimeoutSeconds 90
