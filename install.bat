@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
title Установка парсера Profi.ru

echo === Установка парсера целевых заявок ===
echo.

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ОШИБКА: Python не найден.
        echo Установите Python 3.10 или новее с сайта python.org.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=python"
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo ОШИБКА: требуется Python 3.10 или новее.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :error
)

echo Устанавливаю зависимости...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
if errorlevel 1 goto :error

echo Проверяю зависимости по базе OSV...
".venv\Scripts\python.exe" audit_dependencies.py
set "AUDIT_EXIT=%errorlevel%"
if "%AUDIT_EXIT%"=="1" goto :security_error
if "%AUDIT_EXIT%"=="2" echo ПРЕДУПРЕЖДЕНИЕ: OSV недоступен, повторите аудит позже.

echo Устанавливаю Chromium...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :error

if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo Создан файл .env. Впишите BOT_TOKEN и PROFI_LOGIN. ADMIN_CHAT_ID необязателен.
) else (
    echo Файл .env уже существует и не был изменён.
)

echo.
echo Установка завершена.
echo 1. Заполните в .env PROFI_LOGIN и при необходимости ADMIN_CHAT_ID
echo 2. Запустите check.bat
echo 3. Запустите login.bat
echo 4. Запустите start.bat
pause
exit /b 0

:error
echo.
echo ОШИБКА: установка не завершена. Скопируйте текст ошибки разработчику.
pause
exit /b 1

:security_error
echo.
echo ОШИБКА: установка остановлена из-за известных уязвимостей зависимостей.
pause
exit /b 1
