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