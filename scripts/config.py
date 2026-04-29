"""Configuration loader: env vars + YAML configs."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

load_dotenv(ROOT / ".env")


@dataclass
class ActressEntry:
    id: str
    note: Optional[str] = None


@dataclass
class GenreEntry:
    slug: str
    genre_id: str
    name: str
    description: Optional[str] = None


@dataclass
class Settings:
    api_id: str
    affiliate_id: str
    site_base_url: str
    discord_webhook_url: Optional[str]


def load_settings() -> Settings:
    api_id = os.environ.get("DMM_API_ID", "").strip()
    affiliate_id = os.environ.get("DMM_AFFILIATE_ID", "").strip()
    site_base_url = os.environ.get(
        "SITE_BASE_URL", "https://av-actress-navi.pages.dev"
    ).rstrip("/")
    discord = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() or None
    if not api_id or not affiliate_id:
        raise RuntimeError(
            "DMM_API_ID and DMM_AFFILIATE_ID must be set in .env or environment"
        )
    return Settings(
        api_id=api_id,
        affiliate_id=affiliate_id,
        site_base_url=site_base_url,
        discord_webhook_url=discord,
    )


def load_actresses() -> list[ActressEntry]:
    path = CONFIG_DIR / "actresses.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("actresses") or []
    return [ActressEntry(id=str(e["id"]), note=e.get("note")) for e in raw if e.get("id")]


def save_actresses(entries: list[ActressEntry]) -> None:
    path = CONFIG_DIR / "actresses.yaml"
    payload = {
        "actresses": [
            {"id": e.id, **({"note": e.note} if e.note else {})} for e in entries
        ]
    }
    header = (
        "# 生成対象の女優IDリスト（真実の唯一情報源）\n"
        "# 月次で見直し、コミット履歴で変更を追跡する。\n"
        "# 初回ブートストラップは `python scripts/generate.py --bootstrap-actresses N`\n"
    )
    path.write_text(
        header + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def save_genres(entries: list[GenreEntry]) -> None:
    path = CONFIG_DIR / "genres.yaml"
    payload = {
        "genres": [
            {
                "slug": e.slug,
                "genre_id": e.genre_id,
                "name": e.name,
                **({"description": e.description} if e.description else {}),
            }
            for e in entries
        ]
    }
    header = (
        "# ジャンル一覧（FANZA videoa floor）\n"
        "# genre_id は GenreSearch API で取得した DMM 公式 ID を使う。\n"
    )
    path.write_text(
        header + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_genres() -> list[GenreEntry]:
    path = CONFIG_DIR / "genres.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("genres") or []
    return [
        GenreEntry(
            slug=e["slug"],
            genre_id=str(e["genre_id"]),
            name=e["name"],
            description=e.get("description"),
        )
        for e in raw
        if e.get("slug") and e.get("genre_id") and e.get("name")
    ]
