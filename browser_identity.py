from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import get_args
from uuid import uuid4

from curl_cffi.requests.impersonate import BrowserTypeLiteral


_IDENTITY_FILENAME = "profi-browser-identity.json"
_PLATFORM_DISPLAYS = {
    "Windows": (
        (1920, 1080, 1920, 947, 1.0),
        (1536, 864, 1536, 730, 1.0),
        (1440, 900, 1440, 766, 1.0),
        (1366, 768, 1366, 635, 1.0),
    ),
    "macOS": (
        (1728, 1117, 1728, 982, 2.0),
        (1512, 982, 1512, 847, 2.0),
        (1440, 900, 1440, 766, 2.0),
        (1280, 800, 1280, 665, 2.0),
    ),
    "Linux": (
        (1920, 1080, 1920, 947, 1.0),
        (1536, 864, 1536, 730, 1.0),
        (1366, 768, 1366, 635, 1.0),
    ),
}
_DEVICE_PROFILES = {
    ("Windows", "x86"): (
        (
            8,
            8,
            0,
            "Google Inc. (NVIDIA)",
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
        (
            12,
            8,
            0,
            "Google Inc. (AMD)",
            "ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
        (
            4,
            4,
            0,
            "Google Inc. (Intel)",
            "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
    ),
    ("Windows", "arm"): (
        (8, 8, 0, "Google Inc. (Qualcomm)", "ANGLE (Qualcomm, Adreno(TM) 690, OpenGL ES 3.2)"),
        (12, 8, 0, "Google Inc. (Qualcomm)", "ANGLE (Qualcomm, Adreno(TM) 740, OpenGL ES 3.2)"),
    ),
    ("macOS", "x86"): (
        (
            4,
            8,
            0,
            "Google Inc. (Intel Inc.)",
            "ANGLE (Intel Inc., Intel(R) Iris(TM) Plus Graphics 655, OpenGL 4.1)",
        ),
        (
            8,
            8,
            0,
            "Google Inc. (AMD)",
            "ANGLE (AMD, AMD Radeon Pro 5500M OpenGL Engine, OpenGL 4.1)",
        ),
    ),
    ("macOS", "arm"): (
        (8, 8, 0, "Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        (8, 8, 0, "Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"),
    ),
    ("Linux", "x86"): (
        (
            8,
            8,
            0,
            "Google Inc. (Intel)",
            "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)",
        ),
        (
            12,
            8,
            0,
            "Google Inc. (AMD)",
            "ANGLE (AMD, AMD Radeon RX 6600 (RADV NAVI23), OpenGL 4.6)",
        ),
    ),
    ("Linux", "arm"): (
        (4, 4, 0, "Google Inc. (ARM)", "ANGLE (ARM, Mali-G76, OpenGL ES 3.2)"),
        (8, 8, 0, "Google Inc. (ARM)", "ANGLE (ARM, Mali-G78, OpenGL ES 3.2)"),
    ),
}
_PLATFORM_FONTS = {
    "Windows": [
        "Arial",
        "Calibri",
        "Cambria",
        "Consolas",
        "Courier New",
        "Georgia",
        "Segoe UI",
        "Tahoma",
        "Times New Roman",
        "Trebuchet MS",
        "Verdana",
    ],
    "macOS": [
        "Arial",
        "Courier New",
        "Georgia",
        "Helvetica",
        "Helvetica Neue",
        "Menlo",
        "Monaco",
        "Times New Roman",
        "Verdana",
    ],
    "Linux": [
        "Arial",
        "DejaVu Sans",
        "DejaVu Sans Mono",
        "DejaVu Serif",
        "Liberation Mono",
        "Liberation Sans",
        "Liberation Serif",
        "Noto Sans",
    ],
}
_WEBGL_EXTENSIONS = [
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_float_linear",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_multi_draw",
]


@dataclass(frozen=True, slots=True)
class BrowserIdentity:
    identity_id: str
    user_agent: str
    impersonate: str
    locale: str
    timezone_id: str
    platform: str
    client_hint_platform: str
    client_hint_architecture: str
    client_hint_bitness: str
    client_hint_platform_version: str
    client_hint_wow64: bool
    screen_width: int
    screen_height: int
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    canvas_seed: int
    audio_seed: int
    hardware_concurrency: int
    device_memory: int
    max_touch_points: int
    fonts: list[str]
    webgl_vendor: str
    webgl_renderer: str
    webgl_extensions: list[str]

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


def _platform_for_user_agent(
    user_agent: str,
) -> tuple[str, str, str, str, str, bool]:
    lowered = user_agent.lower()
    if "windows" in lowered:
        architecture = "arm" if "arm64" in lowered else "x86"
        bitness = "64" if any(marker in lowered for marker in ("win64", "x64", "arm64")) else "32"
        wow64 = "wow64" in lowered
        version_match = re.search(r"Windows NT (\d+)\.(\d+)", user_agent, re.IGNORECASE)
        platform_version = (
            f"{version_match.group(1)}.{version_match.group(2)}.0"
            if version_match
            else "10.0.0"
        )
        return "Win32", "Windows", architecture, bitness, platform_version, wow64
    if "macintosh" in lowered or "mac os" in lowered:
        architecture = "arm" if any(marker in lowered for marker in (" arm", "arm64", "aarch64")) else "x86"
        version_match = re.search(
            r"Mac OS X (\d+)[_.](\d+)(?:[_.](\d+))?",
            user_agent,
            re.IGNORECASE,
        )
        platform_version = (
            ".".join(
                [
                    version_match.group(1),
                    version_match.group(2),
                    version_match.group(3) or "0",
                ]
            )
            if version_match
            else "13.0.0"
        )
        return "MacIntel", "macOS", architecture, "64", platform_version, False
    architecture = "arm" if any(marker in lowered for marker in ("arm64", "aarch64")) else "x86"
    navigator_platform = "Linux aarch64" if architecture == "arm" else "Linux x86_64"
    bitness = "64" if architecture == "arm" or "x86_64" in lowered else "32"
    return navigator_platform, "Linux", architecture, bitness, "", False


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
    (
        platform,
        client_hint_platform,
        client_hint_architecture,
        client_hint_bitness,
        client_hint_platform_version,
        client_hint_wow64,
    ) = _platform_for_user_agent(user_agent)
    displays = _PLATFORM_DISPLAYS[client_hint_platform]
    candidates = [
        display
        for display in displays
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
        random.choice(candidates or list(displays))
    )
    device_profiles = _DEVICE_PROFILES[
        (client_hint_platform, client_hint_architecture)
    ]
    device_candidates = [
        profile
        for profile in device_profiles
        if previous is None or profile[4] != previous.webgl_renderer
    ]
    (
        hardware_concurrency,
        device_memory,
        max_touch_points,
        webgl_vendor,
        webgl_renderer,
    ) = random.choice(device_candidates or list(device_profiles))
    identity_id = uuid4().hex[:12]
    return BrowserIdentity(
        identity_id=identity_id,
        user_agent=user_agent,
        impersonate=impersonate,
        locale=locale,
        timezone_id=timezone_id,
        platform=platform,
        client_hint_platform=client_hint_platform,
        client_hint_architecture=client_hint_architecture,
        client_hint_bitness=client_hint_bitness,
        client_hint_platform_version=client_hint_platform_version,
        client_hint_wow64=client_hint_wow64,
        screen_width=screen_width,
        screen_height=screen_height,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        device_scale_factor=scale,
        canvas_seed=random.randrange(1, 2**31),
        audio_seed=random.randrange(1, 2**31),
        hardware_concurrency=hardware_concurrency,
        device_memory=device_memory,
        max_touch_points=max_touch_points,
        fonts=list(_PLATFORM_FONTS[client_hint_platform]),
        webgl_vendor=webgl_vendor,
        webgl_renderer=webgl_renderer,
        webgl_extensions=list(_WEBGL_EXTENSIONS),
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
            "clientHintArchitecture": identity.client_hint_architecture,
            "clientHintBitness": identity.client_hint_bitness,
            "clientHintPlatformVersion": identity.client_hint_platform_version,
            "clientHintWow64": identity.client_hint_wow64,
            "chromeMajor": identity.chrome_major,
            "languages": identity.languages,
            "canvasSeed": identity.canvas_seed,
            "audioSeed": identity.audio_seed,
            "hardwareConcurrency": identity.hardware_concurrency,
            "deviceMemory": identity.device_memory,
            "maxTouchPoints": identity.max_touch_points,
            "fonts": identity.fonts,
            "webglVendor": identity.webgl_vendor,
            "webglRenderer": identity.webgl_renderer,
            "webglExtensions": identity.webgl_extensions,
        },
        ensure_ascii=False,
    )
    return rf"""
(() => {{
  const identity = {values};
  Object.defineProperty(Navigator.prototype, 'webdriver', {{ get: () => undefined }});
  Object.defineProperty(Navigator.prototype, 'platform', {{ get: () => identity.platform }});
  Object.defineProperty(Navigator.prototype, 'vendor', {{ get: () => 'Google Inc.' }});
  Object.defineProperty(Navigator.prototype, 'languages', {{ get: () => identity.languages }});
  Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {{
    get: () => identity.hardwareConcurrency
  }});
  Object.defineProperty(Navigator.prototype, 'deviceMemory', {{
    get: () => identity.deviceMemory
  }});
  Object.defineProperty(Navigator.prototype, 'maxTouchPoints', {{
    get: () => identity.maxTouchPoints
  }});
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
        architecture: identity.clientHintArchitecture,
        bitness: identity.clientHintBitness,
        brands,
        fullVersionList: brands.map((brand) => ({{
          ...brand, version: `${{brand.version}}.0.0.0`
        }})),
        formFactors: ['Desktop'],
        mobile: false,
        model: '',
        platform: identity.clientHintPlatform,
        platformVersion: identity.clientHintPlatformVersion,
        uaFullVersion: `${{identity.chromeMajor}}.0.0.0`,
        wow64: identity.clientHintWow64
      }};
      return Object.assign(
        {{ brands, mobile: false, platform: identity.clientHintPlatform }},
        Object.fromEntries(
          hints.filter((hint) => hint in values).map((hint) => [hint, values[hint]])
        )
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

  const hash = (seed, value) => {{
    let result = (seed ^ value) >>> 0;
    result = Math.imul(result ^ (result >>> 16), 0x45d9f3b);
    result = Math.imul(result ^ (result >>> 16), 0x45d9f3b);
    return (result ^ (result >>> 16)) >>> 0;
  }};

  const perturbPixels = (pixels, width, seed) => {{
    for (let offset = 0; offset < pixels.length; offset += 4) {{
      const pixel = offset >>> 2;
      const noise = hash(seed, pixel + width);
      if ((noise & 63) === 0) {{
        const channel = noise % 3;
        const index = offset + channel;
        pixels[index] = Math.max(0, Math.min(255, pixels[index] + ((noise & 64) ? 1 : -1)));
      }}
    }}
    return pixels;
  }};

  const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
    const imageData = originalGetImageData.apply(this, args);
    perturbPixels(imageData.data, imageData.width, identity.canvasSeed);
    return imageData;
  }};

  const noisyCanvas = (canvas) => {{
    if (!canvas.width || !canvas.height) return canvas;
    const clone = document.createElement('canvas');
    clone.width = canvas.width;
    clone.height = canvas.height;
    const context = clone.getContext('2d');
    if (!context) return canvas;
    context.drawImage(canvas, 0, 0);
    const imageData = originalGetImageData.call(context, 0, 0, clone.width, clone.height);
    perturbPixels(imageData.data, imageData.width, identity.canvasSeed);
    context.putImageData(imageData, 0, 0);
    return clone;
  }};
  const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(...args) {{
    return originalToDataURL.apply(noisyCanvas(this), args);
  }};
  const originalToBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function(...args) {{
    return originalToBlob.apply(noisyCanvas(this), args);
  }};

  const perturbAudio = (values, seed) => {{
    for (let index = 0; index < values.length; index++) {{
      if ((hash(seed, index) & 255) === 0) {{
        const direction = (hash(seed ^ 0x9e3779b9, index) & 1) ? 1 : -1;
        if (values instanceof Float32Array) values[index] += direction * 1e-7;
        else values[index] = Math.max(0, Math.min(255, values[index] + direction));
      }}
    }}
  }};
  for (const [name, Constructor] of [
    ['getFloatFrequencyData', globalThis.AnalyserNode],
    ['getByteFrequencyData', globalThis.AnalyserNode],
    ['getFloatTimeDomainData', globalThis.AnalyserNode],
    ['getByteTimeDomainData', globalThis.AnalyserNode]
  ]) {{
    const original = Constructor?.prototype?.[name];
    if (original) Constructor.prototype[name] = function(array) {{
      const result = original.call(this, array);
      perturbAudio(array, identity.audioSeed);
      return result;
    }};
  }}
  const originalGetChannelData = globalThis.AudioBuffer?.prototype?.getChannelData;
  const audioBuffers = new WeakMap();
  if (originalGetChannelData) AudioBuffer.prototype.getChannelData = function(channel) {{
    const data = originalGetChannelData.call(this, channel);
    let channels = audioBuffers.get(this);
    if (!channels) audioBuffers.set(this, channels = new Set());
    if (!channels.has(channel)) {{
      perturbAudio(data, identity.audioSeed ^ channel);
      channels.add(channel);
    }}
    return data;
  }};

  const normalizeFont = (font) => String(font).replace(/["']/g, '').trim().toLowerCase();
  const availableFonts = new Set(identity.fonts.map(normalizeFont));
  const genericFonts = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui']);
  const originalFontCheck = globalThis.FontFaceSet?.prototype?.check;
  if (originalFontCheck) FontFaceSet.prototype.check = function(font, text) {{
    const family = String(font).replace(/^.*?\d(?:px|pt|em|rem|%)\s*/i, '').split(',')[0];
    const normalized = normalizeFont(family);
    if (availableFonts.has(normalized) || genericFonts.has(normalized)) return true;
    return false;
  }};
  if ('queryLocalFonts' in window) Object.defineProperty(window, 'queryLocalFonts', {{
    value: async () => identity.fonts.map((family) => ({{
      family, fullName: family, postscriptName: family.replace(/\s+/g, '-'), style: 'Regular'
    }}))
  }});

  const webglParameters = new Map([
    [0x1F00, identity.webglVendor],
    [0x1F01, identity.webglRenderer],
    [0x1F02, 'WebGL 1.0 (OpenGL ES 2.0 Chromium)'],
    [0x8B8C, 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)'],
    [0x9245, identity.webglVendor],
    [0x9246, identity.webglRenderer],
    [0x0D33, 16384],
    [0x851C, 16384],
    [0x84E8, 16384],
    [0x8872, 16],
    [0x8B4D, 32],
    [0x8869, 16],
    [0x8B4C, 32],
    [0x8DFB, 1024],
    [0x8DFC, 1024],
    [0x8DFD, 1024],
    [0x8B4A, 16],
    [0x8B49, 1024]
  ]);
  const patchWebGL = (Constructor, webgl2 = false) => {{
    if (!Constructor) return;
    const prototype = Constructor.prototype;
    const originalGetParameter = prototype.getParameter;
    const originalGetSupportedExtensions = prototype.getSupportedExtensions;
    const originalGetExtension = prototype.getExtension;
    const originalReadPixels = prototype.readPixels;
    prototype.getParameter = function(parameter) {{
      if (webgl2 && parameter === 0x1F02) return 'WebGL 2.0 (OpenGL ES 3.0 Chromium)';
      if (webgl2 && parameter === 0x8B8C) return 'WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)';
      if (webglParameters.has(parameter)) return webglParameters.get(parameter);
      return originalGetParameter.call(this, parameter);
    }};
    prototype.getSupportedExtensions = function() {{
      const supported = originalGetSupportedExtensions.call(this) || [];
      return identity.webglExtensions.filter((extension) => supported.includes(extension));
    }};
    prototype.getExtension = function(name) {{
      if (!identity.webglExtensions.includes(name)) return null;
      if (name === 'WEBGL_debug_renderer_info') {{
        return {{ UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 }};
      }}
      return originalGetExtension.call(this, name);
    }};
    prototype.readPixels = function(...args) {{
      const result = originalReadPixels.apply(this, args);
      const pixels = args[6];
      if (pixels instanceof Uint8Array || pixels instanceof Uint8ClampedArray) {{
        perturbPixels(pixels, Number(args[2]) || 1, identity.canvasSeed ^ 0x7f4a7c15);
      }}
      return result;
    }};
  }};
  patchWebGL(globalThis.WebGLRenderingContext);
  patchWebGL(globalThis.WebGL2RenderingContext, true);
}})();
""".strip()
