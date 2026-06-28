#!/usr/bin/env python3
"""
core/halim_learn_catalog.py — Halim learn topic pool (wiki, URLs, RSS, local market clock).

Read-only sources only; rotated each learn batch so Halim is not limited to day trading or Wikipedia.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

# ── Wikipedia: all trading styles ───────────────────────────────────────────
WIKI_TRADING_CORE: List[str] = [
    "wiki:Stock_market",
    "wiki:Algorithmic_trading",
    "wiki:Day_trading",
    "wiki:Swing_trading",
    "wiki:Position_trading",
    "wiki:Scalping_(trading)",
    "wiki:Trend_following",
    "wiki:Volatility_(finance)",
    "wiki:Technical_analysis",
    "wiki:Fundamental_analysis",
    "wiki:Risk_management",
    "wiki:Market_liquidity",
    "wiki:Options_(finance)",
    "wiki:Futures_contract",
    "wiki:Margin_(finance)",
    "wiki:Short_(finance)",
    "wiki:Bid–ask_spread",
    "wiki:Order_book",
    "wiki:Market_maker",
    "wiki:High-frequency_trading",
    "wiki:Arbitrage",
    "wiki:Portfolio_management",
]

WIKI_CHARTS: List[str] = [
    "wiki:Candlestick_pattern",
    "wiki:Chart_pattern",
    "wiki:Moving_average",
    "wiki:Relative_strength_index",
    "wiki:MACD",
    "wiki:Bollinger_Bands",
    "wiki:Support_and_resistance",
    "wiki:Fibonacci_retracement",
    "wiki:Volume_profile",
    "wiki:Ichimoku_Kinkō_Hyō",
    "wiki:Pivot_point_(technical_analysis)",
]

WIKI_MACRO_MARKETS: List[str] = [
    "wiki:Macroeconomics",
    "wiki:Federal_Reserve",
    "wiki:Interest_rate",
    "wiki:Inflation",
    "wiki:Monetary_policy",
    "wiki:Recession",
    "wiki:Yield_curve",
    "wiki:Foreign_exchange_market",
    "wiki:Commodity_market",
    "wiki:Economic_indicator",
    "wiki:Earnings",
]

WIKI_SENTIMENT: List[str] = [
    "wiki:Market_sentiment",
    "wiki:Behavioral_finance",
    "wiki:Herd_behavior",
    "wiki:Cognitive_bias",
    "wiki:Fear_and_greed_index",
    "wiki:VIX",
    "wiki:Investor_psychology",
]

WIKI_GENERAL: List[str] = [
    "wiki:Artificial_intelligence",
    "wiki:Machine_learning",
    "wiki:Reinforcement_learning",
    "wiki:Natural_language_processing",
    "wiki:Neural_network",
    "wiki:Probability",
    "wiki:Statistics",
    "wiki:Bayesian_inference",
    "wiki:Decision-making",
    "wiki:Psychology",
    "wiki:Time_management",
]

WIKI_CODING: List[str] = [
    "wiki:Python_(programming_language)",
    "wiki:JavaScript",
    "wiki:Algorithm",
    "wiki:Data_structure",
    "wiki:Git",
    "wiki:Application_programming_interface",
    "wiki:JSON",
    "wiki:SQL",
    "wiki:Software_engineering",
]

# ── Direct URLs (investopedia, SEC, investor.gov, docs) ─────────────────────
URL_REFERENCE: List[str] = [
    "https://www.investopedia.com/terms/s/swingtrading.asp",
    "https://www.investopedia.com/terms/p/position-trading.asp",
    "https://www.investopedia.com/terms/t/technicalanalysis.asp",
    "https://www.investopedia.com/terms/m/marketsentiment.asp",
    "https://www.investopedia.com/terms/r/riskmanagement.asp",
    "https://www.investopedia.com/terms/c/candlestick.asp",
    "https://www.investopedia.com/terms/o/optionscontract.asp",
    "https://www.investopedia.com/terms/f/futurescontract.asp",
    "https://www.investopedia.com/terms/v/volatility.asp",
    "https://www.investor.gov/introduction-investing/investing-basics/risk-and-return",
    "https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work",
    "https://www.sec.gov/investor/pubs/tbegin.htm",
    "https://docs.python.org/3/tutorial/index.html",
    "https://docs.python.org/3/library/json.html",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide",
]

# ── RSS headlines (descriptions only — no link following) ───────────────────
RSS_FEEDS: List[str] = [
    "rss:https://feeds.reuters.com/reuters/businessNews",
    "rss:https://feeds.reuters.com/reuters/topNews",
    "rss:https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
]

# Refreshed every batch — live US market session clock (local, not a web fetch)
LOCAL_TOPICS: List[str] = [
    "local:market_hours",
]

WIKI_LANGUAGE_MANNERS: List[str] = [
    "wiki:Etiquette",
    "wiki:Communication",
    "wiki:Active_listening",
    "wiki:Emotional_intelligence",
    "wiki:Persuasion",
    "wiki:Rhetoric",
    "wiki:Creative_writing",
    "wiki:Grammar",
    "wiki:Politeness",
    "wiki:Nonverbal_communication",
    "wiki:Interpersonal_communication",
    "wiki:Storytelling",
    "wiki:Empathy",
    "wiki:Conflict_resolution",
]

WIKI_GENERATIVE: List[str] = [
    "wiki:Large_language_model",
    "wiki:Prompt_engineering",
    "wiki:Natural_language_generation",
    "wiki:Dialogue_system",
    "wiki:Chatbot",
    "wiki:Summarization",
    "wiki:Translation",
    "wiki:Style_(fiction)",
]

GOOGLE_LANGUAGE_QUERIES: List[str] = [
    "professional email communication etiquette examples",
    "clear concise technical writing best practices",
    "empathetic supportive language patterns conversation",
    "how to explain complex topics simply to non experts",
    "polite refusal and boundary setting phrases",
    "active listening techniques in dialogue",
    "tone and voice in customer facing messages",
    "storytelling structure hook context call to action",
]

GOOGLE_LEARN_QUERIES: List[str] = [
    "swing trading strategy entry exit rules",
    "position trading vs day trading risk profile",
    "market sentiment indicators fear greed VIX",
    "macroeconomic data impact on stock market",
    "technical analysis chart patterns cheat sheet",
    "options strategies for hedging portfolio risk",
    "futures vs spot market differences trading",
    "pre market after hours trading rules US",
    "algorithmic trading backtesting best practices",
    "behavioral finance cognitive biases traders",
    "Python pandas financial time series analysis",
    "how to read candlestick patterns trading",
    "Federal Reserve interest rate impact equities",
    "sector rotation market cycle investing",
    "risk management position sizing Kelly criterion",
    *GOOGLE_LANGUAGE_QUERIES,
]


_GOOGLE_OFFSET_PATH = Path("models/halim_learn_google_offset.txt")


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def build_learn_topic_pool() -> List[str]:
    """Full rotated pool — trading (all styles), charts, macro, sentiment, coding, news RSS."""
    pool: List[str] = []

    if _flag("HALIM_LEARN_INCLUDE_TRADING", "true"):
        pool.extend(WIKI_TRADING_CORE)
    if _flag("HALIM_LEARN_INCLUDE_CHARTS", "true"):
        pool.extend(WIKI_CHARTS)
    if _flag("HALIM_LEARN_INCLUDE_MACRO", "true"):
        pool.extend(WIKI_MACRO_MARKETS)
    if _flag("HALIM_LEARN_INCLUDE_SENTIMENT", "true"):
        pool.extend(WIKI_SENTIMENT)
    if _flag("HALIM_LEARN_INCLUDE_GENERAL", "true"):
        pool.extend(WIKI_GENERAL)
    if _flag("HALIM_LEARN_INCLUDE_CODING", "true"):
        pool.extend(WIKI_CODING)
    if _flag("HALIM_LEARN_INCLUDE_LANGUAGE", "true"):
        pool.extend(WIKI_LANGUAGE_MANNERS)
    if _flag("HALIM_LEARN_INCLUDE_GENERATIVE", "true"):
        pool.extend(WIKI_GENERATIVE)
    if _flag("HALIM_LEARN_INCLUDE_URLS", "true"):
        pool.extend(URL_REFERENCE)
    if _flag("HALIM_LEARN_INCLUDE_RSS", "true"):
        pool.extend(RSS_FEEDS)
    if _flag("HALIM_LEARN_INCLUDE_MARKET_HOURS", "true"):
        pool.extend(LOCAL_TOPICS)

    raw = os.getenv("HALIM_LEARN_TOPICS", "").strip()
    if raw:
        pool = [t.strip() for t in raw.split(",") if t.strip()]
    return pool


def pick_google_queries(cap: int) -> List[str]:
    if not _flag("HALIM_LEARN_GOOGLE_SNIPPETS", "true"):
        return []
    raw = os.getenv("HALIM_LEARN_GOOGLE_QUERIES", "").strip()
    if raw:
        queries = [q.strip() for q in raw.split("|") if q.strip()]
    else:
        queries = list(GOOGLE_LEARN_QUERIES)
    if not queries:
        return []
    offset = 0
    if _GOOGLE_OFFSET_PATH.is_file():
        try:
            offset = int(_GOOGLE_OFFSET_PATH.read_text().strip()) % len(queries)
        except Exception:
            offset = 0
    picked = [queries[(offset + i) % len(queries)] for i in range(min(cap, len(queries)))]
    try:
        _GOOGLE_OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOOGLE_OFFSET_PATH.write_text(str((offset + len(picked)) % len(queries)), encoding="utf-8")
    except Exception:
        pass
    return picked
