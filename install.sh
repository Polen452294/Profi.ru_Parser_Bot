#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ОШИБКА: python3 не найден. Установите Python 3.10 или новее."
    exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "ОШИБКА: требуется Python 3.10 или новее."
    exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
    echo "Создаю виртуальное окружение .venv..."
    if ! "$PYTHON_BIN" -m venv .venv; then
        echo "ОШИБКА: не удалось создать .venv."
        echo "Для Ubuntu/Debian установите пакет: sudo apt install python3-venv"
        exit 1
    fi
fi

echo "Устанавливаю Python-зависимости..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --upgrade --upgrade-strategy eager -r requirements.txt

echo "Проверяю зависимости по базе OSV..."
set +e
.venv/bin/python audit_dependencies.py
AUDIT_EXIT=$?
set -e
if [[ "$AUDIT_EXIT" -eq 1 ]]; then
    echo "ОШИБКА: установка остановлена из-за известных уязвимостей."
    exit 1
fi
if [[ "$AUDIT_EXIT" -eq 2 ]]; then
    echo "ПРЕДУПРЕЖДЕНИЕ: повторите аудит позже: .venv/bin/python audit_dependencies.py"
fi

echo "Устанавливаю Chromium и системные библиотеки Playwright..."
echo "На Ubuntu/Debian программа может запросить пароль sudo."
.venv/bin/python -m playwright install --with-deps chromium

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Создан файл .env. Заполните BOT_TOKEN и PROFI_LOGIN."
    echo "ADMIN_CHAT_ID необязателен: пустое значение включает открытый режим."
else
    echo "Существующий .env сохранён без изменений."
fi

chmod 600 .env
mkdir -p data logs

echo
echo "Установка завершена."
echo "1. Заполните .env: nano .env"
echo "2. Проверьте проект: bash check.sh"
echo "3. Запустите в терминале: bash start.sh"
echo "4. Для постоянной работы установите сервис: bash service.sh install"
