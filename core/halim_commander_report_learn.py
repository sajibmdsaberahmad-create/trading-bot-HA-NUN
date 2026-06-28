#!/usr/bin/env python3
"""
core/halim_commander_report_learn.py — Halim consumes commander IB report like web + trades.

Mirrors halim_web_learn flow:
  1. Structured corpus → halim/data/learn_cache/*.json (RAG + export_action_gold)
  2. record_action → action_log.jsonl (journal)
  3. commander_gold.jsonl + experience_buffer (via commander_ib_gold)

Good + bad + calculated-lottery (80–97% conviction) are all first-class learn sources.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

CACHE_DIR = Path("halim/data/learn_cache")
JOURNAL_PATH = Path("models/halim_commander_report_learn.jsonl")
MONITOR_PATH = Path("models/halim_commander_report_monitor.jsonl")

# Re-use report facts from gold module (single source of truth)
from core.commander_ib_gold import (  # noqa: E402
    HUMAN_FAILURE_RULES,
    REPORT_META,
    TRADE_CASES,
    WINNER_PATTERNS,
    ingest_commander_ib_lessons,
)

REPORT_SECTIONS = (
    "overview",
    "calculated_lottery",
    "human_failures",
    "human_wins",
    "turnover_fees",
    "trade_cases",
)


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_COMMANDER_REPORT_LEARN", "true").lower() in ("1", "true", "yes")


def _append(path: Path, row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _cache_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _write_learn_cache(
    *,
    topic: str,
    text: str,
    source_type: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Same shape as halim_web_learn cache docs — picked up by RAG + export_action_gold."""
    content_hash = _cache_hash(f"{topic}|{text[:500]}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", content_hash)
    cache_file = CACHE_DIR / f"{safe}.json"
    doc = {
        "url": f"local:commander_ib_report/{topic}",
        "final_url": f"local:commander_ib_report/{topic}",
        "host": "local",
        "topic": f"commander:ib_report_{topic}",
        "text": text[:50000],
        "text_excerpt": text[:2000],
        "text_chars": len(text),
        "content_hash": content_hash,
        "read_only": True,
        "source_type": source_type,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "report": REPORT_META["report"],
        **(meta or {}),
    }
    cache_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def _record_halim_action(
    capability: str,
    action: str,
    *,
    input_text: str,
    output_text: str,
    outcome: str = "teacher",
    meta: Optional[Dict[str, Any]] = None,
    cfg: Optional[BotConfig] = None,
) -> None:
    try:
        from core.halim_action_learn import record_action
        record_action(
            capability,
            action,
            input_text=input_text[:8000],
            output_text=output_text[:12000],
            outcome=outcome,
            source="commander_ib_report",
            meta={"report": REPORT_META["report"], **(meta or {})},
            cfg=cfg,
        )
    except Exception:
        pass


def build_corpus_sections() -> Dict[str, str]:
    """Full-text teachable sections — good, bad, and calculated lottery."""
    winners = [c for c in TRADE_CASES if c["outcome"] == "win"]
    losers = [c for c in TRADE_CASES if c["outcome"] == "loss"]

    overview = f"""Commander IB PortfolioAnalyst report ({REPORT_META['period_start']} → {REPORT_META['period_end']})
Account: individual US equities scalper | Ending NAV ${REPORT_META['ending_nav_usd']:,.2f}
Cumulative TWR: +{REPORT_META['cumulative_return_pct']}% | Sharpe {REPORT_META['sharpe']} | Max DD {REPORT_META['max_drawdown_pct']}%
Trading P&L (MTM): +${REPORT_META['mtm_pnl_usd']:,.2f} | Fees: −${REPORT_META['fees_usd']:.2f} ({REPORT_META['fee_drag_pct_of_mtm']:.0f}% of MTM)
Turnover ~${REPORT_META['turnover_bought_usd']:,.0f} bought / ${REPORT_META['turnover_sold_usd']:,.0f} sold | Ended 100% cash (flat)

KEY DISTINCTION FOR HALIM:
- Commander's "lottery" trades were NOT random gambling — calculated setups with ~80–97% conviction.
- Failures were human EXECUTION errors (hope-hold, late exit) on specific TRIPS — never blacklist a symbol.
- PLUG lost one report trip (−9.29%) but commander profits same vol/energy class on other entries (USEG +3.21%).
- Learn: EXIT bad trips fast; RE-ENTER same ticker when next 80%+ setup qualifies."""

    lottery = """CALCULATED LOTTERY DOCTRINE (commander manual trading — NOT coincidental)

Commander label: "lottery" = asymmetric high-beta scalp with CALCULATED edge, not casino randomness.
Typical conviction band: 80–97% before entry.

SETUP CHECKLIST (all must align for ENTER):
1. Volume spike ≥ 2.0× recent average (momentum ignition)
2. Price breaking micro-structure / prior bar high with follow-through
3. Sector/regime tailwind (materials +13.6%, tech momentum — not energy bleed)
4. Scan score ≥ 70 and profit_probability ≥ 0.80
5. Fakeout_risk ≤ 0.25 — if elevated, SKIP even if ticker is "hot"
6. Defined exit: spike_top or hard stop — round-trip complete, no bag-holding
7. Expected move must exceed 2× round-trip fees (net-positive churn)

WINNING LOTTERY EXAMPLES FROM REPORT (fast round-trips):
- NAK +7.84% | materials vol spike → exit into strength (+$154)
- RXT +9.06% | tech momentum burst (+$165)
- ELAB +13.44% | materials breakout (+$130)
- VSA +12.02% | education sector spike (+$121)
- MASK +5.88% | tech vol-led scalp (+$118)

BOT TRANSLATION:
- ENTER when entry_quality ≥ 0.80 AND spike_ratio ≥ 2.0 (maps commander's 80–97% sure)
- 97% tier: spike ≥ 2.8x, scan ≥ 82, profit_prob ≥ 0.90, fakeout ≤ 0.18
- 80% tier: spike ≥ 2.0x, scan ≥ 70, profit_prob ≥ 0.80, fakeout ≤ 0.25
- Below 80% conviction → SKIP THIS SETUP ONLY (SMTK-class) — not a ticker ban
- PLUG/energy: ENTER on calculated spike (USEG +3.21%); EXIT fast on losing trip; re-enter next setup
- Size by risk.py — never hope-scale into uncertainty"""

    failures = "HUMAN EXECUTION FAILURES — learn the mistake, keep the ticker tradeable:\n\n"
    for rule in HUMAN_FAILURE_RULES:
        failures += f"• [{rule['label']}] {rule['lesson']}\n  → Bot: {rule['bot_action']}\n\n"
    for case in losers:
        failures += (
            f"• {case['ticker']} ({case['sector']}): {case['return_pct']:+.1f}% "
            f"contribution {case['contribution_pct']:+.1f}% | ${case['realized_usd']:+.0f}\n"
            f"  Human error: {case['human_error']}\n"
            f"  Correct: {case['correct']}\n\n"
        )

    wins = "HUMAN WINS — reinforce for Halim/PPO:\n\n"
    for pat in WINNER_PATTERNS:
        wins += f"• [{pat['label']}] {pat['lesson']}\n  → {pat['bot_action']}\n\n"
    for case in winners:
        wins += (
            f"• {case['ticker']} ({case['sector']}): {case['return_pct']:+.1f}% "
            f"contribution {case['contribution_pct']:+.1f}% | ${case['realized_usd']:+.0f}\n"
            f"  Pattern: {case['correct']}\n\n"
        )

    turnover = f"""TURNOVER + FEES (commander report economics)

High turnover was INTENTIONAL — ~${REPORT_META['turnover_bought_usd']:,.0f} notional on ~$2k capital.
But fees −${REPORT_META['fees_usd']:.2f} consumed {REPORT_META['fee_drag_pct_of_mtm']:.0f}% of gross MTM profit.

RULE: churn is good ONLY when net_edge_per_dollar > fee_rate.
- Productive turnover: calculated lottery wins (NAK, RXT, ELAB…) — vol spike, quick exit
- Unproductive turnover: low-conviction entries (<80% setup) and hope-holds — not sector/ticker bans
- Bot metric: reward = net_pnl / notional_traded; penalize fee-bleed trips

February −7.94% = traded without edge (cold start). March/April +20%/+22% = edge confirmed.
Stand aside when conviction < 80% or after loss streak — commander ended flat in cash."""

    cases = "TRADE CASE STUDIES (good + bad for SFT):\n\n"
    for case in TRADE_CASES:
        tag = "WIN" if case["outcome"] == "win" else "LOSS"
        cases += (
            f"[{tag}] {case['ticker']} {case['sector']} | ret {case['return_pct']:+.2f}% | "
            f"ctr {case['contribution_pct']:+.2f}% | ${case['realized_usd']:+.2f}\n"
        )
        if case.get("human_error"):
            cases += f"  mistake: {case['human_error']}\n"
        cases += f"  teach: {case['correct']}\n\n"

    return {
        "overview": overview,
        "calculated_lottery": lottery,
        "human_failures": failures,
        "human_wins": wins,
        "turnover_fees": turnover,
        "trade_cases": cases,
    }


def _section_capability(topic: str) -> Tuple[str, str]:
    table = {
        "overview": ("read_understand", "commander_report_overview"),
        "calculated_lottery": ("trade_reflex", "calculated_lottery_doctrine"),
        "human_failures": ("exit_decision", "commander_failure_study"),
        "human_wins": ("trade_reflex", "commander_winner_study"),
        "turnover_fees": ("reasoning", "turnover_fee_economics"),
        "trade_cases": ("decision_text", "trade_case_review"),
    }
    return table.get(topic, ("read_understand", f"commander_{topic}"))


def fetch_commander_report_section(
    section: str,
    cfg: Optional[BotConfig] = None,
    *,
    record: bool = True,
) -> Dict[str, Any]:
    """
    Like fetch_learn_page / fetch_market_hours_learn — one report section → cache + action log.
    section: overview | calculated_lottery | human_failures | human_wins | turnover_fees | trade_cases
    """
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return {"ok": False, "reason": "HALIM_COMMANDER_REPORT_LEARN_disabled", "section": section}

    corpus = build_corpus_sections()
    if section not in corpus:
        return {"ok": False, "reason": f"unknown_section:{section}", "section": section}

    text = corpus[section]
    doc = _write_learn_cache(
        topic=section,
        text=text,
        source_type="commander_ib_report",
        meta={"section": section, "polarity": _section_polarity(section)},
    )

    out = {
        "ok": True,
        "url": doc["url"],
        "final_url": doc["final_url"],
        "host": "local",
        "topic": doc["topic"],
        "text_chars": len(text),
        "text_excerpt": text[:2000],
        "content_hash": doc["content_hash"],
        "read_only": True,
        "source_type": "commander_ib_report",
        "section": section,
    }

    if record:
        cap, action = _section_capability(section)
        _record_halim_action(
            cap,
            action,
            input_text=f"commander:ib_report_{section}\n\n{text[:4000]}",
            output_text=_section_teaching_output(section, text),
            outcome="teacher",
            meta={"section": section, "content_hash": doc["content_hash"]},
            cfg=cfg,
        )

    _append(JOURNAL_PATH, {k: v for k, v in out.items() if k != "text_excerpt"})
    _append(MONITOR_PATH, {
        "event": "commander_report_learn_ok",
        "section": section,
        "chars": len(text),
        "hash": doc["content_hash"],
        "read_only": True,
    })
    log.info(f"📚 Halim commander report READ {section} — {len(text)} chars (local, teacher)")
    return out


def _section_polarity(section: str) -> str:
    if section in ("human_failures",):
        return "bad"
    if section in ("human_wins", "calculated_lottery"):
        return "good"
    return "mixed"


def _section_teaching_output(section: str, text: str) -> str:
    """Condensed Halim response for action_log / SFT."""
    if section == "calculated_lottery":
        return (
            "CALCULATED LOTTERY: ENTER at 80–97% conviction — "
            "spike≥2x, scan≥70, profit_prob≥0.80, fakeout≤0.25. "
            "EXIT spike_top or hard stop per trip. Below 80% → SKIP weak setup only. "
            "PLUG/vol names OK when setup qualifies — learn exit, not symbol skip."
        )
    if section == "human_failures":
        return (
            "LEARN per-trip execution: hope-hold on CTEV/PLUG/ENVB trips was the mistake — "
            "EXIT earlier, never widen stops. PLUG/energy still tradeable (USEG won +3.21%). "
            "Do NOT blacklist symbols; fix exit discipline and re-enter on next 80%+ setup."
        )
    if section == "human_wins":
        return (
            "REINFORCE: fast vol-spike round-trips (NAK/RXT/ELAB/VSA/MASK), "
            "exit into strength, go flat when session edge fades."
        )
    if section == "turnover_fees":
        return (
            f"Turnover OK when net/dollar > fees ({REPORT_META['fee_drag_pct_of_mtm']:.0f}% drag on human). "
            "Productive churn on calculated lottery only."
        )
    return text[:1500]


def fetch_commander_report_learn(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Fetch ALL sections — used by learn browse topic local:commander_ib_report."""
    cfg = cfg or BotConfig()
    sections: List[Dict[str, Any]] = []
    total_chars = 0
    for sec in REPORT_SECTIONS:
        r = fetch_commander_report_section(sec, cfg, record=True)
        sections.append(r)
        if r.get("ok"):
            total_chars += int(r.get("text_chars") or 0)
    ok = sum(1 for s in sections if s.get("ok"))
    return {
        "ok": ok > 0,
        "topic": "local:commander_ib_report",
        "sections_ok": ok,
        "sections_total": len(REPORT_SECTIONS),
        "text_chars": total_chars,
        "sections": sections,
    }


def consume_commander_report(
    cfg: Optional[BotConfig] = None,
    *,
    force_gold: bool = False,
    seed_buffer: bool = True,
    export_action_gold: bool = True,
) -> Dict[str, Any]:
    """
    Full consume path (mirrors web browse cycle end-state):
      learn_cache sections → action_log → commander_gold → experience_buffer → action_gold export
    """
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return {"ok": False, "reason": "HALIM_COMMANDER_REPORT_LEARN_disabled"}

    cache_result = fetch_commander_report_learn(cfg)

    gold_result = ingest_commander_ib_lessons(
        cfg,
        force=force_gold,
        seed_buffer=seed_buffer,
        append_guidelines=False,
    )

    action_export: Dict[str, Any] = {}
    if export_action_gold:
        try:
            from core.halim_action_learn import export_action_gold
            action_export = export_action_gold(include_learn_cache=True)
        except Exception as exc:
            action_export = {"ok": False, "error": str(exc)[:120]}

    return {
        "ok": True,
        "learn_cache": cache_result,
        "commander_gold": gold_result,
        "action_gold_export": action_export,
        "report": REPORT_META["report"],
    }


def commander_report_rag_boost(topic: str) -> int:
    """Extra RAG score for commander cache hits (used by halim_learn_rag)."""
    t = (topic or "").lower()
    if t.startswith("commander:ib_report"):
        return 6
    return 0
