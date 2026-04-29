"""Main generator: fetch DMM API, render templates, write to tmp/build, atomically promote."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# package-style relative imports work both when run as `python -m scripts.generate`
# and as `python scripts/generate.py`
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.config import (
    ActressEntry,
    Settings,
    load_actresses,
    load_genres,
    load_settings,
    save_actresses,
)
from scripts.dmm_client import ActressDTO, DMMClient, ItemDTO
from scripts.render import render
from scripts.validate import (
    validate_actress_page,
    validate_index_page,
    validate_ranking_page,
)


JST = timezone(timedelta(hours=9))
# ROOT is already defined above (SCRIPT_DIR.parent); no redefinition needed
TMP_BUILD = ROOT / "tmp" / "build"
LOGS_DIR = ROOT / "logs"
MANIFEST_PATH = ROOT / "manifest.json"

ACTRESS_OUT_DIR = ROOT / "actress"
RANKING_OUT = ROOT / "ranking-top10.html"
INDEX_OUT = ROOT / "index.html"
SITEMAP_OUT = ROOT / "sitemap.xml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("generate")


def _ensure_dirs() -> None:
    for d in (TMP_BUILD, TMP_BUILD / "actress", LOGS_DIR, ACTRESS_OUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _content_hash(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
    return h.hexdigest()[:16]


def _atomic_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _load_prev_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _bootstrap_actresses(client: DMMClient, n: int) -> list[ActressEntry]:
    """Fetch popular actresses to seed actresses.yaml.

    Heuristic: fetch top-ranking video items and collect actress IDs from iteminfo.actress.
    """
    logger.info("bootstrap: collecting actresses from top-ranked items")
    seen: dict[str, str] = {}
    offset = 1
    while len(seen) < n and offset <= 500:
        # we cannot directly query items with iteminfo from the DTO since we strip it.
        # Use raw cache by re-requesting.
        params: dict[str, Any] = {
            "site": "FANZA",
            "service": "digital",
            "floor": "videoa",
            "sort": "rank",
            "hits": 100,
            "offset": offset,
        }
        data = client._request("ItemList", params)  # noqa: SLF001 — internal use
        items = data.get("result", {}).get("items", []) or []
        if not items:
            break
        for item in items:
            iteminfo = item.get("iteminfo") or {}
            for a in iteminfo.get("actress") or []:
                aid = str(a.get("id", "")).strip()
                name = a.get("name", "").strip()
                if aid and aid not in seen:
                    seen[aid] = name
                    if len(seen) >= n:
                        break
            if len(seen) >= n:
                break
        offset += 100
    entries = [ActressEntry(id=aid, note=name) for aid, name in seen.items()]
    logger.info("bootstrap: collected %d actresses", len(entries))
    return entries


def generate_actress_page(
    client: DMMClient,
    entry: ActressEntry,
    settings: Settings,
    related_actresses: list[ActressDTO],
    generated_at: str,
    generated_date: str,
) -> Optional[dict]:
    """Returns manifest entry on success, None on skip."""
    actress = client.get_actress_by_id(entry.id)
    if not actress:
        logger.warning("actress %s: not found, skip", entry.id)
        return {"id": entry.id, "status": "skipped", "reason": "not_found"}

    works = client.search_items(
        article="actress",
        article_id=entry.id,
        sort="-date",
        hits=6,
    )
    if len(works) < 3:
        logger.warning(
            "actress %s (%s): only %d works, skip", entry.id, actress.name, len(works)
        )
        return {"id": entry.id, "status": "skipped", "reason": "insufficient_works"}

    canonical_url = f"{settings.site_base_url}/actress/{entry.id}.html"
    context = {
        "page": {
            "actress": actress,
            "works": works,
            "related_actresses": [a for a in related_actresses if a.actress_id != entry.id][:5],
        },
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("actress.html", context)
    result = validate_actress_page(html)
    if not result.ok:
        logger.error("actress %s: validation failed: %s", entry.id, result.errors)
        return {"id": entry.id, "status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "actress" / f"{entry.id}.html"
    tmp_path.write_text(html, encoding="utf-8")
    final_path = ACTRESS_OUT_DIR / f"{entry.id}.html"
    _atomic_replace(tmp_path, final_path)

    page_hash = _content_hash(asdict(actress), [asdict(w) for w in works])
    return {
        "id": entry.id,
        "name": actress.name,
        "status": "ok",
        "url": canonical_url,
        "hash": page_hash,
        "works_count": len(works),
    }


def generate_ranking_page(
    client: DMMClient, settings: Settings, generated_at: str, generated_date: str, year: int
) -> Optional[dict]:
    items = client.search_items(
        site="FANZA", service="digital", floor="videoa", sort="rank", hits=10
    )
    if len(items) < 5:
        logger.error("ranking: only %d items fetched", len(items))
        return {"status": "failed", "reason": "insufficient_items"}

    canonical_url = f"{settings.site_base_url}/ranking-top10.html"
    context = {
        "page": {"ranked_items": items, "year": year},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("ranking.html", context)
    result = validate_ranking_page(html)
    if not result.ok:
        logger.error("ranking: validation failed: %s", result.errors)
        return {"status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "ranking-top10.html"
    tmp_path.write_text(html, encoding="utf-8")
    _atomic_replace(tmp_path, RANKING_OUT)
    page_hash = _content_hash([asdict(i) for i in items])
    return {"status": "ok", "url": canonical_url, "hash": page_hash, "items_count": len(items)}


def generate_index_page(
    settings: Settings,
    actresses: list[ActressDTO],
    thumb_urls: dict[str, str],
    top_item: Optional[ItemDTO],
    generated_at: str,
    generated_date: str,
    year: int,
) -> Optional[dict]:
    canonical_url = f"{settings.site_base_url}/"
    actress_view = []
    for a in actresses[:8]:
        actress_view.append({"dto": a, "thumb_url": thumb_urls.get(a.actress_id) or a.image_large})
    context = {
        "page": {"actresses": actress_view, "top_item": top_item, "year": year},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("index.html", context)
    result = validate_index_page(html)
    if not result.ok:
        logger.error("index: validation failed: %s", result.errors)
        return {"status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "index.html"
    tmp_path.write_text(html, encoding="utf-8")
    _atomic_replace(tmp_path, INDEX_OUT)
    return {"status": "ok", "url": canonical_url}


def generate_sitemap(
    settings: Settings, actress_urls: list[tuple[str, str]], today: str
) -> None:
    """actress_urls: list of (url, lastmod_iso)"""
    base = settings.site_base_url
    urls = [
        (f"{base}/", today),
        (f"{base}/ranking-top10.html", today),
        (f"{base}/privacy.html", today),
        (f"{base}/contact.html", today),
    ]
    urls.extend(actress_urls)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url, lastmod in urls:
        lines.append(f"  <url><loc>{url}</loc><lastmod>{lastmod}</lastmod></url>")
    lines.append("</urlset>")
    SITEMAP_OUT.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    _ensure_dirs()
    try:
        settings = load_settings()
    except RuntimeError as e:
        logger.error("config error: %s", e)
        return 2

    client = DMMClient(api_id=settings.api_id, affiliate_id=settings.affiliate_id)

    if args.bootstrap_actresses:
        entries = _bootstrap_actresses(client, args.bootstrap_actresses)
        if entries:
            save_actresses(entries)
            logger.info("wrote %d actresses to config/actresses.yaml", len(entries))
            return 0
        logger.error("bootstrap returned 0 actresses")
        return 1

    actresses_cfg = load_actresses()
    if args.limit_actresses:
        actresses_cfg = actresses_cfg[: args.limit_actresses]

    now = datetime.now(JST)
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S %z")
    generated_date = now.strftime("%Y年%m月%d日")
    today_iso = now.strftime("%Y-%m-%d")
    year = now.year

    prev_manifest = _load_prev_manifest()
    prev_pages = {p["id"]: p for p in prev_manifest.get("actress_pages", []) if isinstance(p, dict) and p.get("id")}

    # Pre-fetch actress DTOs for "related actresses" sidebar
    actress_dtos: list[ActressDTO] = []
    actress_thumb_urls: dict[str, str] = {}  # actress_id -> latest work image (hi-res)
    for entry in actresses_cfg:
        a = client.get_actress_by_id(entry.id)
        if a:
            actress_dtos.append(a)
            latest = client.search_items(article="actress", article_id=entry.id, sort="-date", hits=1)
            if latest and latest[0].image_large:
                actress_thumb_urls[entry.id] = latest[0].image_large

    # Generate ranking & top item
    ranking_result = generate_ranking_page(client, settings, generated_at, generated_date, year)
    if not ranking_result or ranking_result.get("status") != "ok":
        logger.error("ranking generation failed; aborting before any push")
        return 3

    top_items = client.search_items(
        site="FANZA", service="digital", floor="videoa", sort="rank", hits=1
    )
    top_item = top_items[0] if top_items else None

    # Generate actress pages
    actress_results: list[dict] = []
    success_count = 0
    for entry in actresses_cfg:
        result = generate_actress_page(
            client, entry, settings, actress_dtos, generated_at, generated_date
        )
        if result:
            actress_results.append(result)
            if result.get("status") == "ok":
                success_count += 1

    # Anomaly detection: drop >20% from previous run, or <50% of configured actresses on first run
    prev_ok = sum(1 for p in prev_pages.values() if p.get("status") == "ok")
    configured = len(actresses_cfg)
    if not args.force:
        if prev_ok > 0 and success_count < prev_ok * 0.8:
            logger.error(
                "anomaly: actress page success count %d < 80%% of previous %d. push aborted (use --force to override)",
                success_count,
                prev_ok,
            )
            return 4
        if prev_ok == 0 and configured > 0 and success_count < configured * 0.5:
            logger.error(
                "anomaly: first run success count %d < 50%% of configured %d. push aborted (use --force to override)",
                success_count,
                configured,
            )
            return 4

    # Generate index
    index_result = generate_index_page(
        settings, actress_dtos, actress_thumb_urls, top_item, generated_at, generated_date, year
    )
    if not index_result or index_result.get("status") != "ok":
        logger.error("index generation failed")
        return 5

    # Sitemap
    actress_urls = [
        (r["url"], today_iso)
        for r in actress_results
        if r.get("status") == "ok" and r.get("url")
    ]
    generate_sitemap(settings, actress_urls, today_iso)

    # Manifest
    manifest = {
        "generated_at": generated_at,
        "last_successful_run": generated_at,
        "consecutive_failures": 0,
        "stats": {
            "total_actress_pages": success_count,
            "skipped_actresses": [r for r in actress_results if r.get("status") != "ok"],
            "ranking_items": ranking_result.get("items_count", 0),
        },
        "actress_pages": actress_results,
        "ranking": ranking_result,
        "index": index_result,
        "generator_version": "0.1.0",
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "DONE: %d actress pages, ranking %d items, index ok",
        success_count,
        ranking_result.get("items_count", 0),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="av-actress-blog generator")
    parser.add_argument(
        "--bootstrap-actresses",
        type=int,
        metavar="N",
        help="Discover N popular actresses from API and write to config/actresses.yaml",
    )
    parser.add_argument(
        "--limit-actresses",
        type=int,
        metavar="N",
        help="Process only first N actresses from config (for testing)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip anomaly detection thresholds",
    )
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
