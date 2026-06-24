#!/usr/bin/env python3
"""
core/ollama_brain.py — Local LLM reasoning head via Ollama.

PURPOSE
═══════════════════════════════════════════════════════════════════════════
Adds a natural-language reasoning layer on top of the multi-model fusion engine.
This module calls a local Ollama model (llama3, mistral, gemma, etc.) to:

1. Explain WHY the fused AI made a specific trading decision
2. Provide market sentiment commentary on current regime
3. Assess risk in plain language
4. Generate daily trading journal summaries

All Ollama calls are:
- Non-blocking with timeout (never stalls the trading loop)
- Lazy-loaded (no Ollama import at module level)
- Best-effort (failures are logged, never crash the bot)

SETUP
  1. Install Ollama from https://ollama.ai
  2. Pull a model: `ollama pull llama3` or `ollama pull mistral`
  3. Set OLLAMA_HOST if not default (http://localhost:11434)
  4. Enable in .env: OLLAMA_ENABLED=true, OLLAMA_MODEL=llama3
"""

import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime

from core.config import BotConfig
from core.notify import log
from core.hmrs import MarketRegime
from core.human_cognition import get_system_prompt, enrich_prompt, apply_gut_override
from core.memory_guard import (
    should_allow_ollama,
    should_allow_ollama_decide,
    should_allow_ollama_notify,
    available_ram_mb,
    is_low_ram_machine,
)


@dataclass
class OllamaConfig:
    """Ollama client configuration."""
    enabled: bool = False
    host: str = "http://localhost:11434"
    model: str = "llama3"
    timeout_seconds: int = 10
    max_tokens: int = 256
    temperature: float = 0.7
    system_prompt: str = (
        "You are a professional trading assistant. "
        "Explain AI trading decisions concisely. "
        "Focus on risk, market regime, and actionable insights. "
        "Never give financial advice. Always include disclaimers."
    )


class OllamaBrain:
    """
    Lightweight client for local Ollama LLM reasoning.
    
    USAGE
        brain = OllamaBrain(cfg)
        
        # Explain a fusion decision
        explanation = brain.explain_decision(decision, price, ticker)
        
        # Get market sentiment
        sentiment = brain.analyze_sentiment(market_context, regime)
        
        # Assess risk
        risk_commentary = brain.assess_risk(trade_plan, account_equity)
        
        # Summarize trading journal
        summary = brain.summarize_journal(trades)
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.config = OllamaConfig(
            enabled=getattr(cfg, 'OLLAMA_ENABLED', False),
            host=getattr(cfg, 'OLLAMA_HOST', 'http://localhost:11434'),
            model=getattr(cfg, 'OLLAMA_MODEL', 'llama3'),
            timeout_seconds=getattr(cfg, 'OLLAMA_TIMEOUT', 10),
            max_tokens=getattr(cfg, 'OLLAMA_MAX_TOKENS', 256),
            temperature=getattr(cfg, 'OLLAMA_TEMPERATURE', 0.7),
        )
        self._last_call_time = 0.0
        self._call_count = 0
        self._error_count = 0
        self._skipped_pressure = 0
        self._skipped_rate = 0
        self._keep_alive = getattr(cfg, 'OLLAMA_KEEP_ALIVE', 0)
        self._num_ctx = int(getattr(cfg, 'OLLAMA_NUM_CTX', 1024 if is_low_ram_machine() else 2048))
        self._min_call_interval = float(getattr(cfg, 'OLLAMA_MIN_CALL_INTERVAL_SEC', 1))
        self._unload_after_call = bool(getattr(cfg, 'OLLAMA_UNLOAD_AFTER_CALL', True))
        self._system_prompt = get_system_prompt(cfg)
        # Meta-optimizer state — never use 70B on low-RAM machines
        self.meta_enabled = getattr(cfg, 'OLLAMA_META_OPTIMIZER_ENABLED', False) if is_low_ram_machine() else getattr(cfg, 'OLLAMA_META_OPTIMIZER_ENABLED', True)
        self.meta_model = getattr(cfg, 'META_OPTIMIZER_MODEL', self.config.model)
        self.max_mutations_per_day = getattr(cfg, 'MAX_PARAM_MUTATIONS_PER_DAY', 5)
        self._mutations_today = 0
        self._last_mutation_date = ""
    
    def _is_allowed(self) -> bool:
        """Rate limit + RAM pressure gate."""
        if not self.config.enabled:
            return False
        allowed, reason = should_allow_ollama(self.cfg)
        if not allowed:
            self._skipped_pressure += 1
            if self._skipped_pressure <= 3 or self._skipped_pressure % 50 == 0:
                log.debug(f"Ollama skipped ({reason}), free={available_ram_mb()}MB")
            return False
        elapsed = time.time() - self._last_call_time
        if self._last_call_time > 0 and elapsed < self._min_call_interval:
            self._skipped_rate += 1
            return False
        return True
    
    def _call_ollama(self, prompt: str, system: Optional[str] = None,
                     model: Optional[str] = None) -> Optional[str]:
        """
        Call Ollama API synchronously with timeout.
        """
        if not self._is_allowed():
            return None

        return self._execute_call(prompt, system=system, model=model, update_rate_clock=True)

    def decide_call(self, prompt: str, system: Optional[str] = None,
                    model: Optional[str] = None) -> Optional[str]:
        """
        Priority Ollama path for live trading decisions — bypasses rate limit
        so entry/exit/position calls are never deferred to PPO-only fallbacks.
        """
        if not self.config.enabled:
            return None
        allowed, reason = should_allow_ollama_decide(self.cfg)
        if not allowed:
            log.debug(f"Ollama decide skipped ({reason})")
            return None
        return self._execute_call(prompt, system=system, model=model, update_rate_clock=True)

    def _execute_call(self, prompt: str, system: Optional[str] = None,
                      model: Optional[str] = None, update_rate_clock: bool = True) -> Optional[str]:
        """Shared HTTP generate — used by standard, decide, and notify paths."""
        try:
            url = f"{self.config.host}/api/generate"
            use_model = model or self.config.model
            use_system = system or self._system_prompt
            payload = {
                "model": use_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": 0 if self._unload_after_call else self._keep_alive,
                "options": {
                    "num_predict": self.config.max_tokens,
                    "temperature": self.config.temperature,
                    "num_ctx": self._num_ctx,
                },
            }
            if use_system:
                payload["system"] = use_system

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")

            start = time.time()
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                elapsed = (time.time() - start) * 1000

                if update_rate_clock:
                    self._last_call_time = time.time()
                self._call_count += 1

                log.debug(f"Ollama {use_model}: {elapsed:.0f}ms | "
                         f"{result.get('prompt_eval_count', 0)} prompt tokens, "
                         f"{result.get('eval_count', 0)} completion tokens")

                text = result.get("response", "").strip()
                if self._unload_after_call:
                    self._unload_model(use_model)
                return text

        except urllib.error.URLError as exc:
            self._error_count += 1
            log.warning(f"Ollama connection failed: {exc}")
        except json.JSONDecodeError:
            self._error_count += 1
            log.warning("Ollama returned invalid JSON")
        except Exception as exc:
            self._error_count += 1
            log.debug(f"Ollama call failed: {exc}")

        return None

    def compose_notification(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """
        Fast Ollama path for Telegram alerts — bypasses the trading-loop rate limit
        so notifications are not blocked after warmup or position-management calls.
        """
        if not self.config.enabled:
            return None
        allowed, reason = should_allow_ollama_notify(self.cfg)
        if not allowed:
            log.debug(f"Notify compose skipped ({reason})")
            return None

        notify_system = system or (
            "You are HANOON — an autonomous AI trading pilot briefing your commander on Telegram. "
            "Write short, sharp, analytical messages with exact numbers. First-person pilot voice. "
            "Sound alive and intentional — never robotic canned templates."
        )
        max_tokens = int(getattr(self.cfg, "AI_TELEGRAM_OLLAMA_MAX_TOKENS", 120))
        timeout = int(getattr(self.cfg, "AI_TELEGRAM_OLLAMA_TIMEOUT", 12))

        try:
            url = f"{self.config.host}/api/generate"
            payload = {
                "model": self.config.model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": self._keep_alive,
                "system": notify_system,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": 0.75,
                    "num_ctx": min(self._num_ctx, 1536),
                },
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")

            start = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                elapsed = (time.time() - start) * 1000
                self._call_count += 1
                log.debug(f"Ollama notify: {elapsed:.0f}ms | {result.get('eval_count', 0)} tokens")
                return (result.get("response") or "").strip()
        except Exception as exc:
            self._error_count += 1
            log.debug(f"Ollama notify compose failed: {exc}")
        return None

    def _warmup(self) -> None:
        """Load model into reserved RAM at startup."""
        try:
            self._last_call_time = 0.0  # bypass rate limit for warmup
            r = self._call_ollama("Reply with one word: ready")
            if r:
                log.info(f"🧠 Ollama warmed up ({self.config.model} loaded, ~{getattr(self.cfg, 'OLLAMA_MEMORY_BUDGET_MB', 2560)}MB budget)")
        except Exception as exc:
            log.debug(f"Ollama warmup skipped: {exc}")

    def _unload_model(self, model: Optional[str] = None) -> None:
        """Drop model from RAM immediately (critical on 8GB Macs)."""
        use_model = model or self.config.model
        try:
            url = f"{self.config.host}/api/generate"
            payload = json.dumps({
                "model": use_model,
                "prompt": "",
                "keep_alive": 0,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
        except Exception:
            pass
    
    def explain_decision(self, decision, price: float, ticker: str, 
                         regime_info: str = "") -> str:
        """
        Explain the fused AI decision in natural language.
        
        Args:
            decision: FusedDecision from multi-model fusion engine
            price: Current price
            ticker: Ticker symbol
            regime_info: Optional market regime description
            
        Returns:
            Human-readable explanation
        """
        if not self.config.enabled:
            return ""
        
        action = decision.action_name
        confidence = decision.confidence
        models = ", ".join(
            f"{m.model_name} ({['HOLD','BUY','SELL'][m.action]} {m.confidence:.0%})"
            for m in decision.model_predictions
        )
        
        prompt = (
            f"Trading decision for {ticker} @ ${price:.2f}:\n"
            f"Action: {action} (confidence: {confidence:.0%})\n"
            f"Models voted: {models}\n"
            f"Reasoning: {decision.reasoning}\n"
            f"Market regime: {regime_info if regime_info else 'normal'}\n\n"
            f"Explain this decision in 2-3 sentences. Focus on why the models chose {action}, "
            f"what risks to watch, and what could invalidate this view. "
            f"Include a brief risk disclaimer at the end."
        )
        
        result = self._call_ollama(prompt)
        if result:
            log.info(f"🧠 Ollama explanation generated ({len(result)} chars)")
        return result or ""
    
    def analyze_sentiment(self, market_context: Dict, regime_result: Any = None) -> str:
        """
        Analyze overall market sentiment from context.
        
        Args:
            market_context: Dict with SPY trend, VIX regime, etc.
            regime_result: Optional regime classification
            
        Returns:
            Sentiment analysis string
        """
        if not self.config.enabled:
            return ""
        
        spy_trend = market_context.get('spy_trend', 'unknown')
        vix_regime = market_context.get('vix_regime', 'unknown')
        
        prompt = (
            f"Market context: SPY trend={spy_trend}, VIX regime={vix_regime}\n"
            f"Regime: {regime_result.regime.value if regime_result else 'unknown'}\n\n"
            f"Summarize the current market sentiment in 1-2 sentences. "
            f"Is it a risk-on or risk-off environment? What should traders watch?"
        )
        
        result = self._call_ollama(prompt)
        return result or ""
    
    def assess_risk(self, trade_plan: Any, account_equity: float, 
                    recent_wins: int = 0, recent_losses: int = 0) -> str:
        """
        Natural language risk assessment for a planned trade.
        
        Args:
            trade_plan: TradePlan with entry/stop/target
            account_equity: Current account equity
            recent_wins: Number of recent winning trades
            recent_losses: Number of recent losing trades
            
        Returns:
            Risk commentary
        """
        if not self.config.enabled or trade_plan is None:
            return ""
        
        win_rate = recent_wins / max(recent_wins + recent_losses, 1)
        risk_usd = getattr(trade_plan, 'risk_usd', 0)
        risk_pct = (risk_usd / account_equity * 100) if account_equity > 0 else 0
        
        prompt = (
            f"Trade plan: {getattr(trade_plan, 'side', 'LONG')} at ${getattr(trade_plan, 'entry_price', 0):.2f}\n"
            f"Stop: ${getattr(trade_plan, 'initial_stop_price', 0):.2f}\n"
            f"Target: ${getattr(trade_plan, 'take_profit_price', 0):.2f}\n"
            f"Risk: ${risk_usd:.2f} ({risk_pct:.1f}% of account)\n"
            f"Recent win rate: {win_rate:.0%} ({recent_wins}W / {recent_losses}L)\n\n"
            f"Assess the risk of this trade in 1-2 sentences. "
            f"Is the risk/reward ratio sound given recent performance? "
            f"Any specific concerns? Include disclaimer."
        )
        
        result = self._call_ollama(prompt)
        return result or ""
    
    def summarize_journal(self, trades: List[Dict], max_trades: int = 10) -> str:
        """
        Summarize recent trading activity.
        
        Args:
            trades: List of trade dicts with entry, exit, pnl, result
            max_trades: Max trades to include in summary
            
        Returns:
            Summary string
        """
        if not self.config.enabled or not trades:
            return ""
        
        recent = trades[-max_trades:]
        wins = sum(1 for t in recent if t.get('result') == 'win')
        losses = sum(1 for t in recent if t.get('result') == 'loss')
        total_pnl = sum(t.get('pnl_usd', 0) for t in recent)
        
        prompt = (
            f"Trading journal summary (last {len(recent)} trades):\n"
            f"Wins: {wins}, Losses: {losses}, Net P&L: ${total_pnl:+,.2f}\n"
            f"Trades: {json.dumps(recent, indent=2)[:1500]}\n\n"
            f"Summarize the trading performance in 2-3 sentences. "
            f"What patterns do you see? Any actionable feedback? Include disclaimer."
        )
        
        result = self._call_ollama(prompt)
        return result or ""
    
    def generate_daily_commentary(self, nav: float, baseline: float, 
                                   trades: int, win_rate: float) -> str:
        """
        Generate end-of-day commentary.
        
        Args:
            nav: Current portfolio value
            baseline: Starting capital
            trades: Number of trades today
            win_rate: Win rate as percentage
            
        Returns:
            Daily commentary string
        """
        if not self.config.enabled:
            return ""
        
        pnl = nav - baseline
        pnl_pct = (pnl / baseline * 100) if baseline > 0 else 0
        
        prompt = (
            f"Daily trading summary:\n"
            f"Portfolio value: ${nav:,.2f}\n"
            f"Baseline: ${baseline:,.2f}\n"
            f"Day P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"Trades: {trades}\n"
            f"Win rate: {win_rate:.0f}%\n\n"
            f"Write a brief daily commentary (3-4 sentences) for a trader. "
            f"Evaluate the day's performance, note any concerns, and suggest focus areas for tomorrow. "
            f"Include a standard trading disclaimer."
        )
        
        result = self._call_ollama(prompt)
        return result or ""
    
    # ═════════════════════════════════════════════════════════════════════════════
    # META-OPTIMIZER (Active Hyperparameter Tuning)
    # ═════════════════════════════════════════════════════════════════════════════
    
    def meta_optimize(self, performance_report: Dict[str, Any],
                      market_context: Dict[str, Any],
                      guideline_path: str = "models/ai_guidelines.txt",
                      weights_path: str = "models/scalper_weights.json",
                      config_path: str = "core/config.py") -> Dict[str, Any]:
        """
        LLM-in-the-loop meta-optimization: analyze recent performance,
        suggest structural parameter tweaks, and write them to disk.
        
        This is the Active Meta-Optimizer phase. It only runs when the
        market is closed and respects the daily mutation cap.
        
        Args:
            performance_report: Dict from FusionEngine / backtest
            market_context: Dict with regime, news summaries, etc.
            guideline_path: Path to ai_guidelines.txt
            weights_path: Path to scalper_weights.json
            config_path: Path to core/config.py
            
        Returns:
            Dict of mutations applied (or suggested)
        """
        if not self.meta_enabled:
            return {"status": "disabled"}
        
        # Gate: only run when market closed
        if getattr(self.cfg, 'META_OPTIMIZE_ONLY_WHEN_MARKET_CLOSED', True):
            from core.connector import IBConnector
            conn = IBConnector(self.cfg)
            if conn.is_market_open():
                return {"status": "skipped", "reason": "market_open"}
        
        # Gate: daily mutation budget
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_mutation_date != today:
            self._mutations_today = 0
            self._last_mutation_date = today
        
        if self._mutations_today >= self.max_mutations_per_day:
            return {"status": "budget_exhausted", "mutations_today": self._mutations_today}
        
        try:
            # 1. Ingest structured telemetry
            perf_summary = json.dumps(performance_report, indent=2)[:3000]
            ctx_summary = json.dumps(market_context, indent=2)[:2000]
            
            # 2. Load current guidelines
            guidelines = ""
            try:
                with open(guideline_path, 'r') as f:
                    guidelines = f.read()[:3000]
            except Exception:
                guidelines = "No existing guidelines."
            
            # 3. Construct meta-optimizer prompt
            system = (
                "You are a quantitative trading meta-optimizer. "
                "Your job is to analyze AI trading performance and suggest "
                "tunable parameter changes in JSON format. Be conservative: "
                "only suggest changes when there is clear statistical evidence. "
                "Never suggest changes that violate hard risk limits."
            )
            prompt = (
                "Recent performance report:\n"
                f"{perf_summary}\n\n"
                "Market context:\n"
                f"{ctx_summary}\n\n"
                "Current AI guidelines:\n"
                f"{guidelines}\n\n"
                "Based on this data, suggest 1-3 parameter mutations in JSON format:\n"
                '{"mutations": [{"param": "CONFIDENCE_THRESHOLD", "value": 0.58, "reason": "..."}], "summary": "..."}\n'
                "Allowed params: CONFIDENCE_THRESHOLD, SCALP_MIN_RR, SCALP_TP_ATR_MULTIPLIER, ATR_THRESHOLD\n"
                "Respond ONLY with valid JSON."
            )
            
            # 4. Query Ollama (use meta-optimizer model if different)
            response = self._call_ollama(prompt, system=system, model=self.meta_model)
            if not response:
                return {"status": "llm_failed"}
            
            # 5. Parse mutations (defensive)
            mutations = []
            try:
                # Extract JSON from response (strip markdown fences if present)
                clean = response.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
                parsed = json.loads(clean)
                mutations = parsed.get("mutations", [])
            except Exception as e:
                log.warning(f"Meta-optimizer JSON parse failed: {e}")
                return {"status": "parse_failed", "raw_response": response[:500]}
            
            # 6. Apply mutations via background worker (non-blocking)
            applied = []
            for mutation in mutations[:self.max_mutations_per_day - self._mutations_today]:
                param = mutation.get("param", "")
                value = mutation.get("value")
                reason = mutation.get("reason", "")
                
                if not param or value is None:
                    continue
                
                # Safety: never mutate hardcoded risk limits
                forbidden = {"MAX_DAILY_LOSS_PCT", "MAX_RISK_PER_TRADE_USD",
                             "MAX_SHARES_PER_TRADE", "MIN_CASH_RESERVE_PCT"}
                if param in forbidden:
                    log.warning(f"Meta-optimizer blocked forbidden param: {param}")
                    continue
                
                success = self._apply_mutation(param, value, weights_path, config_path)
                if success:
                    applied.append({"param": param, "value": value, "reason": reason})
                    self._mutations_today += 1
                    log.info(f"🧬 Meta-optimizer mutation: {param} = {value} ({reason})")
            
            return {
                "status": "success",
                "mutations_applied": applied,
                "mutations_today": self._mutations_today,
                "summary": json.loads(response).get("summary", "") if response else "",
            }
            
        except Exception as e:
            log.error(f"Meta-optimizer failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _apply_mutation(self, param: str, value: Any,
                        weights_path: str, config_path: str) -> bool:
        """
        Apply a single parameter mutation to weights.json or config.py.
        
        Uses atomic writes with fsync to prevent corruption.
        """
        import re
        
        # Decide target file based on parameter namespace
        scalper_params = {
            "SCALP_STOP_ATR_MULTIPLIER", "SCALP_MIN_STOP_PCT", "SCALP_MAX_STOP_PCT",
            "SCALP_TP_ATR_MULTIPLIER", "SCALP_MAX_TP_PCT", "SCALP_MIN_RR",
            "SCALP_TRAILING_ACTIVATE_PCT", "SCALP_TRAILING_ATR_MULTIPLIER",
            "SCALP_PROFIT_ACTIVATE_PCT", "SCALP_PROFIT_GIVEBACK_PCT",
            "CONFIDENCE_THRESHOLD",
        }
        
        try:
            if param in scalper_params:
                # Target: weights.json
                data = {}
                if os.path.exists(weights_path):
                    try:
                        with open(weights_path, 'r') as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                data[param] = value
                atomic_write_json(weights_path, data)
                return True
            
            else:
                # Target: config.py (regex replace)
                if not os.path.exists(config_path):
                    return False
                with open(config_path, 'r') as f:
                    content = f.read()
                
                pattern = rf'({param}:\s*)([-+]?\d+\.?\d*|True|False)'
                replacement = r'\g<1>' + str(value)
                new_content, count = re.subn(pattern, replacement, content)
                
                if count > 0:
                    atomic_write_text(config_path, new_content)
                    return True
                return False
                
        except Exception as e:
            log.error(f"Mutation apply failed for {param}: {e}")
            return False
    
    def health_check(self) -> Dict[str, Any]:
        """
        Check if Ollama service is reachable and model is loaded.
        
        Returns:
            Health status dict
        """
        try:
            url = f"{self.config.host}/api/tags"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get('name', '') for m in data.get('models', [])]
                return {
                    "status": "healthy",
                    "host": self.config.host,
                    "model_available": self.config.model in models,
                    "models_loaded": models,
                    "calls_made": self._call_count,
                    "errors": self._error_count,
                    "skipped_pressure": self._skipped_pressure,
                    "skipped_rate": self._skipped_rate,
                    "free_ram_mb": available_ram_mb(),
                }
        except Exception as exc:
            return {
                "status": "unreachable",
                "host": self.config.host,
                "error": str(exc),
                "calls_made": self._call_count,
                "errors": self._error_count,
            }


# ═════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def atomic_write_json(path: str, data: Dict):
    """Atomic JSON write with fsync to prevent corruption on crash."""
    tmp = path + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: str, content: str):
    """Atomic text write with fsync to prevent corruption on crash."""
    tmp = path + ".tmp"
    with open(tmp, 'w') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def create_ollama_brain(cfg: BotConfig) -> Optional[OllamaBrain]:
    """
    Create OllamaBrain if enabled, otherwise return None.
    
    Args:
        cfg: BotConfig
        
    Returns:
        OllamaBrain instance or None
    """
    if not getattr(cfg, 'OLLAMA_ENABLED', False):
        log.info("Ollama brain disabled")
        return None
    
    brain = OllamaBrain(cfg)
    health = brain.health_check()
    
    if health.get("status") == "healthy":
        from core.memory_guard import memory_status
        mem = memory_status(cfg)
        log.info(
            f"✅ Ollama brain active | model={cfg.OLLAMA_MODEL} | "
            f"warm={not getattr(cfg, 'OLLAMA_UNLOAD_AFTER_CALL', False)} | "
            f"budget={getattr(cfg, 'OLLAMA_MEMORY_BUDGET_MB', 2560)}MB | "
            f"interval={getattr(cfg, 'OLLAMA_MIN_CALL_INTERVAL_SEC', 5)}s | "
            f"RAM free={mem['available_ram_mb']}MB"
        )
        # Pre-load model into reserved RAM so first trade decision is fast
        if int(getattr(cfg, 'OLLAMA_KEEP_ALIVE', 0)) > 0:
            brain._warmup()
        return brain
    else:
        log.warning(f"⚠️ Ollama brain unreachable: {health.get('error', 'unknown')}")
        return None
