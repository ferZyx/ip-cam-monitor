@echo off
title Stream Viewer
cd /d "%~dp0"

echo ============================================
echo   Stream Viewer - IP Camera Streaming
echo ============================================
echo.

:: Проверяем Python
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python не найден! Установите Python 3.10+
    pause
    exit /b 1
)

:: Устанавливаем зависимости если нужно
py -c "import flask; import cv2" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Установка зависимостей...
    py -m pip install -r requirements.txt
    echo.
)

:: Открываем фронт в браузере (один раз)
:: Важно: обычный fullscreen через JS блокируется без жеста пользователя.
:: Поэтому пытаемся запустить Edge/Chrome в kiosk/fullscreen режиме.
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0open_browser.ps1" -Url "http://localhost:5050/" -TimeoutSeconds 10

:: Бесконечный цикл — самовосстановление при падении
:loop
echo [%date% %time%] Запуск сервера...
py server.py
echo.
echo [%date% %time%] Сервер упал. Перезапуск через 3 сек...
timeout /t 3 /nobreak >nul
goto loop
