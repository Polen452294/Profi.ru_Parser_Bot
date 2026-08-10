from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import get_args
from uuid import uuid4

from curl_cffi.requests.impersonate import BrowserTypeLiteral


_DESKTOP_DISPLAYS = (
    (1920, 1080, 1920, 947, 1.0),
    (1536, 864, 1536, 730, 1.0),
    (1440, 900, 1440, 766, 1.0),
    (1366, 768, 1366, 635, 1.0),
)
_IDENTITY_FILENAME = "profi-browser-identity.json"


@dataclass(frozen=True, slots=True)
class BrowserIdentity:
    identity_id: str
    user_agent: str
    impersonate: str
    locale: str
    timezone_id: str
    platform: str
    client_hint_platform: str
    screen_width: int
    screen_height: int
    viewport_width: int
    viewport_height: int
    device_scale_factor: float

    @property
    def viewport(self) -> dict[str, int]:
        return {"width": self.viewport_width, "height": self.viewport_height}

    @property
    def screen(self) -> dict[str, int]:
        return {"width": self.screen_width, "height": self.screen_height}

    @property
    def chrome_major(self) -> str:
        match = re.search(r"(?:Chrome|Chromium)/(\d+)", self.user_agent)
        return match.group(1) if match else "136"

    @property
    def languages(self) -> list[str]:
        primary = self.locale.split("-", 1)[0]
        return list(dict.fromkeys([self.locale, primary, "en-US", "en"]))

    @property
    def http_headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept-language": ",".join(
                [self.locale, f"{self.locale.split('-', 1)[0]};q=0.9", "en;q=0.8"]
            ),
            "sec-ch-ua": (
                f'"Chromium";v="{self.chrome_major}", '
                f'"Google Chrome";v="{self.chrome_major}", "Not_A Brand";v="99"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{self.client_hint_platform}"',
        }


def _platform_for_user_agent(user_agent: str) -> tuple[str, str]:
    lowered = user_agent.lower()
    if "windows" in lowered:
        return "Win32", "Windows"
    if "macintosh" in lowered or "mac os" in lowered:
        return "MacIntel", "macOS"
    return "Linux x86_64", "Linux"


def resolve_http_impersonate(user_agent: str, configured: str) -> str:
    if configured != "chrome":
        return configured
    match = re.search(r"(?:Chrome|Chromium)/(\d+)", user_agent)
    if match is None:
        return configured
    candidate = f"chrome{match.group(1)}"
    return candidate if candidate in get_args(BrowserTypeLiteral) else configured


def generate_browser_identity(
    *,
    user_agent: str,
    impersonate: str,
    locale: str,
    timezone_id: str,
    previous: BrowserIdentity | None = None,
) -> BrowserIdentity:
    platform, client_hint_platform = _platform_for_user_agent(user_agent)
    candidates = [
        display
        for display in _DESKTOP_DISPLAYS
        if previous is None
        or display[:4]
        != (
            previous.screen_width,
            previous.screen_height,
            previous.viewport_width,
            previous.viewport_height,
        )
    ]
    screen_width, screen_height, viewport_width, viewport_height, scale = (
        random.choice(candidates or list(_DESKTOP_DISPLAYS))
    )
    return BrowserIdentity(
        identity_id=uuid4().hex[:12],
        user_agent=user_agent,
        impersonate=impersonate,
        locale=locale,
        timezone_id=timezone_id,
        platform=platform,
        client_hint_platform=client_hint_platform,
        screen_width=screen_width,
        screen_height=screen_height,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        device_scale_factor=scale,
    )


def identity_path(profile_path: Path) -> Path:
    return profile_path / _IDENTITY_FILENAME


def save_browser_identity(profile_path: Path, identity: BrowserIdentity) -> None:
    profile_path.mkdir(parents=True, exist_ok=True)
    target = identity_path(profile_path)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(identity), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def load_browser_identity(
    *,
    profile_path: Path,
    user_agent: str,
    impersonate: str,
    locale: str,
    timezone_id: str,
) -> BrowserIdentity:
    target = identity_path(profile_path)
    if target.exists():
        try:
            identity = BrowserIdentity(**json.loads(target.read_text(encoding="utf-8")))
            if (
                identity.user_agent == user_agent
                and identity.impersonate == impersonate
                and identity.locale == locale
                and identity.timezone_id == timezone_id
            ):
                return identity
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    identity = generate_browser_identity(
        user_agent=user_agent,
        impersonate=impersonate,
        locale=locale,
        timezone_id=timezone_id,
    )
    save_browser_identity(profile_path, identity)
    return identity


def rotate_browser_identity(
    *,
    profile_path: Path,
    current: BrowserIdentity,
) -> BrowserIdentity:
    identity = generate_browser_identity(
        user_agent=current.user_agent,
        impersonate=current.impersonate,
        locale=current.locale,
        timezone_id=current.timezone_id,
        previous=current,
    )
    save_browser_identity(profile_path, identity)
    return identity


def stealth_init_script(identity: BrowserIdentity) -> str:
    values = json.dumps(
        {
            "platform": identity.platform,
            "clientHintPlatform": identity.client_hint_platform,
            "chromeMajor": identity.chrome_major,
            "languages": identity.languages,
        },
        ensure_ascii=False,
    )
    return f"""
(() => {{
  const identity = {values};
  Object.defineProperty(Navigator.prototype, 'webdriver', {{ get: () => undefined }});
  Object.defineProperty(Navigator.prototype, 'platform', {{ get: () => identity.platform }});
  Object.defineProperty(Navigator.prototype, 'vendor', {{ get: () => 'Google Inc.' }});
  Object.defineProperty(Navigator.prototype, 'languages', {{ get: () => identity.languages }});
  const brands = [
    {{ brand: 'Chromium', version: identity.chromeMajor }},
    {{ brand: 'Google Chrome', version: identity.chromeMajor }},
    {{ brand: 'Not_A Brand', version: '99' }}
  ];
  const userAgentData = {{
    brands,
    mobile: false,
    platform: identity.clientHintPlatform,
    toJSON: () => ({{ brands, mobile: false, platform: identity.clientHintPlatform }}),
    getHighEntropyValues: async (hints) => {{
      const values = {{
        architecture: 'x86', bitness: '64', brands,
        fullVersionList: brands.map((brand) => ({{
          ...brand, version: `${{brand.version}}.0.0.0`
        }})),
        mobile: false, model: '', platform: identity.clientHintPlatform,
        platformVersion: identity.clientHintPlatform === 'Windows' ? '10.0.0' : '',
        uaFullVersion: `${{identity.chromeMajor}}.0.0.0`, wow64: false
      }};
      return Object.fromEntries(
        hints.filter((hint) => hint in values).map((hint) => [hint, values[hint]])
      );
    }}
  }};
  Object.defineProperty(Navigator.prototype, 'userAgentData', {{ get: () => userAgentData }});
  if (!window.chrome) Object.defineProperty(window, 'chrome', {{ value: {{ runtime: {{}} }} }});
  const originalQuery = window.navigator.permissions?.query?.bind(window.navigator.permissions);
  if (originalQuery) {{
    window.navigator.permissions.query = (parameters) =>
      parameters?.name === 'notifications'
        ? Promise.resolve({{ state: Notification.permission }})
        : originalQuery(parameters);
  }}
}})();
""".strip()
