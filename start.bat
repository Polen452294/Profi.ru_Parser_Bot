@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
title Парсер целевых заявок Profi.ru

if not exist ".venv\Scripts\python.exe" (
    echo Парсер ещё не установлен. Сначала запустите install.bat.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" app.py run
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Программа завершилась с ошибкой %EXIT_CODE%.
    echo Запустите check.bat и передайте разработчику файлы из папки logs.
    pause
)
exit /b %EXIT_CODE%
