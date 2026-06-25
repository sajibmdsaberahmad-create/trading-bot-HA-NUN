#!/usr/bin/env python3
"""
core/chart_vision.py — Live intraday chart rendering + llava vision for entry council.

Non-blocking: prefetch on locked watchlist, consume at entry decision.
Vision text is stored on position close for experience-buffer training.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, Optional

import pandas as pd

from core.config import BotConfig
from core.live_ai_pipeline import entry_fingerprint
from core.notify import log


def chart_fingerprint(ticker: str, price: float, spike_ratio: float, scan_score: float) -> str:
    return entry_fingerprint(ticker, price, spike_ratio, scan_score)


def render_intraday_chart_png(df: pd.DataFrame, ticker: str, *, bars: int = 72) -> bytes:
    """Render a compact 1m scalp chart (price + volume) for llava."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tail = df.tail(max(20, bars)).copy()
    if len(tail) < 10:
        raise ValueError("insufficient bars for chart")

    closes = tail["close"].astype(float).values
    vols = tail["volume"].astype(float).values if "volume" in tail.columns else None
    x = list(range(len(closes)))

    fig, axes = plt.subplots(
        2 if vols is not None else 1,
        1,
        figsize=(6.4, 3.6),
        gridspec_kw={"height_ratios": [3, 1]} if vols is not None else None,
        sharex=True,
    )
    if vols is None:
        ax_price = axes
        ax_vol = None
    else:
        ax_price, ax_vol = axes

    ax_price.plot(x, closes, color="#22c55e", linewidth=1.4, label="close")
    if len(closes) >= 5:
        import numpy as np

        ma = pd.Series(closes).rolling(5, min_periods=1).mean().values
        ax_price.plot(x, ma, color="#94a3b8", linewidth=0.9, linestyle="--", label="MA5")
    ax_price.set_title(f"{ticker} 1m scalp", fontsize=10)
    ax_price.grid(alpha=0.25)
    ax_price.legend(loc="upper left", fontsize=7)

    if ax_vol is not None and vols is not None:
        colors = ["#22c55e" if i == 0 or closes[i] >= closes[i - 1] else "#ef4444" for i in range(len(closes))]
        ax_vol.bar(x, vols, color=colors, width=0.8, alpha=0.85)
        ax_vol.set_ylabel("vol", fontsize=8)

    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


_VISION_ENTRY_PROMPT = (
    "You are HANOON pilot AI reviewing a LIVE 1-minute scalp chart for a US equity.\n"
    "Describe ONLY what you see: trend, key levels, volume behavior, breakout vs trap risk, "
    "and a lean (enter / skip / wait). Max 100 words. No disclaimers."
)


@dataclass
class ChartVisionSlot:
    ticker: str
    fingerprint: str
    seq: int
    submitted_at: float
    completed_at: float = 0.0
    read: str = ""
    in_flight: bool = False
    latency_ms: float = 0.0


class ChartVisionLine:
    """Async llava chart reads — same non-blocking pattern as LiveAILine."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._slots: Dict[str, ChartVisionSlot] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def _key(self, ticker: str) -> str:
        return ticker.upper()

    def _max_age(self) -> float:
        return float(getattr(self.cfg, "LIVE_CHART_VISION_MAX_AGE_SEC", 12.0))

    def _min_ring(self) -> float:
        return float(getattr(self.cfg, "LIVE_CHART_VISION_MIN_RING_SEC", 2.5))

    def _should_ring(self, key: str, fingerprint: str) -> bool:
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return True
            if slot.fingerprint != fingerprint:
                return True
            if slot.in_flight:
                return False
            if slot.completed_at and (time.time() - slot.completed_at) < self._min_ring():
                return False
            return True

    def _vision_active(self, scan_score: float) -> bool:
        if getattr(self.cfg, "LIVE_CHART_VISION_ENABLED", False):
            return True
        if getattr(self.cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False):
            min_score = float(getattr(self.cfg, "LIVE_CHART_VISION_MIN_SCORE", 85.0))
            return scan_score >= min_score
        return False

    def ring(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        analyze_fn,
    ) -> bool:
        if not self._vision_active(scan_score):
            return False
        from core.memory_guard import should_allow_chart_vision

        allowed, reason = should_allow_chart_vision(self.cfg)
        if not allowed:
            log.debug(f"ChartVision skip {ticker}: {reason}")
            return False
        min_score = float(getattr(self.cfg, "LIVE_CHART_VISION_MIN_SCORE", 65.0))
        if scan_score < min_score:
            return False

        from core.ollama_vision import is_vision_model_present, resolve_vision_model

        vmodel = resolve_vision_model(self.cfg)
        if not is_vision_model_present(self.cfg, vmodel):
            return False

        opportunistic = (
            not getattr(self.cfg, "LIVE_CHART_VISION_ENABLED", False)
            and getattr(self.cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False)
        )
        if opportunistic:
            log.info(
                f"📈 ChartVision opportunistic {ticker} "
                f"(score={scan_score:.0f}) via {vmodel}"
            )

        key = self._key(ticker)
        fp = chart_fingerprint(ticker, current_px, spike_ratio, scan_score)
        if not self._should_ring(key, fp):
            return False

        try:
            png = render_intraday_chart_png(df, ticker)
        except Exception as exc:
            log.debug(f"Chart render {ticker}: {exc}")
            return False

        with self._lock:
            self._seq += 1
            seq = self._seq
            slot = ChartVisionSlot(
                ticker=ticker,
                fingerprint=fp,
                seq=seq,
                submitted_at=time.time(),
                in_flight=True,
            )
            self._slots[key] = slot

        def _worker():
            start = time.time()
            read = ""
            try:
                from core.ollama_vision import prepare_for_vision_call

                prepare_for_vision_call(self.cfg)
                read = (analyze_fn(_VISION_ENTRY_PROMPT, png) or "").strip()
            except Exception as exc:
                log.debug(f"Chart vision {ticker}: {exc}")
            finally:
                if getattr(self.cfg, "OLLAMA_VISION_UNLOAD_AFTER_CALL", False):
                    try:
                        from core.ollama_vision import stop_vision_model

                        stop_vision_model(self.cfg)
                    except Exception:
                        pass
            elapsed_ms = (time.time() - start) * 1000
            with self._lock:
                current = self._slots.get(key)
                if current is None or current.seq != seq:
                    return
                current.completed_at = time.time()
                current.read = read[:1200]
                current.in_flight = False
                current.latency_ms = elapsed_ms
                if read:
                    log.info(
                        f"📈 ChartVision {ticker} ready {elapsed_ms:.0f}ms | "
                        f"{read[:100]}"
                    )

        try:
            from core.async_utils import get_background_worker

            get_background_worker()._executor.submit(_worker)
        except Exception as exc:
            log.debug(f"ChartVision submit {ticker}: {exc}")
            with self._lock:
                s = self._slots.get(key)
                if s and s.seq == seq:
                    s.in_flight = False
            return False
        return True

    def consume(self, ticker: str, fingerprint: str) -> Dict[str, Any]:
        key = self._key(ticker)
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return {"status": "missing", "read": ""}
            if slot.fingerprint != fingerprint:
                return {"status": "stale_context", "read": "", "in_flight": slot.in_flight}
            if slot.in_flight:
                return {"status": "in_flight", "read": "", "age_sec": time.time() - slot.submitted_at}
            if not slot.read:
                return {"status": "empty", "read": ""}
            age = time.time() - slot.completed_at
            if age > self._max_age():
                return {"status": "expired", "read": "", "age_sec": age}
            return {
                "status": "fresh",
                "read": slot.read,
                "age_sec": age,
                "latency_ms": slot.latency_ms,
            }

    def peek_read(self, ticker: str, fingerprint: str) -> str:
        return (self.consume(ticker, fingerprint).get("read") or "").strip()
