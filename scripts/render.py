"""Jinja2 renderer with HTML autoescape."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"

# Singleton — reused across all render() calls to avoid recompiling templates
_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=False,
            lstrip_blocks=False,
        )
    return _env


def render(template_name: str, context: dict[str, Any]) -> str:
    return _get_env().get_template(template_name).render(**context)
