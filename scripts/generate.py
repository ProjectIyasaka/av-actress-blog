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
    GenreEntry,
    Settings,
    load_actresses,
    load_genres,
    load_settings,
    save_actresses,
    save_genres,
)
from scripts.dmm_client import ActressDTO, DMMClient, GenreDTO, ItemDTO
from scripts.render import render
from scripts.validate import (
    validate_actress_index_page,
    validate_actress_page,
    validate_genre_index_page,
    validate_genre_page,
    validate_index_page,
    validate_ranking_page,
)


JST = timezone(timedelta(hours=9))
# ROOT is already defined above (SCRIPT_DIR.parent); no redefinition needed
TMP_BUILD = ROOT / "tmp" / "build"
LOGS_DIR = ROOT / "logs"
MANIFEST_PATH = ROOT / "manifest.json"

ACTRESS_OUT_DIR = ROOT / "actress"
ACTRESS_INDEX_OUT = ROOT / "actress" / "index.html"
GENRE_OUT_DIR = ROOT / "genre"
GENRE_INDEX_OUT = ROOT / "genre" / "index.html"
RANKING_OUT = ROOT / "ranking-top10.html"
GENRE_RANKING_OUT_DIR = ROOT / "ranking" / "genre"
MONTHLY_RANKING_OUT_DIR = ROOT / "ranking" / "monthly"
INDEX_OUT = ROOT / "index.html"
SITEMAP_OUT = ROOT / "sitemap.xml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("generate")


def _ensure_dirs() -> None:
    for d in (
        TMP_BUILD, TMP_BUILD / "actress", TMP_BUILD / "genre",
        TMP_BUILD / "ranking" / "genre", TMP_BUILD / "ranking" / "monthly",
        LOGS_DIR, ACTRESS_OUT_DIR, GENRE_OUT_DIR,
        GENRE_RANKING_OUT_DIR, MONTHLY_RANKING_OUT_DIR,
    ):
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


def _bootstrap_genres(client: DMMClient, n: int) -> list[GenreEntry]:
    """Fetch popular genres to seed genres.yaml."""
    logger.info("bootstrap: collecting genres from GenreSearch API")
    genres = client.search_genres(hits=min(n, 100))[:n]
    entries = [GenreEntry(slug=g.genre_id, genre_id=g.genre_id, name=g.name) for g in genres]
    logger.info("bootstrap: collected %d genres", len(entries))
    return entries


def generate_genre_page(
    client: DMMClient,
    entry: GenreEntry,
    settings: Settings,
    generated_at: str,
    generated_date: str,
) -> Optional[dict]:
    """Returns manifest entry on success, None on skip."""
    works = client.search_items(
        article="genre",
        article_id=entry.genre_id,
        sort="rank",
        hits=20,
    )
    if len(works) < 3:
        logger.warning("genre %s (%s): only %d works, skip", entry.genre_id, entry.name, len(works))
        return {"id": entry.genre_id, "name": entry.name, "status": "skipped", "reason": "insufficient_works"}

    canonical_url = f"{settings.site_base_url}/genre/{entry.slug}.html"
    context = {
        "page": {"genre": entry, "works": works},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("genre.html", context)
    result = validate_genre_page(html)
    if not result.ok:
        logger.error("genre %s: validation failed: %s", entry.genre_id, result.errors)
        return {"id": entry.genre_id, "name": entry.name, "status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "genre" / f"{entry.slug}.html"
    tmp_path.write_text(html, encoding="utf-8")
    final_path = GENRE_OUT_DIR / f"{entry.slug}.html"
    _atomic_replace(tmp_path, final_path)

    page_hash = _content_hash(entry.genre_id, [asdict(w) for w in works])
    thumb_url = next((w.image_large for w in works if w.image_large), None)
    return {
        "id": entry.genre_id,
        "slug": entry.slug,
        "name": entry.name,
        "status": "ok",
        "url": canonical_url,
        "hash": page_hash,
        "works_count": len(works),
        "thumb_url": thumb_url,
    }


def generate_actress_index_page(
    settings: Settings,
    actresses: list[ActressDTO],
    thumb_urls: dict[str, str],
    generated_at: str,
    generated_date: str,
) -> Optional[dict]:
    canonical_url = f"{settings.site_base_url}/actress/"
    actress_view = [
        {"dto": a, "thumb_url": thumb_urls.get(a.actress_id) or a.image_large}
        for a in actresses
    ]
    context = {
        "page": {"actresses": actress_view},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("actress_index.html", context)
    result = validate_actress_index_page(html)
    if not result.ok:
        logger.error("actress index: validation failed: %s", result.errors)
        return {"status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "actress" / "index.html"
    tmp_path.write_text(html, encoding="utf-8")
    _atomic_replace(tmp_path, ACTRESS_INDEX_OUT)
    return {"status": "ok", "url": canonical_url}


def generate_genre_index_page(
    settings: Settings,
    genre_results: list[dict],
    genres_cfg: list[GenreEntry],
    generated_at: str,
    generated_date: str,
) -> Optional[dict]:
    canonical_url = f"{settings.site_base_url}/genre/"
    ok_results = {r["id"]: r for r in genre_results if r.get("status") == "ok"}
    genres_view = []
    for entry in genres_cfg:
        r = ok_results.get(entry.slug)
        if not r:
            continue
        genres_view.append({
            "slug": entry.slug,
            "name": entry.name,
            "description": entry.description,
            "works_count": r.get("works_count", 0),
            "thumb_url": r.get("thumb_url"),
        })
    context = {
        "page": {"genres": genres_view},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("genre_index.html", context)
    result = validate_genre_index_page(html)
    if not result.ok:
        logger.error("genre index: validation failed: %s", result.errors)
        return {"status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "genre" / "index.html"
    tmp_path.write_text(html, encoding="utf-8")
    _atomic_replace(tmp_path, GENRE_INDEX_OUT)
    return {"status": "ok", "url": canonical_url}


def generate_actress_page(
    client: DMMClient,
    entry: ActressEntry,
    settings: Settings,
    related_actresses: list[ActressDTO],
    generated_at: str,
    generated_date: str,
    genre_slug_map: Optional[dict] = None,
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
        hits=12,
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
            "genre_slug_map": genre_slug_map or {},
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

    # Pick best thumbnail: solo/duo work cover → actress profile image → any work cover
    solo_works = [w for w in works if w.actress_count <= 2 and w.image_large]
    if solo_works:
        thumb_url = solo_works[0].image_large
    elif actress.image_large:
        thumb_url = actress.image_large  # avoid compilation covers if no solo work found
    else:
        thumb_url = next((w.image_large for w in works if w.image_large), None)

    page_hash = _content_hash(asdict(actress), [asdict(w) for w in works])
    return {
        "id": entry.id,
        "name": actress.name,
        "status": "ok",
        "url": canonical_url,
        "hash": page_hash,
        "works_count": len(works),
        "thumb_url": thumb_url,
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


def generate_genre_ranking_page(
    client: DMMClient,
    entry: GenreEntry,
    settings: Settings,
    generated_at: str,
    generated_date: str,
    year: int,
) -> Optional[dict]:
    items = client.search_items(
        article="genre",
        article_id=entry.genre_id,
        sort="rank",
        hits=10,
    )
    if len(items) < 5:
        logger.warning("genre ranking %s (%s): only %d items, skip", entry.genre_id, entry.name, len(items))
        return {"id": entry.genre_id, "slug": entry.slug, "name": entry.name, "status": "skipped", "reason": "insufficient_items"}

    canonical_url = f"{settings.site_base_url}/ranking/genre/{entry.slug}.html"
    context = {
        "page": {"ranked_items": items, "genre_name": entry.name, "genre_slug": entry.slug, "year": year},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("ranking_genre.html", context)
    result = validate_ranking_page(html)
    if not result.ok:
        logger.error("genre ranking %s: validation failed: %s", entry.genre_id, result.errors)
        return {"id": entry.genre_id, "slug": entry.slug, "name": entry.name, "status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "ranking" / "genre" / f"{entry.slug}.html"
    tmp_path.write_text(html, encoding="utf-8")
    final_path = GENRE_RANKING_OUT_DIR / f"{entry.slug}.html"
    _atomic_replace(tmp_path, final_path)

    page_hash = _content_hash(entry.genre_id, [asdict(i) for i in items])
    return {
        "id": entry.genre_id,
        "slug": entry.slug,
        "name": entry.name,
        "status": "ok",
        "url": canonical_url,
        "hash": page_hash,
        "items_count": len(items),
    }


def generate_monthly_ranking_page(
    client: DMMClient,
    settings: Settings,
    generated_at: str,
    generated_date: str,
    year: int,
    month: int,
) -> Optional[dict]:
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    gte = f"{year}-{month:02d}-01T00:00:00"
    lte = f"{year}-{month:02d}-{last_day:02d}T23:59:59"
    month_str = f"{month:02d}"
    slug = f"{year}-{month_str}"

    items = client.search_items(
        sort="rank",
        hits=10,
        gte_date=gte,
        lte_date=lte,
    )
    if len(items) < 5:
        logger.warning("monthly ranking %s: only %d items, skip", slug, len(items))
        return {"slug": slug, "status": "skipped", "reason": "insufficient_items"}

    canonical_url = f"{settings.site_base_url}/ranking/monthly/{slug}.html"
    context = {
        "page": {"ranked_items": items, "year": year, "month": month_str},
        "canonical_url": canonical_url,
        "generated_at": generated_at,
        "generated_date": generated_date,
    }
    html = render("ranking_monthly.html", context)
    result = validate_ranking_page(html)
    if not result.ok:
        logger.error("monthly ranking %s: validation failed: %s", slug, result.errors)
        return {"slug": slug, "status": "failed", "reason": ";".join(result.errors)}

    tmp_path = TMP_BUILD / "ranking" / "monthly" / f"{slug}.html"
    tmp_path.write_text(html, encoding="utf-8")
    final_path = MONTHLY_RANKING_OUT_DIR / f"{slug}.html"
    _atomic_replace(tmp_path, final_path)

    page_hash = _content_hash(slug, [asdict(i) for i in items])
    return {
        "slug": slug,
        "status": "ok",
        "url": canonical_url,
        "hash": page_hash,
        "items_count": len(items),
    }


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
    for a in actresses[:12]:
        actress_view.append({"dto": a, "thumb_url": thumb_urls.get(a.actress_id) or a.image_large})
    context = {
        "page": {"actresses": actress_view, "top_item": top_item, "year": year, "total_actresses": len(actresses)},
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
        (f"{base}/actress/", today),
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

    if args.bootstrap_genres:
        genre_entries = _bootstrap_genres(client, args.bootstrap_genres)
        if genre_entries:
            save_genres(genre_entries)
            logger.info("wrote %d genres to config/genres.yaml", len(genre_entries))
            return 0
        logger.error("bootstrap returned 0 genres")
        return 1

    actresses_cfg = load_actresses()
    if args.limit_actresses:
        actresses_cfg = actresses_cfg[: args.limit_actresses]

    genres_cfg = load_genres()
    genre_slug_map = {g.name: g.slug for g in genres_cfg}

    now = datetime.now(JST)
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S %z")
    generated_date = now.strftime("%Y年%m月%d日")
    today_iso = now.strftime("%Y-%m-%d")
    year = now.year

    prev_manifest = _load_prev_manifest()
    prev_pages = {p["id"]: p for p in prev_manifest.get("actress_pages", []) if isinstance(p, dict) and p.get("id")}

    # Pre-fetch actress DTOs for "related actresses" sidebar (no thumb here — built after page gen)
    actress_dtos: list[ActressDTO] = []
    for entry in actresses_cfg:
        a = client.get_actress_by_id(entry.id)
        if a:
            actress_dtos.append(a)
    actress_thumb_urls: dict[str, str] = {}  # populated after actress page generation

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
            client, entry, settings, actress_dtos, generated_at, generated_date, genre_slug_map
        )
        if result:
            actress_results.append(result)
            if result.get("status") == "ok":
                success_count += 1

    # Build thumb URLs from generated actress pages (solo/duo work covers, not compilation images)
    for r in actress_results:
        if r.get("status") == "ok" and r.get("thumb_url"):
            actress_thumb_urls[r["id"]] = r["thumb_url"]

    # Deduplicate: if two actresses share the same thumb, fall back to profile image
    url_counts: dict[str, int] = {}
    for url in actress_thumb_urls.values():
        url_counts[url] = url_counts.get(url, 0) + 1
    dto_map = {a.actress_id: a for a in actress_dtos}
    for aid, url in list(actress_thumb_urls.items()):
        if url_counts[url] > 1 and dto_map.get(aid) and dto_map[aid].image_large:
            actress_thumb_urls[aid] = dto_map[aid].image_large

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

    # Generate actress index (full list)
    actress_index_result = generate_actress_index_page(
        settings, actress_dtos, actress_thumb_urls, generated_at, generated_date
    )
    if actress_index_result and actress_index_result.get("status") != "ok":
        logger.error("actress index generation failed")

    # Generate genre pages
    genre_results: list[dict] = []
    genre_success_count = 0
    for genre_entry in genres_cfg:
        g_result = generate_genre_page(client, genre_entry, settings, generated_at, generated_date)
        if g_result:
            genre_results.append(g_result)
            if g_result.get("status") == "ok":
                genre_success_count += 1
    if genre_success_count:
        logger.info("GENRE: %d genre pages generated", genre_success_count)

    # Genre index page
    genre_index_result = generate_genre_index_page(
        settings, genre_results, genres_cfg, generated_at, generated_date
    )
    if genre_index_result and genre_index_result.get("status") != "ok":
        logger.error("genre index generation failed")

    # Genre ranking pages (top 10 per genre)
    genre_ranking_results: list[dict] = []
    for genre_entry in genres_cfg:
        gr = generate_genre_ranking_page(client, genre_entry, settings, generated_at, generated_date, year)
        if gr:
            genre_ranking_results.append(gr)
    logger.info("GENRE RANKING: %d pages generated", sum(1 for r in genre_ranking_results if r.get("status") == "ok"))

    # Monthly ranking pages (current month + past 2 months)
    monthly_ranking_results: list[dict] = []
    for delta in range(3):
        target = now - timedelta(days=delta * 30)
        mr = generate_monthly_ranking_page(
            client, settings, generated_at, generated_date, target.year, target.month
        )
        if mr:
            monthly_ranking_results.append(mr)
    seen_slugs: set[str] = set()
    deduped_monthly: list[dict] = []
    for r in monthly_ranking_results:
        if r.get("slug") not in seen_slugs:
            seen_slugs.add(r["slug"])
            deduped_monthly.append(r)
    monthly_ranking_results = deduped_monthly
    logger.info("MONTHLY RANKING: %d pages generated", sum(1 for r in monthly_ranking_results if r.get("status") == "ok"))

    # Sitemap
    actress_urls = [
        (r["url"], today_iso)
        for r in actress_results
        if r.get("status") == "ok" and r.get("url")
    ]
    genre_urls = [
        (r["url"], today_iso)
        for r in genre_results
        if r.get("status") == "ok" and r.get("url")
    ]
    if genre_index_result and genre_index_result.get("status") == "ok":
        genre_urls.insert(0, (genre_index_result["url"], today_iso))
    genre_ranking_urls = [
        (r["url"], today_iso)
        for r in genre_ranking_results
        if r.get("status") == "ok" and r.get("url")
    ]
    monthly_ranking_urls = [
        (r["url"], today_iso)
        for r in monthly_ranking_results
        if r.get("status") == "ok" and r.get("url")
    ]
    generate_sitemap(settings, actress_urls + genre_urls + genre_ranking_urls + monthly_ranking_urls, today_iso)

    # Manifest
    manifest = {
        "generated_at": generated_at,
        "last_successful_run": generated_at,
        "consecutive_failures": 0,
        "stats": {
            "total_actress_pages": success_count,
            "skipped_actresses": [r for r in actress_results if r.get("status") != "ok"],
            "ranking_items": ranking_result.get("items_count", 0),
            "genre_ranking_pages": sum(1 for r in genre_ranking_results if r.get("status") == "ok"),
            "monthly_ranking_pages": sum(1 for r in monthly_ranking_results if r.get("status") == "ok"),
        },
        "actress_pages": actress_results,
        "genre_pages": genre_results,
        "genre_ranking_pages": genre_ranking_results,
        "monthly_ranking_pages": monthly_ranking_results,
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
        "--bootstrap-genres",
        type=int,
        metavar="N",
        help="Discover N genres from API and write to config/genres.yaml",
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
