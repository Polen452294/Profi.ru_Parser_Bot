#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
    echo "ОШИБКА: сначала выполните: bash install.sh"
    exit 1
fi

export RUN_BROWSER_INTEGRATION=1
export PYTHONUTF8=1
exec .venv/bin/python -m unittest tests.test_integration_fake_profi -v
