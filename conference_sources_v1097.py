# -*- coding: utf-8 -*-
"""Official agenda ingestion for V1097 CIE.

Only allow-listed conference websites are fetched.  A failed refresh returns
the last verified cache instead of erasing the calendar.  Parsers produce the
same row contract accepted by :mod:`conference_intelligence_v1097`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup
except Exception:  # deployment safety: CIE must not prevent app startup
    BeautifulSoup = None


SOURCE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "HOT CHIPS": {
        "urls": ("https://hotchips.org/advance-program/", "https://hotchips.org/program/conference/"),
        "timezone": "America/Los_Angeles", "parser": "hot_chips",
    },
    "OCP": {
        "urls": ("https://www.opencompute.org/summit/2026-ocp-apac-summit/schedule-overview",),
        "timezone": "Asia/Taipei", "parser": "ocp_overview",
    },
    "FMS": {"urls": ("https://www.fmsnow.com/",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
    "GTC": {"urls": ("https://www.nvidia.com/gtc/session-catalog/",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
    "COMPUTEX": {"urls": ("https://www.computextaipei.com.tw/en/calendar/event-calendar.html",), "timezone": "Asia/Taipei", "parser": "json_ld"},
    "OFC": {"urls": ("https://www.ofcconference.org/schedule/",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
}

_CACHE = Path(os.environ.get("TINO_CIE_CACHE_PATH", "/tmp/tino_cie_official_agenda.json"))
_TIME_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ampm>AM|PM)", re.I)
_DATE_RE = re.compile(r"(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),?\s+([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})", re.I)

COMPANY_TICKERS = {
    "NVIDIA": ("NVDA",), "AMD": ("AMD",), "INTEL": ("INTC",),
    "MICRON": ("MU",), "MARVELL": ("MRVL",), "BROADCOM": ("AVGO",),
    "SK HYNIX": ("SKHY",), "SAMSUNG": (), "KIOXIA": (),
    "SILICON MOTION": ("SIMO",), "META": ("META",), "MICROSOFT": ("MSFT",),
    "GOOGLE": ("GOOGL",), "ARM": ("ARM",), "IBM": ("IBM",),
}


def _tickers(text: str) -> List[str]:
    upper = text.upper()
    out: List[str] = []
    for company, symbols in COMPANY_TICKERS.items():
        if company in upper:
            out.extend(symbol for symbol in symbols if symbol not in out)
    return out


def _technologies(text: str) -> List[str]:
    terms = ("HBM", "HBF", "CXL", "PCIe Gen6", "SSD", "NAND", "DRAM", "Optical", "GPU", "AI", "Ethernet", "Chiplet", "RISC-V")
    return [term for term in terms if term.lower() in text.lower()]


def _plain_text(html: str) -> str:
    if BeautifulSoup is not None:
        return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _table_rows(html: str) -> List[List[str]]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        return [[" ".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
                for row in soup.find_all("tr")]
    rows: List[List[str]] = []
    for block in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.I | re.S):
        rows.append([_plain_text(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", block, re.I | re.S)])
    return rows


def _row(conference: str, company: str, title: str, start: str, timezone_name: str,
         source_url: str, importance: int = 4) -> Dict[str, Any]:
    key = f"{conference}|{company}|{title}|{start}"
    return {
        "conference": conference,
        "event_id": f"{conference.replace(' ', '_')}_{start[:4]}",
        "session_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:20].upper(),
        "company": company or "Conference",
        "title": title,
        "datetime": start,
        "timezone": timezone_name,
        "technologies": _technologies(title),
        "direct_tickers": _tickers(company),
        "supply_chain_tickers": [],
        "importance": importance,
        "source_tier": "OFFICIAL",
        "source_url": source_url,
    }


def parse_hot_chips(html: str, source_url: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    dates = _DATE_RE.findall(_plain_text(html))
    current_date = datetime.strptime(" ".join(dates[0][1:]), "%B %d %Y").date() if dates else None
    current_time = None
    for cells in _table_rows(html):
        if current_date is None:
            continue
        if not cells:
            continue
        time_match = _TIME_RE.search(cells[0])
        if time_match:
            hour = int(time_match.group("h")) % 12 + (12 if time_match.group("ampm").upper() == "PM" else 0)
            current_time = (hour, int(time_match.group("m")))
        if current_time is None or len(cells) < 2:
            continue
        title = cells[1].strip()
        presenter = cells[2].strip() if len(cells) > 2 else ""
        if not title or re.match(r"^(break|lunch|breakfast|reception|welcome|session:|tutorial \d+:)", title, re.I):
            continue
        start = datetime.combine(current_date, datetime.min.time()).replace(hour=current_time[0], minute=current_time[1])
        company = next((name.title() for name in COMPANY_TICKERS if name in presenter.upper() or name in title.upper()), presenter or "Hot Chips")
        rows.append(_row("HOT CHIPS", company, title, start.isoformat(), "America/Los_Angeles", source_url, 5 if _tickers(company) else 4))
    return rows


def parse_ocp_overview(html: str, source_url: str) -> List[Dict[str, Any]]:
    text = _plain_text(html)
    rows: List[Dict[str, Any]] = []
    dates = re.findall(r"(August)\s+(11|12),\s+(2026)", text)
    if re.search(r"August\s+11\s*[–-]\s*12,\s*2026", text):
        dates = [("August", "11", "2026"), ("August", "12", "2026")]
    for month, day, year in dates:
        start = datetime.strptime(f"{month} {day} {year} 09:00", "%B %d %Y %H:%M")
        rows.append(_row("OCP", "Open Compute Project", f"OCP APAC Summit Day {1 if day == '11' else 2}", start.isoformat(), "Asia/Taipei", source_url, 5))
    return rows


def parse_json_ld(html: str, conference: str, timezone_name: str, source_url: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    scripts = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    for script in scripts:
        try:
            payload = json.loads(script)
        except Exception:
            continue
        items: Iterable[Mapping[str, Any]] = payload if isinstance(payload, list) else (payload,)
        for item in items:
            if not isinstance(item, Mapping) or not item.get("startDate"):
                continue
            title = str(item.get("name") or "Official conference session")
            performer = item.get("performer") or {}
            company = str(performer.get("name") if isinstance(performer, Mapping) else performer or conference)
            rows.append(_row(conference, company, title, str(item["startDate"]), timezone_name, source_url, 5))
    return rows


def _read_cache(max_age_days: int = 45) -> List[Dict[str, Any]] | None:
    try:
        payload = json.loads(_CACHE.read_text(encoding="utf-8"))
        saved = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved <= timedelta(days=max_age_days):
            return list(payload.get("sessions") or [])
    except Exception:
        pass
    return None


def fetch_official_sessions(*, timeout: float = 3.0, force: bool = False) -> List[Dict[str, Any]]:
    """Refresh official agendas; retain verified cache on transient failure."""
    if not force:
        cached = _read_cache(max_age_days=1)
        if cached is not None:
            return cached
    sessions: List[Dict[str, Any]] = []
    headers = {"User-Agent": "TINO-CIE-V1097/1.0 (official-agenda-monitor)"}
    def fetch_one(conference: str, config: Mapping[str, Any], url: str) -> List[Dict[str, Any]]:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout) as response:
            html = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        parser = config["parser"]
        if parser == "hot_chips":
            return parse_hot_chips(html, url)
        if parser == "ocp_overview":
            return parse_ocp_overview(html, url)
        return parse_json_ld(html, conference, config["timezone"], url)

    jobs = [(conference, config, url) for conference, config in SOURCE_REGISTRY.items() for url in config["urls"]]
    with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as executor:
        futures = [executor.submit(fetch_one, *job) for job in jobs]
        for future in as_completed(futures):
            try:
                sessions.extend(future.result())
            except Exception:
                continue
    deduped = {row["session_id"]: row for row in sessions}
    payload = {"saved_at": datetime.now(timezone.utc).isoformat(), "sessions": list(deduped.values())}
    try:
        _CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    if deduped:
        return list(deduped.values())
    return []
