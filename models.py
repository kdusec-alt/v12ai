# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TickerInfo:
    raw: str
    resolved_symbol: str
    name: str
    market: str  # TW / US
    asset_type: str  # stock / etf
    exchange: str = ""
    currency: str = "TWD"
    price_limit_pct: Optional[float] = None


@dataclass(frozen=True)
class DataTruth:
    source: str
    date: str
    fallback: bool
    accepted: bool
    reason: str
    freshness: str = "latest"


@dataclass
class PriceFrame:
    ticker: TickerInfo
    truth: DataTruth
    open: float
    high: float
    low: float
    last: float
    previous_close: float
    volume: float
    vwap: float
    atr14: float
    recent_closes: List[float] = field(default_factory=list)
    recent_highs: List[float] = field(default_factory=list)
    recent_lows: List[float] = field(default_factory=list)
    recent_volumes: List[float] = field(default_factory=list)
    price_date: str = ""
    market_status: str = "closed_reference"
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalPacket:
    module: str
    signal: str
    score: float
    confidence: float
    risk: float
    bias: float
    reason: str
    source: str
    date: str
    accepted: bool


@dataclass(frozen=True)
class RawForecast:
    raw_t0: float
    raw_t1: float
    raw_t1_high: float
    raw_t1_low: float
    raw_abc: Dict[str, float]
    raw_low_entry: float
    raw_no_chase: float


@dataclass(frozen=True)
class TraceStep:
    name: str
    signal: str
    adjustment: float
    confidence_delta: float
    accepted: bool
    reason: str
    source: str
    date: str


@dataclass
class PredictionTrace:
    ticker: str
    raw_t1: Optional[float]
    steps: List[TraceStep]
    final_t1: Optional[float]

    def reconstruct_final_t1(self) -> Optional[float]:
        if self.raw_t1 is None:
            return None
        return round(self.raw_t1 + sum(step.adjustment for step in self.steps), 4)

    def to_rows(self) -> List[Dict[str, Any]]:
        rows = [{
            "name": "Raw T1",
            "signal": "raw forecast",
            "adjustment": 0.0,
            "confidence_delta": 0.0,
            "accepted": True,
            "reason": "Forecast Engine raw output",
            "source": "forecast_engine",
            "date": "",
        }]
        rows.extend(asdict(step) for step in self.steps)
        rows.append({
            "name": "Orchestrator Final",
            "signal": "final forecast",
            "adjustment": 0.0,
            "confidence_delta": 0.0,
            "accepted": True,
            "reason": f"Final T1={self.final_t1}",
            "source": "orchestrator",
            "date": "",
        })
        return rows


@dataclass(frozen=True)
class NewsItem:
    source: str
    time: str
    score: float
    tag: str
    title: str
    link: str


@dataclass
class FinalForecast:
    ticker: TickerInfo
    stopped: bool
    stop_reason: str
    raw: Optional[RawForecast]
    final_t0: Optional[float]
    final_t1: Optional[float]
    final_t1_high: Optional[float]
    final_t1_low: Optional[float]
    confidence: float
    no_chase: Optional[float]
    low_entry: Optional[float]
    decision_card: Dict[str, Any]
    tags: List[str]
    one_liner: str
    reality_anchor: str
    radar: Dict[str, str]
    trace: PredictionTrace
    data_truths: List[DataTruth]
    deep_report: str = ""
    news_items: List[NewsItem] = field(default_factory=list)
    signals: List[SignalPacket] = field(default_factory=list)


@dataclass(frozen=True)
class LearningSuggestion:
    ticker: str
    error_type: str
    proposed_change: str
    max_weight_delta_pct: float
    reason: str
    safe_to_apply: bool
    requires_tino_approval: bool = True
