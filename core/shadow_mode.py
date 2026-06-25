#!/usr/bin/env python3
"""
core/shadow_mode.py — Circuit breaker: LIVE → SHADOW after loss streak / daily DD.

In SHADOW: council + ATR math continue; IB orders blocked. Resume LIVE after
shadow sample shows positive mathematical expectancy.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/shadow_circuit_state.json")


@dataclass
class ShadowPosition:
    ticker: str
    entry: float
    stop: float
    target: float
    shares: int
    opened_at: float
    regime: str = ""


class ShadowCircuitBreaker:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.in_shadow: bool = False
        self.consecutive_losses: int = 0
        self.daily_start_equity: float = 0.0
        self.daily_pnl_usd: float = 0.0
        self.shadow_open: Dict[str, ShadowPosition] = {}
        self.shadow_closed: Deque[Dict[str, Any]] = deque(maxlen=200)
        self._load()

    def _load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text())
            self.in_shadow = bool(data.get("in_shadow", False))
            self.consecutive_losses = int(data.get("consecutive_losses", 0))
            self.daily_start_equity = float(data.get("daily_start_equity", 0))
            self.daily_pnl_usd = float(data.get("daily_pnl_usd", 0))
            for row in data.get("shadow_closed", [])[-200:]:
                self.shadow_closed.append(row)
            for t, row in (data.get("shadow_open") or {}).items():
                self.shadow_open[t] = ShadowPosition(**row)
        except Exception:
            pass

    def _save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "in_shadow": self.in_shadow,
            "consecutive_losses": self.consecutive_losses,
            "daily_start_equity": self.daily_start_equity,
            "daily_pnl_usd": self.daily_pnl_usd,
            "shadow_open": {k: vars(v) for k, v in self.shadow_open.items()},
            "shadow_closed": list(self.shadow_closed)[-100:],
            "updated_at": time.time(),
        }, indent=2))

    def reset_daily(self, equity: float) -> None:
        self.daily_start_equity = equity
        self.daily_pnl_usd = 0.0
        self._save()

    def block_broker(self) -> bool:
        if not getattr(self.cfg, "SHADOW_CIRCUIT_ENABLED", True):
            return False
        if getattr(self.cfg, "PAPER_TRADING", False) and not getattr(
            self.cfg, "SHADOW_ON_PAPER", False,
        ):
            return False
        return self.in_shadow

    def on_live_trade_closed(self, pnl_usd: float, equity: float) -> None:
        if not getattr(self.cfg, "SHADOW_CIRCUIT_ENABLED", True):
            return
        self.daily_pnl_usd += pnl_usd
        if pnl_usd < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        trigger_n = int(getattr(self.cfg, "SHADOW_CONSECUTIVE_LOSS_TRIGGER", 4))
        dd_pct = float(getattr(self.cfg, "SHADOW_DAILY_DD_PCT", 0.02))
        daily_dd = 0.0
        if self.daily_start_equity > 0:
            daily_dd = -self.daily_pnl_usd / self.daily_start_equity
        if self.consecutive_losses >= trigger_n or daily_dd >= dd_pct:
            self._enter_shadow(
                f"losses={self.consecutive_losses} daily_dd={daily_dd:.2%}"
            )
        elif self.in_shadow and self.can_resume_live():
            self._exit_shadow("shadow expectancy recovered")
        else:
            self._save()

    def _enter_shadow(self, reason: str) -> None:
        if self.in_shadow:
            return
        self.in_shadow = True
        log.warning(
            f"  🌑 SHADOW MODE ON — IB blocked | {reason} | "
            f"re-entry after {getattr(self.cfg, 'SHADOW_REENTRY_MIN_TRADES', 20)} "
            f"positive-expectancy shadow trades"
        )
        self._save()

    def _exit_shadow(self, reason: str) -> None:
        self.in_shadow = False
        self.consecutive_losses = 0
        log.info(f"  ☀️ SHADOW MODE OFF — resuming IB routing | {reason}")
        self._save()

    def can_resume_live(self) -> bool:
        min_n = int(getattr(self.cfg, "SHADOW_REENTRY_MIN_TRADES", 20))
        min_wr = float(getattr(self.cfg, "SHADOW_REENTRY_MIN_WIN_RATE", 0.45))
        min_exp = float(getattr(self.cfg, "SHADOW_REENTRY_MIN_EXPECTANCY", 0.0))
        recent = [t for t in self.shadow_closed if t.get("closed_at", 0) > 0][-min_n:]
        if len(recent) < min_n:
            return False
        wins = sum(1 for t in recent if float(t.get("pnl_usd", 0)) > 0)
        wr = wins / len(recent)
        expectancy = sum(float(t.get("pnl_usd", 0)) for t in recent) / len(recent)
        return wr >= min_wr and expectancy > min_exp

    def open_shadow_trade(
        self,
        ticker: str,
        entry: float,
        stop: float,
        target: float,
        shares: int,
        regime: str = "",
    ) -> None:
        self.shadow_open[ticker] = ShadowPosition(
            ticker=ticker, entry=entry, stop=stop, target=target,
            shares=shares, opened_at=time.time(), regime=regime,
        )
        log.info(
            f"  🌑 SHADOW enter {ticker}: {shares}sh @ ${entry:.4f} | "
            f"stop ${stop:.4f} tp ${target:.4f}"
        )
        self._save()

    def update_shadow_price(self, ticker: str, price: float) -> Optional[Dict[str, Any]]:
        pos = self.shadow_open.get(ticker)
        if not pos or price <= 0:
            return None
        exit_px = None
        reason = ""
        if price <= pos.stop:
            exit_px, reason = pos.stop, "shadow_stop"
        elif price >= pos.target:
            exit_px, reason = pos.target, "shadow_target"
        if exit_px is None:
            return None
        pnl = (exit_px - pos.entry) * pos.shares
        rec = {
            "ticker": ticker,
            "entry": pos.entry,
            "exit": exit_px,
            "shares": pos.shares,
            "pnl_usd": round(pnl, 2),
            "result": "win" if pnl > 0 else "loss",
            "reason": reason,
            "regime": pos.regime,
            "hold_sec": time.time() - pos.opened_at,
            "closed_at": time.time(),
        }
        self.shadow_closed.append(rec)
        del self.shadow_open[ticker]
        log.info(f"  🌑 SHADOW exit {ticker}: ${pnl:+.2f} ({reason})")
        if self.in_shadow and self.can_resume_live():
            self._exit_shadow("shadow sample passed re-entry gate")
        else:
            self._save()
        return rec

    def shadow_stats(self) -> Dict[str, Any]:
        recent = list(self.shadow_closed)[-20:]
        if not recent:
            return {"count": 0, "in_shadow": self.in_shadow}
        pnls = [float(t.get("pnl_usd", 0)) for t in recent]
        return {
            "in_shadow": self.in_shadow,
            "count": len(recent),
            "win_rate": sum(1 for p in pnls if p > 0) / len(pnls),
            "expectancy": sum(pnls) / len(pnls),
            "open": len(self.shadow_open),
        }
