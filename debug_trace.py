# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, List
from models import SignalPacket, TraceStep

REQUIRED_TRACE = ["VWAP", "FQC", "LCR", "BSI", "RCRS", "法人", "資券", "Macro", "GRR", "事件"]


def trace_step_from_signal(name: str, signal: SignalPacket, adjustment: float) -> TraceStep:
    return TraceStep(name=name, signal=signal.signal, adjustment=round(float(adjustment), 4), confidence_delta=round(float(signal.confidence), 2), accepted=bool(signal.accepted), reason=signal.reason, source=signal.source, date=signal.date)


def ensure_required_trace_rows(steps: Iterable[TraceStep], date: str = "") -> List[TraceStep]:
    rows = list(steps)
    existing = {x.name for x in rows}
    for name in REQUIRED_TRACE:
        if name not in existing:
            rows.append(TraceStep(name, "未提供｜不計分", 0.0, 0.0, False, "required trace placeholder", "orchestrator", date))
    return rows


def trace_to_text(rows: Iterable[TraceStep], raw_t1: float, final_t1: float) -> str:
    lines = [f"Raw T1 {raw_t1:.2f}"]
    for r in rows:
        conf = f" | confidence {r.confidence_delta:+.0f}" if abs(r.confidence_delta) > 0 else ""
        lines.append(f"{r.name} {r.adjustment:+.2f}{conf}｜{r.signal}")
    lines.append(f"Final T1 {final_t1:.2f}")
    return "\n".join(lines)
