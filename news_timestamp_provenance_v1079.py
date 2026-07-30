# -*- coding: utf-8 -*-
"""Verify publisher publication time for aggregator-sourced news."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import threading
import time
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")
_GOOGLE_HOSTS = {"news.google.com", "news.google.com.tw"}
_CACHE: dict[str, tuple[float, "TimestampProvenance"]] = {}
_LOCK = threading.Lock()
_TTL = 6 * 60 * 60
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TINO-NewsTimestampTruthGuard/1.0)"}


@dataclass(frozen=True)
class TimestampProvenance:
    status: str
    publisher_published_at: str
    publisher_modified_at: str
    aggregator_seen_at: str
    publisher_url: str
    reason: str

    @property
    def model_eligible(self) -> bool:
        return self.status == "verified"

    @property
    def display_time(self) -> str:
        if self.publisher_published_at:
            return self.publisher_published_at
        if self.aggregator_seen_at:
            return f"原始日期待驗證｜收錄 {self.aggregator_seen_at}"
        return "原始日期待驗證"

    def tag_tokens(self) -> tuple[str, ...]:
        out = [
            "timestamp_provenance=v1079",
            f"timestamp_status={self.status}",
            f"timestamp_verified={1 if self.model_eligible else 0}",
        ]
        if self.publisher_published_at:
            out.append(f"publisher_published_at={self.publisher_published_at}")
        if self.publisher_modified_at:
            out.append(f"publisher_modified_at={self.publisher_modified_at}")
        if self.aggregator_seen_at:
            out.append(f"aggregator_seen_at={self.aggregator_seen_at}")
        return tuple(out)


def _datetime(value: Any) -> datetime | None:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed:
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except Exception:
        pass
    raw = raw.replace("Z", "+00:00")
    for text in (raw, raw[:25], raw[:19], raw[:16], raw[:10]):
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.replace(tzinfo=_TAIPEI) if parsed.tzinfo is None else parsed
        except Exception:
            continue
    return None


def _iso(value: datetime | None) -> str:
    return value.astimezone(_TAIPEI).isoformat(timespec="minutes") if value else ""


def _walk(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def extract_publisher_dates(html: str) -> tuple[datetime | None, datetime | None]:
    published: list[datetime] = []
    modified: list[datetime] = []
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html or "", flags=re.I | re.S,
    ):
        try:
            payload = json.loads(match.group(1).strip())
        except Exception:
            continue
        for row in _walk(payload):
            pub, mod = _datetime(row.get("datePublished")), _datetime(row.get("dateModified"))
            if pub:
                published.append(pub)
            if mod:
                modified.append(mod)
    pairs = re.findall(
        r"<meta[^>]+(?:property|name|itemprop)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']+)[\"'][^>]*>",
        html or "", flags=re.I,
    )
    pairs += [
        (name, content) for content, name in re.findall(
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name|itemprop)=[\"']([^\"']+)[\"'][^>]*>",
            html or "", flags=re.I,
        )
    ]
    for name, content in pairs:
        parsed, key = _datetime(content), name.lower()
        if not parsed:
            continue
        if key in {"article:published_time", "datepublished", "date", "pubdate", "publishdate"}:
            published.append(parsed)
        elif key in {"article:modified_time", "datemodified", "last-modified", "lastmodified"}:
            modified.append(parsed)
    return (min(published) if published else None, max(modified) if modified else None)


def assess_timestamp_provenance(
    aggregator_pub_date: str, publisher_url: str, publisher_html: str = ""
) -> TimestampProvenance:
    aggregator = _datetime(aggregator_pub_date)
    published, modified = extract_publisher_dates(publisher_html)
    host = (urlparse(publisher_url or "").hostname or "").lower()
    if published is None:
        reason = "publisher_date_missing" if host and host not in _GOOGLE_HOSTS else "publisher_url_unresolved"
        return TimestampProvenance("unverified", "", _iso(modified), _iso(aggregator), publisher_url, reason)
    gap = abs((aggregator - published).total_seconds()) / 3600 if aggregator else 0
    status = "stale_reindexed" if aggregator and gap > 24 else "verified"
    reason = "aggregator_publisher_conflict" if status == "stale_reindexed" else "publisher_date_verified"
    return TimestampProvenance(status, _iso(published), _iso(modified), _iso(aggregator), publisher_url, reason)


def resolve_aggregator_timestamp(
    aggregator_pub_date: str, aggregator_url: str, *, timeout: float = 3.5
) -> TimestampProvenance:
    key = f"{aggregator_pub_date}|{aggregator_url}"
    with _LOCK:
        cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]
    try:
        request = Request(aggregator_url, headers=_HEADERS)
        with urlopen(request, timeout=timeout) as response:
            publisher_url = response.geturl()
            html = response.read(450_000).decode("utf-8", errors="replace")
        result = assess_timestamp_provenance(aggregator_pub_date, publisher_url, html)
    except Exception:
        result = TimestampProvenance(
            "unverified", "", "", _iso(_datetime(aggregator_pub_date)),
            aggregator_url, "publisher_fetch_failed",
        )
    with _LOCK:
        _CACHE[key] = (time.time(), result)
    return result


def timestamp_is_model_eligible(item: Any) -> bool:
    source = str(getattr(item, "source", "") or (item.get("source") if isinstance(item, Mapping) else ""))
    if "googlenews" not in source.lower():
        return True
    tag = str(getattr(item, "tag", "") or (item.get("tag") if isinstance(item, Mapping) else "")).lower()
    # V1079 fetchers always add the provenance contract.  Preserve compatibility
    # for synthetic tests and non-fetcher legacy rows, while explicitly blocking
    # every row that the new resolver marks unverified or re-indexed.
    if "timestamp_provenance=v1079" not in tag:
        return True
    return "timestamp_status=verified" in tag and "timestamp_verified=1" in tag


def guarded_score(score: float, provenance: TimestampProvenance) -> float:
    return float(score) if provenance.model_eligible else 0.0


def append_provenance_tag(tag: str, provenance: TimestampProvenance) -> str:
    return "|".join([str(tag or "").strip("|"), *provenance.tag_tokens()]).strip("|")
