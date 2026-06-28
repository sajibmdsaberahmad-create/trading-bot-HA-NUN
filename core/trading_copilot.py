#!/usr/bin/env python3
"""
core/trading_copilot.py — Session-aware reasoning AI alongside PPO (live + replay).

This is the "literal AI" layer: large session context, reasoning, narrative generation.
It does NOT replace PPO (reflex) or Grandmaster (offline LLM training). It runs in
parallel — one async cloud/local LLM call every N seconds with full session memory,
producing a CopilotBrief that entry/exit logic and PPO hints consume.

Architecture:
  Copilot (context + reason + generate)  ← Groq/Gemini, async, throttled
       ↓ brief: ticker_bias, regime, narrative, ppo_hints
  PPO (milliseconds) + TeacherProxy + mechanical rules
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

STATE_PATH = Path("models/copilot_state.json")
JOURNAL_PATH = Path("models/copilot_journal.jsonl")


@dataclass
class CopilotBrief:
    """Structured output from the reasoning layer — consumed by entry pipeline + PPO hints."""
    narrative: str = ""
    regime_read: str = "unknown"
    risk_posture: str = "normal"  # defensive | normal | aggressive
    session_wr: float = 0.0
    session_pnl: float = 0.0
    ticker_bias: Dict[str, str] = field(default_factory=dict)  # SKIP | CAUTION | OK | FAVOR
    repeat_losers: List[str] = field(default_factory=list)
    ppo_hints: Dict[str, Any] = field(default_factory=dict)
    lessons: List[str] = field(default_factory=list)
    updated_at: float = 0.0
    source: str = "none"

    def bias_for(self, ticker: str) -> str:
        return str(self.ticker_bias.get(ticker.upper(), "OK")).upper()

    def should_skip(self, ticker: str) -> bool:
        return self.bias_for(ticker) in ("SKIP", "AVOID", "NO")

    def conf_boost(self) -> float:
        return float(self.ppo_hints.get("confidence_boost", 0.0))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _copilot_enabled(cfg: BotConfig) -> bool:
    return os.getenv("TRADING_COPILOT_ENABLED", "true").lower() in ("1", "true", "yes")


def copilot_block_repeat_losers(cfg: Optional[BotConfig] = None) -> bool:
    """Hard SKIP on repeat losers — off by default; live markets need adaptation not bans."""
    return os.getenv("COPILOT_BLOCK_REPEAT_LOSERS", "false").lower() in ("1", "true", "yes")


def copilot_repeat_loss_threshold(cfg: Optional[BotConfig] = None) -> int:
    """Losses on same ticker before CAUTION (or SKIP if blocking enabled)."""
    try:
        return max(2, int(os.getenv("COPILOT_REPEAT_LOSS_THRESHOLD", "3")))
    except (TypeError, ValueError):
        return 3


def copilot_caution_conf_bump(cfg: Optional[BotConfig] = None) -> float:
    """Extra PPO confidence required when copilot marks ticker CAUTION."""
    try:
        return max(0.0, float(os.getenv("COPILOT_CAUTION_CONF_BUMP", "0.07")))
    except (TypeError, ValueError):
        return 0.07


def copilot_caution_for_ticker(cfg: BotConfig, ticker: str) -> float:
    """Return confidence bump if ticker is on copilot caution list (not a hard block)."""
    if is_replay_relax_copilot() or not _copilot_enabled(cfg):
        return 0.0
    brief = get_copilot_brief()
    age = time.time() - brief.updated_at
    if age > float(getattr(cfg, "COPILOT_MAX_AGE_SEC", 300.0)):
        return 0.0
    if brief.bias_for(ticker) == "CAUTION":
        return copilot_caution_conf_bump(cfg)
    return 0.0


def is_replay_relax_copilot() -> bool:
    """Replay gold collection — allow entries; don't veto from stale live skip list."""
    return (
        os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
        and os.getenv("REPLAY_RELAX_COPILOT", "true").lower() in ("1", "true", "yes")
    )


def reset_copilot_for_replay(cfg: Optional[BotConfig] = None) -> None:
    """Clear stale live-session SKIP list so replay can trade and collect gold."""
    global _copilot
    brief = CopilotBrief(
        narrative="Replay session — training gold collection (copilot entry veto relaxed).",
        regime_read="replay",
        risk_posture="aggressive",
        ticker_bias={},
        repeat_losers=[],
        ppo_hints={"confidence_boost": 0.0, "skip_repeat_losers": False},
        updated_at=time.time(),
        source="replay_reset",
    )
    _save_brief(brief)
    _copilot = None
    log.info("🧭 Copilot reset for replay — skip list cleared, entries allowed for gold")


def _load_brief() -> CopilotBrief:
    if not STATE_PATH.exists():
        return CopilotBrief()
    try:
        data = json.loads(STATE_PATH.read_text())
        return CopilotBrief(**{k: v for k, v in data.items() if k in CopilotBrief.__dataclass_fields__})
    except Exception:
        return CopilotBrief()


def _save_brief(brief: CopilotBrief) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(brief.to_dict(), indent=2))


def get_copilot_brief() -> CopilotBrief:
    """Latest session brief — safe to call from hot path (reads cached file)."""
    return _load_brief()


def _build_session_context(runner: Optional["ScalperRunner"], cfg: BotConfig) -> str:
    """Aggregate session memory into one prompt block (the 'context window')."""
    lines: List[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        from core.ppo_teacher_training import trade_stats
        ts = trade_stats(n=80)
        lines.append(
            f"SESSION TRADES: {ts['count']} round-trips | WR={ts.get('win_rate', 0):.0%} "
            f"| avg_pnl=${ts.get('avg_pnl', 0):+.2f}"
        )
        for t in (ts.get("trades") or [])[-12:]:
            pnl = float(t.get("pnl_usd", 0) or 0)
            lines.append(
                f"  - {t.get('ticker')} ${pnl:+.2f} "
                f"({str(t.get('exit_reason', t.get('reason', '')))[:35]})"
            )
    except Exception:
        pass

    if runner is not None:
        try:
            nav = float(getattr(runner, "bot_nav", 0) or cfg.INITIAL_CASH)
            cash = float(getattr(runner, "bot_cash", 0) or nav)
            lines.append(f"ACCOUNT: NAV=${nav:,.0f} cash=${cash:,.0f} trades_today={getattr(runner, 'trades_today', 0)}")
        except Exception:
            pass
        locked = getattr(runner, "_locked_targets", None) or []
        if locked:
            names = [getattr(t, "ticker", str(t)) for t in locked[:12]]
            lines.append(f"LOCKED WATCH: {', '.join(names)}")
        open_pos = getattr(runner, "_position_slots", {}) or {}
        if open_pos:
            for sym, slot in list(open_pos.items())[:5]:
                lines.append(
                    f"OPEN {sym}: {slot.get('shares', 0)}sh @ ${slot.get('entry_price', 0):.4f} "
                    f"stop=${slot.get('stop', 0):.4f} tp=${slot.get('target', 0):.4f}"
                )

    try:
        from core.experience_buffer import stats as buf_stats
        bs = buf_stats()
        replay_n = (bs.get("sources") or {}).get("replay_live", 0)
        if replay_n:
            lines.append(f"MODE: replay-live ({replay_n} replay events in buffer)")
    except Exception:
        pass

    try:
        from core.commander_learning import load_commander_guidance
        notes = load_commander_guidance(6)
        if notes:
            lines.append("COMMANDER NOTES: " + " | ".join(notes[-3:]))
    except Exception:
        pass

    try:
        from core.commander_runtime import commander_runtime_context, commander_runtime_enabled
        if commander_runtime_enabled(cfg):
            lines.append(commander_runtime_context().split("\n")[0])
    except Exception:
        pass

    if getattr(runner, "consciousness", None):
        try:
            c = runner.consciousness
            lessons = getattr(getattr(c, "state", c), "learned_lessons", []) or []
            if lessons:
                lines.append("LESSONS: " + "; ".join(str(x) for x in lessons[-4:]))
        except Exception:
            pass

    lines.insert(0, f"HANOON COPILOT SESSION BRIEF @ {now}")
    return "\n".join(lines)


def _parse_brief_json(raw: str, stats: Dict[str, Any]) -> CopilotBrief:
    from core.commander_learning import _parse_plan_json
    plan = _parse_plan_json(raw)
    if not plan:
        return CopilotBrief(
            narrative=(raw or "")[:500],
            session_wr=float(stats.get("win_rate", 0)),
            updated_at=time.time(),
            source="parse_fallback",
        )
    bias = plan.get("ticker_bias") or {}
    if isinstance(bias, list):
        bias = {str(x.get("ticker", "")).upper(): x.get("bias", "OK") for x in bias if x.get("ticker")}
    return CopilotBrief(
        narrative=str(plan.get("narrative", plan.get("summary", "")))[:800],
        regime_read=str(plan.get("regime_read", plan.get("regime", "unknown"))),
        risk_posture=str(plan.get("risk_posture", "normal")),
        session_wr=float(stats.get("win_rate", 0)),
        ticker_bias={str(k).upper(): str(v) for k, v in bias.items()},
        repeat_losers=[str(x).upper() for x in (plan.get("repeat_losers") or [])],
        ppo_hints=dict(plan.get("ppo_hints") or {}),
        lessons=[str(x) for x in (plan.get("lessons") or [])[:6]],
        updated_at=time.time(),
        source="cloud_copilot",
    )


def _heuristic_brief(stats: Dict[str, Any], cfg: Optional[BotConfig] = None) -> CopilotBrief:
    """No API — pattern-based brief from trade stats."""
    from collections import defaultdict
    cfg = cfg or BotConfig()
    trades = stats.get("trades") or []
    by_t: Dict[str, List] = defaultdict(list)
    for t in trades:
        by_t[str(t.get("ticker", "")).upper()].append(t)
    bias: Dict[str, str] = {}
    repeat: List[str] = []
    relax = is_replay_relax_copilot()
    hard_block = copilot_block_repeat_losers(cfg) and not relax
    loss_thr = copilot_repeat_loss_threshold(cfg)
    for tk, xs in by_t.items():
        losses = sum(1 for x in xs if float(x.get("pnl_usd", 0) or 0) < 0)
        if losses >= loss_thr:
            repeat.append(tk)
            bias[tk] = "SKIP" if hard_block else "CAUTION"
        elif losses >= 1:
            bias[tk] = "CAUTION"
    wr = float(stats.get("win_rate", 0))
    posture = "defensive" if wr < 0.25 else "normal"
    return CopilotBrief(
        narrative=(
            f"Local copilot: {wr:.0%} session WR. "
            f"Adapt entry quality on: {', '.join(repeat) or 'none'} "
            f"({'hard skip' if hard_block else 'caution — tighter setup required'})."
        ),
        regime_read="mixed",
        risk_posture=posture,
        session_wr=wr,
        ticker_bias=bias,
        repeat_losers=repeat,
        ppo_hints={
            "confidence_boost": 0.04 if wr < 0.2 else 0.0,
            "require_quality_on_repeat": bool(repeat) and not relax,
            "min_spike_mult": 1.15 if repeat else 1.0,
        },
        lessons=(
            ["Replay: collect gold on all tickers; copilot skip relaxed"]
            if relax
            else [
                "Repeat-loss tickers: require profit_probability + spike confirmation — do not blanket-skip",
                "Raise PPO bar on re-entry after losses; adapt stops not ticker bans",
            ]
        ),
        updated_at=time.time(),
        source="heuristic",
    )


class TradingCopilot:
    """
    Async session brain — one rich LLM call, full context, non-blocking.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._in_flight = False
        self._last_refresh = 0.0
        self._brief = _load_brief()

    @property
    def brief(self) -> CopilotBrief:
        return self._brief

    def refresh_async(self, runner: Optional["ScalperRunner"] = None) -> bool:
        if not _copilot_enabled(self.cfg):
            return False
        interval = float(getattr(self.cfg, "COPILOT_REFRESH_SEC", 90.0))
        if time.time() - self._last_refresh < interval:
            return False
        with self._lock:
            if self._in_flight:
                return False
            self._in_flight = True
            self._last_refresh = time.time()

        def _worker():
            try:
                self._refresh_sync(runner)
            finally:
                with self._lock:
                    self._in_flight = False

        try:
            from core.async_utils import get_background_worker
            get_background_worker()._executor.submit(_worker)
            return True
        except Exception:
            with self._lock:
                self._in_flight = False
            return False

    def _refresh_sync(self, runner: Optional["ScalperRunner"]) -> CopilotBrief:
        from core.ppo_teacher_training import trade_stats
        stats = trade_stats(n=80)
        ctx = _build_session_context(runner, self.cfg)

        prompt = (
            "You are HANOON Trading Copilot — a full reasoning AI running ALONGSIDE a PPO scalper.\n"
            "You have the FULL session context below. Think step-by-step, then output JSON only.\n\n"
            f"{ctx}\n\n"
            "Analyze: what is going wrong/right? Which tickers need tighter entry quality?\n"
            "Prefer CAUTION (raise bar) over SKIP for repeat-loss tickers — markets fluctuate.\n"
            "Give PPO hints (confidence boost/penalty, quality requirements).\n\n"
            "JSON schema:\n"
            "{\n"
            '  "narrative": "2-4 sentences pilot voice — what you see and plan",\n'
            '  "regime_read": "trending|choppy|opening_noise|...",\n'
            '  "risk_posture": "defensive|normal|aggressive",\n'
            '  "ticker_bias": {"QS": "SKIP", "SOFI": "OK"},\n'
            '  "repeat_losers": ["QS", "LCID"],\n'
            '  "ppo_hints": {"confidence_boost": 0.05, "min_spike_mult": 1.2},\n'
            '  "lessons": ["lesson1", "lesson2"]\n'
            "}\n"
            "ticker_bias values: SKIP | CAUTION | OK | FAVOR"
        )

        raw = None
        try:
            from core.brain_maturity import allow_teacher_api
            ok, reason = allow_teacher_api("copilot", self.cfg)
            if not ok:
                log.debug(f"Copilot: local student ({reason})")
                brief = _heuristic_brief(stats, self.cfg)
                self._brief = brief
                _save_brief(brief)
                try:
                    from core.halim_capabilities import record_teacher_action
                    record_teacher_action(
                        "copilot", ctx[:2000], brief.narrative[:1500],
                        source="copilot_heuristic", cfg=self.cfg,
                    )
                except Exception:
                    pass
                return brief
            from core.council_client import CouncilClient
            client = CouncilClient(self.cfg)
            if client.enabled():
                raw = client._complete(
                    prompt, priority=False, fast=True, purpose="copilot",
                )
        except Exception as exc:
            log.debug(f"Copilot API: {exc}")

        if raw:
            brief = _parse_brief_json(raw, stats)
        else:
            brief = _heuristic_brief(stats, self.cfg)

        self._brief = brief
        _save_brief(brief)
        try:
            from core.halim_capabilities import record_teacher_action
            record_teacher_action(
                "copilot",
                ctx[:2000],
                f"{brief.narrative}\nRegime: {brief.regime_read} | Risk: {brief.risk_posture}",
                source=brief.source or "copilot",
                cfg=self.cfg,
            )
        except Exception:
            pass
        try:
            with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({**brief.to_dict(), "timestamp": datetime.now(timezone.utc).isoformat()}) + "\n")
        except Exception:
            pass

        log.info(
            f"🧭 COPILOT [{brief.source}]: {brief.risk_posture} | {brief.regime_read} | "
            f"skip={brief.repeat_losers[:5]} | {brief.narrative[:120]}…"
        )
        return brief


# Module singleton
_copilot: Optional[TradingCopilot] = None


def get_trading_copilot(cfg: BotConfig) -> TradingCopilot:
    global _copilot
    if _copilot is None:
        _copilot = TradingCopilot(cfg)
    return _copilot


def maybe_refresh_copilot(runner: Optional["ScalperRunner"] = None) -> None:
    """Call from main loop — non-blocking."""
    cfg = runner.cfg if runner is not None else BotConfig()
    get_trading_copilot(cfg).refresh_async(runner)


def copilot_blocks_entry(cfg: BotConfig, ticker: str) -> tuple[bool, str]:
    """Gate used by entry path — hard SKIP only when COPILOT_BLOCK_REPEAT_LOSERS=true."""
    if is_replay_relax_copilot():
        return False, ""
    if not _copilot_enabled(cfg):
        return False, ""
    if not copilot_block_repeat_losers(cfg):
        return False, ""
    brief = get_copilot_brief()
    age = time.time() - brief.updated_at
    if age > float(getattr(cfg, "COPILOT_MAX_AGE_SEC", 300.0)):
        return False, ""
    if brief.should_skip(ticker):
        return True, f"copilot_skip:{ticker} in {brief.repeat_losers}"
    return False, ""

