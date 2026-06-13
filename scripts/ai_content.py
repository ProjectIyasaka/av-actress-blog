"""AI-generated actress bios using Claude Haiku with persistent cache."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.dmm_client import ActressDTO

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
AI_CACHE_DIR = ROOT / "data" / "ai_cache"

_PROMPTS = {
    "vr": "VR作品での臨場感・没入感・ヘッドセット越しに感じる魅力を中心に",
    "amateur": "素人っぽさ・リアル感・親近感を中心に",
    "hitozuma": "人妻・熟女としての色気・経験値を中心に",
    "jk": "制服姿の可愛らしさ・清楚感を中心に",
    "ryojoku": "凌辱作品での演技力・迫力を中心に",
    "rookie": "新人女優としてのフレッシュさ・今後の活躍への期待を中心に",
    "general": "全体的な魅力・代表作・個性を中心に",
}


def get_ai_bio(actress: "ActressDTO", site_type: str = "general") -> Optional[str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    site_type = site_type if site_type in _PROMPTS else "general"
    cache_dir = AI_CACHE_DIR / site_type
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{actress.actress_id}.json"

    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))["bio"]
        except Exception:
            pass

    profile_parts = []
    if actress.height:
        profile_parts.append(f"身長{actress.height}cm")
    if actress.cup:
        profile_parts.append(f"{actress.cup}カップ")
    if actress.prefectures:
        profile_parts.append(f"{actress.prefectures}出身")
    profile_str = "・".join(profile_parts) if profile_parts else "プロフィール非公開"

    angle = _PROMPTS[site_type]
    prompt = (
        f"AV女優「{actress.name}」の紹介文を150〜200字で書いてください。\n"
        f"プロフィール: {profile_str}\n"
        f"角度: {angle}\n"
        "条件: 自然な日本語・検索者が求める情報を含める・同じ語句の繰り返しを避ける・体言止めで締める"
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        bio = message.content[0].text.strip()
        cache_path.write_text(json.dumps({"bio": bio}, ensure_ascii=False), encoding="utf-8")
        return bio
    except Exception as e:
        logger.warning("ai_content: failed to generate bio for %s: %s", actress.actress_id, e)
        return None
