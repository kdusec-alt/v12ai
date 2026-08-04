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
import html as html_lib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence
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
    "FMS": {
        "urls": (
            "https://www.terrapinn.com/conference/future-memory-storage/agenda.stm",
            "https://www.terrapinn.com/conference/future-memory-storage/index.stm",
            "https://www.fmsnow.com/program/",
            "https://www.fmsnow.com/agenda/",
            "https://www.fmsnow.com/",
        ),
        "timezone": "America/Los_Angeles", "parser": "fms", "timeout": 12.0,
    },
    "GTC": {"urls": ("https://www.nvidia.com/gtc/session-catalog/",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
    "COMPUTEX": {"urls": ("https://www.computextaipei.com.tw/en/calendar/event-calendar.html",), "timezone": "Asia/Taipei", "parser": "json_ld"},
    "OFC": {"urls": ("https://www.ofcconference.org/schedule/",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
    "SC": {"urls": ("https://sc26.supercomputing.org/program/",), "timezone": "America/Chicago", "parser": "json_ld"},
    "AMD ADVANCING AI": {"urls": ("https://www.amd.com/en/corporate/events/advancing-ai.html",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
    "INTEL INNOVATION": {"urls": ("https://www.intel.com/content/www/us/en/events/on-event-series.html",), "timezone": "America/Los_Angeles", "parser": "json_ld"},
}

_CACHE = Path(os.environ.get("TINO_CIE_CACHE_PATH", "/tmp/tino_cie_official_agenda.json"))
_CACHE_SCHEMA = "TINO_CIE_AGENDA_V1097_3"
_TIME_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ampm>AM|PM)", re.I)
_DATE_RE = re.compile(r"(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),?\s+([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})", re.I)
_ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
_MONTH_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[–—-]\s*(\d{1,2})(?:st|nd|rd|th)?)?,?\s+(20\d{2})\b",
    re.I,
)

COMPANY_TICKERS = {
    "NVIDIA": ("NVDA",), "AMD": ("AMD",), "INTEL": ("INTC",),
    "MICRON": ("MU",), "MARVELL": ("MRVL",), "BROADCOM": ("AVGO",),
    "SK HYNIX": ("SKHY",), "SAMSUNG": (), "KIOXIA": (),
    "SILICON MOTION": ("SIMO",), "META": ("META",), "MICROSOFT": ("MSFT",),
    "GOOGLE": ("GOOGL",), "ARM": ("ARM",), "IBM": ("IBM",),
    "FADU": ("440110.KQ",), "PHISON": ("8299.TW",),
    "MACRONIX": ("2337.TW",), "WINBOND": ("2344.TW",), "NANYA": ("2408.TW",),
    "CREDO": ("CRDO",),
}

COMPANY_CHAIN_TICKERS = {
    "MARVELL": ("CRDO", "AVGO"),
    "SK HYNIX": ("MU", "MRVL"),
    "FADU": ("SIMO", "8299.TW", "MRVL"),
    "MICRON": ("MRVL", "SIMO", "8299.TW"),
}


def _contains_term(text: str, term: str) -> bool:
    """Match company/technology names without ARM-in-market style collisions."""
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(term)}(?![A-Z0-9])", text.upper()))

TECHNOLOGY_CHAIN_TICKERS = {
    "HBM": ("MU", "SKHY", "MRVL"),
    "HBF": ("MU", "SKHY", "MRVL"),
    "CXL": ("MRVL", "CRDO", "AVGO"),
    "OPTICAL": ("MRVL", "CRDO", "AVGO"),
    "PCIE GEN6": ("SIMO", "8299.TW", "MRVL"),
    "SSD": ("MU", "SIMO", "8299.TW"),
    "NAND": ("MU", "2337.TW", "2344.TW"),
    "DRAM": ("MU", "2408.TW", "2344.TW"),
}


def _tickers(text: str) -> List[str]:
    upper = text.upper()
    out: List[str] = []
    for company, symbols in COMPANY_TICKERS.items():
        if _contains_term(upper, company):
            out.extend(symbol for symbol in symbols if symbol not in out)
    return out


def _technologies(text: str) -> List[str]:
    terms = ("HBM", "HBF", "CXL", "PCIe Gen6", "SSD", "NAND", "DRAM", "Optical", "GPU", "AI", "Ethernet", "Chiplet", "RISC-V")
    return [term for term in terms if _contains_term(text, term)]


def _supply_chain_tickers(company: str, title: str, direct: Sequence[str]) -> List[str]:
    upper_company = company.upper()
    upper_title = title.upper()
    out: List[str] = []
    for name, symbols in COMPANY_CHAIN_TICKERS.items():
        if _contains_term(upper_company, name):
            out.extend(symbols)
    for term, symbols in TECHNOLOGY_CHAIN_TICKERS.items():
        if term in upper_title:
            out.extend(symbols)
    return list(dict.fromkeys(symbol for symbol in out if symbol not in direct))


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
    direct = _tickers(company)
    return {
        "conference": conference,
        "event_id": f"{conference.replace(' ', '_')}_{start[:4]}",
        "session_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:20].upper(),
        "company": company or "Conference",
        "title": title,
        "datetime": start,
        "timezone": timezone_name,
        "technologies": _technologies(title),
        "direct_tickers": direct,
        "supply_chain_tickers": _supply_chain_tickers(company, title, direct),
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
        company = next((name.title() for name in COMPANY_TICKERS if _contains_term(presenter, name) or _contains_term(title, name)), presenter or "Hot Chips")
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


def _date_candidates(text: str) -> List[datetime]:
    """Extract explicit official dates without guessing a year or timezone."""
    found: List[datetime] = []
    for year, month, day in _ISO_DATE_RE.findall(text):
        try:
            found.append(datetime(int(year), int(month), int(day), 9, 0))
        except ValueError:
            continue
    for month, first, last, year in _MONTH_DATE_RE.findall(text):
        days: Sequence[str]
        if last:
            days = tuple(str(day) for day in range(int(first), int(last) + 1))
        else:
            days = (first,)
        for day in days:
            if not day:
                continue
            try:
                found.append(datetime.strptime(f"{month} {day} {year} 09:00", "%B %d %Y %H:%M"))
            except ValueError:
                continue
    return sorted(set(found))


def _session_blocks(html: str) -> List[str]:
    """Return bounded agenda-like blocks from tables, cards and embedded JSON."""
    blocks: List[str] = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        selectors = (
            "tr", "article", "[class*='session']", "[class*='agenda']",
            "[class*='schedule']", "[data-start]", "[data-date]",
        )
        for node in soup.select(",".join(selectors)):
            text = " ".join(node.stripped_strings)
            if 12 <= len(text) <= 1600:
                blocks.append(text)
    if not blocks:
        blocks.extend(" ".join(row) for row in _table_rows(html) if row)
        for tag, attrs, body in re.findall(r"<(article|div|li)\b([^>]*)>(.*?)</\1>", html, re.I | re.S):
            if re.search(r"session|agenda|schedule|program", attrs, re.I):
                text = _plain_text(body)
                if 12 <= len(text) <= 1600:
                    blocks.append(text)
    # Modern event sites often hydrate cards from __NEXT_DATA__ / JSON payloads.
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.I | re.S):
        if re.search(r"start(?:Date|Time)|session|agenda", script, re.I):
            blocks.extend(re.findall(r'\{[^{}]{0,2000}"(?:startDate|startTime|title|name)"[^{}]{0,2000}\}', script, re.I))
    return list(dict.fromkeys(blocks))


def parse_fms(html: str, source_url: str) -> List[Dict[str, Any]]:
    """Parse FMS agenda cards/tables/JSON and retain a conference-level warning.

    FMS has changed site vendors and markup repeatedly.  This parser therefore
    keys off explicit date/time content rather than one brittle CSS selector.
    It never invents session times: when the official page only publishes the
    event dates, a 09:00 conference-day row keeps the advance warning visible.
    """
    rows = parse_json_ld(html, "FMS", "America/Los_Angeles", source_url)

    # FMS 2026 moved from fmsnow.com to Terrapinn.  Its official agenda uses
    # one .ASession card per talk and publishes an exact UTC timestamp in the
    # .Time[data] attribute.  Parse that contract before the generic fallback.
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("div.ASession"):
            time_node = card.select_one(".Time[data]")
            title_node = card.select_one(".session h4")
            if time_node is None or title_node is None:
                continue
            start_raw = str(time_node.get("data") or "").strip()
            title = " ".join(title_node.stripped_strings).strip()
            if not start_raw or not title or re.match(
                r"^(registration|welcome|break|lunch|reception|exhibition|chair'?s remarks)\b",
                title, re.I,
            ):
                continue
            stream_node = card.select_one(".StreamTitle")
            stream = " ".join(stream_node.stripped_strings) if stream_node else ""
            organisations = [" ".join(node.stripped_strings) for node in card.select(".Org")]
            searchable = " | ".join([title, stream, *organisations])
            company = next(
                (name.title() for name in COMPANY_TICKERS if _contains_term(searchable, name)),
                organisations[0] if organisations else "FMS",
            )
            # The complete agenda contains hundreds of housekeeping and niche
            # papers.  Keep market-relevant public-company or key-tech sessions
            # so the 24-row UI limit cannot hide today's investable events.
            if not _tickers(company) and not _technologies(f"{title} {stream}"):
                continue
            rows.append(_row(
                "FMS", company, title[:500], start_raw,
                "America/Los_Angeles", source_url, 5,
            ))

    # Dependency-free fallback for deployments where BeautifulSoup is absent.
    if "terrapinn.com" in source_url and not rows:
        marker = r'(?=<div[^>]*class=["\'][^"\']*\bASession\b[^"\']*["\'])'
        for card in re.split(marker, html, flags=re.I)[1:]:
            stamp = re.search(r'class=["\'][^"\']*\bTime\b[^"\']*["\'][^>]*\bdata=["\']([^"\']+)', card, re.I)
            heading = re.search(r'<h4\b[^>]*>(.*?)</h4>', card, re.I | re.S)
            if not stamp or not heading:
                continue
            title = html_lib.unescape(_plain_text(heading.group(1)))
            if re.match(r"^(registration|welcome|break|lunch|reception|exhibition|chair'?s remarks)\b", title, re.I):
                continue
            searchable = html_lib.unescape(_plain_text(card))
            organisations = [
                html_lib.unescape(_plain_text(value))
                for value in re.findall(
                    r'<span[^>]*class=["\'][^"\']*\bOrg\b[^"\']*["\'][^>]*>(.*?)</span>',
                    card, re.I | re.S,
                )
            ]
            company = next(
                (name.title() for name in COMPANY_TICKERS if _contains_term(" | ".join(organisations), name)),
                organisations[0] if organisations else "FMS",
            )
            if not _tickers(company) and not _technologies(searchable):
                continue
            rows.append(_row("FMS", company, title[:500], stamp.group(1), "America/Los_Angeles", source_url, 5))
    page_text = _plain_text(html)
    page_dates = _date_candidates(page_text)
    default_date = page_dates[0].date() if page_dates else None

    for block in _session_blocks(html):
        time_match = _TIME_RE.search(block)
        if not time_match:
            continue
        dates = _date_candidates(block)
        session_date = dates[0].date() if dates else default_date
        if session_date is None:
            continue
        hour = int(time_match.group("h")) % 12 + (12 if time_match.group("ampm").upper() == "PM" else 0)
        start = datetime.combine(session_date, datetime.min.time()).replace(
            hour=hour, minute=int(time_match.group("m"))
        )
        clean = re.sub(r"\s+", " ", block).strip()
        title = re.sub(_TIME_RE, "", clean, count=1).strip(" |–—-:")
        if len(title) < 4 or re.match(r"^(break|lunch|reception|registration)\b", title, re.I):
            continue
        company = next((name.title() for name in COMPANY_TICKERS if _contains_term(title, name)), "FMS")
        rows.append(_row("FMS", company, title[:500], start.isoformat(), "America/Los_Angeles", source_url, 5))

    # An official conference-day alert is materially better than an empty CIE,
    # while remaining honest that detailed session times are not yet published.
    if not rows:
        for index, start in enumerate(page_dates[:7], start=1):
            rows.append(_row(
                "FMS", "FMS", f"FMS Conference Day {index}｜詳細議程待官方公布",
                start.isoformat(), "America/Los_Angeles", source_url, 5,
            ))
    return list({row["session_id"]: row for row in rows}.values())


def parse_conference_dates(html: str, conference: str, timezone_name: str, source_url: str) -> List[Dict[str, Any]]:
    """Fallback to honest event-day warnings when session details are absent."""
    dates = _date_candidates(_plain_text(html))
    return [
        _row(
            conference, conference,
            f"{conference} Conference Day {index}｜詳細議程待官方公布",
            start.isoformat(), timezone_name, source_url,
            5 if conference in {"FMS", "GTC", "COMPUTEX", "OFC", "OCP", "HOT CHIPS"} else 4,
        )
        for index, start in enumerate(dates[:7], start=1)
    ]


def _read_cache(max_age_days: int = 45, *, require_current_schema: bool = False) -> List[Dict[str, Any]] | None:
    try:
        payload = json.loads(_CACHE.read_text(encoding="utf-8"))
        if require_current_schema and payload.get("schema") != _CACHE_SCHEMA:
            return None
        saved = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved <= timedelta(days=max_age_days):
            return list(payload.get("sessions") or [])
    except Exception:
        pass
    return None


def conference_source_health(*, max_age_days: int = 45) -> Dict[str, Any]:
    """Admin-safe fetch provenance; contains no exception trace or secrets."""
    try:
        payload = json.loads(_CACHE.read_text(encoding="utf-8"))
        saved = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved > timedelta(days=max_age_days):
            return {"status": "STALE", "saved_at": payload.get("saved_at", ""), "sources": payload.get("sources", {})}
        return {
            "status": "OK" if payload.get("sessions") else "DEGRADED",
            "saved_at": payload.get("saved_at", ""),
            "session_count": len(payload.get("sessions") or []),
            "sources": payload.get("sources", {}),
        }
    except Exception:
        return {"status": "NO_CACHE", "saved_at": "", "session_count": 0, "sources": {}}


def fetch_official_sessions(*, timeout: float = 3.0, force: bool = False) -> List[Dict[str, Any]]:
    """Refresh official agendas; retain verified cache on transient failure."""
    if not force:
        cached = _read_cache(max_age_days=1, require_current_schema=True)
        # V1097.3: invalidate caches written before the official Terrapinn FMS
        # source/parser existed.  Only this parser contract may suppress refresh.
        if cached:
            return cached
    sessions: List[Dict[str, Any]] = []
    health: Dict[str, Dict[str, Any]] = {name: {"status": "FAILED", "sessions": 0} for name in SOURCE_REGISTRY}
    headers = {"User-Agent": "TINO-CIE-V1097/1.0 (official-agenda-monitor)"}
    def fetch_one(conference: str, config: Mapping[str, Any], url: str) -> List[Dict[str, Any]]:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=float(config.get("timeout", timeout))) as response:
            html = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        parser = config["parser"]
        if parser == "hot_chips":
            return parse_hot_chips(html, url)
        if parser == "ocp_overview":
            return parse_ocp_overview(html, url)
        if parser == "fms":
            return parse_fms(html, url)
        parsed = parse_json_ld(html, conference, config["timezone"], url)
        return parsed or parse_conference_dates(html, conference, config["timezone"], url)

    jobs = [(conference, config, url) for conference, config in SOURCE_REGISTRY.items() for url in config["urls"]]
    with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as executor:
        futures = {executor.submit(fetch_one, *job): job for job in jobs}
        for future in as_completed(futures):
            conference, _config, _url = futures[future]
            try:
                result = future.result()
                sessions.extend(result)
                if result:
                    health[conference] = {"status": "OK", "sessions": health[conference].get("sessions", 0) + len(result)}
                elif health[conference]["status"] != "OK":
                    health[conference] = {"status": "NO_AGENDA", "sessions": 0}
            except Exception:
                continue
    deduped = {row["session_id"]: row for row in sessions}
    if deduped:
        payload = {
            "schema": _CACHE_SCHEMA,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "sessions": list(deduped.values()),
            "sources": health,
        }
        try:
            _CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return list(deduped.values())
    # Never replace a last-known-good agenda with an empty refresh.
    stale = _read_cache(max_age_days=45)
    return stale or []
