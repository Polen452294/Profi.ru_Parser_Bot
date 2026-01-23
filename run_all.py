import asyncio
import os
import sys
from asyncio.subprocess import Process

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.enums import ParseMode

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

    try:
        while True:
            try:
                orders, _ = read_new_orders()
                for order in orders:
                    log_json(log, "SEND_ORDER", order)
                    text = format_order(order)
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                await asyncio.sleep(BOT_POLL_SEC)

            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram notifier error")
                await asyncio.sleep(5)

    finally:
        # закрываем aiohttp-сессию, иначе будет "Unclosed client session"
        await bot.session.close()
        log.info("Telegram notifier stopped.")



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



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass