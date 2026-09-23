"""The layer rules from PLAN §3, enforced as tests rather than as a convention.

- `domain/` is pure Python, so the state machine stays trivially testable.
- `ai/` knows nothing about Django models, so swapping providers touches only
  that package and the LLM code can never write to the database behind a
  service's back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import work_items

PACKAGE_ROOT = Path(work_items.__file__).parent


def _imported_modules(path: Path) -> set[str]:
    """Every module name imported by `path`, including `from x import y` forms."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _python_files(package: str) -> list[Path]:
    files = sorted((PACKAGE_ROOT / package).rglob("*.py"))
    assert files, f"no Python files found under {package}/ — did the layout change?"
    return files


@pytest.mark.parametrize("path", _python_files("domain"), ids=lambda p: p.name)
def test_domain_does_not_import_django(path: Path) -> None:
    offenders = {name for name in _imported_modules(path) if name.split(".")[0] == "django"}
    assert not offenders, (
        f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(offenders)}. "
        "domain/ must stay pure Python (PLAN §3)."
    )


@pytest.mark.parametrize("path", _python_files("ai"), ids=lambda p: p.name)
def test_ai_does_not_import_django_models(path: Path) -> None:
    offenders = {
        name
        for name in _imported_modules(path)
        if name.startswith("work_items.models") or name == "django.db.models"
    }
    assert not offenders, (
        f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(offenders)}. "
        "ai/ takes a plain dataclass and returns a validated result (PLAN §3)."
    )
