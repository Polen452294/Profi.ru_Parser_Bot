from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Callable, Iterator, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from playwright.sync_api import Browser, BrowserContext, Page

from browser_identity import BrowserIdentity


logger = logging.getLogger("parser.browser_sessions")
DEFAULT_PROFILE_NAME = "profi_desktop"
MOSCOW_GEO_PROFILE_NAME = "profi_desktop_moscow_geo"


class BrowserStorageMode(str, Enum):
    FRESH = "fresh"
    AUTHENTICATED = "authenticated"


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    name: str
    locale: str
    timezone_id: str
    viewport: Mapping[str, int]
    screen: Mapping[str, int]
    device_scale_factor: float
    user_agent: str
    permissions: tuple[str, ...] = ()
    geolocation: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "viewport", MappingProxyType(dict(self.viewport)))
        object.__setattr__(self, "screen", MappingProxyType(dict(self.screen)))
        if self.geolocation is not None:
            object.__setattr__(
                self,
                "geolocation",
                MappingProxyType(dict(self.geolocation)),
            )

    @classmethod
    def from_identity(
        cls,
        identity: BrowserIdentity,
        *,
        name: str = "profi_desktop",
        permissions: tuple[str, ...] = (),
        geolocation: dict[str, float] | None = None,
    ) -> "BrowserProfile":
        return cls(
            name=name,
            locale=identity.locale,
            timezone_id=identity.timezone_id,
            viewport=identity.viewport,
            screen=identity.screen,
            device_scale_factor=identity.device_scale_factor,
            user_agent=identity.user_agent,
            permissions=permissions,
            geolocation=geolocation,
        )

    def context_options(self) -> dict[str, object]:
        options: dict[str, object] = {
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "viewport": dict(self.viewport),
            "screen": dict(self.screen),
            "device_scale_factor": self.device_scale_factor,
            "user_agent": self.user_agent,
        }
        if self.permissions:
            options["permissions"] = list(self.permissions)
        if self.geolocation is not None:
            options["geolocation"] = dict(self.geolocation)
        return options


class BrowserProfileRegistry:
    def __init__(self, profiles: tuple[BrowserProfile, ...] = ()):
        self._profiles: dict[str, BrowserProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: BrowserProfile) -> None:
        if profile.name in self._profiles:
            raise ValueError(f"Browser profile already registered: {profile.name}")
        self._profiles[profile.name] = profile

    def get(self, name: str) -> BrowserProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise KeyError(f"Unknown browser profile: {name}") from exc

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._profiles)


def build_profile_catalog(identity: BrowserIdentity) -> BrowserProfileRegistry:
    """Build the supported coherent environment profiles for one identity."""
    return BrowserProfileRegistry(
        (
            BrowserProfile.from_identity(
                identity,
                name=DEFAULT_PROFILE_NAME,
            ),
            BrowserProfile.from_identity(
                identity,
                name=MOSCOW_GEO_PROFILE_NAME,
                permissions=("geolocation",),
                geolocation={"latitude": 55.7558, "longitude": 37.6173},
            ),
        )
    )


@dataclass(slots=True)
class BrowserSession:
    session_id: str
    profile: BrowserProfile
    storage_mode: BrowserStorageMode
    context: BrowserContext
    page: Page
    _on_close: Callable[[str], None] | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        errors: list[str] = []
        try:
            self.page.close()
        except Exception as exc:
            errors.append(f"page:{type(exc).__name__}")
            logger.warning(
                "Не удалось закрыть page browser session %s: %s",
                self.session_id,
                type(exc).__name__,
            )
        try:
            self.context.close()
        except Exception as exc:
            errors.append(f"context:{type(exc).__name__}")
            logger.warning(
                "Не удалось закрыть context browser session %s: %s",
                self.session_id,
                type(exc).__name__,
            )
        finally:
            self._closed = True
            if self._on_close is not None:
                self._on_close(self.session_id)
        return tuple(errors)


class BrowserSessionManager:
    def __init__(
        self,
        browser: Browser,
        profiles: BrowserProfileRegistry,
        *,
        auth_state_path: Path | None = None,
        extra_http_headers: Mapping[str, str] | None = None,
        init_scripts: tuple[str, ...] = (),
    ):
        self.browser = browser
        self.profiles = profiles
        self.auth_state_path = auth_state_path
        self.extra_http_headers = dict(extra_http_headers or {})
        self.init_scripts = init_scripts
        self._active_sessions: dict[str, BrowserSession] = {}
        self._lock = RLock()

    @property
    def active_session_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._active_sessions)

    @property
    def active_session_count(self) -> int:
        with self._lock:
            return len(self._active_sessions)

    def _forget_session(self, session_id: str) -> None:
        with self._lock:
            self._active_sessions.pop(session_id, None)

    def create_session(
        self,
        profile_name: str,
        *,
        storage_mode: BrowserStorageMode | str = BrowserStorageMode.FRESH,
    ) -> BrowserSession:
        try:
            normalized_mode = BrowserStorageMode(storage_mode)
        except ValueError as exc:
            supported = ", ".join(mode.value for mode in BrowserStorageMode)
            raise ValueError(
                f"Unknown browser storage mode {storage_mode!r}; supported: {supported}"
            ) from exc
        profile = self.profiles.get(profile_name)
        options = profile.context_options()
        if normalized_mode is BrowserStorageMode.AUTHENTICATED:
            if self.auth_state_path is None or not self.auth_state_path.exists():
                raise FileNotFoundError("Authenticated browser session requires storage_state")
            options["storage_state"] = str(self.auth_state_path)

        context = self.browser.new_context(**options)
        try:
            if self.extra_http_headers:
                context.set_extra_http_headers(self.extra_http_headers)
            for script in self.init_scripts:
                context.add_init_script(script=script)
            page = context.new_page()
        except Exception:
            with suppress(Exception):
                context.close()
            raise
        session = BrowserSession(
            session_id=str(uuid4()),
            profile=profile,
            storage_mode=normalized_mode,
            context=context,
            page=page,
            _on_close=self._forget_session,
        )
        with self._lock:
            self._active_sessions[session.session_id] = session
        return session

    def close_all(self) -> dict[str, tuple[str, ...]]:
        with self._lock:
            sessions = tuple(self._active_sessions.values())
        errors: dict[str, tuple[str, ...]] = {}
        for session in sessions:
            session_errors = session.close()
            if session_errors:
                errors[session.session_id] = session_errors
        return errors

    @contextmanager
    def session(
        self,
        profile_name: str,
        *,
        storage_mode: BrowserStorageMode | str = BrowserStorageMode.FRESH,
    ) -> Iterator[BrowserSession]:
        session = self.create_session(profile_name, storage_mode=storage_mode)
        try:
            yield session
        finally:
            session.close()


def identity_launch_options(
    base_options: Mapping[str, object],
    profile: BrowserProfile,
    *,
    stealth: bool,
) -> dict[str, object]:
    options = dict(base_options)
    args = list(options.get("args", []))
    args.append(f"--window-size={profile.screen['width']},{profile.screen['height']}")
    if stealth:
        args.append("--disable-blink-features=AutomationControlled")
    options["args"] = args
    return options


_SNAPSHOT_SCRIPT = """
async () => {
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 16;
  const canvasContext = canvas.getContext('2d');
  canvasContext.fillStyle = '#165DFF';
  canvasContext.font = '12px sans-serif';
  canvasContext.fillText('session', 1, 12);
  const canvasData = canvas.toDataURL();
  let canvasHash = 2166136261;
  for (let index = 0; index < canvasData.length; index++) {
    canvasHash ^= canvasData.charCodeAt(index);
    canvasHash = Math.imul(canvasHash, 16777619);
  }

  const webglCanvas = document.createElement('canvas');
  const gl = webglCanvas.getContext('webgl');
  const debugInfo = gl?.getExtension('WEBGL_debug_renderer_info');
  const databases = indexedDB.databases ? await indexedDB.databases() : [];
  const registrations = 'serviceWorker' in navigator
    ? await navigator.serviceWorker.getRegistrations()
    : [];
  const mediaDevices = navigator.mediaDevices?.enumerateDevices
    ? await navigator.mediaDevices.enumerateDevices()
    : [];
  const permissionStates = {};
  for (const name of ['geolocation', 'notifications']) {
    try {
      permissionStates[name] = (await navigator.permissions.query({name})).state;
    } catch (error) {
      permissionStates[name] = 'unsupported';
    }
  }

  return {
    navigator: {
      userAgent: navigator.userAgent,
      language: navigator.language,
      languages: Array.from(navigator.languages || []),
      platform: navigator.platform,
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemory: navigator.deviceMemory ?? null,
      maxTouchPoints: navigator.maxTouchPoints,
      webdriver: navigator.webdriver ?? null
    },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      deviceScaleFactor: window.devicePixelRatio
    },
    screen: {
      width: screen.width,
      height: screen.height,
      colorDepth: screen.colorDepth
    },
    fingerprints: {
      canvas: (canvasHash >>> 0).toString(16).padStart(8, '0'),
      webglVendor: gl && debugInfo ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : null,
      webglRenderer: gl && debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : null
    },
    storage: {
      localStorageKeys: Object.keys(localStorage).sort(),
      sessionStorageKeys: Object.keys(sessionStorage).sort(),
      indexedDBNames: databases.map((item) => item.name).filter(Boolean).sort(),
      cacheNames: (await caches.keys()).sort(),
      serviceWorkerScopes: registrations.map((item) => item.scope).sort()
    },
    permissions: permissionStates,
    mediaDeviceKinds: mediaDevices.map((item) => item.kind).sort()
  };
}
"""


def collect_browser_snapshot(session: BrowserSession) -> dict[str, object]:
    snapshot = session.page.evaluate(_SNAPSHOT_SCRIPT)
    cookies = session.context.cookies()
    snapshot.update(
        {
            "sessionId": session.session_id,
            "profile": session.profile.name,
            "storageMode": session.storage_mode.value,
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            "cookies": sorted(cookie["name"] for cookie in cookies),
        }
    )
    return snapshot


def snapshot_json(snapshot: Mapping[str, object]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def diff_browser_snapshots(
    left: Mapping[str, object],
    right: Mapping[str, object],
    *,
    ignored_keys: tuple[str, ...] = ("sessionId", "collectedAt"),
) -> dict[str, dict[str, object]]:
    differences: dict[str, dict[str, object]] = {}

    def walk(path: str, first: object, second: object) -> None:
        key = path.rsplit(".", 1)[-1]
        if key in ignored_keys:
            return
        if isinstance(first, Mapping) and isinstance(second, Mapping):
            for child in sorted(set(first) | set(second)):
                child_path = f"{path}.{child}" if path else str(child)
                walk(child_path, first.get(child), second.get(child))
            return
        if first != second:
            differences[path] = {"left": first, "right": second}

    walk("", left, right)
    return differences


@dataclass(frozen=True, slots=True)
class StorageIsolationReport:
    cookies: bool
    local_storage: bool
    session_storage: bool
    indexed_db: bool
    cache_storage: bool
    service_workers: bool
    permissions: bool
    details: dict[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(
            (
                self.cookies,
                self.local_storage,
                self.session_storage,
                self.indexed_db,
                self.cache_storage,
                self.service_workers,
                self.permissions,
            )
        )


def audit_storage_isolation(
    manager: BrowserSessionManager,
    profile_name: str,
    url: str,
    *,
    service_worker_url: str | None = None,
) -> StorageIsolationReport:
    marker = f"isolation-{uuid4().hex}"
    service_worker_marker: str | None = None
    with manager.session(profile_name) as first:
        first.page.goto(url, wait_until="domcontentloaded")
        first.context.add_cookies([{"name": marker, "value": "A", "url": url}])
        first.context.grant_permissions(["geolocation"], origin=url)
        first.page.evaluate(
            """marker => {
              localStorage.setItem(marker, 'A');
              sessionStorage.setItem(marker, 'A');
            }""",
            marker,
        )
        first.page.evaluate(
            """marker => new Promise((resolve, reject) => {
              const request = indexedDB.open(marker, 1);
              request.onupgradeneeded = () => request.result.createObjectStore('values');
              request.onsuccess = () => { request.result.close(); resolve(true); };
              request.onerror = () => reject(request.error);
            })""",
            marker,
        )
        first.page.evaluate(
            """async marker => {
              const cache = await caches.open(marker);
              await cache.put('/isolation-marker', new Response('A'));
            }""",
            marker,
        )
        if service_worker_url:
            parts = urlsplit(service_worker_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query["isolation_session"] = marker
            service_worker_marker = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
            )
            first.page.evaluate(
                "async url => { await navigator.serviceWorker.register(url); await navigator.serviceWorker.ready; }",
                service_worker_marker,
            )

    with manager.session(profile_name) as second:
        second.page.goto(url, wait_until="domcontentloaded")
        cookies = {cookie["name"] for cookie in second.context.cookies()}
        observed = second.page.evaluate(
            """async marker => ({
              localStorage: localStorage.getItem(marker),
              sessionStorage: sessionStorage.getItem(marker),
              indexedDB: indexedDB.databases
                ? (await indexedDB.databases()).map((item) => item.name)
                : [],
              caches: await caches.keys(),
              serviceWorkers: 'serviceWorker' in navigator
                ? (await navigator.serviceWorker.getRegistrations()).map((item) => ({
                    scope: item.scope,
                    scriptURL: (item.active || item.waiting || item.installing)?.scriptURL || null
                  }))
                : [],
              permission: (await navigator.permissions.query({name: 'geolocation'})).state
            })""",
            marker,
        )

    return StorageIsolationReport(
        cookies=marker not in cookies,
        local_storage=observed["localStorage"] is None,
        session_storage=observed["sessionStorage"] is None,
        indexed_db=marker not in observed["indexedDB"],
        cache_storage=marker not in observed["caches"],
        service_workers=(
            service_worker_marker is None
            or all(
                worker.get("scriptURL") != service_worker_marker
                for worker in observed["serviceWorkers"]
            )
        ),
        permissions=observed["permission"] != "granted",
        details=observed,
    )
