#!/usr/bin/env python3
"""
core/ai_commander.py — Full AI control plane for HANOON.

PRIMARY MISSION: FULL-TIME PROFIT HUNTING. Making money is the only main goal.
When AI_FULL_CONTROL is enabled, every decision serves opportunistic profit extraction.
Full freedom within hard risk guardrails. All hunts tracked in profit_hunt_ledger + buffer.
"""

from __future__ import annotations

import json
import os
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

from core.ai_commander_verdict import CommanderVerdictMixin
from core.ai_commander_deferred import CommanderLearningMixin
from core.ai_commander_entry import CommanderEntryMixin
from core.ai_commander_exit import CommanderExitMixin

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


def _deferred_gold_log_tag(cfg: Optional[BotConfig] = None) -> str:
    """When PPO enters now and decision/coevolution text is logged asynchronously."""
    cfg = cfg or BotConfig()
    if os.getenv("HALIM_PPO_DIALOGUE", "true").lower() in ("1", "true", "yes"):
        return "Halim gold async"
    backend = str(getattr(cfg, "COUNCIL_BACKEND", None) or os.getenv("COUNCIL_BACKEND", "groq"))
    return f"{backend} gold async"


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


# Live trading tasks always use the priority council path (no rate-limit deferral).
_DECISION_TASKS = frozenset({
    "entry_decision", "position_manage", "exit_decision", "stagnation_check",
    "scan_score", "rank_scan", "pick_next_target", "risk_exit", "lock_review",
    "gut_check", "decide", "exit", "account_eval", "account_brief",
})


class AICommander(CommanderVerdictMixin, CommanderLearningMixin, CommanderEntryMixin, CommanderExitMixin):
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
        self._live_line = LiveAILine(cfg, self._council_decide_raw)
        self._chart_line = ChartVisionLine(cfg)
        from core.halim_entry_line import HalimEntryLine
        from core.halim_exit_line import HalimExitLine
        self._halim_entry = HalimEntryLine(cfg)
        self._halim_exit = HalimExitLine(cfg)
        self._deferred = DeferredCouncilLearner(cfg, self._live_line)
        self._ppo_model_ref: Any = None
        DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._load_journal()
    def full_control(self) -> bool:
        return bool(getattr(self.cfg, "AI_FULL_CONTROL", True))
    def council_mode(self) -> bool:
        return is_ai_council_mode(self.cfg)
    def council_audit_snapshot(self, ticker: str, task: str = "entry_decision") -> Dict[str, Any]:
        """Raw + parsed cloud council output for post-mortem."""
        return self._live_line.peek(ticker, task)
    def ollama_audit_snapshot(self, ticker: str, task: str = "entry_decision") -> Dict[str, Any]:
        """Legacy alias — council snapshot (not local Ollama)."""
        return self.council_audit_snapshot(ticker, task)
    def _vision_analyze(self, prompt: str, image_bytes: bytes) -> str:
        ollama = None
        if self.autopilot and getattr(self.autopilot, "core", None):
            ollama = getattr(self.autopilot.core, "ollama", None)
        if ollama and hasattr(ollama, "analyze_image"):
            return (ollama.analyze_image(prompt, image_bytes, trading_context=True) or "").strip()
        from core.ollama_brain import OllamaBrain

        brain = OllamaBrain(self.cfg)
        return (brain.analyze_image(prompt, image_bytes, trading_context=True) or "").strip()
    def prefetch_chart_vision(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
    ) -> None:
        """Non-blocking Gemini chart read on locked watchlist — feeds entry council."""
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
        return f"CHART VISION (Gemini): {read[:700]}\n"
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
    def _council_decide_raw(self, full_prompt: str) -> str:
        """Cloud council call — Groq primary, Gemini fallback."""
        council = None
        if self.autopilot and getattr(self.autopilot, "core", None):
            council = getattr(self.autopilot.core, "ollama", None)
        if council and hasattr(council, "decide_call"):
            try:
                return (council.decide_call(full_prompt) or "").strip()
            except Exception as exc:
                log.debug(f"Council decide: {exc}")
        try:
            from core.council_client import get_council_client
            return (get_council_client(self.cfg).decide_call(full_prompt) or "").strip()
        except Exception as exc:
            log.debug(f"Council client: {exc}")
        return ""
    def compose_telegram(
        self,
        prompt: str,
        *,
        purpose: str = "notify",
        event_type: Optional[str] = None,
        copilot: bool = False,
    ) -> str:
        """Cloud council path for Telegram — budget-gated."""
        council = None
        if self.autopilot and getattr(self.autopilot, "core", None):
            council = getattr(self.autopilot.core, "ollama", None)
        if council and hasattr(council, "compose_notification"):
            try:
                return (
                    council.compose_notification(
                        prompt,
                        purpose=purpose,
                        event_type=event_type,
                        copilot=copilot,
                    ) or ""
                ).strip()
            except Exception as exc:
                log.debug(f"AI telegram notify: {exc}")
        try:
            from core.council_client import get_council_client
            text = get_council_client(self.cfg).compose_notification(
                prompt,
                purpose=purpose,
                event_type=event_type,
                copilot=copilot,
            )
            if text:
                return text.strip()
        except Exception:
            pass
        return ""
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
    def bind_ppo_model(self, model: Any) -> None:
        """Scalper attaches live PPO for per-entry micro-improvement."""
        self._ppo_model_ref = model
    def service_deferred_learning(self) -> int:
        """Log late Ollama answers after PPO-led execute — non-blocking."""
        from core.ppo_entry_learning import set_ppo_model
        if self._ppo_model_ref is not None:
            set_ppo_model(self._ppo_model_ref)
        return self._deferred.service()
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
            src = context.get("pnl_source", "")
            src_tag = " IB" if src == "ib_truth" else ""
            return (
                f"🧠 LIVE_PULSE{src_tag}: {t} ${px:.4f} | P&L ${pnl:+.2f} ({pct:+.2f}%) | "
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
        return {"commentary": "", "pending": False, "ranked": tickers}
