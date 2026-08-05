from __future__ import annotations

from importlib.metadata import Distribution, distributions
import json
from pathlib import Path
import re
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
REQUIREMENTS_FILE = Path(__file__).with_name("requirements.txt")
PACKAGE_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str | None:
    match = PACKAGE_NAME_RE.match(requirement)
    return normalize_package_name(match.group(1)) if match else None


def direct_requirement_names(path: Path = REQUIREMENTS_FILE) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://")):
            continue
        name = requirement_name(line)
        if name:
            names.add(name)
    return names


def project_packages(
    requirements_path: Path = REQUIREMENTS_FILE,
    installed: list[Distribution] | None = None,
) -> list[tuple[str, str]]:
    installed_by_name: dict[str, Distribution] = {}
    for distribution in installed if installed is not None else list(distributions()):
        name = distribution.metadata.get("Name", "").strip()
        if name:
            installed_by_name[normalize_package_name(name)] = distribution

    pending = list(direct_requirement_names(requirements_path))
    selected: dict[str, tuple[str, str]] = {}
    while pending:
        normalized_name = pending.pop()
        if normalized_name in selected:
            continue
        distribution = installed_by_name.get(normalized_name)
        if distribution is None:
            raise RuntimeError(
                f"Зависимость {normalized_name} из requirements.txt не установлена"
            )
        display_name = distribution.metadata.get("Name", normalized_name).strip()
        selected[normalized_name] = (display_name, distribution.version)
        for dependency in distribution.requires or []:
            dependency_name = requirement_name(dependency)
            if dependency_name in installed_by_name and dependency_name not in selected:
                pending.append(dependency_name)

    return sorted(selected.values(), key=lambda item: normalize_package_name(item[0]))


def query_osv(packages: list[tuple[str, str]]) -> list[tuple[str, str, list[str]]]:
    payload = {
        "queries": [
            {
                "package": {"ecosystem": "PyPI", "name": name},
                "version": version,
            }
            for name, version in packages
        ]
    }
    request = Request(
        OSV_BATCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    findings: list[tuple[str, str, list[str]]] = []
    for (name, version), item in zip(packages, result.get("results", [])):
        identifiers = [
            str(vulnerability.get("id"))
            for vulnerability in item.get("vulns", [])
            if vulnerability.get("id")
        ]
        if identifiers:
            findings.append((name, version, identifiers))
    return findings


def main() -> int:
    try:
        packages = project_packages()
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(f"ОШИБКА: не удалось собрать дерево зависимостей проекта: {exc}")
        return 2
    try:
        findings = query_osv(packages)
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        print(f"ПРЕДУПРЕЖДЕНИЕ: OSV недоступен, аудит пропущен: {exc}")
        return 2

    if findings:
        print("ОШИБКА: найдены известные уязвимости зависимостей:")
        for name, version, identifiers in findings:
            print(f"- {name} {version}: {', '.join(identifiers)}")
        return 1

    print(f"OSV-аудит пройден: проверено пакетов — {len(packages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
