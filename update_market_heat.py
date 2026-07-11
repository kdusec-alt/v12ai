# -*- coding: utf-8 -*-
"""TINO V12 RC25.3 Market Heat updater — stable automatic version.

Rules:
- Runs only outside Streamlit (GitHub Actions).
- TWSE is the mandatory primary source.
- TPEx is optional; failure never makes the job fail.
- Weekends are skipped before any request.
- Existing valid cache is preserved when a secondary source is unavailable.
- Never replaces a good cache with an empty/broken record.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TAIPEI = timezone(timedelta(hours=8))
CACHE_PATH = Path(__file__).resolve().with_name("market_heat_cache.json")
SCHEMA_VERSION = 3
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (TINO-RC25.3-MarketHeat/1.0)"


def _now_tw() -> datetime:
    return datetime.now(TAIPEI)


def _log(msg: str) -> None:
    print(f"[RC25_MARKET_HEAT] {msg}", flush=True)


def _request(url: str) -> str:
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
    })
    with urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = text.replace(",", "").replace("+", "").replace("億", "").replace("元", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _to_yi(value: float, context: str) -> float:
    # Official summary tables commonly use 仟元/千元.
    if "仟元" in context or "千元" in context or abs(value) > 200000:
        return round(value / 100000.0, 2)
    return round(value, 2)


def _candidate_weekdays(max_backtrack_days: int) -> Iterable[datetime]:
    base = _now_tw()
    for i in range(max_backtrack_days + 1):
        dt = base - timedelta(days=i)
        if dt.weekday() >= 5:
            continue
        yield dt


def _rows_from_payload(payload: Any) -> List[Tuple[List[str], List[str]]]:
    rows: List[Tuple[List[str], List[str]]] = []

    def add(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        headers = [str(x) for x in obj.get("fields", [])] if isinstance(obj.get("fields"), list) else []
        data = obj.get("data") or obj.get("aaData") or obj.get("tbody") or []
        if isinstance(data, list):
            for row in data:
                if isinstance(row, list):
                    rows.append((headers, [str(x) for x in row]))
                elif isinstance(row, dict):
                    rows.append(([str(k) for k in row.keys()], [str(v) for v in row.values()]))

    if isinstance(payload, dict):
        add(payload)
        for value in payload.values():
            if isinstance(value, dict):
                add(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        add(item)
                    elif isinstance(item, list):
                        rows.append(([], [str(x) for x in item]))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                rows.append(([str(k) for k in item.keys()], [str(v) for v in item.values()]))
            elif isinstance(item, list):
                rows.append(([], [str(x) for x in item]))
    return rows


def _extract_margin(rows: List[Tuple[List[str], List[str]]]) -> Optional[float]:
    candidates: List[Tuple[int, float]] = []
    for headers, row in rows:
        joined = "|".join(headers + row)
        label = row[0] if row else ""
        if "融資" not in joined:
            continue
        if not any(token in joined for token in ("融資金額", "融資餘額", "融資余额")):
            continue
        if any(token in joined for token in ("融券", "信用曝險", "維持率", "資券相抵")):
            continue
        value: Optional[float] = None
        if headers and len(headers) == len(row):
            for i, h in enumerate(headers):
                if any(token in h for token in ("今日餘額", "本日餘額", "餘額", "余额")):
                    value = _num(row[i])
                    if value is not None:
                        break
        if value is None:
            nums = [_num(cell) for cell in row]
            nums = [n for n in nums if n is not None]
            if nums:
                value = nums[-1]
        if value is None:
            continue
        yi = _to_yi(value, joined)
        if 100 <= yi <= 20000:
            priority = 0 if ("融資金額" in label or "融資餘額" in label) else 1
            candidates.append((priority, yi))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], -x[1]))
    return candidates[0][1]


def _fetch_twse(dt: datetime) -> float:
    date8 = dt.strftime("%Y%m%d")
    urls = [
        "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?" + urlencode({"date": date8, "selectType": "MS", "response": "json"}),
        "https://www.twse.com.tw/exchangeReport/MI_MARGN?" + urlencode({"date": date8, "selectType": "MS", "response": "json"}),
    ]
    errors: List[str] = []
    for url in urls:
        try:
            payload = json.loads(_request(url))
            margin = _extract_margin(_rows_from_payload(payload))
            if margin is not None:
                _log(f"TWSE date={dt.date().isoformat()} margin_yi={margin}")
                return margin
            errors.append("parse_no_margin")
        except Exception as exc:
            errors.append(type(exc).__name__)
    raise RuntimeError(f"TWSE no data {date8}: {errors}")


def _fetch_tpex(dt: datetime) -> Optional[float]:
    # Secondary source only. Any failure returns None and never aborts the job.
    roc = f"{dt.year - 1911:03d}/{dt.month:02d}/{dt.day:02d}"
    ymd = dt.strftime("%Y/%m/%d")
    urls = [
        "https://www.tpex.org.tw/www/zh-tw/margin/marginBalance?" + urlencode({"date": ymd, "response": "json"}),
        "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?" + urlencode({"l": "zh-tw", "d": roc}),
    ]
    for url in urls:
        try:
            text = _request(url)
            try:
                payload = json.loads(text)
                rows = _rows_from_payload(payload)
            except Exception:
                rows = []
            margin = _extract_margin(rows)
            if margin is not None:
                _log(f"TPEx date={dt.date().isoformat()} margin_yi={margin}")
                return margin
        except Exception as exc:
            _log(f"TPEx optional failure date={dt.date().isoformat()} type={type(exc).__name__}")
    _log(f"TPEx optional unavailable date={dt.date().isoformat()}; preserve previous cache")
    return None


def _load_cache(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _latest_two_twse(max_backtrack_days: int) -> List[Tuple[datetime, float]]:
    found: List[Tuple[datetime, float]] = []
    for dt in _candidate_weekdays(max_backtrack_days):
        try:
            found.append((dt, _fetch_twse(dt)))
            if len(found) >= 2:
                return found
        except Exception as exc:
            _log(f"TWSE skip date={dt.date().isoformat()} reason={exc}")
    return found


def _classify(value: float) -> Dict[str, Any]:
    if value < 6000:
        return {"level": "偏健康", "icon": "🟢", "risk_score": 20, "note": "可積極布局"}
    if value < 6100:
        return {"level": "中性", "icon": "🟡", "risk_score": 42, "note": "觀察外資是否同步買超"}
    if value < 6300:
        return {"level": "中性偏熱", "icon": "🟡", "risk_score": 58, "note": "需觀察外資是否同步買超"}
    if value < 6500:
        return {"level": "偏熱", "icon": "🟠", "risk_score": 75, "note": "法人沒跟容易震盪"}
    return {"level": "警戒", "icon": "🔴", "risk_score": 92, "note": "提高警戒，容易融資殺盤"}


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def update_cache(max_backtrack_days: int, output: Path) -> Dict[str, Any]:
    previous = _load_cache(output)
    found = _latest_two_twse(max_backtrack_days)

    if len(found) < 2:
        if previous.get("accepted") is True:
            previous["updated_at"] = _now_tw().isoformat(timespec="seconds")
            previous["status"] = "沿用前次快取；TWSE 暫時無法更新"
            previous["stale"] = True
            _atomic_write(output, previous)
            _log("TWSE unavailable; preserved previous accepted cache; workflow remains successful")
            return previous
        # No valid prior cache: write a safe pending record but do not crash the workflow.
        pending = {
            "schema_version": SCHEMA_VERSION,
            "accepted": False,
            "date": "",
            "reason": "TWSE 暫時無法取得，等待下次自動更新",
            "source": "RC25.3 stable auto updater",
            "updated_at": _now_tw().isoformat(timespec="seconds"),
        }
        _atomic_write(output, pending)
        _log("TWSE unavailable and no prior cache; wrote pending record; workflow remains successful")
        return pending

    (latest_dt, latest_twse), (prev_dt, prev_twse) = found[0], found[1]
    latest_tpex = _fetch_tpex(latest_dt)

    prior_tpex = _num(previous.get("tpex_margin_yi"))
    prior_tpex_change = _num(previous.get("tpex_change_yi"))
    tpex_value = latest_tpex if latest_tpex is not None else prior_tpex
    tpex_status = "official_current" if latest_tpex is not None else ("preserved_previous" if prior_tpex is not None else "unavailable")

    twse_change = round(latest_twse - prev_twse, 2)
    total = round(latest_twse + tpex_value, 2) if tpex_value is not None else None
    total_change = round(twse_change + prior_tpex_change, 2) if total is not None and prior_tpex_change is not None else None

    credit = _num(previous.get("credit_exposure_yi"))
    credit_change = _num(previous.get("credit_exposure_change_yi"))
    cls = _classify(latest_twse)

    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "accepted": True,
        "date": latest_dt.date().isoformat(),
        "twse_margin_yi": round(latest_twse, 2),
        "twse_change_yi": twse_change,
        "tpex_margin_yi": round(tpex_value, 2) if tpex_value is not None else None,
        "tpex_change_yi": round(prior_tpex_change, 2) if prior_tpex_change is not None else None,
        "listed_otc_margin_yi": total,
        "listed_otc_change_yi": total_change,
        "credit_exposure_yi": round(credit, 2) if credit is not None else None,
        "credit_exposure_change_yi": round(credit_change, 2) if credit_change is not None else None,
        "heat_base_yi": round(latest_twse, 2),
        "level": cls["level"],
        "icon": cls["icon"],
        "risk_score": cls["risk_score"],
        "note": cls["note"],
        "source": "TWSE official primary; TPEx optional with last-good fallback",
        "updated_at": _now_tw().isoformat(timespec="seconds"),
        "previous_date": prev_dt.date().isoformat(),
        "status": "正常更新" if latest_tpex is not None else "上市已更新；上櫃沿用前值或待補",
        "components": {
            "twse_status": "official_current",
            "tpex_status": tpex_status,
            "weekend_filtered": True,
        },
    }
    _atomic_write(output, payload)
    _log(json.dumps(payload, ensure_ascii=False))
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-backtrack-days", type=int, default=14)
    parser.add_argument("--output", type=Path, default=CACHE_PATH)
    args = parser.parse_args(argv)
    update_cache(args.max_backtrack_days, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
