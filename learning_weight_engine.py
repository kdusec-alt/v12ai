# -*- coding: utf-8 -*-
"""TINO V14 bounded autonomous family-weight learning engine.

The engine learns *data weights*, never rewrites Python source code.  It reads
verified T1 audits, trains market-specific family multipliers, validates them on
a chronological holdout, and persists only a small bounded JSON model.

Safety contract
---------------
- TW and US learn independently.
- Duplicate ticker/date audits are collapsed to one canonical observation.
- Only verified, valid, directional T1 audits are eligible.
- Chronological holdout validation is mandatory before activation.
- Each family multiplier is limited to 0.94..1.06.
- One refresh may move an active multiplier by at most 0.0125.
- A previously validated model is frozen when a new candidate fails.
- A materially degraded active model is rolled back to neutral 1.0 weights.
- No Streamlit, Pandas, network, or model-code mutation occurs here.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from memory_store import LEARNING_WEIGHTS, read_audit_log, read_json, write_json

TW_TZ = ZoneInfo("Asia/Taipei")
SCHEMA = "TINO_V14_LEARNING_WEIGHTS_V1"

ALLOWED_FAMILIES = {
    "trend", "intraday", "price_action", "flow", "short", "exhaustion", "news",
    "fundamental_event", "overnight", "leverage", "market_heat", "futures",
    "foreign_pressure", "geo_policy", "analyst_event",
}

ABS_MIN = 0.94
ABS_MAX = 1.06
MAX_STEP = 0.0125
MIN_TOTAL = 24
MIN_TRAIN = 16
MIN_HOLDOUT = 8
MIN_UNIQUE_TICKERS = 6
MIN_FAMILY_COUNT = 8
MIN_FAMILY_TICKERS = 4
MAX_AUDITS = 900

_LOCK = threading.RLock()
_CACHE_MTIME_NS: int | None = None
_CACHE_DOC: Dict[str, Any] = {}


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def _enabled(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default) or default).strip().lower() not in {"0", "false", "off", "no"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _market(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in {"TW", "US"} else ""


def _empty_doc() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "updated_at_tw": None,
        "decision_influence": True,
        "safety": {
            "absolute_multiplier_range": [ABS_MIN, ABS_MAX],
            "max_step_per_refresh": MAX_STEP,
            "chronological_holdout_required": True,
            "verified_t1_only": True,
            "duplicate_ticker_date_collapsed": True,
        },
        "models": {},
    }


def _load_doc() -> Dict[str, Any]:
    doc = read_json(Path(LEARNING_WEIGHTS), {})
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return _empty_doc()
    doc.setdefault("models", {})
    doc.setdefault("decision_influence", True)
    return doc


def _load_doc_cached() -> Dict[str, Any]:
    global _CACHE_MTIME_NS, _CACHE_DOC
    try:
        path = Path(LEARNING_WEIGHTS)
        mtime_ns = path.stat().st_mtime_ns if path.exists() else -1
    except Exception:
        mtime_ns = -1
    if _CACHE_MTIME_NS == mtime_ns:
        return _CACHE_DOC
    _CACHE_DOC = _load_doc()
    _CACHE_MTIME_NS = mtime_ns
    return _CACHE_DOC


def _canonical_audits(rows: Iterable[Mapping[str, Any]], market: str) -> List[Dict[str, Any]]:
    """Return one newest eligible T1 audit per ticker/trade-date."""
    wanted = _market(market)
    chosen: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw or {})
        if _market(row.get("market")) != wanted:
            continue
        if str(row.get("target") or "").lower() != "next":
            continue
        if str(row.get("price_sample_quality") or "").lower() != "verified":
            continue
        if not bool(row.get("actual_valid")):
            continue
        if str(row.get("actual_direction") or "").upper() not in {"UP", "DOWN"}:
            continue
        attribution = row.get("family_attribution")
        contributions = row.get("direction_family_contributions")
        if not isinstance(attribution, dict) or not attribution:
            continue
        if not isinstance(contributions, dict) or not contributions:
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        trade_date = str(row.get("target_trade_date") or "").strip()
        if not ticker or not trade_date:
            continue
        key = f"{wanted}|{ticker}|{trade_date}"
        old = chosen.get(key)
        if old is None or str(row.get("audit_time_tw") or "") >= str(old.get("audit_time_tw") or ""):
            chosen[key] = row
    out = list(chosen.values())
    out.sort(key=lambda row: (str(row.get("target_trade_date") or ""), str(row.get("audit_time_tw") or "")))
    return out


def _audit_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append("|".join([
            str(row.get("audit_id") or row.get("prediction_id") or ""),
            str(row.get("ticker") or ""),
            str(row.get("target_trade_date") or ""),
            str(row.get("actual_direction") or ""),
        ]))
    payload = "\n".join(parts).encode("utf-8", "replace")
    return hashlib.sha1(payload).hexdigest()[:16]


def _family_statistics(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        for family, raw in dict(row.get("family_attribution") or {}).items():
            name = str(family)
            if name not in ALLOWED_FAMILIES or not isinstance(raw, dict):
                continue
            aligned = raw.get("aligned")
            if aligned is None:
                continue
            impact = _clamp(_num(raw.get("impact_weight"), 0.0), 0.0, 0.65)
            if impact <= 0.0:
                continue
            item = stats.setdefault(name, {
                "count": 0, "hits": 0, "impact_sum": 0.0,
                "aligned_impact_sum": 0.0, "tickers": set(),
            })
            item["count"] += 1
            item["hits"] += 1 if bool(aligned) else 0
            item["impact_sum"] += impact
            item["aligned_impact_sum"] += impact if bool(aligned) else 0.0
            item["tickers"].add(ticker)

    out: Dict[str, Dict[str, Any]] = {}
    for family, item in stats.items():
        count = int(item["count"])
        hits = int(item["hits"])
        unique = len(item["tickers"])
        impact_sum = float(item["impact_sum"])
        bayes = (hits + 3.0) / (count + 6.0)
        weighted = (float(item["aligned_impact_sum"]) + 1.5) / (impact_sum + 3.0)
        reliability = _clamp(bayes * 0.60 + weighted * 0.40, 0.0, 1.0)
        maturity = min(1.0, count / 24.0) * min(1.0, unique / 8.0)
        eligible = count >= MIN_FAMILY_COUNT and unique >= MIN_FAMILY_TICKERS
        deviation = _clamp((reliability - 0.5) * 0.24 * maturity, -0.06, 0.06) if eligible else 0.0
        out[family] = {
            "count": count,
            "hits": hits,
            "unique_tickers": unique,
            "bayes_hit_rate": round(bayes, 5),
            "impact_weighted_hit_rate": round(weighted, 5),
            "blended_reliability": round(reliability, 5),
            "maturity": round(maturity, 5),
            "eligible": bool(eligible),
            "candidate_multiplier": round(_clamp(1.0 + deviation, ABS_MIN, ABS_MAX), 5),
        }
    return out


def _candidate_multipliers(stats: Mapping[str, Mapping[str, Any]]) -> Dict[str, float]:
    return {
        family: _clamp(_num(row.get("candidate_multiplier"), 1.0), ABS_MIN, ABS_MAX)
        for family, row in stats.items()
        if family in ALLOWED_FAMILIES and bool(row.get("eligible"))
    }


def _sigmoid(value: float) -> float:
    value = _clamp(value, -40.0, 40.0)
    return 1.0 / (1.0 + math.exp(-value))


def _evaluate(rows: List[Dict[str, Any]], multipliers: Mapping[str, float]) -> Dict[str, Any]:
    losses: List[float] = []
    hits = 0
    margins: List[float] = []
    used = 0
    for row in rows:
        actual = str(row.get("actual_direction") or "").upper()
        sign = 1.0 if actual == "UP" else -1.0 if actual == "DOWN" else 0.0
        if sign == 0.0:
            continue
        contributions = {
            str(name): _num(value)
            for name, value in dict(row.get("direction_family_contributions") or {}).items()
            if str(name) in ALLOWED_FAMILIES
        }
        if not contributions:
            continue
        score = sum(value * _clamp(_num(multipliers.get(name), 1.0), ABS_MIN, ABS_MAX) for name, value in contributions.items())
        signed_margin = sign * score
        probability = _clamp(_sigmoid(signed_margin / 14.0), 1e-6, 1.0 - 1e-6)
        losses.append(-math.log(probability))
        hits += 1 if signed_margin > 0 else 0
        margins.append(math.tanh(signed_margin / 25.0))
        used += 1
    return {
        "count": used,
        "hit_rate": round(hits / used, 6) if used else None,
        "log_loss": round(sum(losses) / used, 6) if used else None,
        "signed_margin": round(sum(margins) / used, 6) if used else None,
    }


def _model_is_applied(model: Mapping[str, Any]) -> bool:
    return str(model.get("status") or "").upper() in {"ACTIVE", "FROZEN"}


def _step_toward(previous: Mapping[str, Any], candidate: Mapping[str, float]) -> Dict[str, float]:
    names = set(ALLOWED_FAMILIES) | set(previous.keys()) | set(candidate.keys())
    result: Dict[str, float] = {}
    for name in names:
        old = _clamp(_num(previous.get(name), 1.0), ABS_MIN, ABS_MAX)
        target = _clamp(_num(candidate.get(name), 1.0), ABS_MIN, ABS_MAX)
        change = _clamp(target - old, -MAX_STEP, MAX_STEP)
        value = _clamp(old + change, ABS_MIN, ABS_MAX)
        if abs(value - 1.0) >= 0.0005:
            result[name] = round(value, 5)
    return result


def _train_market(market: str, rows: List[Dict[str, Any]], previous: Mapping[str, Any]) -> Dict[str, Any]:
    count = len(rows)
    unique_tickers = len({str(row.get("ticker") or "").upper() for row in rows})
    generation = int(_num(previous.get("generation"), 0.0)) + 1
    base = {
        "market": market,
        "updated_at_tw": _now(),
        "generation": generation,
        "sample_count": count,
        "unique_tickers": unique_tickers,
        "trained_through": str(rows[-1].get("target_trade_date") or "") if rows else None,
        "last_audit_count": count,
        "audit_fingerprint": _audit_fingerprint(rows),
        "bounds": [ABS_MIN, ABS_MAX],
        "max_step": MAX_STEP,
    }

    if count < MIN_TOTAL or unique_tickers < MIN_UNIQUE_TICKERS:
        return {
            **base,
            "status": "COLLECTING",
            "gate": f"need_total>={MIN_TOTAL}_and_unique>={MIN_UNIQUE_TICKERS}",
            "multipliers": dict(previous.get("multipliers") or {}) if _model_is_applied(previous) else {},
            "candidate_multipliers": {},
            "family_statistics": {},
            "validation": {},
        }

    holdout_n = max(MIN_HOLDOUT, int(round(count * 0.25)))
    holdout_n = min(holdout_n, count - MIN_TRAIN)
    if holdout_n < MIN_HOLDOUT:
        return {
            **base,
            "status": "COLLECTING",
            "gate": f"need_train>={MIN_TRAIN}_holdout>={MIN_HOLDOUT}",
            "multipliers": dict(previous.get("multipliers") or {}) if _model_is_applied(previous) else {},
            "candidate_multipliers": {},
            "family_statistics": {},
            "validation": {},
        }

    train = rows[:-holdout_n]
    holdout = rows[-holdout_n:]
    stats = _family_statistics(train)
    candidate = _candidate_multipliers(stats)
    baseline_eval = _evaluate(holdout, {})
    candidate_eval = _evaluate(holdout, candidate)
    prior_multipliers = dict(previous.get("multipliers") or {}) if _model_is_applied(previous) else {}
    prior_eval = _evaluate(holdout, prior_multipliers) if prior_multipliers else baseline_eval

    baseline_loss = _num(baseline_eval.get("log_loss"), 999.0)
    candidate_loss = _num(candidate_eval.get("log_loss"), 999.0)
    baseline_hit = _num(baseline_eval.get("hit_rate"), 0.0)
    candidate_hit = _num(candidate_eval.get("hit_rate"), 0.0)
    baseline_margin = _num(baseline_eval.get("signed_margin"), -1.0)
    candidate_margin = _num(candidate_eval.get("signed_margin"), -1.0)
    meaningful = any(abs(value - 1.0) >= 0.002 for value in candidate.values())
    validation_pass = bool(
        candidate_eval.get("count", 0) >= MIN_HOLDOUT
        and meaningful
        and candidate_hit + 1e-9 >= baseline_hit
        and candidate_loss <= baseline_loss - 0.001
        and candidate_margin >= baseline_margin
    )

    prior_degraded = bool(
        prior_multipliers
        and _num(prior_eval.get("hit_rate"), 0.0) + 0.05 < baseline_hit
        and _num(prior_eval.get("log_loss"), 999.0) > baseline_loss + 0.01
    )

    if prior_degraded:
        status = "ROLLED_BACK"
        active: Dict[str, float] = {}
        gate = "previous_active_model_failed_current_holdout"
    elif validation_pass:
        status = "ACTIVE"
        active = _step_toward(prior_multipliers, candidate)
        gate = "chronological_holdout_pass"
    elif prior_multipliers:
        status = "FROZEN"
        active = prior_multipliers
        gate = "new_candidate_failed_keep_last_validated"
    else:
        status = "SHADOW"
        active = {}
        gate = "candidate_failed_holdout"

    return {
        **base,
        "status": status,
        "gate": gate,
        "train_count": len(train),
        "validation_count": len(holdout),
        "multipliers": {k: round(_clamp(v, ABS_MIN, ABS_MAX), 5) for k, v in active.items()},
        "candidate_multipliers": {k: round(v, 5) for k, v in candidate.items()},
        "family_statistics": stats,
        "validation": {
            "baseline": baseline_eval,
            "candidate": candidate_eval,
            "previous_active": prior_eval,
            "passed": validation_pass,
            "previous_degraded": prior_degraded,
        },
    }


def refresh_learning_weights(market: Optional[str] = None, *, force: bool = False) -> Dict[str, Any]:
    """Train and persist bounded TW/US family weights after new T1 audits."""
    global _CACHE_MTIME_NS, _CACHE_DOC
    if not _enabled("TINO_V14_WEIGHT_LEARNING", "1"):
        return {
            "status": "DISABLED",
            "path": str(LEARNING_WEIGHTS),
            "markets": {},
            "decision_influence": False,
        }
    with _LOCK:
        doc = _load_doc()
        models = dict(doc.get("models") or {})
        rows = read_audit_log(MAX_AUDITS)
        targets = [_market(market)] if _market(market) else ["TW", "US"]
        changed = False
        reports: Dict[str, Any] = {}
        for target in targets:
            eligible = _canonical_audits(rows, target)
            previous = dict(models.get(target) or {})
            previous_count = int(_num(previous.get("last_audit_count"), -1.0))
            fingerprint = _audit_fingerprint(eligible)
            previous_fingerprint = str(previous.get("audit_fingerprint") or "")
            if not force and previous_count >= 0 and len(eligible) < previous_count:
                reports[target] = {
                    "status": "SHRINK_GUARD",
                    "sample_count": len(eligible),
                    "previous_sample_count": previous_count,
                    "model_status": previous.get("status", "MISSING"),
                }
                continue
            if not force and len(eligible) == previous_count and fingerprint == previous_fingerprint:
                reports[target] = {
                    "status": "UNCHANGED",
                    "sample_count": len(eligible),
                    "model_status": previous.get("status", "MISSING"),
                }
                continue
            trained = _train_market(target, eligible, previous)
            models[target] = trained
            reports[target] = {
                "status": "UPDATED",
                "sample_count": len(eligible),
                "model_status": trained.get("status"),
                "gate": trained.get("gate"),
            }
            changed = True

        if changed:
            doc["schema"] = SCHEMA
            doc["updated_at_tw"] = _now()
            doc["decision_influence"] = True
            doc["models"] = models
            write_json(Path(LEARNING_WEIGHTS), doc)
            _CACHE_MTIME_NS = None
            _CACHE_DOC = {}
        return {
            "status": "UPDATED" if changed else "UNCHANGED",
            "path": str(LEARNING_WEIGHTS),
            "markets": reports,
            "decision_influence": True,
        }


def get_learning_weight_state(market: str) -> Dict[str, Any]:
    """Return the applied model snapshot for one market.

    Only ACTIVE/FROZEN models influence Direction.  COLLECTING, SHADOW and
    ROLLED_BACK models expose diagnostics but return neutral multipliers.
    """
    target = _market(market)
    doc = _load_doc_cached()
    model = dict((doc.get("models") or {}).get(target) or {})
    applied = (
        _enabled("TINO_V14_WEIGHT_LEARNING", "1")
        and _enabled("TINO_V14_WEIGHT_INFLUENCE", "1")
        and _model_is_applied(model)
        and bool(doc.get("decision_influence", True))
    )
    multipliers = {
        str(name): _clamp(_num(value, 1.0), ABS_MIN, ABS_MAX)
        for name, value in dict(model.get("multipliers") or {}).items()
        if str(name) in ALLOWED_FAMILIES
    } if applied else {}
    return {
        "schema": SCHEMA,
        "market": target,
        "status": str(model.get("status") or "MISSING"),
        "applied": bool(applied),
        "multipliers": multipliers,
        "generation": int(_num(model.get("generation"), 0.0)),
        "sample_count": int(_num(model.get("sample_count"), 0.0)),
        "unique_tickers": int(_num(model.get("unique_tickers"), 0.0)),
        "trained_through": model.get("trained_through"),
        "gate": model.get("gate") or "not_trained",
        "updated_at_tw": model.get("updated_at_tw") or doc.get("updated_at_tw"),
    }


def learned_family_multiplier(market: str, family: str) -> float:
    state = get_learning_weight_state(market)
    return _clamp(_num((state.get("multipliers") or {}).get(str(family)), 1.0), ABS_MIN, ABS_MAX)
