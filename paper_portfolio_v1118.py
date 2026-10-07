"""Forward-only cash ledger for verified autonomous T+1 paper trades."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math

DEFAULTS = {"TWD": 1_000_000.0, "USD": 20_000.0}
MAX_TRADE_FRACTION = 0.10


def _path():
    from memory_store import MEMORY_DIR
    return Path(MEMORY_DIR) / "paper_lab" / "portfolio_events.jsonl"


def _events():
    from memory_store import read_jsonl
    return read_jsonl(_path(), 10000)


def snapshot(events=None):
    events = _events() if events is None else events
    cash = dict(DEFAULTS)
    initial = dict(DEFAULTS)
    trades = []
    seen = set()
    for event in events:
        kind, currency = event.get("type"), event.get("currency")
        if currency not in cash:
            continue
        if kind == "capital_adjustment":
            amount = float(event.get("amount") or 0)
            if math.isfinite(amount) and cash[currency] + amount >= 0 and initial[currency] + amount > 0:
                cash[currency] += amount
                initial[currency] += amount
        elif kind == "paper_trade" and event.get("experiment_id") not in seen:
            quantity = int(event.get("quantity") or 0)
            debit, credit = float(event.get("debit") or 0), float(event.get("credit") or 0)
            if quantity > 0 and 0 < debit <= cash[currency] + 1e-6 and credit >= 0:
                cash[currency] += credit - debit
                trades.append(event)
                seen.add(event.get("experiment_id"))
    return {"cash": cash, "initial": initial, "trades": trades, "seen": seen}


def add_capital(currency, amount):
    """Admin-only caller; append deposit without rewriting earlier trades."""
    from memory_store import append_jsonl
    if currency not in DEFAULTS or not math.isfinite(float(amount)) or float(amount) <= 0:
        raise ValueError("invalid deposit")
    event = {"type": "capital_adjustment", "currency": currency,
             "amount": round(float(amount), 2), "at": datetime.now(timezone.utc).isoformat()}
    append_jsonl(_path(), event)
    return event


def set_capital(currency, target):
    """Change allocated capital prospectively, preserving every historical fill."""
    from memory_store import append_jsonl
    state = snapshot()
    target = float(target)
    if currency not in DEFAULTS or not math.isfinite(target) or target <= 0:
        raise ValueError("invalid target")
    difference = round(target - state["initial"][currency], 2)
    if difference < 0 and state["cash"][currency] + difference < 0:
        raise ValueError("withdrawal exceeds available cash")
    if difference:
        append_jsonl(_path(), {"type": "capital_adjustment", "currency": currency,
                                 "amount": difference, "at": datetime.now(timezone.utc).isoformat()})
    return snapshot()


def record_verified_trades():
    """Run once after T+1 audits; no quotes, historical replay or broker access."""
    from memory_store import MEMORY_DIR, read_jsonl, append_jsonl
    root = Path(MEMORY_DIR) / "paper_lab"
    seeds = {r.get("experiment_id"): r for r in read_jsonl(root / "experiments.jsonl", 2000)
             if r.get("origin") == "autonomous"}
    outcomes = sorted(read_jsonl(root / "outcomes.jsonl", 4000),
                      key=lambda r: (str(r.get("date") or ""), str(r.get("settled_at") or ""), str(r.get("experiment_id") or "")))
    state = snapshot()
    count = 0
    for result in outcomes:
        identifier = result.get("experiment_id")
        seed = seeds.get(identifier)
        if not seed or result.get("status") != "CLOSED" or identifier in state["seen"]:
            continue
        currency = "USD" if seed.get("market") == "US" else "TWD"
        buy, sell, fee = (float(result.get(key) or 0) for key in ("entry_price", "exit_price", "fees_unit"))
        if not all(math.isfinite(v) for v in (buy, sell, fee)) or min(buy, sell) <= 0 or fee < 0:
            continue
        # Reserve the complete round-trip costs, while limiting exposure to 10% of initial pool.
        unit_cost = buy + fee / 2
        quantity = min(int(state["initial"][currency] * MAX_TRADE_FRACTION // unit_cost),
                       int(state["cash"][currency] // unit_cost))
        if quantity < 1:
            continue
        debit = round(unit_cost * quantity, 2)
        credit = round(max(0, sell - fee / 2) * quantity, 2)
        event = {"type": "paper_trade", "experiment_id": identifier, "ticker": seed.get("ticker"),
                 "currency": currency, "date": result.get("date"), "quantity": quantity,
                 "entry_price": buy, "exit_price": sell, "debit": debit, "credit": credit,
                 "profit": round(credit - debit, 2), "at": datetime.now(timezone.utc).isoformat()}
        append_jsonl(_path(), event)
        state["cash"][currency] += credit - debit
        state["trades"].append(event)
        state["seen"].add(identifier)
        count += 1
    return {"recorded": count, "cash": state["cash"]}
