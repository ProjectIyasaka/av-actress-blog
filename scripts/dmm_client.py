"""DMM Affiliate API v3 client with DTO normalization, caching, and retry."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.dmm.com/affiliate/v3"
USER_AGENT = "av-actress-navi-generator/1.0"
TIMEOUT = 10
CACHE_TTL_SECONDS = 24 * 3600
MAX_RETRIES = 3
RATE_LIMIT_SLEEP = 0.5

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


@dataclass
class ItemDTO:
    content_id: str
    title: str
    affiliate_url: str
    image_large: Optional[str] = None
    image_small: Optional[str] = None
    sample_video_url: Optional[str] = None
    price: Optional[str] = None
    release_date: Optional[str] = None
    description: Optional[str] = None
    genre_names: list[str] = field(default_factory=list)


@dataclass
class ActressDTO:
    actress_id: str
    name: str
    ruby: Optional[str] = None
    image_large: Optional[str] = None
    image_small: Optional[str] = None
    bust: Optional[int] = None
    cup: Optional[str] = None
    waist: Optional[int] = None
    hip: Optional[int] = None
    height: Optional[int] = None
    birthday: Optional[str] = None
    blood_type: Optional[str] = None
    hobby: Optional[str] = None
    prefectures: Optional[str] = None
    list_url: Optional[str] = None


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return value[:2] + "***" + value[-2:]


class DMMClient:
    def __init__(self, api_id: str, affiliate_id: str):
        if not api_id or not affiliate_id:
            raise ValueError("DMM_API_ID and DMM_AFFILIATE_ID are required")
        self.api_id = api_id
        self.affiliate_id = affiliate_id
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        sanitized = {k: v for k, v in params.items() if k not in ("api_id", "affiliate_id")}
        h = hashlib.sha1(
            json.dumps(sanitized, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        return CACHE_DIR / f"{endpoint.lower()}_{h}.json"

    def _load_cache(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_cache(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict:
        full_params = {
            "api_id": self.api_id,
            "affiliate_id": self.affiliate_id,
            "output": "json",
            **params,
        }
        cache_path = self._cache_path(endpoint, full_params)
        cached = self._load_cache(cache_path)
        if cached:
            logger.debug("cache hit: %s", cache_path.name)
            return cached

        url = f"{API_BASE}/{endpoint}?{urlencode(full_params)}"
        masked = url.replace(self.api_id, _mask(self.api_id)).replace(
            self.affiliate_id, _mask(self.affiliate_id)
        )
        logger.info("GET %s", masked)

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=TIMEOUT)
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    backoff = (2 ** attempt) * RATE_LIMIT_SLEEP
                    logger.warning(
                        "HTTP %s, retry %d/%d after %.1fs",
                        resp.status_code,
                        attempt + 1,
                        MAX_RETRIES,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                data = resp.json()
                self._save_cache(cache_path, data)
                time.sleep(RATE_LIMIT_SLEEP)
                return data
            except requests.RequestException as e:
                last_exc = e
                backoff = (2 ** attempt) * RATE_LIMIT_SLEEP
                logger.warning(
                    "request failed (%s), retry %d/%d after %.1fs",
                    e,
                    attempt + 1,
                    MAX_RETRIES,
                    backoff,
                )
                time.sleep(backoff)
        raise RuntimeError(f"DMM API failed after {MAX_RETRIES} retries: {last_exc}")

    def search_items(
        self,
        site: str = "FANZA",
        service: str = "digital",
        floor: str = "videoa",
        sort: str = "rank",
        hits: int = 20,
        offset: int = 1,
        article: Optional[str] = None,
        article_id: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> list[ItemDTO]:
        params: dict[str, Any] = {
            "site": site,
            "service": service,
            "floor": floor,
            "sort": sort,
            "hits": hits,
            "offset": offset,
        }
        if article and article_id:
            params["article"] = article
            params["article_id"] = article_id
        if keyword:
            params["keyword"] = keyword
        data = self._request("ItemList", params)
        items_raw = data.get("result", {}).get("items", []) or []
        return [self._normalize_item(raw) for raw in items_raw]

    def search_actresses(
        self,
        keyword: Optional[str] = None,
        initial: Optional[str] = None,
        sort: str = "-id",
        hits: int = 100,
        offset: int = 1,
        actress_id: Optional[str] = None,
    ) -> list[ActressDTO]:
        params: dict[str, Any] = {"sort": sort, "hits": hits, "offset": offset}
        if keyword:
            params["keyword"] = keyword
        if initial:
            params["initial"] = initial
        if actress_id:
            params["actress_id"] = actress_id
        data = self._request("ActressSearch", params)
        actresses_raw = data.get("result", {}).get("actress", []) or []
        return [self._normalize_actress(raw) for raw in actresses_raw]

    def get_actress_by_id(self, actress_id: str) -> Optional[ActressDTO]:
        results = self.search_actresses(actress_id=actress_id, hits=1)
        return results[0] if results else None

    @staticmethod
    def _normalize_item(raw: dict) -> ItemDTO:
        image = raw.get("imageURL") or raw.get("image") or {}
        sample_data = raw.get("sampleMovieURL") or raw.get("sample_data") or {}
        sample_url = (
            sample_data.get("size_720_480")
            or sample_data.get("size_644_414")
            or sample_data.get("sample_m_url")
            or sample_data.get("sample_s_url")
        )
        iteminfo = raw.get("iteminfo") or {}
        genres = [g.get("name", "") for g in (iteminfo.get("genre") or []) if g.get("name")]
        prices = raw.get("prices") or {}
        price = prices.get("price") or raw.get("price")
        return ItemDTO(
            content_id=str(raw.get("content_id") or raw.get("cid") or raw.get("id") or ""),
            title=raw.get("title", ""),
            affiliate_url=raw.get("affiliateURL") or raw.get("affiliate_url") or "",
            image_large=image.get("large"),
            image_small=image.get("small"),
            sample_video_url=sample_url,
            price=str(price) if price else None,
            release_date=raw.get("date") or raw.get("release_date"),
            description=raw.get("description"),
            genre_names=genres,
        )

    @staticmethod
    def _normalize_actress(raw: dict) -> ActressDTO:
        image = raw.get("imageURL") or {}
        list_url = raw.get("listURL") or {}
        list_url_str = list_url.get("digital") or list_url.get("mono") or list_url.get("url")
        return ActressDTO(
            actress_id=str(raw.get("id", "")),
            name=raw.get("name", ""),
            ruby=raw.get("ruby"),
            image_large=image.get("large"),
            image_small=image.get("small"),
            bust=_to_int(raw.get("bust")),
            cup=raw.get("cup"),
            waist=_to_int(raw.get("waist")),
            hip=_to_int(raw.get("hip")),
            height=_to_int(raw.get("height")),
            birthday=raw.get("birthday"),
            blood_type=raw.get("blood_type"),
            hobby=raw.get("hobby"),
            prefectures=raw.get("prefectures"),
            list_url=list_url_str,
        )


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
