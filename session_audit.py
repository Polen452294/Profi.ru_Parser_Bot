from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import sync_playwright

from browser_identity import (
    generate_browser_identity,
    resolve_http_impersonate,
    stealth_init_script,
)
from browser_sessions import (
    DEFAULT_PROFILE_NAME,
    BrowserSessionManager,
    StorageIsolationReport,
    audit_storage_isolation,
    build_profile_catalog,
    collect_browser_snapshot,
    diff_browser_snapshots,
    identity_launch_options,
)
from config import Settings


class _AuditHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/sw-"):
            content_type = "application/javascript"
            body = b"self.addEventListener('fetch', () => {});"
        else:
            content_type = "text/html; charset=utf-8"
            body = b"<!doctype html><title>browser session audit</title>"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@dataclass(frozen=True, slots=True)
class SessionAuditResult:
    storage: StorageIsolationReport
    environment_diff: dict[str, dict[str, object]]
    active_sessions_after_audit: int
    close_errors: dict[str, tuple[str, ...]]

    @property
    def passed(self) -> bool:
        return (
            self.storage.passed
            and not self.environment_diff
            and self.active_sessions_after_audit == 0
            and not self.close_errors
        )


def run_local_session_audit(settings: Settings) -> SessionAuditResult:
    """Audit browser isolation locally without sending requests to Profi.ru."""
    identity = generate_browser_identity(
        user_agent=settings.profi_user_agent,
        impersonate=resolve_http_impersonate(
            settings.profi_user_agent,
            settings.profi_http_impersonate,
        ),
        locale=settings.profi_browser_locale,
        timezone_id=settings.profi_browser_timezone,
    )
    profiles = build_profile_catalog(identity)
    profile = profiles.get(DEFAULT_PROFILE_NAME)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuditHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/"

    browser = None
    manager = None
    close_errors: dict[str, tuple[str, ...]] = {}
    try:
        with sync_playwright() as playwright:
            launch_options = settings.playwright_launch_options(
                headless=True,
                proxy_url=None,
                use_primary_proxy=False,
            )
            browser = playwright.chromium.launch(
                **identity_launch_options(
                    launch_options,
                    profile,
                    stealth=settings.profi_browser_stealth,
                )
            )
            manager = BrowserSessionManager(
                browser,
                profiles,
                extra_http_headers=identity.http_headers,
                init_scripts=(stealth_init_script(identity),)
                if settings.profi_browser_stealth
                else (),
            )
            storage = audit_storage_isolation(
                manager,
                profile.name,
                base_url,
                service_worker_url=f"{base_url}sw-audit.js",
            )
            with manager.session(profile.name) as first:
                first.page.goto(base_url, wait_until="domcontentloaded")
                first_snapshot = collect_browser_snapshot(first)
            with manager.session(profile.name) as second:
                second.page.goto(base_url, wait_until="domcontentloaded")
                second_snapshot = collect_browser_snapshot(second)
            environment_diff = diff_browser_snapshots(
                first_snapshot,
                second_snapshot,
            )
            close_errors = manager.close_all()
            active_sessions = manager.active_session_count
            browser.close()
            browser = None
    finally:
        if manager is not None:
            close_errors.update(manager.close_all())
        if browser is not None:
            browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    return SessionAuditResult(
        storage=storage,
        environment_diff=environment_diff,
        active_sessions_after_audit=active_sessions,
        close_errors=close_errors,
    )
