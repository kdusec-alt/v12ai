# -*- coding: utf-8 -*-
"""Short-lived, cross-ticker lifecycle for material event alerts.

The event watcher remains responsible for deciding whether V12 must run again.
This module only preserves the operational yellow/red alert across Streamlit
reruns and ticker changes.  It deliberately uses a compact replace-in-place
JSON document instead of Prediction Memory/JSONL so polling cannot pollute the
learning datasets.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Dict, Iterable, Mapping
from zoneinfo import ZoneInfo

from memory_store import EVENT_ALERT_STATE, write_json


STATE_SCHEMA = "TINO_EVENT_ALERT_STATE_V1"
STATE_PATH = EVENT_ALERT_STATE
MAX_EVENT_ROWS = 20
MAX_RELATED_FINGERPRINTS = 8
MAX_ACTIVE_TRADING_DAYS = 3
RESOLVED_RETENTION_SECONDS = 3 * 24 * 60 * 60
_TAIPEI = ZoneInfo("Asia/Taipei")

_LOCK = threading.RLock()

# Absence of another headline is weak evidence.  Decay is intentionally slow,
# while an explicit counter-event (for example a ceasefire) acts immediately.
_YELLOW_AFTER_SECONDS = {
    "geopolitical": 18 * 60 * 60,
    "systemic_market": 12 * 60 * 60,
    "company": 8 * 60 * 60,
    "high_impact": 6 * 60 * 60,
}


def _now_epoch(now: datetime | float | int | None = None) -> float:
    if isinstance(now, datetime):
        value = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return float(value.timestamp())
    if isinstance(now, (float, int)):
        return float(now)
    return datetime.now(timezone.utc).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _empty_state() -> Dict[str, Any]:
    return {"schema": STATE_SCHEMA, "updated_at": "", "events": []}


def _normalise_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != STATE_SCHEMA:
        return _empty_state()
    rows = [dict(row) for row in (value.get("events") or []) if isinstance(row, Mapping)]
    return {"schema": STATE_SCHEMA, "updated_at": _clean(value.get("updated_at")), "events": rows}


def _read_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    try:
        return _normalise_state(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return _empty_state()


def _serialise(state: Mapping[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_state_if_changed(before: Mapping[str, Any], after: Dict[str, Any], path: Path = STATE_PATH) -> None:
    if _serialise(before) == _serialise(after):
        return
    # Reuse the established atomic JSON writer.  Optional GitHub-memory sync
    # therefore preserves this one compact file across redeploys without ever
    # appending it to Prediction/Audit/Research logs.
    write_json(path, after)


def _family(category: str) -> str:
    value = _clean(category).lower()
    if value.startswith("geo_"):
        return "geopolitical"
    if value == "systemic_market_shock":
        return "systemic_market"
    if value.startswith("company_"):
        return "company"
    return "high_impact"


def _topic(title: str, family: str) -> str:
    text = _clean(title).lower()
    if family == "geopolitical":
        topics = (
            ("iran_middle_east", ("伊朗", "以伊", "美伊", "iran", "israel", "hormuz", "荷姆茲")),
            ("taiwan_strait", ("台海", "台灣", "taiwan", "strait")),
            ("trade_controls", ("制裁", "出口管制", "sanction", "export control")),
            ("ukraine_russia", ("烏克蘭", "俄羅斯", "ukraine", "russia")),
        )
        for name, terms in topics:
            if any(term in text for term in terms):
                return name
        return "general_geo"
    if family == "systemic_market":
        return "systemic_market"
    return ""


def _event_key(event: Mapping[str, Any], ticker: str) -> tuple[str, str, str]:
    category = _clean(event.get("category")).lower()
    family = _family(category)
    topic = _topic(_clean(event.get("title")), family)
    if family in {"geopolitical", "systemic_market"}:
        return f"global:{family}:{topic}", family, "global"
    symbol = _clean(ticker).upper() or "UNKNOWN"
    return f"ticker:{symbol}:{family}:{category}", family, "ticker"


def _event_id(key: str, fingerprint: str) -> str:
    raw = f"{key}|{fingerprint}".encode("utf-8")
    return "EV-" + hashlib.sha1(raw).hexdigest()[:14].upper()


def _status_for(severity: int) -> str:
    return "active_red" if severity >= 3 else "active_yellow"


def _is_open(row: Mapping[str, Any]) -> bool:
    return _clean(row.get("status")) in {"active_red", "active_yellow", "cooling"}


def _trading_days_elapsed(start_epoch: float, end_epoch: float) -> int:
    """Count weekday boundaries after evidence time (holiday-neutral fallback)."""
    start = datetime.fromtimestamp(float(start_epoch), _TAIPEI).date()
    end = datetime.fromtimestamp(float(end_epoch), _TAIPEI).date()
    count = 0
    cursor: date = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def _advance_time(state: Dict[str, Any], now_epoch: float) -> None:
    for row in state.get("events") or []:
        if not _is_open(row):
            continue
        family = _clean(row.get("family")) or "high_impact"
        yellow_after = _YELLOW_AFTER_SECONDS.get(family, _YELLOW_AFTER_SECONDS["high_impact"])
        last_evidence = float(row.get("last_evidence_epoch") or row.get("last_seen_epoch") or now_epoch)
        quiet_for = max(0.0, now_epoch - last_evidence)
        status = _clean(row.get("status"))
        if status == "active_red" and quiet_for >= yellow_after:
            row["status"] = "cooling"
            row["severity"] = 2
            row["last_transition_at"] = _iso(now_epoch)
            row["transition_reason"] = "長時間無新增風險證據，自動降為黃燈觀察"
        # Quiet time alone may soften a red alert, but it does not erase it
        # before three trading days.  Weekends therefore cannot prematurely
        # make a Friday event disappear on Monday morning.
        if _trading_days_elapsed(last_evidence, now_epoch) >= MAX_ACTIVE_TRADING_DAYS:
            row["status"] = "resolved"
            row["severity"] = 1
            row["resolved_at"] = _iso(now_epoch)
            row["resolved_epoch"] = now_epoch
            row["last_transition_at"] = _iso(now_epoch)
            row["transition_reason"] = "觀察期內未再出現風險證據，自動解除"


def _prune(state: Dict[str, Any], now_epoch: float) -> None:
    kept = []
    for row in state.get("events") or []:
        if _clean(row.get("status")) == "resolved":
            resolved = float(row.get("resolved_epoch") or 0.0)
            if resolved and now_epoch - resolved > RESOLVED_RETENTION_SECONDS:
                continue
        kept.append(row)
    kept.sort(
        key=lambda row: (
            1 if _is_open(row) else 0,
            int(row.get("severity") or 0),
            float(row.get("last_seen_epoch") or 0.0),
        ),
        reverse=True,
    )
    state["events"] = kept[:MAX_EVENT_ROWS]


def _matching_open(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, Any] | None:
    candidates = [row for row in rows if _clean(row.get("event_key")) == key and _is_open(row)]
    if not candidates:
        return None
    return max(candidates, key=lambda row: float(row.get("last_seen_epoch") or 0.0))


def _ingest_event(state: Dict[str, Any], event: Mapping[str, Any], ticker: str, market: str, now_epoch: float) -> None:
    severity = int(event.get("severity") or 0)
    fingerprint = _clean(event.get("fingerprint"))
    if severity < 2 or not fingerprint:
        return
    category = _clean(event.get("category")).lower()
    key, family, scope = _event_key(event, ticker)
    rows = state.setdefault("events", [])

    # A syndicated or repeated observation is not a new lifecycle transition.
    for row in rows:
        known = set(row.get("related_fingerprints") or [])
        known.add(_clean(row.get("fingerprint")))
        if fingerprint in known:
            tickers = list(dict.fromkeys([*(row.get("source_tickers") or []), ticker]))[-8:]
            row["source_tickers"] = [value for value in tickers if value]
            return

    current = _matching_open(rows, key)
    if current is None and category == "geo_deescalation":
        # Follow-up wires often shorten "US/Iran ceasefire" to just
        # "ceasefire takes effect".  Attach that generic counter-headline only
        # when exactly one geopolitical lifecycle is open; ambiguity must
        # create a separate observation instead of closing the wrong crisis.
        open_geo = [row for row in rows if row.get("family") == "geopolitical" and _is_open(row)]
        if len(open_geo) == 1:
            current = open_geo[0]
            key = _clean(current.get("event_key")) or key
    risk_sign = int(event.get("risk_sign") or 0)
    current_is_cooling = bool(current is not None and _clean(current.get("status")) == "cooling")
    is_counter_event = category == "geo_deescalation" or (
        current is not None
        and not current_is_cooling
        and int(current.get("risk_sign") or 0) != 0
        and risk_sign != 0
        and int(current.get("risk_sign") or 0) != risk_sign
    )

    if current is None:
        row = {
            "event_id": _event_id(key, fingerprint),
            "event_key": key,
            "scope": scope,
            "family": family,
            "category": category,
            "fingerprint": fingerprint,
            "related_fingerprints": [],
            "title": _clean(event.get("title")),
            "source": _clean(event.get("source")),
            "risk_sign": risk_sign,
            "severity": severity,
            "peak_severity": severity,
            "status": "cooling" if is_counter_event else _status_for(severity),
            "first_seen_at": _iso(now_epoch),
            "first_seen_epoch": now_epoch,
            "last_seen_at": _iso(now_epoch),
            "last_seen_epoch": now_epoch,
            "last_evidence_epoch": now_epoch,
            "last_transition_at": _iso(now_epoch),
            "transition_reason": _clean(event.get("reason")),
            "source_tickers": [ticker] if ticker else [],
            "source_markets": [market] if market else [],
            "reinforcement_count": 1,
            "cooling_count": 1 if is_counter_event else 0,
            "acknowledged": False,
        }
        if is_counter_event:
            row["severity"] = 2
        rows.append(row)
        return

    previous_fingerprint = _clean(current.get("fingerprint"))
    related = [*(current.get("related_fingerprints") or []), previous_fingerprint]
    current["related_fingerprints"] = list(dict.fromkeys(value for value in related if value))[-MAX_RELATED_FINGERPRINTS:]
    current["fingerprint"] = fingerprint
    current["title"] = _clean(event.get("title"))
    current["source"] = _clean(event.get("source"))
    current["category"] = category
    current["last_seen_at"] = _iso(now_epoch)
    current["last_seen_epoch"] = now_epoch
    current["last_evidence_epoch"] = now_epoch
    current["risk_sign"] = risk_sign
    current["source_tickers"] = list(dict.fromkeys([*(current.get("source_tickers") or []), ticker]))[-8:]
    current["source_markets"] = list(dict.fromkeys([*(current.get("source_markets") or []), market]))[-4:]
    if is_counter_event:
        current["status"] = "cooling"
        current["severity"] = 2
        current["cooling_count"] = int(current.get("cooling_count") or 0) + 1
        if int(current.get("cooling_count") or 0) >= 2:
            current["status"] = "resolved"
            current["severity"] = 1
            current["resolved_at"] = _iso(now_epoch)
            current["resolved_epoch"] = now_epoch
            current["transition_reason"] = "連續偵測到同一事件的降溫證據，自動解除"
        else:
            current["transition_reason"] = "偵測到同一事件的反向／降溫證據，降為黃燈觀察"
    else:
        current["severity"] = severity
        current["peak_severity"] = max(int(current.get("peak_severity") or 0), severity)
        current["status"] = _status_for(severity)
        current["reinforcement_count"] = int(current.get("reinforcement_count") or 0) + 1
        # A renewed adverse event after a cooling headline starts a fresh risk
        # confirmation sequence.  Keeping the old counter count would let the
        # next benign headline close a newly re-escalated crisis too early.
        current["cooling_count"] = 0
        current["transition_reason"] = _clean(event.get("reason"))
    # A genuinely new material update reopens a previously acknowledged story.
    current["acknowledged"] = False
    current.pop("acknowledged_at", None)
    current["last_transition_at"] = _iso(now_epoch)


def _view(state: Mapping[str, Any]) -> Dict[str, Any]:
    rows = [deepcopy(row) for row in (state.get("events") or [])]
    visible = [row for row in rows if _is_open(row) and not bool(row.get("acknowledged"))]
    visible.sort(
        key=lambda row: (
            int(row.get("severity") or 0),
            float(row.get("last_seen_epoch") or 0.0),
        ),
        reverse=True,
    )
    recent = sorted(rows, key=lambda row: float(row.get("last_seen_epoch") or 0.0), reverse=True)[:5]
    return {"dominant": visible[0] if visible else None, "active_count": len(visible), "recent": recent}


def update_global_event_state(
    plan: Mapping[str, Any] | None,
    *,
    ticker: str = "",
    market: str = "",
    now: datetime | float | int | None = None,
    path: Path = STATE_PATH,
) -> Dict[str, Any]:
    """Merge one poll result into bounded operational state and return its view."""
    now_epoch = _now_epoch(now)
    with _LOCK:
        before = _read_state(path)
        after = deepcopy(before)
        _advance_time(after, now_epoch)
        for event in (dict(plan or {}).get("events") or [])[:8]:
            if isinstance(event, Mapping):
                _ingest_event(after, event, _clean(ticker).upper(), _clean(market).upper(), now_epoch)
        _prune(after, now_epoch)
        if _serialise(before) != _serialise(after):
            after["updated_at"] = _iso(now_epoch)
        _write_state_if_changed(before, after, path)
        return _view(after)


def get_global_event_view(
    *, now: datetime | float | int | None = None, path: Path = STATE_PATH
) -> Dict[str, Any]:
    """Load current alerts, applying conservative time decay when due."""
    return update_global_event_state({}, now=now, path=path)


def acknowledge_global_event(
    event_id: str, *, now: datetime | float | int | None = None, path: Path = STATE_PATH
) -> bool:
    """Admin acknowledgement hides an alert without deleting its audit trace."""
    target = _clean(event_id)
    now_epoch = _now_epoch(now)
    with _LOCK:
        before = _read_state(path)
        after = deepcopy(before)
        changed = False
        for row in after.get("events") or []:
            if _clean(row.get("event_id")) == target and _is_open(row):
                row["acknowledged"] = True
                row["acknowledged_at"] = _iso(now_epoch)
                row["last_transition_at"] = _iso(now_epoch)
                row["transition_reason"] = "Admin 已讀並關閉畫面警示"
                changed = True
                break
        if changed:
            after["updated_at"] = _iso(now_epoch)
            _write_state_if_changed(before, after, path)
        return changed


def global_event_display(event: Mapping[str, Any] | None) -> Dict[str, str]:
    """Build the cross-ticker banner without importing Streamlit."""
    row = dict(event or {})
    if not row:
        return {"level": "caption", "text": ""}
    severity = int(row.get("severity") or 0)
    if _clean(row.get("scope")) == "global":
        scope = "全市場"
    else:
        tickers = [_clean(value).upper() for value in (row.get("source_tickers") or []) if _clean(value)]
        scope = f"個股 {tickers[-1]}" if tickers else "個股"
    status = "風險降溫觀察" if _clean(row.get("status")) == "cooling" else "重大事件持續監測"
    title = _clean(row.get("title") or row.get("transition_reason"))
    if severity >= 3:
        return {"level": "error", "text": f"🚨 {scope}｜Severity {severity}｜{status}｜{title}"}
    return {"level": "warning", "text": f"⚠️ {scope}｜Severity {severity}｜{status}｜{title}"}
