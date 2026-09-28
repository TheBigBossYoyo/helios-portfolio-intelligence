"""Fail fast when the environment cannot actually run Helios.

An unsynced virtualenv is the failure mode this guards: every declared runtime dependency
is importable in ``pyproject.toml`` but absent from the interpreter, so the first symptom is
fifteen pytest collection errors or a traceback deep inside a request. Checking imports up
front turns that into one sentence naming the missing distributions and the command to fix
them.

The check is deliberately import-based rather than metadata-based: a distribution can be
recorded as installed while its module fails to import, and it is the import that matters.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec

#: Declared runtime dependencies mapped to the module each one provides. Keys are the names
#: used on the install command line; values are what ``import`` actually needs to resolve.
#: Kept in step with ``[project].dependencies`` in ``pyproject.toml``.
REQUIRED_RUNTIME_MODULES: dict[str, str] = {
    "alembic": "alembic",
    "anthropic": "anthropic",
    "apscheduler": "apscheduler",
    "aiosqlite": "aiosqlite",
    "defusedxml": "defusedxml",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "keyring": "keyring",
    "numpy": "numpy",
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "PyYAML": "yaml",
    "scipy": "scipy",
    "sqlalchemy": "sqlalchemy",
    "sqlmodel": "sqlmodel",
    "structlog": "structlog",
    "uvicorn": "uvicorn",
}

#: Lower bound from ``requires-python``. The upper bound is intentionally not enforced here:
#: running ahead of the pinned interpreter is a supportable warning, not a hard stop.
MINIMUM_PYTHON: tuple[int, int] = (3, 12)


@dataclass(frozen=True)
class PreflightResult:
    """What the environment is missing, if anything."""

    missing: list[str]
    python_version: tuple[int, int, int]

    @property
    def ok(self) -> bool:
        return not self.missing and self.python_supported

    @property
    def python_supported(self) -> bool:
        return self.python_version[:2] >= MINIMUM_PYTHON


def check_environment() -> PreflightResult:
    """Report every missing runtime dependency, not just the first one.

    Collecting all of them matters: installing one at a time and re-running is the slow path
    this function exists to avoid.
    """
    missing: list[str] = []
    for distribution, module in REQUIRED_RUNTIME_MODULES.items():
        if not _importable(module):
            missing.append(distribution)
    return PreflightResult(
        missing=missing,
        python_version=(sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
    )


def _importable(module: str) -> bool:
    try:
        if find_spec(module) is None:
            return False
    except (ImportError, ValueError):
        return False
    try:
        import_module(module)
    except Exception:
        # A module that is present but explodes on import is as unusable as an absent one.
        return False
    return True


def format_report(result: PreflightResult) -> str:
    """Render a result as the operator-facing message."""
    if result.ok:
        version = ".".join(str(part) for part in result.python_version)
        count = len(REQUIRED_RUNTIME_MODULES)
        return f"helios preflight: OK (Python {version}, {count} deps present)"

    lines = ["helios preflight: FAILED"]
    if not result.python_supported:
        running = ".".join(str(part) for part in result.python_version)
        wanted = ".".join(str(part) for part in MINIMUM_PYTHON)
        lines.append(f"  Python {running} is below the minimum supported {wanted}.")
    if result.missing:
        lines.append(f"  Missing runtime dependencies: {', '.join(result.missing)}")
        lines.append("  Install them with:")
        lines.append('    python -m pip install -e ".[dev]" -c constraints.txt')
    return "\n".join(lines)


def main() -> None:
    result = check_environment()
    report = format_report(result)
    if result.ok:
        print(report)
        return
    print(report, file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
