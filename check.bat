@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
title Проверка парсера Profi.ru

if not exist ".venv\Scripts\python.exe" (
    echo Парсер ещё не установлен. Сначала запустите install.bat.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" app.py doctor
echo.
pause
