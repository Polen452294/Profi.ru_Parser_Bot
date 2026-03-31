import asyncio
import os
import sys
import json
from pathlib import Path
from asyncio.subprocess import Process

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.enums import ParseMode

from config import Settings
from tg_formatter import format_order
from logger_setup import setup_logger


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

PARSER_SCRIPT = "main.py"
BOT_POLL_SEC = 3

RESTART_DELAY_SEC = 10
MAX_RESTARTS = 50

CURRENT_PARSER_PROC: Process | None = None


async def start_parser_process(log) -> Process:
    python_exe = sys.executable

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    for key in (
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PROXYCHAINS_CONF_FILE",
        "PROXYCHAINS_QUIET_MODE",
        "PROXYRESOLV_DNS",
    ):
        env.pop(key, None)

    log.info("Starting parser subprocess WITHOUT proxychains: %s %s", python_exe, PARSER_SCRIPT)
    proc = await asyncio.create_subprocess_exec(
        python_exe,
        PARSER_SCRIPT,
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
    Path(path).write_text(
        json.dumps({"offset": offset}, ensure_ascii=False),
        encoding="utf-8",
    )


async def telegram_notifier(log):
    if not BOT_TOKEN:
        log.error("BOT_TOKEN is missing in .env")
        return

    if ADMIN_CHAT_ID == 0:
        log.error("ADMIN_CHAT_ID is missing/invalid in .env")
        return

    bot = Bot(token=BOT_TOKEN)
    log.info("Telegram notifier started. poll=%ss", BOT_POLL_SEC)

    s = Settings()
    orders_path = getattr(s, "out_jsonl_path", None) or getattr(s, "out_new_jsonl")
    cursor_path = getattr(s, "bot_cursor_path", "bot_cursor.json")

    orders_file = Path(orders_path)
    offset = load_cursor(cursor_path)

    try:
        while True:
            try:
                if orders_file.exists():
                    with orders_file.open("r", encoding="utf-8") as f:
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

                        offset = f.tell()
                        save_cursor(cursor_path, offset)

                await asyncio.sleep(BOT_POLL_SEC)

            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram notifier error")
                await asyncio.sleep(BOT_POLL_SEC)
    finally:
        await bot.session.close()


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
        for t in tasks:
            t.cancel()

        global CURRENT_PARSER_PROC
        if CURRENT_PARSER_PROC and CURRENT_PARSER_PROC.returncode is None:
            runlog.info("Terminating parser subprocess...")
            CURRENT_PARSER_PROC.terminate()
            try:
                await asyncio.wait_for(CURRENT_PARSER_PROC.wait(), timeout=5)
            except Exception:
                runlog.warning("Killing parser subprocess...")
                CURRENT_PARSER_PROC.kill()

        await asyncio.gather(*tasks, return_exceptions=True)
        runlog.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass