#!/usr/bin/env python3
"""
core/ai_commander.py — Full AI control plane for HANOON.

PRIMARY MISSION: PROFIT HUNTING. When AI_FULL_CONTROL is enabled, every decision
serves opportunistic profit extraction. Full freedom within hard risk guardrails.
All hunt signals, exits, and misses are tracked in profit_hunt_ledger + buffer.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log
from core.market_hours import min_confidence_for_state
from core.pilot_mode import generative_think, get_effective_confidence_threshold, get_ai_deploy_budget, get_trade_risk_usd, effective_max_concurrent_positions, is_ai_unlimited, is_ai_council_mode
from core.bracket_validator import (
    compute_atr_bracket,
    validate_decision_bracket,
    adjust_managed_stop,
    adjust_managed_target,
)
from core.trade_telemetry import log_bracket_reject
from core.risk import compute_atr, compute_momentum_score
from core.human_cognition import enrich_prompt, apply_gut_override
from core.fast_execution import should_spike_fast_entry
from core.deferred_council_learning import DeferredCouncilLearner, deferred_learning_enabled
from core.live_ai_pipeline import (
    LiveAILine,
    entry_fingerprint,
    position_fingerprint,
    exit_fingerprint,
    stagnation_fingerprint,
    merge_entry_decision,
    merge_exit_decision,
    merge_position_manage_decision,
    merge_stagnation_decision,
    merge_scan_score_decision,
    merge_rank_scan_decision,
    merge_pick_target_decision,
    merge_risk_signal_decision,
    rank_scan_fingerprint,
    pick_target_fingerprint,
    risk_signal_fingerprint,
    scan_fingerprint,
)
from core.chart_vision import ChartVisionLine, chart_fingerprint

if TYPE_CHECKING:
    from core.cognitive_autopilot import CognitiveAutopilot
    from core.consciousness import AIConsciousness


def _parse_float_price(value: Any, default: float) -> float:
    """Parse AI JSON numbers that may include $ or commas."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).strip().replace("$", "").replace(",", "")
        return float(cleaned) if cleaned else default
    except (TypeError, ValueError):
        return default

DECISION_LOG = Path("models/ai_decision_log.jsonl")
TRADE_JOURNAL = Path("models/trade_journal.json")


def _parse_json_response(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {}


# Live trading tasks always use the priority Ollama path (no rate-limit deferral).
_DECISION_TASKS = frozenset({
    "entry_decision", "position_manage", "exit_decision", "stagnation_check",
    "scan_score", "rank_scan", "pick_next_target", "risk_exit", "lock_review",
    "gut_check", "decide", "exit", "account_eval", "account_brief",
})


class AICommander:
    """Single brain for all live trading decisions and narratives."""

    def __init__(
        self,
        cfg: BotConfig,
        autopilot: Optional["CognitiveAutopilot"] = None,
        consciousness: Optional["AIConsciousness"] = None,
        model=None,
        ai_components: Optional[Dict] = None,
    ):
        self.cfg = cfg
        self.autopilot = autopilot
        self.consciousness = consciousness
        self.model = model
        self.ai_components = ai_components or {}
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._trade_journal: List[Dict] = []
        self._live_line = LiveAILine(cfg, self._ollama_decide_raw)
        self._chart_line = ChartVisionLine(cfg)
        self._deferred = DeferredCouncilLearner(cfg, self._live_line)
        DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._load_journal()

    @property
    def full_control(self) -> bool:
        return bool(getattr(self.cfg, "AI_FULL_CONTROL", True))

    @property
    def council_mode(self) -> bool:
        return is_ai_council_mode(self.cfg)

    def ollama_audit_snapshot(self, ticker: str, task: str = "entry_decision") -> Dict[str, Any]:
        """Raw + parsed Ollama output for post-mortem (hallucination audit)."""
        return self._live_line.peek(ticker, task)

    def _vision_analyze(self, prompt: str, image_bytes: bytes) -> str:
        ollama = None
        if self.autopilot and getattr(self.autopilot, "core", None):
            ollama = getattr(self.autopilot.core, "ollama", None)
        if ollama and hasattr(ollama, "analyze_image"):
            return (ollama.analyze_image(prompt, image_bytes) or "").strip()
        from core.ollama_brain import OllamaBrain

        brain = OllamaBrain(self.cfg)
        return (brain.analyze_image(prompt, image_bytes) or "").strip()

    def prefetch_chart_vision(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
    ) -> None:
        """Non-blocking llava read on locked watchlist — feeds entry council."""
        if df is None or len(df) < 20:
            return
        self._chart_line.ring(
            ticker, df, current_px, spike_ratio, scan_score, self._vision_analyze,
        )

    def chart_read_for(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
    ) -> str:
        fp = chart_fingerprint(ticker, current_px, spike_ratio, scan_score)
        return self._chart_line.peek_read(ticker, fp)

    def _chart_context_line(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
    ) -> str:
        if not getattr(self.cfg, "LIVE_CHART_VISION_ENABLED", True):
            return ""
        fp = chart_fingerprint(ticker, current_px, spike_ratio, scan_score)
        live = self._chart_line.consume(ticker, fp)
        if live.get("status") != "fresh":
            return ""
        read = (live.get("read") or "").strip()
        if not read:
            return ""
        return f"CHART VISION (llava): {read[:700]}\n"

    def _build_entry_bracket(
        self,
        current_px: float,
        df: Optional[pd.DataFrame],
        *,
        equity: float,
        cash: float,
        deploy_cap: float,
        is_penny: bool,
        avg_vol: float,
        atr: Optional[float] = None,
    ) -> Any:
        """ATR-only brackets — Ollama never supplies stop/TP/shares."""
        if atr is None or atr <= 0:
            if df is not None and len(df) >= 5:
                atr = compute_atr(df, period=5)
            else:
                atr = current_px * float(getattr(self.cfg, "SCALP_MIN_STOP_PCT", 0.004))
        momentum = 0.0
        if df is not None and len(df) >= 10:
            try:
                momentum = float(compute_momentum_score(df, lookback=10))
            except Exception:
                momentum = 0.0
        shares_hint = max(1, int(deploy_cap / current_px)) if deploy_cap > 0 and current_px > 0 else 0
        return compute_atr_bracket(
            self.cfg,
            current_px,
            float(atr),
            equity=equity,
            cash=cash,
            deploy_cap=deploy_cap,
            shares_hint=shares_hint,
            momentum_score=momentum,
            is_penny=is_penny,
            avg_vol=avg_vol,
            use_fixed_risk=bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False)),
            max_risk_usd=get_trade_risk_usd(self.cfg, equity),
        )

    def _load_journal(self):
        if TRADE_JOURNAL.exists():
            try:
                self._trade_journal = json.loads(TRADE_JOURNAL.read_text())
            except Exception:
                self._trade_journal = []

    def _save_journal(self):
        try:
            TRADE_JOURNAL.write_text(json.dumps(self._trade_journal[-500:], indent=2))
        except Exception:
            pass

    def think(self, prompt: str, task: str = "reason") -> str:
        if self.autopilot and getattr(self.autopilot, "core", None):
            try:
                return (self.autopilot.core.think(prompt, task=task) or "").strip()
            except Exception:
                pass
        return generative_think(self.cfg, self.autopilot, prompt)

    def _ollama_decide_raw(self, full_prompt: str) -> str:
        """Direct priority Ollama call — bypasses rate limit for trading decisions."""
        ollama = None
        if self.autopilot and getattr(self.autopilot, "core", None):
            ollama = getattr(self.autopilot.core, "ollama", None)
        if ollama and hasattr(ollama, "decide_call"):
            try:
                return (ollama.decide_call(full_prompt) or "").strip()
            except Exception as exc:
                log.debug(f"Ollama decide: {exc}")
        return ""

    def compose_telegram(self, prompt: str) -> str:
        """Dedicated Ollama path for Telegram — bypasses 30s trading rate limit."""
        ollama = None
        if self.autopilot and getattr(self.autopilot, "core", None):
            ollama = getattr(self.autopilot.core, "ollama", None)
        if ollama and hasattr(ollama, "compose_notification"):
            try:
                return (ollama.compose_notification(prompt) or "").strip()
            except Exception as exc:
                log.debug(f"AI telegram notify: {exc}")
        return self.think(prompt)

    def _mood_context(self) -> tuple:
        if self.autopilot and getattr(self.autopilot, "core", None):
            st = self.autopilot.core.state
            return st.mood, st.confidence, list(st.learned_lessons)
        return "awake", 0.5, []

    def _human_think(self, task: str, context: Dict[str, Any]) -> str:
        mood, conf, lessons = self._mood_context()
        prompt = enrich_prompt(task, context, self.cfg, mood, conf, lessons)
        return self.think(prompt)

    def think_json_live(
        self,
        ticker: str,
        task: str,
        prompt: str,
        fingerprint: str,
    ) -> Dict[str, Any]:
        """
        Non-blocking live Ollama — ring async + consume fresh result only.
        Never uses TTL cache; stale fingerprints are discarded.
        """
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(task, {"request": prompt[:2500]}, self.cfg, mood, conf, lessons)
        self._live_line.ring(ticker, task, full, fingerprint)
        live = self._live_line.consume(ticker, task, fingerprint)
        return live.get("parsed") or {}

    def live_status(self, ticker: str, task: str) -> Dict[str, Any]:
        return self._live_line.status(ticker, task)

    def service_deferred_learning(self) -> int:
        """Log late Ollama answers after PPO-led execute — non-blocking."""
        return self._deferred.service()

    def _entry_council_prompt(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        *,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        account: Dict[str, Any],
        market_ctx: Optional[Dict[str, Any]] = None,
        is_penny: bool = False,
        chart_line: str = "",
        extra_lines: str = "",
    ) -> str:
        mctx = market_ctx or {}
        bid = mctx.get("bid")
        ask = mctx.get("ask")
        spread = mctx.get("spread_pct", 0)
        avg_vol = mctx.get("avg_volume", 0)
        open_n = int(account.get("open_positions", 0))
        max_pos = int(account.get("max_positions", effective_max_concurrent_positions(self.cfg)))
        held = account.get("held_tickers") or []
        deployed = float(account.get("deployed_usd", 0))
        return (
            f"DECIDE ENTRY for {ticker} @ ${current_px:.4f}\n"
            f"Volume spike {spike_ratio:.2f}x | Scan score {scan_score:.0f}\n"
            f"PPO entry signal: action={ppo_action} conf={ppo_conf:.2f} reason={ppo_reason[:80]}\n"
            f"Account: equity ${account.get('equity', 0):,.0f} | cash ${account.get('cash', 0):,.0f} | "
            f"NAV ${account.get('nav', 0):,.0f}\n"
            f"Open: {open_n}/{max_pos} | Held: {', '.join(held) if held else 'none'} | "
            f"Deployed ${deployed:,.0f}\n"
            f"{extra_lines}"
            f"Bid ${bid or 0:.4f} Ask ${ask or 0:.4f} Spread {spread:.2%} | Avg vol {avg_vol:,.0f}\n"
            + (chart_line if chart_line else "")
            + (
                "PENNY STOCK: IB rejects large MARKET orders (error 2161). "
                "Use smaller size — max deploy $350, max ~1200 shares. Limit entry only.\n"
                if is_penny else ""
            )
            + "You are the STRATEGIST pilot — judgment only. Do NOT output stop, target, or share prices.\n"
            "Math engine sets brackets from ATR after you decide enter/skip.\n"
            'JSON: {"enter":true/false,"confidence":0-1,"gut_feel":0-1,"intuition":"gut read",'
            '"reason":"why","journal":"first-person pilot log"}'
        )

    def _ring_entry_council_for_learning(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        *,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        account: Dict[str, Any],
        market_ctx: Optional[Dict[str, Any]] = None,
        is_penny: bool = False,
        df: Optional[pd.DataFrame] = None,
    ) -> str:
        """Fire Ollama async — never blocks execution."""
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return ""
        chart_line = ""
        if df is not None and len(df) >= 20:
            chart_line = self._chart_context_line(ticker, current_px, spike_ratio, scan_score)
        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        prompt = self._entry_council_prompt(
            ticker, current_px, spike_ratio, scan_score,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            account=account, market_ctx=market_ctx, is_penny=is_penny, chart_line=chart_line,
        )
        mood, conf_m, lessons = self._mood_context()
        full = enrich_prompt(
            "entry_decision", {"request": prompt[:2500]}, self.cfg, mood, conf_m, lessons,
        )
        self._live_line.ring(ticker, "entry_decision", full, fp)
        return fp

    def _schedule_deferred_entry(
        self,
        *,
        ticker: str,
        fingerprint: str,
        decision: Dict[str, Any],
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        market_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not decision.get("enter") or not deferred_learning_enabled(self.cfg):
            return
        self._deferred.schedule(
            ticker=ticker,
            task="entry_decision",
            fingerprint=fingerprint,
            executed=decision,
            ppo_signal=ppo_action,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            market_ctx=market_ctx,
        )

    def ring_exit_for_deferred_learning(
        self,
        ctx: Dict[str, Any],
        *,
        ppo_exit: bool,
        ppo_conf: float,
        ppo_reason: str,
        executed_exit: bool,
        pipeline: str,
    ) -> None:
        """Ring exit council after mechanical/PPO profit lock — log when Ollama answers."""
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", ctx.get("current_px", 0)) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        fp = exit_fingerprint(ticker, price, pnl_pct)
        prompt = (
            f"Should we EXIT position {ticker} now?\n"
            f"{json.dumps(ctx, default=str)[:700]}\n"
            f"PPO exit signal: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:60]}\n"
            'JSON: {"exit":true/false,"confidence":0-1,"gut_feel":0-1,'
            '"reason":"why","journal":"exit log"}'
        )
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(
            "exit_decision", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
        )
        self._live_line.ring(ticker, "exit_decision", full, fp)
        self._deferred.schedule(
            ticker=ticker,
            task="exit_decision",
            fingerprint=fp,
            executed={
                "exit": executed_exit,
                "pipeline": pipeline,
                "reason": ctx.get("reason", pipeline),
            },
            ppo_signal=ppo_exit,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            market_ctx=ctx,
        )

    def prefetch_entry_decision(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        ppo_action: int = 0,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
        market_ctx: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
    ) -> None:
        """Keep Ollama hotline open on watchlist — non-blocking."""
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        mctx = market_ctx or {}
        chart_line = self._chart_context_line(ticker, current_px, spike_ratio, scan_score)
        prompt = (
            f"DECIDE ENTRY for {ticker} @ ${current_px:.4f}\n"
            f"Volume spike {spike_ratio:.2f}x | Scan score {scan_score:.0f}\n"
            f"PPO entry signal: action={ppo_action} conf={ppo_conf:.2f} reason={ppo_reason[:80]}\n"
            f"Bid ${mctx.get('bid') or 0:.4f} Ask ${mctx.get('ask') or 0:.4f} "
            f"Spread {mctx.get('spread_pct', 0):.2%}\n"
            + (chart_line if chart_line else "")
            + "You are the pilot on the LIVE hotline. Judgment only — no stop, target, or shares.\n"
            '{"enter":true/false,"confidence":0-1,"gut_feel":0-1,"intuition":"brief",'
            '"reason":"why","journal":"log"}'
        )
        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt("entry_decision", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons)
        self._live_line.ring(ticker, "entry_decision", full, fp)

    def think_json(self, prompt: str, cache_key: str = "", ttl: float = 2.0,
                   task: str = "decide") -> Dict[str, Any]:
        """Blocking path — only for non-live tasks (journals, rankings)."""
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True) and task in _DECISION_TASKS:
            log.debug(f"think_json blocked for live task {task} — use think_json_live")
            return {}

        if cache_key and cache_key in self._cache:
            ts, val = self._cache[cache_key]
            if val and time.time() - ts < ttl:
                return val

        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(task, {"request": prompt[:2500]}, self.cfg, mood, conf, lessons)
        raw = self.think(full, task=task)
        parsed = _parse_json_response(raw)
        if cache_key and parsed:
            self._cache[cache_key] = (time.time(), parsed)
        return parsed

    def journal(self, category: str, message: str, data: Optional[Dict] = None):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "message": message,
            "data": data or {},
        }
        try:
            with open(DECISION_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
        if self.consciousness:
            try:
                self.consciousness._write_thought(category, message, data)
            except Exception:
                pass
        if self.autopilot:
            try:
                self.autopilot.observe_market_event({"type": category, "message": message, **(data or {})})
            except Exception:
                pass

    def _fast_log_line(self, category: str, context: Dict[str, Any]) -> str:
        """Structured log line — no Ollama (hot path must not block IB loop)."""
        if category == "LIVE_PULSE":
            t = context.get("ticker", "?")
            px = context.get("price", 0)
            pnl = context.get("pnl_usd", 0)
            pct = context.get("pnl_pct", 0)
            return (
                f"🧠 LIVE_PULSE: {t} ${px:.4f} | P&L ${pnl:+.2f} ({pct:+.2f}%) | "
                f"Stop ${context.get('stop', 0):.4f} | TP ${context.get('target', 0):.4f}"
            )
        if category == "ENTRY_DECISION":
            t = context.get("ticker", "?")
            enter = context.get("enter", False)
            conf = context.get("confidence", 0)
            pipe = context.get("pipeline", "")
            pipe_tag = f" | {pipe}" if pipe else ""
            return (
                f"🧠 ENTRY {t}: {'ENTER' if enter else 'SKIP'} "
                f"conf={conf:.0%} | {(context.get('reason') or '')[:80]}{pipe_tag}"
            )
        return f"🧠 {category}: {json.dumps(context, default=str)[:120]}"

    def ai_log(self, category: str, context: Dict[str, Any], level: str = "info"):
        """AI writes the log line itself."""
        if not self.full_control:
            return
        # Hot-path categories: never block the trading loop on Ollama
        hot = {"LIVE_PULSE", "STOP_UPDATE", "TP_UPDATE", "TRAIL_UPDATE", "ENTRY_DECISION"}
        if category in hot:
            line = self._fast_log_line(category, context)
            getattr(log, level, log.info)(line)
            self.journal(category, line, context)
            return
        prompt = (
            f"You are HANOON trading AI. Write ONE concise log line for category '{category}'.\n"
            f"Context: {json.dumps(context, default=str)[:800]}\n"
            "Include emoji, key numbers, and reasoning. Max 160 chars. No JSON."
        )
        line = self.think(prompt) or f"🧠 {category}: {context.get('summary', '')}"
        line = line.strip().split("\n")[0][:200]
        getattr(log, level, log.info)(line)
        self.journal(category, line, context)

    def notify(self, notifier, event_type: str, context: Dict[str, Any]):
        """AI-only notification — delegates to Telegram AI composer."""
        from core.ai_notifier import send_smart_telegram
        fallback = f"🧠 {event_type}: {json.dumps(context, default=str)[:120]}"
        send_smart_telegram(
            notifier, event_type, context, fallback,
            ai_commander=self,
            autopilot=self.autopilot,
            consciousness=self.consciousness,
        )

    def ppo_action(self, obs: np.ndarray, bar_df: Optional[pd.DataFrame] = None,
                   for_entry: bool = False) -> Tuple[int, float, str]:
        if self.model is None or not self.ai_components:
            return 0, 0.5, "no model"
        try:
            from core.ai_guardrails import normalize_ppo_obs
            from core.agent import predict_with_reasoning
            obs = normalize_ppo_obs(obs, self.cfg)
            action, conf, reason = predict_with_reasoning(
                self.model, obs, self.cfg, self.ai_components, bar_df=bar_df,
                for_entry=for_entry,
            )
            return action, conf, reason or ""
        except Exception as exc:
            return 0, 0.5, str(exc)

    def score_ticker(self, ticker: str, df: pd.DataFrame, hints: Optional[Dict] = None) -> Dict[str, Any]:
        """AI scores a ticker for scan ranking (replaces static scoring when full control)."""
        hints = hints or {}
        if not self.full_control:
            return hints

        closes = df["close"].values
        vols = df["volume"].values
        px = float(closes[-1])
        ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        vol_ratio = float(np.mean(vols[-5:])) / (float(np.mean(vols[-20:])) + 1e-9)

        prompt = (
            f"Score ticker {ticker} for intraday momentum scalp 0-100.\n"
            f"Price ${px:.4f} | 5bar ret {ret5:.2f}% | vol_ratio {vol_ratio:.2f}x\n"
            f"Rule hints: {json.dumps(hints, default=str)[:300]}\n"
            "Use computational analysis AND gut feel — does this stock feel tradeable right now?\n"
            'JSON only: {"score":0-100,"enter_bias":true/false,"reasons":"brief",'
            '"confidence":0-1,"gut_feel":0-1,"intuition":"one sentence"}'
        )
        fp = scan_fingerprint(ticker, px, vol_ratio)
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "scan_score", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "scan_score", full, fp)
            live = self._live_line.consume(ticker, "scan_score", fp)
            merged = merge_scan_score_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                float(hints.get("total_score", 0) or 0),
                str(hints.get("reasons", "rule_score")),
                ppo_bias=float(hints.get("confidence", 0.5) or 0.5),
            )
            if merged.get("pending"):
                return {
                    "ticker": ticker,
                    "price": px,
                    "volume": int(vols[-1]),
                    "avg_volume": int(np.mean(vols[-20:])),
                    "rel_vol": round(vol_ratio, 2),
                    "total_score": round(float(hints.get("total_score", 0) or 0), 1),
                    "reasons": merged.get("reasons", "council_pending"),
                    "pending": True,
                    "pipeline": merged.get("pipeline", ""),
                }
            score = float(merged.get("score", hints.get("total_score", 0)) or 0)
            reasons = str(merged.get("reasons", hints.get("reasons", "ai_score")))
            out = {"score": score, "reasons": reasons, "confidence": merged.get("confidence", 0.5)}
        else:
            out = self.think_json(prompt, cache_key=f"score_{ticker}", ttl=5.0, task="scan_score")
        score = float(out.get("score", hints.get("total_score", 0)) or 0)
        reasons = str(out.get("reasons", hints.get("reasons", "ai_score")))
        self.journal("SCAN_SCORE", f"{ticker} score={score:.0f}", {"ticker": ticker, "score": score, "out": out})
        return {
            "ticker": ticker,
            "price": px,
            "volume": int(vols[-1]),
            "avg_volume": int(np.mean(vols[-20:])),
            "rel_vol": round(vol_ratio, 2),
            "total_score": round(score, 1),
            "reasons": reasons,
            "ai_score": round(score, 1),
            "confidence": float(out.get("confidence", 0.5)),
        }

    def decide_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        account: Dict[str, Any],
        obs: Optional[np.ndarray] = None,
        bar_df: Optional[pd.DataFrame] = None,
        pilot=None,
        market_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """AI decides entry, sizing, stop, target — guardrails clamp output."""
        ppo_action, ppo_conf, ppo_reason = (0, 0.5, "")
        if obs is not None:
            ppo_action, ppo_conf, ppo_reason = self.ppo_action(obs, bar_df, for_entry=True)

        deploy_cap = get_ai_deploy_budget(
            self.cfg, pilot,
            float(account.get("equity", 0)),
            float(account.get("cash", 0)),
            int(account.get("open_positions", 0)),
        )
        use_fixed_cap = bool(getattr(self.cfg, "USE_FIXED_DEPLOY_CAP", False))
        use_fixed_risk = bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False))
        equity = float(account.get("equity", 0))
        max_risk = get_trade_risk_usd(self.cfg, equity)
        min_conf = max(
            get_effective_confidence_threshold(self.cfg, pilot),
            min_confidence_for_state(self.cfg),
        )
        mctx = market_ctx or {}
        bid = mctx.get("bid")
        ask = mctx.get("ask")
        spread = mctx.get("spread_pct", 0)
        avg_vol = mctx.get("avg_volume", 0)
        penny_thr = float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        is_penny = current_px < penny_thr
        open_n = int(account.get("open_positions", 0))
        max_pos = int(account.get("max_positions", effective_max_concurrent_positions(self.cfg)))
        held = account.get("held_tickers") or []
        deployed = float(account.get("deployed_usd", 0))

        cap_line = (
            f"Fixed deploy cap ${deploy_cap:.0f} | Fixed max risk ${max_risk:.0f}/trade\n"
            if use_fixed_cap and use_fixed_risk
            else (
                f"Fixed deploy cap ${deploy_cap:.0f} | Max risk ${max_risk:.0f}/trade\n"
                if use_fixed_cap
                else (
                    f"Fixed max risk ${max_risk:.0f}/trade | "
                    f"Budget hint ${deploy_cap:,.0f}/slot\n"
                    if use_fixed_risk
                    else (
                        f"AI sizes from full account (no fixed $1k cap) | "
                        f"Budget hint ${deploy_cap:,.0f}/slot\n"
                        f"ATR math engine sets stop/TP after council enter (no LLM prices) — "
                        f"equity ${equity:,.0f}\n"
                    )
                )
            )
        )

        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        micro = (account or {}).get("micro_forecast") or {}
        from core.fast_execution import (
            should_spike_fast_entry,
            should_micro_fast_entry,
            council_fast_sec,
            council_fast_min_score,
            council_fast_min_spike,
        )
        if should_micro_fast_entry(self.cfg, spike_ratio, scan_score, micro):
            fp = self._ring_entry_council_for_learning(
                ticker, current_px, spike_ratio, scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                account=account, market_ctx=mctx, is_penny=is_penny, df=df,
            )
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 80.0, 0.85)),
                "reason": (
                    f"⚡ PPO-led micro-fast: score={scan_score:.0f} micro={float(micro.get('spike_likelihood', 0)):.0%} "
                    f"vol={spike_ratio:.1f}x | PPO {ppo_conf:.0%} (Ollama logging async)"
                )[:200],
                "journal": f"PPO profit hunt — {ticker}",
                "pipeline": "ppo:micro_fast",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            if fp:
                self._schedule_deferred_entry(
                    ticker=ticker, fingerprint=fp, decision=decision,
                    ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    market_ctx=mctx,
                )
            return decision
        if should_spike_fast_entry(self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf):
            fp = self._ring_entry_council_for_learning(
                ticker, current_px, spike_ratio, scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                account=account, market_ctx=mctx, is_penny=is_penny, df=df,
            )
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 80.0, 0.85)),
                "reason": (
                    f"⚡ PPO-led spike-fast: vol={spike_ratio:.1f}x score={scan_score:.0f} "
                    f"| PPO {ppo_conf:.0%} (Ollama logging async)"
                )[:200],
                "journal": f"PPO fast execution — hunting spike on {ticker}",
                "pipeline": "ppo:spike_fast",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            if fp:
                self._schedule_deferred_entry(
                    ticker=ticker, fingerprint=fp, decision=decision,
                    ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    market_ctx=mctx,
                )
            return decision

        if (
            df is not None
            and len(df) >= 20
            and getattr(self.cfg, "CHART_VISION_ENTRY_ONLY", True)
        ):
            self.prefetch_chart_vision(ticker, df, current_px, spike_ratio, scan_score)
        chart_line = self._chart_context_line(ticker, current_px, spike_ratio, scan_score)
        pipeline_on = getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True)

        prompt = self._entry_council_prompt(
            ticker, current_px, spike_ratio, scan_score,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            account=account, market_ctx=mctx, is_penny=is_penny,
            chart_line=chart_line, extra_lines=cap_line,
        )
        if pipeline_on:
            mood, conf_m, lessons = self._mood_context()
            full = enrich_prompt(
                "entry_decision", {"request": prompt[:2500]}, self.cfg, mood, conf_m, lessons,
            )
            self._live_line.ring(ticker, "entry_decision", full, fp)
            live = self._live_line.consume(ticker, "entry_decision", fp)
            merged = merge_entry_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_action, ppo_conf, ppo_reason, min_conf,
                scan_score=scan_score, spike_ratio=spike_ratio,
            )
            out = merged
            if out.get("pending"):
                return {
                    "pending": True,
                    "enter": False,
                    "fingerprint": fp,
                    "ppo_action": ppo_action,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "spike_ratio": spike_ratio,
                    "scan_score": scan_score,
                    "pipeline": out.get("pipeline", ""),
                    "reason": out.get("reason", ""),
                }
            enter = bool(out.get("enter"))
            confidence = float(out.get("confidence", ppo_conf))
            if live.get("status") == "fresh":
                gut_feel = float(out.get("gut_feel", 0.5) or 0.5)
                intuition = str(out.get("intuition", ""))[:120]
                enter, gut_note = apply_gut_override(enter, gut_feel, ppo_action, ppo_conf, min_conf)
                if gut_note:
                    out["reason"] = f"{out.get('reason', '')} | {gut_note}".strip(" |")
                if intuition:
                    out["journal"] = f"{intuition} — {out.get('journal', '')}"[:300]
        else:
            out = self.think_json(
                prompt, cache_key=f"entry_{ticker}",
                ttl=float(getattr(self.cfg, "OLLAMA_MIN_CALL_INTERVAL_SEC", 1.0)),
                task="entry_decision",
            )
            if not out:
                enter = ppo_action == 1 and ppo_conf >= min_conf
                confidence = ppo_conf
                out = {
                    "reason": ppo_reason or f"PPO ensemble conf={ppo_conf:.0%} (Ollama offline)",
                    "journal": f"PPO ensemble: spike {spike_ratio:.1f}x score {scan_score:.0f}",
                }
            else:
                enter = bool(out.get("enter", ppo_action == 1))
                confidence = float(out.get("confidence", ppo_conf) or ppo_conf)
                gut_feel = float(out.get("gut_feel", 0.5) or 0.5)
                intuition = str(out.get("intuition", ""))[:120]
                enter, gut_note = apply_gut_override(enter, gut_feel, ppo_action, ppo_conf, min_conf)
                if gut_note:
                    out["reason"] = f"{out.get('reason', '')} | {gut_note}".strip(" |")
                if intuition:
                    out["journal"] = f"{intuition} — {out.get('journal', '')}"[:300]
                if self.full_control and not enter and ppo_action == 1 and ppo_conf >= min_conf * 0.85:
                    enter = True
                    confidence = max(confidence, ppo_conf)
                    out["reason"] = f"PPO+AI ensemble: {ppo_reason}"

        # PPO-led momentum — execute now, Ollama logs async when council paths timeout
        if not enter and getattr(self.cfg, "AI_FAST_EXECUTION", True):
            if should_spike_fast_entry(self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf):
                enter = True
                confidence = max(confidence, 0.58)
                out["reason"] = (
                    f"⚡ PPO spike hunt: vol={spike_ratio:.1f}x score={scan_score:.0f} "
                    f"(Ollama logging async)"
                )[:200]
                out["pipeline"] = "ppo:spike_fast_fallback"
            elif ppo_action == 1 and ppo_conf >= min_conf * 0.85:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = f"PPO buy lead: {ppo_reason or 'ensemble'} (Ollama logging async)"
                out["pipeline"] = "ppo:buy_lead"
        if not enter and not is_ai_unlimited(self.cfg) and not self.council_mode:
            if spike_ratio >= 1.5 and scan_score >= 35:
                enter = True
                confidence = max(confidence, 0.55)
                out["reason"] = (
                    f"Momentum entry: spike={spike_ratio:.1f}x score={scan_score:.0f} | "
                    f"{out.get('reason', ppo_reason or '')}"
                )[:200]
            elif spike_ratio >= 1.3 and scan_score >= 45 and ppo_conf >= min_conf * 0.75:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = (
                    f"Scanner+AI: score={scan_score:.0f} spike={spike_ratio:.1f}x | "
                    f"{ppo_reason or ''}"
                )[:200]
            elif ppo_action == 1 and ppo_conf >= min_conf:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = f"PPO buy signal: {ppo_reason or 'ensemble confirmed'}"

        if not enter:
            return {
                "enter": False,
                "confidence": confidence,
                "shares": 0,
                "stop": 0.0,
                "target": 0.0,
                "risk_usd": 0.0,
                "reason": str(out.get("reason", ppo_reason or "AI skip")),
                "journal": str(out.get("journal", ""))[:300],
                "pipeline": str(out.get("pipeline", "")),
                "pending": False,
            }

        bracket = self._build_entry_bracket(
            current_px, df,
            equity=equity,
            cash=float(account.get("cash", 0)),
            deploy_cap=deploy_cap,
            is_penny=is_penny,
            avg_vol=avg_vol,
        )
        if not bracket.ok:
            reason = f"bracket rejected: {bracket.reason}"
            log.warning(f"  🛑 {ticker} {reason}")
            snap = self.ollama_audit_snapshot(ticker)
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=bracket.reason,
                entry=current_px, stop=bracket.stop, target=bracket.target,
                shares=bracket.shares, council_decision=out,
                ollama_raw=snap.get("raw", ""), ollama_parsed=snap.get("parsed"),
                spike_ratio=spike_ratio, pipeline="atr_reject",
            )
            return {
                "enter": False,
                "confidence": confidence,
                "shares": 0,
                "stop": 0.0,
                "target": 0.0,
                "risk_usd": 0.0,
                "reason": reason,
                "journal": str(out.get("journal", reason))[:300],
                "pipeline": "atr_reject",
                "pending": False,
            }

        reason = str(out.get("reason", ppo_reason or "AI entry"))
        journal_note = str(out.get("journal", reason))[:300]
        decision = {
            "enter": True,
            "confidence": confidence,
            "shares": bracket.shares,
            "stop": bracket.stop,
            "target": bracket.target,
            "risk_usd": bracket.risk_usd,
            "reward_risk": bracket.reward_risk,
            "reason": f"{reason} | ATR R:R {bracket.reward_risk:.1f}"[:200],
            "journal": journal_note,
            "pipeline": str(out.get("pipeline", "council+atr_math")),
            "pending": False,
            "council_agreement": out.get("council_agreement"),
            "ticker": ticker,
            "entry": current_px,
        }
        ok, decision, err = validate_decision_bracket(self.cfg, decision, fallback_entry=current_px)
        if not ok:
            snap = self.ollama_audit_snapshot(ticker)
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=err,
                entry=current_px, stop=decision.get("stop", 0),
                target=decision.get("target", 0), shares=int(decision.get("shares", 0)),
                council_decision=out,
                ollama_raw=snap.get("raw", ""), ollama_parsed=snap.get("parsed"),
                spike_ratio=spike_ratio, pipeline="bracket_validator",
            )
            return {
                "enter": False,
                "confidence": confidence,
                "shares": 0,
                "stop": 0.0,
                "target": 0.0,
                "risk_usd": 0.0,
                "reason": err,
                "journal": journal_note,
                "pipeline": "bracket_validator",
                "pending": False,
            }
        self._record_council_learning(ticker, decision, "entry_decision", ppo_action, ppo_conf)
        self.journal("ENTRY_DECISION", journal_note, decision)
        self.ai_log("ENTRY_DECISION", {**decision, "ticker": ticker, "price": current_px})
        return decision

    def poll_entry_council(
        self, state: Dict[str, Any], df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Non-blocking poll — Ollama+PPO council resolves when hotline is fresh."""
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        live = self._live_line.consume(ticker, "entry_decision", fp)
        status = live.get("status", "missing")
        parsed = live.get("parsed") or {}
        age = time.time() - float(state.get("started_at", time.time()))
        in_flight_age = float(live.get("age_sec", 0) or 0)
        from core.fast_execution import (
            council_fast_sec,
            council_fast_min_score,
            council_fast_min_spike,
            council_max_wait_sec,
            should_micro_fast_entry,
        )
        max_wait = council_max_wait_sec(self.cfg)
        fast_sec = council_fast_sec(self.cfg)
        fast_score = council_fast_min_score(self.cfg)
        fast_spike = council_fast_min_spike(self.cfg)
        scan_score = float(state.get("scan_score", 0))
        spike_ratio = float(state.get("spike_ratio", 1.0))
        micro = state.get("micro_forecast") or {}
        if status in ("in_flight", "missing", "empty") and max(in_flight_age, age) >= fast_sec:
            if scan_score >= fast_score and spike_ratio >= fast_spike:
                status = "scanner_fast"
                parsed = {}
            elif should_micro_fast_entry(self.cfg, spike_ratio, scan_score, micro):
                status = "scanner_fast"
                spike_ratio = max(spike_ratio, float(micro.get("vol_accel", spike_ratio)))
                state["spike_ratio"] = spike_ratio
                parsed = {}
        elif status != "fresh" and age > max_wait:
            status = "timeout"
            parsed = {}
        merged = merge_entry_decision(
            parsed,
            status,
            int(state.get("ppo_action", 0)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.5)),
            scan_score=float(state.get("scan_score", 0)),
            spike_ratio=float(state.get("spike_ratio", 1.0)),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "enter": False,
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
            }
        current_px = float(state.get("current_px", 0))
        mctx = state.get("market_ctx") or {}
        account = state.get("account") or {}
        pilot = state.get("pilot")
        deploy_cap = get_ai_deploy_budget(
            self.cfg, pilot,
            float(account.get("equity", 0)),
            float(account.get("cash", 0)),
            int(account.get("open_positions", 0)),
        )
        use_fixed_risk = bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False))
        equity = float(account.get("equity", 0))
        max_risk = get_trade_risk_usd(self.cfg, equity)
        min_conf = float(state.get("min_conf", 0.5))
        ppo_action = int(state.get("ppo_action", 0))
        ppo_conf = float(state.get("ppo_conf", 0.5))
        ppo_reason = str(state.get("ppo_reason", ""))
        is_penny = current_px < float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        avg_vol = float(mctx.get("avg_volume", 0))
        return self._finalize_entry_decision(
            merged, ticker=ticker, current_px=current_px,
            spike_ratio=float(state.get("spike_ratio", 1)),
            scan_score=float(state.get("scan_score", 0)),
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
            use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
            df=df, equity=equity, cash=float(account.get("cash", 0)),
        )

    def _finalize_entry_decision(
        self,
        out: Dict[str, Any],
        *,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        min_conf: float,
        deploy_cap: float,
        max_risk: float,
        use_fixed_risk: bool,
        is_penny: bool,
        avg_vol: float,
        df: Optional[pd.DataFrame] = None,
        equity: float = 0.0,
        cash: float = 0.0,
    ) -> Dict[str, Any]:
        enter = bool(out.get("enter"))
        confidence = float(out.get("confidence", ppo_conf))
        if out.get("gut_feel") is not None:
            gut_feel = float(out.get("gut_feel", 0.5) or 0.5)
            enter, gut_note = apply_gut_override(enter, gut_feel, ppo_action, ppo_conf, min_conf)
            if gut_note:
                out["reason"] = f"{out.get('reason', '')} | {gut_note}".strip(" |")

        if not enter and not is_ai_unlimited(self.cfg) and not is_ai_council_mode(self.cfg):
            if spike_ratio >= 1.5 and scan_score >= 35:
                enter = True
                confidence = max(confidence, 0.55)
                out["reason"] = (
                    f"Momentum entry: spike={spike_ratio:.1f}x score={scan_score:.0f} | "
                    f"{out.get('reason', ppo_reason or '')}"
                )[:200]
            elif ppo_action == 1 and ppo_conf >= min_conf:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = f"PPO buy signal: {ppo_reason or 'ensemble confirmed'}"

        if not enter:
            return {
                "enter": False,
                "confidence": confidence,
                "shares": 0,
                "stop": 0.0,
                "target": 0.0,
                "risk_usd": 0.0,
                "reason": str(out.get("reason", ppo_reason or "AI skip")),
                "journal": str(out.get("journal", ""))[:300],
                "pipeline": str(out.get("pipeline", "")),
                "pending": False,
            }

        bracket = self._build_entry_bracket(
            current_px, df,
            equity=equity,
            cash=cash,
            deploy_cap=deploy_cap,
            is_penny=is_penny,
            avg_vol=avg_vol,
        )
        if not bracket.ok:
            reason = f"bracket rejected: {bracket.reason}"
            log.warning(f"  🛑 {ticker} {reason}")
            snap = self.ollama_audit_snapshot(ticker)
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=bracket.reason,
                entry=current_px, stop=bracket.stop, target=bracket.target,
                shares=bracket.shares, council_decision=out,
                ollama_raw=snap.get("raw", ""), ollama_parsed=snap.get("parsed"),
                spike_ratio=spike_ratio, pipeline="atr_reject",
            )
            return {
                "enter": False,
                "confidence": confidence,
                "shares": 0,
                "stop": 0.0,
                "target": 0.0,
                "risk_usd": 0.0,
                "reason": reason,
                "journal": str(out.get("journal", reason))[:300],
                "pipeline": "atr_reject",
                "pending": False,
            }

        reason = str(out.get("reason", ppo_reason or "AI entry"))
        journal_note = str(out.get("journal", reason))[:300]
        decision = {
            "enter": True,
            "confidence": confidence,
            "shares": bracket.shares,
            "stop": bracket.stop,
            "target": bracket.target,
            "risk_usd": bracket.risk_usd,
            "reward_risk": bracket.reward_risk,
            "reason": f"{reason} | ATR R:R {bracket.reward_risk:.1f}"[:200],
            "journal": journal_note,
            "pipeline": str(out.get("pipeline", "council+atr_math")),
            "pending": False,
            "council_agreement": out.get("council_agreement"),
            "ticker": ticker,
            "entry": current_px,
        }
        ok, decision, err = validate_decision_bracket(self.cfg, decision, fallback_entry=current_px)
        if not ok:
            snap = self.ollama_audit_snapshot(ticker)
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=err,
                entry=current_px, stop=decision.get("stop", 0),
                target=decision.get("target", 0), shares=int(decision.get("shares", 0)),
                council_decision=out,
                ollama_raw=snap.get("raw", ""), ollama_parsed=snap.get("parsed"),
                spike_ratio=spike_ratio, pipeline="bracket_validator",
            )
            return {
                "enter": False,
                "confidence": confidence,
                "shares": 0,
                "stop": 0.0,
                "target": 0.0,
                "risk_usd": 0.0,
                "reason": err,
                "journal": journal_note,
                "pipeline": "bracket_validator",
                "pending": False,
            }
        self._record_council_learning(ticker, decision, "entry_decision", ppo_action, ppo_conf)
        pipeline = str(decision.get("pipeline", ""))
        if pipeline.startswith(("ppo:", "council:scanner_fast", "council:scanner_timeout")):
            fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
            self._schedule_deferred_entry(
                ticker=ticker, fingerprint=fp, decision=decision,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            )
            if deferred_learning_enabled(self.cfg) and pipeline.startswith("council:"):
                self._ring_entry_council_for_learning(
                    ticker, current_px, spike_ratio, scan_score,
                    ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    account={
                        "equity": equity, "cash": cash, "nav": equity,
                        "open_positions": 0,
                        "max_positions": effective_max_concurrent_positions(self.cfg),
                        "held_tickers": [], "deployed_usd": 0,
                    },
                    is_penny=is_penny, df=df,
                )
        self.journal("ENTRY_DECISION", journal_note, decision)
        self.ai_log("ENTRY_DECISION", {**decision, "ticker": ticker, "price": current_px})
        return decision

    def _resolve_manage_prices(
        self,
        result: Dict[str, Any],
        ctx: Dict[str, Any],
        df: Optional[pd.DataFrame] = None,
        *,
        mechanical_stop: Optional[float] = None,
        mechanical_target: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Map council action → ATR stop/TP — never trust LLM price literals."""
        action = str(result.get("action", "HOLD")).upper()
        if action in ("HOLD", "EXIT"):
            return result
        entry = float(ctx.get("entry", 0) or 0)
        price = float(ctx.get("price", 0) or 0)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        if df is not None and len(df) >= 5:
            atr = float(compute_atr(df, period=5))
        else:
            atr = price * float(getattr(self.cfg, "SCALP_MIN_STOP_PCT", 0.004))
        if action in ("TIGHTEN_STOP", "WIDEN_STOP"):
            new_stop = adjust_managed_stop(self.cfg, action, entry, price, stop, atr)
            if new_stop is None and action == "TIGHTEN_STOP" and mechanical_stop:
                new_stop = float(mechanical_stop)
            if new_stop is not None and new_stop > 0:
                result["stop"] = new_stop
            else:
                result["action"] = "HOLD"
                result["reason"] = f"{result.get('reason', '')} | no ATR stop change"[:120]
        elif action == "RAISE_TP":
            new_tp = adjust_managed_target(self.cfg, action, entry, price, target, atr)
            if new_tp is None and mechanical_target:
                new_tp = float(mechanical_target)
            if new_tp is not None and new_tp > target:
                result["target"] = new_tp
            else:
                result["action"] = "HOLD"
                result["reason"] = f"{result.get('reason', '')} | no ATR TP extension"[:120]
        return result

    def _record_council_learning(
        self,
        ticker: str,
        decision: Dict[str, Any],
        task: str,
        ppo_signal: Any,
        ppo_conf: float,
    ):
        """Log PPO-led + council outcomes for incremental learning."""
        try:
            from core.experience_buffer import append as buffer_append
            pipeline = str(decision.get("pipeline", ""))
            ppo_primary = pipeline.startswith("ppo:") or "spike_fast" in pipeline
            weight = float(getattr(self.cfg, "PPO_LEARNING_WEIGHT", 1.5)) if ppo_primary else 1.0
            buffer_append({
                "source": "ppo_led" if ppo_primary else "ai_council",
                "task": task,
                "ticker": ticker,
                "ppo_signal": ppo_signal,
                "ppo_conf": round(ppo_conf, 4),
                "ppo_primary": ppo_primary,
                "final_enter": decision.get("enter"),
                "final_exit": decision.get("exit"),
                "final_action": decision.get("action"),
                "confidence": float(decision.get("confidence", 0)),
                "pipeline": pipeline,
                "council_agreement": decision.get("council_agreement"),
                "reason": str(decision.get("reason", ""))[:200],
                "training_weight": weight,
                "ollama_deferred": ppo_primary or pipeline.startswith("council:scanner"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

    def _poll_live_status(
        self, ticker: str, task: str, fingerprint: str, started_at: float,
    ) -> Tuple[str, Dict[str, Any]]:
        live = self._live_line.consume(ticker, task, fingerprint)
        status = live.get("status", "missing")
        parsed = live.get("parsed") or {}
        max_wait = float(getattr(self.cfg, "AI_COUNCIL_MAX_WAIT_SEC", 15.0))
        if status != "fresh" and (time.time() - started_at) > max_wait:
            return "timeout", {}
        return status, parsed

    def decide_stagnation(
        self,
        ctx: Dict[str, Any],
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
    ) -> Dict[str, Any]:
        """Ollama + PPO decide whether a flat/losing position is dead."""
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        stagnant_sec = float(ctx.get("stagnant_sec", 0) or 0)
        frozen_sec = float(ctx.get("price_frozen_sec", stagnant_sec) or stagnant_sec)
        stagnation_sec = float(getattr(self.cfg, "STAGNATION_EXIT_SEC", 90.0))
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = stagnation_fingerprint(ticker, price, pnl_pct, stagnant_sec)
        prompt = (
            "You are HANOON live pilot. This OPEN position may be DEAD — no progress, bleeding time.\n"
            f"{json.dumps(ctx, default=str)[:1000]}\n"
            f"Stagnant {stagnant_sec:.0f}s (limit {stagnation_sec:.0f}s) | "
            f"Price frozen {frozen_sec:.0f}s\n"
            f"PPO exit signal: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:80]}\n"
            "Use math AND gut feel: is momentum alive or is this a zombie trade?\n"
            'JSON: {"exit":true/false,"confidence":0-1,"gut_feel":0-1,'
            '"intuition":"gut read","force_snapshot":true/false,'
            '"pulse_verbose":true/false,"reason":"brief","journal":"pilot log"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "stagnation_check", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "stagnation_check", full, fp)
            live = self._live_line.consume(ticker, "stagnation_check", fp)
            merged = merge_stagnation_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_exit, ppo_conf, ppo_reason, min_conf,
                stagnant_sec, stagnation_sec,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "exit": False,
                    "fingerprint": fp,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "stagnant_sec": stagnant_sec,
                    "stagnation_sec": stagnation_sec,
                    "ctx": dict(ctx),
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                    "pulse_verbose": bool(merged.get("pulse_verbose", False)),
                }
            result = {
                "exit": bool(merged.get("exit")),
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ""))[:200],
                "journal": str(merged.get("journal", ""))[:300],
                "force_snapshot": bool(merged.get("force_snapshot", False)),
                "pulse_verbose": bool(merged.get("pulse_verbose", False)),
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
                "council_agreement": merged.get("council_agreement"),
            }
        else:
            out = self.think_json(prompt, task="stagnation_check")
            should_exit = bool(out.get("exit", ppo_exit))
            if ppo_exit and ppo_conf >= min_conf:
                should_exit = True
            result = {
                "exit": should_exit,
                "confidence": float(out.get("confidence", ppo_conf)),
                "reason": str(out.get("reason", ppo_reason))[:200],
                "journal": str(out.get("journal", ""))[:300],
                "force_snapshot": bool(out.get("force_snapshot", False)),
                "pulse_verbose": bool(out.get("pulse_verbose", stagnant_sec >= stagnation_sec * 0.5)),
                "pipeline": "ollama_sync",
                "pending": False,
            }
        if result["journal"]:
            self.journal("STAGNATION", result["journal"], {**ctx, **result})
        if not result.get("pending"):
            self._record_council_learning(ticker, result, "stagnation_check", ppo_exit, ppo_conf)
        return result

    def poll_stagnation_council(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        status, parsed = self._poll_live_status(
            ticker, "stagnation_check", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_stagnation_decision(
            parsed, status,
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            float(state.get("stagnant_sec", 0)),
            float(state.get("stagnation_sec", 90)),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "exit": False,
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
                "pulse_verbose": bool(merged.get("pulse_verbose", False)),
            }
        result = {
            "exit": bool(merged.get("exit")),
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:200],
            "journal": str(merged.get("journal", ""))[:300],
            "force_snapshot": bool(merged.get("force_snapshot", False)),
            "pulse_verbose": bool(merged.get("pulse_verbose", False)),
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
            "council_agreement": merged.get("council_agreement"),
        }
        self._record_council_learning(
            ticker, result, "stagnation_check",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result

    def prefetch_stagnation(self, ctx: Dict[str, Any]) -> None:
        """Keep stagnation hotline open while position goes flat."""
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        stagnant_sec = float(ctx.get("stagnant_sec", 0) or 0)
        fp = stagnation_fingerprint(ticker, price, pnl_pct, stagnant_sec)
        stagnation_sec = float(getattr(self.cfg, "STAGNATION_EXIT_SEC", 90.0))
        prompt = (
            f"Prefetch stagnation check {ticker} @ ${price:.4f} P&L {pnl_pct:+.2f}% "
            f"stagnant {stagnant_sec:.0f}s / {stagnation_sec:.0f}s\n"
            f"{json.dumps(ctx, default=str)[:600]}"
        )
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(
            "stagnation_check", {"request": prompt[:2000]}, self.cfg, mood, conf, lessons,
        )
        self._live_line.ring(ticker, "stagnation_check", full, fp)

    def decide_position_manage(
        self,
        ctx: Dict[str, Any],
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
        mechanical_stop: Optional[float] = None,
        mechanical_target: Optional[float] = None,
    ) -> Dict[str, Any]:
        """AI council manages open position: trail stop, profit-take, exit."""
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        peak_pct = float(ctx.get("peak_pct", pnl_pct) or pnl_pct)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = position_fingerprint(ticker, price, pnl_pct, stop, target)
        prompt = (
            "You are HANOON live pilot managing an OPEN position. Full autonomy.\n"
            f"{json.dumps(ctx, default=str)[:900]}\n"
            f"PPO manage signal: exit={ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:80]}\n"
            f"Mechanical trail stop={mechanical_stop} target={mechanical_target}\n"
            "Collaborate with PPO: trail stop on profit, widen on noise, raise TP on momentum, EXIT when dead.\n"
            "You are the STRATEGIST — choose action only. Do NOT output stop or target prices.\n"
            'JSON: {"action":"HOLD|WIDEN_STOP|TIGHTEN_STOP|RAISE_TP|EXIT",'
            '"confidence":0-1,"gut_feel":0-1,'
            '"intuition":"gut read","reason":"brief","journal":"log line"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "position_manage", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "position_manage", full, fp)
            live = self._live_line.consume(ticker, "position_manage", fp)
            merged = merge_position_manage_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_exit, ppo_conf, ppo_reason, min_conf,
                pnl_pct=pnl_pct,
                peak_pct=peak_pct,
                current_stop=stop,
                current_target=target,
                mechanical_stop=mechanical_stop,
                mechanical_target=mechanical_target,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "action": "HOLD",
                    "fingerprint": fp,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "ctx": dict(ctx),
                    "mechanical_stop": mechanical_stop,
                    "mechanical_target": mechanical_target,
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                }
            action = str(merged.get("action", "HOLD")).upper()
            result = {
                "action": action,
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ""))[:120],
                "journal": str(merged.get("journal", ""))[:200],
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
                "council_agreement": merged.get("council_agreement"),
            }
            result = self._resolve_manage_prices(
                result, ctx, bar_df=None,
                mechanical_stop=mechanical_stop,
                mechanical_target=mechanical_target,
            )
        else:
            out = self.think_json(prompt, task="position_manage")
            action = str(out.get("action", "HOLD")).upper()
            if action not in ("HOLD", "WIDEN_STOP", "TIGHTEN_STOP", "RAISE_TP", "EXIT"):
                action = "HOLD"
            result = {
                "action": action,
                "confidence": float(out.get("confidence", 0.5)),
                "reason": str(out.get("reason", ""))[:120],
                "journal": str(out.get("journal", ""))[:200],
                "pending": False,
            }
            result = self._resolve_manage_prices(
                result, ctx,
                mechanical_stop=mechanical_stop,
                mechanical_target=mechanical_target,
            )
        if result["journal"]:
            self.journal("POSITION", result["journal"], {**ctx, **result})
        if not result.get("pending"):
            self._record_council_learning(
                ticker, result, "position_manage", ppo_exit, ppo_conf,
            )
        return result

    def poll_position_council(
        self, state: Dict[str, Any], df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        ctx = state.get("ctx") or {}
        status, parsed = self._poll_live_status(
            ticker, "position_manage", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_position_manage_decision(
            parsed, status,
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            pnl_pct=float(ctx.get("pnl_pct", 0)),
            peak_pct=float(ctx.get("peak_pct", ctx.get("pnl_pct", 0))),
            current_stop=float(ctx.get("stop", 0)),
            current_target=float(ctx.get("target", 0)),
            mechanical_stop=state.get("mechanical_stop"),
            mechanical_target=state.get("mechanical_target"),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "action": "HOLD",
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
            }
        action = str(merged.get("action", "HOLD")).upper()
        result = {
            "action": action,
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:120],
            "journal": str(merged.get("journal", ""))[:200],
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
            "council_agreement": merged.get("council_agreement"),
        }
        result = self._resolve_manage_prices(
            result, ctx, df,
            mechanical_stop=state.get("mechanical_stop"),
            mechanical_target=state.get("mechanical_target"),
        )
        self._record_council_learning(
            ticker, result, "position_manage",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result

    def prefetch_position_manage(self, ctx: Dict[str, Any]) -> None:
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        fp = position_fingerprint(ticker, price, pnl_pct, stop, target)
        prompt = (
            f"Prefetch position manage {ticker} @ ${price:.4f} P&L {pnl_pct:+.2f}% "
            f"stop={stop:.4f} target={target:.4f}\n"
            f"{json.dumps(ctx, default=str)[:600]}\n"
            "Strategist only — action JSON, no stop/target prices."
        )
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(
            "position_manage", {"request": prompt[:2000]}, self.cfg, mood, conf, lessons,
        )
        self._live_line.ring(ticker, "position_manage", full, fp)

    def decide_exit(self, ctx: Dict[str, Any], obs: Optional[np.ndarray] = None,
                    bar_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        ppo_exit, ppo_conf, ppo_reason = (False, 0.5, "")
        if obs is not None:
            action, conf, reason = self.ppo_action(obs, bar_df)
            ppo_exit = action == 2 and conf >= float(self.cfg.CONFIDENCE_THRESHOLD)
            ppo_conf, ppo_reason = conf, reason

        prompt = (
            f"Should we EXIT position {ctx.get('ticker')} now?\n"
            f"{json.dumps(ctx, default=str)[:700]}\n"
            f"PPO exit signal: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:60]}\n"
            'JSON: {"exit":true/false,"confidence":0-1,"gut_feel":0-1,'
            '"reason":"why","journal":"exit log"}'
        )
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", ctx.get("current_px", 0)) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = exit_fingerprint(ticker, price, pnl_pct)
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "exit_decision", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "exit_decision", full, fp)
            live = self._live_line.consume(ticker, "exit_decision", fp)
            merged = merge_exit_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_exit, ppo_conf, ppo_reason, min_conf,
                pnl_pct=pnl_pct,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "exit": False,
                    "fingerprint": fp,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "ctx": dict(ctx),
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                }
            should_exit = bool(merged.get("exit"))
            result = {
                "exit": should_exit,
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ppo_reason))[:200],
                "journal": str(merged.get("journal", ""))[:200],
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
                "council_agreement": merged.get("council_agreement"),
            }
        else:
            out = self.think_json(prompt, ttl=1.0, task="exit_decision")
            should_exit = bool(out.get("exit", ppo_exit))
            if ppo_exit and self.full_control:
                should_exit = True
            result = {
                "exit": should_exit,
                "confidence": float(out.get("confidence", ppo_conf)),
                "reason": str(out.get("reason", ppo_reason)),
                "journal": str(out.get("journal", ""))[:200],
                "pending": False,
            }
        if result.get("exit") and not result.get("pending"):
            self.journal("EXIT_DECISION", result["journal"] or result["reason"], {**ctx, **result})
            self._record_council_learning(
                ticker, result, "exit_decision", ppo_exit, ppo_conf,
            )
        return result

    def poll_exit_council(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        ctx = state.get("ctx") or {}
        status, parsed = self._poll_live_status(
            ticker, "exit_decision", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_exit_decision(
            parsed, status,
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            pnl_pct=float(ctx.get("pnl_pct", 0)),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "exit": False,
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
            }
        result = {
            "exit": bool(merged.get("exit")),
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:200],
            "journal": str(merged.get("journal", ""))[:200],
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
            "council_agreement": merged.get("council_agreement"),
        }
        self._record_council_learning(
            ticker, result, "exit_decision",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result

    def record_trade(self, trade: Dict[str, Any]):
        """AI journals closed trade and updates narrative P&L."""
        self._trade_journal.append(trade)
        self._save_journal()
        prompt = (
            f"Write a pilot trade journal entry for closed trade:\n"
            f"{json.dumps(trade, default=str)[:500]}\n"
            "2 sentences: what happened, lesson learned. First person as AI pilot."
        )
        note = self.think(prompt) or f"Closed {trade.get('ticker')} P&L ${trade.get('pnl_usd', 0):+.2f}"
        self.journal("TRADE_CLOSED", note[:400], trade)
        if self.autopilot:
            try:
                self.autopilot.observe_trade(trade)
            except Exception:
                pass

    def account_narrative(self, metrics: Dict[str, Any]) -> str:
        prompt = (
            "Summarize live account state for dashboard (2 sentences, pilot voice):\n"
            f"{json.dumps(metrics, default=str)[:600]}"
        )
        return (self.think(prompt) or "").strip()[:400]

    def rank_scan_results(self, results: List[Dict]) -> List[Dict]:
        """AI council re-ranks scan pool."""
        if not results or not self.full_control:
            return results
        top = results[:15]
        tickers = [r["ticker"] for r in top]
        fp = rank_scan_fingerprint(tickers)
        prompt = (
            "Rank these tickers best-to-worst for immediate scalp entry. Return JSON array of tickers only.\n"
            f"{json.dumps([{'t': r['ticker'], 's': r.get('total_score', 0)} for r in top])}\n"
            'JSON: {"ranked":["TICK1","TICK2",...],"reason":"brief"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "rank_scan", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring("_scan", "rank_scan", full, fp)
            live = self._live_line.consume("_scan", "rank_scan", fp)
            merged = merge_rank_scan_decision(
                live.get("parsed") or {}, live.get("status", "missing"), top,
            )
            if merged.get("pending"):
                return results
            ranked = merged.get("ranked") or []
        else:
            out = self.think_json(prompt, ttl=10.0, task="rank_scan")
            ranked = out.get("ranked") or []
        if not ranked:
            return results
        order = {t: i for i, t in enumerate(ranked)}
        return sorted(results, key=lambda r: order.get(r["ticker"], 999))

    def pick_next_target(
        self,
        tickers: List[str],
        scores: Dict[str, float],
        skipped_ticker: str = "",
        reason: str = "",
    ) -> str:
        """AI council picks next focus ticker after IB rejects a symbol."""
        if not tickers:
            return ""
        if len(tickers) == 1:
            return tickers[0]
        ppo_pick = max(tickers, key=lambda t: scores.get(t, 0))
        prompt = (
            f"IB rejected entry on {skipped_ticker}: {reason}\n"
            f"Pick the BEST next scalp target from: {tickers}\n"
            f"Scores: {json.dumps({t: round(scores.get(t, 0), 1) for t in tickers})}\n"
            f"PPO top pick: {ppo_pick}\n"
            "Prefer liquid names IB allows to open. Skip closing-only symbols.\n"
            'JSON: {"ticker":"SYM","reason":"one line"}'
        )
        fp = pick_target_fingerprint(skipped_ticker, tickers)
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf_m, lessons = self._mood_context()
            full = enrich_prompt(
                "pick_next_target", {"request": prompt[:2500]}, self.cfg, mood, conf_m, lessons,
            )
            self._live_line.ring(skipped_ticker or "_pick", "pick_next_target", full, fp)
            live = self._live_line.consume(skipped_ticker or "_pick", "pick_next_target", fp)
            merged = merge_pick_target_decision(
                live.get("parsed") or {}, live.get("status", "missing"),
                tickers, scores, ppo_pick=ppo_pick,
            )
            pick = str(merged.get("ticker", ppo_pick) or ppo_pick)
            if pick in tickers:
                log.info(
                    f"  🧠 COUNCIL next target: {pick} — "
                    f"{(merged.get('reason') or '')[:80]} | {merged.get('pipeline', '')}"
                )
                return pick
            return ppo_pick
        out = self.think_json(
            prompt, cache_key=f"next_{skipped_ticker}", ttl=8.0, task="pick_next_target",
        )
        pick = (out or {}).get("ticker", "")
        if pick in tickers:
            log.info(f"  🧠 AI next target: {pick} — {(out.get('reason') or '')[:80]}")
            return pick
        return ppo_pick

    def decide_risk_exit(
        self,
        ctx: Dict[str, Any],
        risk_signal: str,
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
    ) -> Dict[str, Any]:
        """Council exit for risk-engine signals (trail profit/stop, early loss)."""
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = risk_signal_fingerprint(ticker, price, risk_signal)
        prompt = (
            f"Risk engine signal for {ticker}: {risk_signal}\n"
            f"{json.dumps(ctx, default=str)[:700]}\n"
            f"PPO exit: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:80]}\n"
            "Mechanical profit hunts (spike_top, trailing_profit, hard_take_profit) "
            "should EXIT unless clear continuation evidence — be opportunistic.\n"
            'JSON: {"exit":true/false,"confidence":0-1,"reason":"why"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "risk_exit", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "risk_exit", full, fp)
            live = self._live_line.consume(ticker, "risk_exit", fp)
            merged = merge_risk_signal_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                risk_signal, ppo_exit, ppo_conf, ppo_reason, min_conf, pnl_pct=pnl_pct,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "exit": False,
                    "fingerprint": fp,
                    "risk_signal": risk_signal,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "ctx": dict(ctx),
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                }
            return {
                "exit": bool(merged.get("exit")),
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ""))[:200],
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
            }
        return {
            "exit": bool(risk_signal or ppo_exit),
            "confidence": ppo_conf,
            "reason": risk_signal or ppo_reason,
            "pending": False,
        }

    def poll_risk_exit_council(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        ctx = state.get("ctx") or {}
        status, parsed = self._poll_live_status(
            ticker, "risk_exit", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_risk_signal_decision(
            parsed, status,
            str(state.get("risk_signal", "")),
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            pnl_pct=float(ctx.get("pnl_pct", 0)),
        )
        if merged.get("pending"):
            return {"pending": True, "exit": False, "reason": merged.get("reason", "")}
        result = {
            "exit": bool(merged.get("exit")),
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:200],
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
        }
        self._record_council_learning(
            ticker, result, "risk_exit",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result

    def review_lock_watchlist(self, picks: List[Dict]) -> Dict[str, Any]:
        """Council review of locked watchlist (replaces blocking generative_think)."""
        if not picks:
            return {}
        summary = [
            f"{r['ticker']}@${r['price']:.2f} score={r.get('total_score', 0):.0f}"
            for r in picks[:5]
        ]
        tickers = [r["ticker"] for r in picks[:5]]
        fp = rank_scan_fingerprint(tickers)
        prompt = (
            "You are an expert momentum scalper. Locked watchlist from LIVE screener.\n"
            + "\n".join(summary) + "\n"
            'JSON: {"ranked":["T1","T2",...],"gut_pick":"best","commentary":"2 lines"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "lock_review", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring("_lock", "lock_review", full, fp)
            live = self._live_line.consume("_lock", "lock_review", fp)
            merged = merge_rank_scan_decision(
                live.get("parsed") or {}, live.get("status", "missing"), picks[:5],
            )
            out = live.get("parsed") or {}
            if merged.get("pending"):
                return {"pending": True, "pipeline": merged.get("pipeline", "")}
            return {
                "ranked": merged.get("ranked", tickers),
                "gut_pick": out.get("gut_pick", tickers[0] if tickers else ""),
                "commentary": str(out.get("commentary", ""))[:400],
                "pending": False,
                "pipeline": merged.get("pipeline", ""),
            }
        thought = generative_think(self.cfg, self.autopilot, prompt)
        return {"commentary": thought[:400] if thought else "", "pending": False}
