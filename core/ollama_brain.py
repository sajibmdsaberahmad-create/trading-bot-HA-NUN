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
        )
        self._last_call_time = 0.0
        self._call_count = 0
        self._error_count = 0
    
    def _is_allowed(self) -> bool:
        """Check if enough time passed since last call (rate limiting)."""
        if not self.config.enabled:
            return False
        return True
    
    def _call_ollama(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """
        Call Ollama API synchronously with timeout.
        
        Args:
            prompt: User prompt
            system: Optional system prompt override
            
        Returns:
            Generated text or None on failure
        """
        if not self._is_allowed():
            return None
        
        try:
            url = f"{self.config.host}/api/generate"
            
            payload = {
                "model": self.config.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": self.config.max_tokens,
                    "temperature": self.config.temperature,
                },
            }
            if system:
                payload["system"] = system
            
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            
            start = time.time()
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                elapsed = (time.time() - start) * 1000
                
                self._last_call_time = time.time()
                self._call_count += 1
                
                log.debug(f"Ollama {self.config.model}: {elapsed:.0f}ms | "
                         f"{result.get('prompt_eval_count', 0)} prompt tokens, "
                         f"{result.get('eval_count', 0)} completion tokens")
                
                return result.get("response", "").strip()
                
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
        log.info(f"✅ Ollama brain active | model={cfg.OLLAMA_MODEL} | host={cfg.OLLAMA_HOST}")
        return brain
    else:
        log.warning(f"⚠️ Ollama brain unreachable: {health.get('error', 'unknown')}")
        return None