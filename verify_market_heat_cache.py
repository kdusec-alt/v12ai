# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path

p = Path(__file__).resolve().with_name("market_heat_cache.json")
data = json.loads(p.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit("cache must be a JSON object")
if data.get("accepted") is True and data.get("twse_margin_yi") is None:
    raise SystemExit("accepted cache requires twse_margin_yi")
print("market_heat_cache.json OK")
