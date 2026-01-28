import asyncio
import os
import sys
import json
from pathlib import Path
from typing import Any, Dict, List
from asyncio.subprocess import Process

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.enums import ParseMode

from config import Settings
from tg_watcher import read_new_orders
from tg_formatter import format_order
from logger_setup import setup_logger, log_json

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

PARSER_SCRIPT = "main.py"
BOT_POLL_SEC = 3

RESTART_DELAY_SEC = 10          # пауза перед перезапуском парсера
MAX_RESTARTS = 50               # защита от бесконечного рестарта при критической ошибке

CURRENT_PARSER_PROC: Process | None = None



async def start_parser_process(log) -> Process:
    python_exe = sys.executable

    # заставляем подпроцесс печатать в UTF-8 
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    log.info("Starting parser subprocess: %s %s", python_exe, PARSER_SCRIPT)
    proc = await asyncio.create_subprocess_exec(
        python_exe, PARSER_SCRIPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    global CURRENT_PARSER_PROC
    CURRENT_PARSER_PROC = proc
    log.info("Parser started. PID=%s", proc.pid)
    return proc


async def pipe_process_output(proc: Process, log):
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="ignore").rstrip()
        log.info("[PARSER] %s", text)


async def telegram_notifier(log):
    if not BOT_TOKEN:
        log.error("BOT_TOKEN is missing in .env")
        return

    if ADMIN_CHAT_ID == 0:
        log.error("ADMIN_CHAT_ID is missing/invalid in .env")
        return

    bot = Bot(token=BOT_TOKEN)
    log.info("Telegram notifier started. poll=%ss", BOT_POLL_SEC)

    # путь к jsonl с заказами
    s = Settings()
    orders_path = getattr(s, "out_jsonl_path", None) or getattr(s, "out_new_jsonl")
    cursor_path = getattr(s, "bot_cursor_path", "bot_cursor.json")

    orders_file = Path(orders_path)
    offset = load_cursor(cursor_path)

    while True:
        try:
            if orders_file.exists():
                with orders_file.open("r", encoding="utf-8") as f:
                    # ✅ прыгаем на место, где остановились в прошлый раз
                    f.seek(offset)

                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            order = json.loads(line)
                        except Exception:
                            log.warning("Bad json line: %r", line[:200])
                            continue

                        if not isinstance(order, dict):
                            continue

                        text = format_order(order)
                        await bot.send_message(
                            ADMIN_CHAT_ID,
                            text,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )

                    # ✅ сохраняем новую позицию (после чтения)
                    offset = f.tell()
                    save_cursor(cursor_path, offset)

            await asyncio.sleep(BOT_POLL_SEC)

        except Exception:
            log.exception("Telegram notifier error")
            await asyncio.sleep(BOT_POLL_SEC)

async def supervise_parser(runlog):
    global CURRENT_PARSER_PROC
    restarts = 0
    try:
        while True:
            proc = await start_parser_process(runlog)
            CURRENT_PARSER_PROC = proc

            pipe_task = asyncio.create_task(pipe_process_output(proc, runlog))

            try:
                rc = await proc.wait()
            except asyncio.CancelledError:
                raise
            finally:
                # дочитываем вывод, если успеваем
                try:
                    await pipe_task
                except asyncio.CancelledError:
                    pass

            runlog.error("Parser subprocess exited. returncode=%s", rc)

            restarts += 1
            if restarts > MAX_RESTARTS:
                runlog.error("Too many restarts (%d). Stop supervising parser.", restarts)
                return

            runlog.info("Restarting parser in %ss (restart #%d)...", RESTART_DELAY_SEC, restarts)
            await asyncio.sleep(RESTART_DELAY_SEC)

    finally:
        CURRENT_PARSER_PROC = None


async def main():
    runlog = setup_logger("run_all")
    runlog.info("run_all started: parser + telegram in one process.")

    supervise_task = asyncio.create_task(supervise_parser(runlog))
    bot_task = asyncio.create_task(telegram_notifier(setup_logger("bot")))

    tasks = [supervise_task, bot_task]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        runlog.info("Stopped by user (Ctrl+C). Shutting down...")
    finally:
        # 1 отменяем задачи
        for t in tasks:
            t.cancel()

        # 2 останавливаем подпроцесс парсера
        global CURRENT_PARSER_PROC
        if CURRENT_PARSER_PROC and CURRENT_PARSER_PROC.returncode is None:
            runlog.info("Terminating parser subprocess...")
            CURRENT_PARSER_PROC.terminate()
            try:
                await asyncio.wait_for(CURRENT_PARSER_PROC.wait(), timeout=5)
            except Exception:
                runlog.warning("Killing parser subprocess...")
                CURRENT_PARSER_PROC.kill()

        # 3 ждём корректного завершения задач
        await asyncio.gather(*tasks, return_exceptions=True)
        runlog.info("Shutdown complete.")

def load_orders_any_format(path: str, log) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []

    # 1) пробуем JSONL (по строкам)
    orders: List[Dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    orders.append(obj)
        if orders:
            return orders
    except Exception:
        pass

    # 2) пробуем обычный JSON (целиком)
    try:
        with p.open("r", encoding="utf-8") as f:
            obj = json.load(f)

        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
    except Exception as e:
        log.warning("Cannot parse orders file %s: %r", str(p), e)

    return []

def load_cursor(path: str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return int(data.get("offset", 0))
    except Exception:
        return 0

def save_cursor(path: str, offset: int) -> None:
    Path(path).write_text(json.dumps({"offset": offset}, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass