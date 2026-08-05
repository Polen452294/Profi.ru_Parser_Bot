#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="profi-parser.service"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$SYSTEMD_USER_DIR/$SERVICE_NAME"

require_systemd() {
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "ОШИБКА: systemd не найден. Запускайте бота командой: bash start.sh"
        exit 1
    fi
}

install_service() {
    require_systemd
    if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        echo "ОШИБКА: сначала выполните: bash install.sh"
        exit 1
    fi
    if [[ ! -f "$PROJECT_DIR/.env" ]]; then
        echo "ОШИБКА: файл .env не найден. Сначала выполните: bash install.sh"
        exit 1
    fi

    mkdir -p "$SYSTEMD_USER_DIR"
    umask 077
    {
        echo "[Unit]"
        echo "Description=Profi.ru target orders parser and Telegram bot"
        echo "Wants=network-online.target"
        echo "After=network-online.target"
        echo
        echo "[Service]"
        echo "Type=simple"
        printf 'WorkingDirectory="%s"\n' "$PROJECT_DIR"
        printf 'ExecStart="%s/.venv/bin/python" "%s/app.py" run\n' \
            "$PROJECT_DIR" "$PROJECT_DIR"
        echo "Restart=always"
        echo "RestartSec=10"
        echo "TimeoutStopSec=30"
        echo "KillSignal=SIGINT"
        echo "NoNewPrivileges=true"
        echo "PrivateTmp=true"
        echo "RestrictSUIDSGID=true"
        echo "RestrictRealtime=true"
        echo "Environment=PYTHONUTF8=1"
        echo "Environment=PYTHONUNBUFFERED=1"
        echo "UMask=0077"
        echo
        echo "[Install]"
        echo "WantedBy=default.target"
    } >"$UNIT_FILE"

    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"

    echo "Сервис установлен и запущен."
    echo "Статус: bash service.sh status"
    echo "Журнал: bash service.sh logs"

    if command -v loginctl >/dev/null 2>&1; then
        local linger
        linger="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
        if [[ "$linger" != "yes" ]]; then
            echo
            echo "Чтобы сервис работал после выхода из SSH и запускался после перезагрузки:"
            echo "sudo loginctl enable-linger $USER"
        fi
    fi
}

case "${1:-status}" in
    install)
        install_service
        ;;
    start|stop|restart)
        require_systemd
        systemctl --user "$1" "$SERVICE_NAME"
        ;;
    status)
        require_systemd
        systemctl --user status "$SERVICE_NAME" --no-pager
        ;;
    logs)
        require_systemd
        journalctl --user -u "$SERVICE_NAME" -f
        ;;
    *)
        echo "Использование: bash service.sh {install|start|stop|restart|status|logs}"
        exit 2
        ;;
esac
