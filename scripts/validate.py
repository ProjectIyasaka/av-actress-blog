"""Pre-publish validation: required-element checks on rendered HTML."""
from __future__ import annotations

import re
from dataclasses import dataclass


AFFILIATE_DOMAIN_RE = re.compile(r"https?://(?:dmm\.to|[\w.-]+\.fanza\.com|[\w.-]+\.dmm\.com|[\w.-]+\.dmm\.co\.jp)/")
TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="([^"]+)"', re.IGNORECASE)
IMG_RE = re.compile(r"<img\s", re.IGNORECASE)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_actress_page(html: str) -> ValidationResult:
    errors: list[str] = []
    if not TITLE_RE.search(html):
        errors.append("missing <title>")
    if not CANONICAL_RE.search(html):
        errors.append("missing <link rel='canonical'>")
    if not AFFILIATE_DOMAIN_RE.search(html):
        errors.append("no affiliate link found")
    if not IMG_RE.search(html):
        errors.append("no <img> found")
    if "AUTO-GENERATED" not in html:
        errors.append("missing AUTO-GENERATED marker")
    return ValidationResult(ok=not errors, errors=errors)


def validate_ranking_page(html: str, expected_min_items: int = 5) -> ValidationResult:
    errors: list[str] = []
    if not TITLE_RE.search(html):
        errors.append("missing <title>")
    if not CANONICAL_RE.search(html):
        errors.append("missing <link rel='canonical'>")
    affiliate_links = AFFILIATE_DOMAIN_RE.findall(html)
    if len(affiliate_links) < expected_min_items:
        errors.append(f"only {len(affiliate_links)} affiliate links (expected >= {expected_min_items})")
    if not IMG_RE.search(html):
        errors.append("no <img> found")
    return ValidationResult(ok=not errors, errors=errors)


def validate_index_page(html: str) -> ValidationResult:
    errors: list[str] = []
    if not TITLE_RE.search(html):
        errors.append("missing <title>")
    if not CANONICAL_RE.search(html):
        errors.append("missing <link rel='canonical'>")
    return ValidationResult(ok=not errors, errors=errors)
