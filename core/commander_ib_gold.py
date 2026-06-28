#!/usr/bin/env python3
"""
core/commander_ib_gold.py — Teach Halim/PPO from commander's IB PortfolioAnalyst report.

Encodes human failure patterns (held too long, tail losses, fee bleed, weak-setup entries)
and winning patterns (spike capture, round-trip discipline, stand aside) as SFT gold +
experience-buffer teacher labels.

IMPORTANT: Teach EXECUTION mistakes on specific trips — never blacklist tickers.
Commander profited on volatile names (including energy/PLUG-class setups) when
calculated lottery + fast exit; losses were hope-holds and late cuts, not "avoid symbol".
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
COMMANDER_GOLD = _REPO / "halim/data/training/commander_gold.jsonl"
COMMANDER_HASHES = _REPO / "halim/data/training/commander_gold_hashes.jsonl"
COMMANDER_EVENTS = _REPO / "halim/data/commander/ib_report_events.jsonl"

# IB Since Inception report: 2026-02-06 → 2026-04-27 (MD Saber Ahmad Sajib)
REPORT_META = {
    "report": "Since_Inception_Detailed_11_20260427",
    "period_start": "2026-02-06",
    "period_end": "2026-04-27",
    "cumulative_return_pct": 35.03,
    "fees_usd": 317.20,
    "mtm_pnl_usd": 1108.31,
    "realized_stocks_usd": 875.35,
    "fee_drag_pct_of_mtm": round(317.20 / 1108.31 * 100, 1),
    "turnover_bought_usd": 73788.91,
    "turnover_sold_usd": 74900.29,
    "ending_nav_usd": 2084.55,
    "max_drawdown_pct": 7.94,
    "sharpe": 2.81,
}

# Human failures the bot must NOT repeat (abstract — not ticker-specific doctrine)
HUMAN_FAILURE_RULES: List[Dict[str, Any]] = [
    {
        "id": "tail_loss_one_trade",
        "label": "disaster_tail_loss",
        "lesson": (
            "One round-trip loss must never exceed ~3% of session equity. "
            "CTEV-style bleeds (−13% portfolio contribution) erase many small wins. "
            "Cut at stop immediately — no hope, no averaging down, no 'it will come back'."
        ),
        "bot_action": "EXIT at hard stop; never widen stop after entry; cap loss at MAX_SESSION_TRADE_LOSS_PCT.",
    },
    {
        "id": "held_through_reversal",
        "label": "held_too_long",
        "lesson": (
            "Humans hold losers hoping for recovery — report trips on CTEV, PLUG, ENVB "
            "were hope-holds, not reasons to ban those symbols. "
            "Algo must EXIT when momentum fades or stop hits; same ticker can be traded again on the next 80%+ setup."
        ),
        "bot_action": "EXIT on spike_top fade or early_loss_threshold; re-enter symbol when conviction returns ≥80%.",
    },
    {
        "id": "fee_bleed_churn",
        "label": "unproductive_turnover",
        "lesson": (
            f"Fees were ${REPORT_META['fees_usd']:.0f} (~{REPORT_META['fee_drag_pct_of_mtm']:.0f}% of gross trading P&L). "
            "High turnover is the goal only when net edge per dollar traded beats round-trip cost."
        ),
        "bot_action": "SKIP weak setups (low conviction) — not symbols; penalize gross-win/net-loss trips.",
    },
    {
        "id": "energy_trip_discipline",
        "label": "wrong_execution_not_ticker",
        "lesson": (
            "Energy sector net −9.97% on report — but USEG +3.21% shows energy names CAN win. "
            "PLUG loss was hope-hold on one trip; commander also profits volatile energy on calculated spikes. "
            "Judge each trip by conviction + exit — never blacklist PLUG or a sector."
        ),
        "bot_action": "ENTER energy/vol names at 80%+ lottery; EXIT fast; repeat symbol when next setup qualifies.",
    },
    {
        "id": "feb_cold_start",
        "label": "traded_without_edge",
        "lesson": "February was −7.94% — trading before setup quality confirmed. Stand aside until spike + entry_quality pass.",
        "bot_action": "SKIP only when profit_probability < MIN on THIS setup — not because of ticker history.",
    },
    {
        "id": "no_stand_aside",
        "label": "overtrading_flat_periods",
        "lesson": "Humans keep clicking; algos should go flat when edge degrades (commander ended 100% cash after strong April).",
        "bot_action": "After N fee-negative trips or loss streak, pause new entries until next session or regime shift.",
    },
]

# Patterns to reinforce (what worked — abstract)
WINNER_PATTERNS: List[Dict[str, Any]] = [
    {
        "id": "spike_round_trip",
        "label": "good_cut",
        "lesson": (
            "Top contributors (NAK, RXT, ELAB, VSA, MASK) were fast momentum round-trips — "
            "enter on vol spike, exit into strength, flat before next setup."
        ),
        "bot_action": "ENTER on volume_spike/momentum_breakout; EXIT via spike_top_exit; complete round-trip.",
    },
    {
        "id": "materials_momentum",
        "label": "productive_turnover",
        "lesson": "Basic materials +13.59% — high turnover paid when trend + vol aligned.",
        "bot_action": "Favor momentum_breakout in strong sectors; keep turnover high only there.",
    },
    {
        "id": "flat_discipline",
        "label": "good_stand_aside",
        "lesson": "Ending 100% cash after +35% period — bank gains, stop forcing trades.",
        "bot_action": "Session stand-aside when daily target hit or edge metrics fall below floor.",
    },
]

# Commander "lottery" = calculated asymmetric scalp at 80–97% conviction (NOT random)
LOTTERY_CONVICTION_TIERS: List[Dict[str, Any]] = [
    {
        "tier": "97",
        "min_confidence_pct": 97,
        "bot_thresholds": {
            "spike_ratio_min": 2.8,
            "scan_score_min": 82,
            "profit_probability_min": 0.90,
            "fakeout_risk_max": 0.18,
            "sector_attribution_min_pct": 0.0,
        },
        "description": "All signals aligned — vol explosion, sector hot, structure break, minimal fakeout.",
    },
    {
        "tier": "90",
        "min_confidence_pct": 90,
        "bot_thresholds": {
            "spike_ratio_min": 2.4,
            "scan_score_min": 78,
            "profit_probability_min": 0.85,
            "fakeout_risk_max": 0.22,
            "sector_attribution_min_pct": 0.0,
        },
        "description": "Strong calculated lottery — commander typical high-conviction entry.",
    },
    {
        "tier": "80",
        "min_confidence_pct": 80,
        "bot_thresholds": {
            "spike_ratio_min": 2.0,
            "scan_score_min": 70,
            "profit_probability_min": 0.80,
            "fakeout_risk_max": 0.25,
            "sector_attribution_min_pct": -2.0,
        },
        "description": "Minimum commander lottery threshold — below this SKIP (not calculated).",
    },
]

LOTTERY_WIN_CASES: List[Dict[str, Any]] = [
    {"ticker": "NAK", "return_pct": 7.84, "conviction_est": 92, "setup": "materials vol spike, momentum break"},
    {"ticker": "RXT", "return_pct": 9.06, "conviction_est": 94, "setup": "tech burst, high vol follow-through"},
    {"ticker": "ELAB", "return_pct": 13.44, "conviction_est": 91, "setup": "materials breakout, fast round-trip"},
    {"ticker": "VSA", "return_pct": 12.02, "conviction_est": 88, "setup": "education sector ignition"},
    {"ticker": "MASK", "return_pct": 5.88, "conviction_est": 86, "setup": "tech vol-led scalp"},
    {"ticker": "SNAL", "return_pct": 11.49, "conviction_est": 90, "setup": "gaming tech spike capture"},
    {"ticker": "ONFO", "return_pct": 10.58, "conviction_est": 87, "setup": "industrial micro momentum"},
    {"ticker": "USEG", "return_pct": 3.98, "conviction_est": 86, "setup": "energy vol spike — same sector as PLUG loss trip but disciplined exit"},
]

LOTTERY_FAIL_CASES: List[Dict[str, Any]] = [
    {
        "ticker": "SMTK", "return_pct": -22.53, "conviction_est": 45,
        "failure_mode": "weak_setup",
        "error": "Weak setup (<80%) — skip this entry, not the symbol forever",
    },
    {
        "ticker": "CTEV", "return_pct": -14.05, "conviction_est": 70,
        "failure_mode": "hope_hold",
        "error": "Entry had some edge; human HOPE-HELD through reversal — should have EXITed at stop",
    },
    {
        "ticker": "PLUG", "return_pct": -10.29, "conviction_est": 72,
        "failure_mode": "hope_hold",
        "error": "This trip: held through reversal. Commander also PROFITS PLUG-class vol names on other spikes — fix exit, not symbol",
    },
    {
        "ticker": "ENVB", "return_pct": -9.12, "conviction_est": 65,
        "failure_mode": "hope_hold",
        "error": "Hope-held biotech after fade — should EXIT; symbol tradeable again on next 80%+ spike",
    },
]

# Per-trade teachable moments from report (symbol for context; lesson is abstract)
TRADE_CASES: List[Dict[str, Any]] = [
    {
        "ticker": "CTEV", "sector": "Industrial", "contribution_pct": -13.37,
        "realized_usd": -327.55, "return_pct": -14.05, "outcome": "loss",
        "human_error": "Hope-held one industrial trip — largest dollar loss. Ticker not banned.",
        "correct": "EXIT at hard stop on THIS trip; CTEV tradeable again when next 80%+ lottery setup fires.",
    },
    {
        "ticker": "PLUG", "sector": "Energy", "contribution_pct": -9.29,
        "realized_usd": -220.99, "return_pct": -10.29, "outcome": "loss",
        "human_error": "Hope-held THIS round-trip through reversal. Commander also profits PLUG/vol energy on other calculated entries.",
        "correct": "EXIT at early_loss/spike_top on losing trip; RE-ENTER PLUG when spike≥2x and conviction≥80% (see USEG win same sector).",
    },
    {
        "ticker": "ENVB", "sector": "Healthcare", "contribution_pct": -9.12,
        "realized_usd": -220.71, "return_pct": -9.12, "outcome": "loss",
        "human_error": "Hope-held biotech after momentum faded — execution error, not symbol veto.",
        "correct": "EXIT on fade; ENVB OK to trade again on next vol spike with mechanical exit.",
    },
    {
        "ticker": "SMTK", "sector": "Technology", "contribution_pct": -5.76,
        "realized_usd": -19.83, "return_pct": -22.53, "outcome": "loss",
        "human_error": "Entered THIS setup below 80% conviction — weak vol, not calculated lottery.",
        "correct": "SKIP this weak setup only; SMTK still allowed when future scan hits 80%+ thresholds.",
    },
    {
        "ticker": "USEG", "sector": "Energy", "contribution_pct": 3.21,
        "realized_usd": 77.00, "return_pct": 3.98, "outcome": "win",
        "human_error": None,
        "correct": "Energy CAN win — same sector as PLUG loss; ENTER on spike, EXIT disciplined. Learn from contrast.",
    },
    {
        "ticker": "NAK", "sector": "Basic Materials", "contribution_pct": 7.19,
        "realized_usd": 153.96, "return_pct": 7.84, "outcome": "win",
        "human_error": None,
        "correct": "ENTER momentum spike; EXIT into strength — model round-trip.",
    },
    {
        "ticker": "RXT", "sector": "Technology", "contribution_pct": 7.18,
        "realized_usd": 164.56, "return_pct": 9.06, "outcome": "win",
        "human_error": None,
        "correct": "Fast tech scalp with vol — take profit at spike top.",
    },
    {
        "ticker": "ELAB", "sector": "Basic Materials", "contribution_pct": 5.72,
        "realized_usd": 130.16, "return_pct": 13.44, "outcome": "win",
        "human_error": None,
        "correct": "Materials momentum — enter early vol, exit before giveback.",
    },
    {
        "ticker": "VSA", "sector": "Education", "contribution_pct": 5.29,
        "realized_usd": 120.57, "return_pct": 12.02, "outcome": "win",
        "human_error": None,
        "correct": "Spike capture on thin name — size within risk, exit quick.",
    },
    {
        "ticker": "MASK", "sector": "Technology", "contribution_pct": 5.23,
        "realized_usd": 117.80, "return_pct": 5.88, "outcome": "win",
        "human_error": None,
        "correct": "Vol-led entry, mechanical exit — no human hesitation.",
    },
]


def _enabled() -> bool:
    return os.getenv("COMMANDER_IB_GOLD", "true").lower() in ("1", "true", "yes")


def _append(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def _row_key(row: Dict[str, Any]) -> str:
    blob = "|".join(str(row.get(k, ""))[:300] for k in ("capability", "instruction", "input", "output"))
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _load_hashes() -> set:
    seen: set = set()
    if COMMANDER_HASHES.is_file():
        with open(COMMANDER_HASHES, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    seen.add(line)
    return seen


def _gold_row(
    *,
    capability: str,
    instruction: str,
    input_text: str,
    output_text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "capability": capability,
        "instruction": instruction,
        "input": input_text,
        "output": output_text,
        "source": "commander_ib_report",
        "phase": "toddler",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(meta or {}),
    }


def build_principle_gold() -> List[Dict[str, Any]]:
    """Session-level rules Halim must internalize."""
    rows: List[Dict[str, Any]] = []
    summary_in = (
        f"Commander IB report {REPORT_META['period_start']} → {REPORT_META['period_end']}\n"
        f"Return +{REPORT_META['cumulative_return_pct']}% | fees −${REPORT_META['fees_usd']:.0f} "
        f"({REPORT_META['fee_drag_pct_of_mtm']:.0f}% of MTM) | turnover ~${REPORT_META['turnover_bought_usd']:,.0f}\n"
        f"Task: List what the AI must do BETTER than the human commander."
    )
    principles = [
        "1. Keep high turnover ONLY when net per dollar traded beats fees.",
        "2. Never allow one trade to lose >3% of session equity (human had −13% trips).",
        "3. Cut losers mechanically on EACH trip — hope-hold was the mistake (CTEV/PLUG/ENVB trips), not the symbols.",
        "4. Exit winners into spikes; do not give back peak (spike_top_exit).",
        "5. Stand aside when edge is gone — human ended 100% cash; bot should pause entries.",
        "6. Same ticker can lose one trip and win the next (PLUG loss vs USEG energy win) — learn execution per trip.",
        "7. Require entry_quality pass before every trip — human traded cold in February.",
        "8. CALCULATED LOTTERY: 80–97% conviction — ENTER volatile names including PLUG when setup qualifies; "
        "EXIT fast on THIS trip if wrong; re-enter when setup returns.",
    ]
    rows.append(_gold_row(
        capability="reasoning",
        instruction="Extract commander trading lessons for autonomous execution.",
        input_text=summary_in,
        output_text="\n".join(principles),
        meta={"outcome_label": "commander_principles", "report": REPORT_META["report"]},
    ))
    return rows


def build_failure_gold() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rule in HUMAN_FAILURE_RULES:
        user = (
            f"Human failure pattern: {rule['id']}\n"
            f"Label: {rule['label']}\n"
            f"Context: commander IB report {REPORT_META['period_start']}–{REPORT_META['period_end']}\n"
            f"What went wrong: {rule['lesson']}"
        )
        assistant = (
            f"BOT RULE | {rule['bot_action']} | "
            f"Learn label={rule['label']} — fix execution per trip, not ticker bans."
        )
        rows.append(_gold_row(
            capability="exit_decision" if "EXIT" in rule["bot_action"] else "decision_text",
            instruction="Given a human trading failure, state the correct algo behavior.",
            input_text=user,
            output_text=assistant,
            meta={"outcome_label": rule["label"], "failure_id": rule["id"]},
        ))
    return rows


def build_lottery_gold() -> List[Dict[str, Any]]:
    """Teach calculated lottery (80–97% conviction) — commander's core edge."""
    rows: List[Dict[str, Any]] = []

    doctrine_in = (
        "Commander calls high-beta scalps 'lottery' but they are CALCULATED, not random.\n"
        "Typical conviction: 80–97% before entry.\n"
        "Task: Define ENTER rules for Halim/PPO and when to SKIP."
    )
    doctrine_out = (
        "CALCULATED LOTTERY RULES:\n"
        "• 97% tier: spike≥2.8x, scan≥82, profit_prob≥0.90, fakeout≤0.18 → ENTER full reflex\n"
        "• 90% tier: spike≥2.4x, scan≥78, profit_prob≥0.85, fakeout≤0.22 → ENTER\n"
        "• 80% tier: spike≥2.0x, scan≥70, profit_prob≥0.80, fakeout≤0.25 → ENTER minimum\n"
        "• Below 80% → SKIP THIS SETUP only (SMTK-class) — symbol can trade again later\n"
        "• EXIT: spike_top or hard stop on each trip — hope-hold was the human mistake\n"
        "• PLUG/CTEV/ENVB: learn exit discipline per trip; re-enter when 80%+ setup returns\n"
        "• Net edge must exceed 2× fees — turnover is the goal only when calculated"
    )
    rows.append(_gold_row(
        capability="trade_reflex",
        instruction="Define calculated lottery entry — 80–97% conviction asymmetric scalps.",
        input_text=doctrine_in,
        output_text=doctrine_out,
        meta={"outcome_label": "calculated_lottery", "pattern_id": "lottery_doctrine"},
    ))

    for tier in LOTTERY_CONVICTION_TIERS:
        t = tier["bot_thresholds"]
        user = (
            f"Conviction tier {tier['tier']}% ({tier['min_confidence_pct']}%+)\n"
            f"{tier['description']}\n"
            f"Signals: spike≥{t['spike_ratio_min']}x scan≥{t['scan_score_min']} "
            f"profit_prob≥{t['profit_probability_min']} fakeout≤{t['fakeout_risk_max']}"
        )
        rows.append(_gold_row(
            capability="decision_text",
            instruction="Map commander conviction tier to bot ENTER/SKIP thresholds.",
            input_text=user,
            output_text=(
                f"ENTER | confidence={tier['min_confidence_pct']/100:.2f} | "
                f"Calculated lottery tier {tier['tier']} — all thresholds met."
            ),
            meta={"outcome_label": "calculated_lottery", "conviction_tier": tier["tier"]},
        ))

    for case in LOTTERY_WIN_CASES:
        rows.append(_gold_row(
            capability="trade_reflex",
            instruction="Reinforce a calculated lottery winner from commander report.",
            input_text=(
                f"{case['ticker']} return {case['return_pct']:+.2f}% | "
                f"est. conviction {case['conviction_est']}%\n"
                f"Setup: {case['setup']}"
            ),
            output_text=(
                f"ENTER | confidence={case['conviction_est']/100:.2f} | "
                f"Calculated lottery WIN — {case['setup']}; EXIT spike_top."
            ),
            meta={"ticker": case["ticker"], "outcome_label": "calculated_lottery_win", "win": True},
        ))

    for case in LOTTERY_FAIL_CASES:
        weak_setup = case.get("failure_mode") == "weak_setup"
        if weak_setup:
            action = "SKIP this setup"
            cap = "decision_text"
        else:
            action = "EXIT earlier on this trip"
            cap = "exit_decision"
        rows.append(_gold_row(
            capability=cap,
            instruction="Learn from commander losing trip — execution mistake, not ticker ban.",
            input_text=(
                f"{case['ticker']} return {case['return_pct']:+.2f}% | "
                f"est. conviction {case['conviction_est']}% on this entry\n"
                f"What went wrong: {case['error']}\n"
                f"Note: symbol remains tradeable on next 80%+ calculated lottery setup."
            ),
            output_text=(
                f"{action} | confidence=0.90 | {case['error']} | "
                f"label={'traded_without_edge' if weak_setup else 'held_too_long'} | "
                f"ticker_not_banned=true"
            ),
            meta={"ticker": case["ticker"], "outcome_label": "held_too_long" if not weak_setup else "traded_without_edge", "win": False},
        ))

    return rows


def build_winner_gold() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for pat in WINNER_PATTERNS:
        user = (
            f"Winning pattern: {pat['id']}\n"
            f"Label: {pat['label']}\n"
            f"Evidence: {pat['lesson']}"
        )
        assistant = f"REINFORCE | {pat['bot_action']} | label={pat['label']}"
        rows.append(_gold_row(
            capability="trade_reflex",
            instruction="Learn what productive turnover looks like from commander wins.",
            input_text=user,
            output_text=assistant,
            meta={"outcome_label": pat["label"], "pattern_id": pat["id"]},
        ))
    return rows


def build_trade_case_gold() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for case in TRADE_CASES:
        win = case["outcome"] == "win"
        user = (
            f"Case study | {case['ticker']} ({case['sector']})\n"
            f"Return {case['return_pct']:+.2f}% | contribution {case['contribution_pct']:+.2f}% "
            f"| realized ${case['realized_usd']:+.2f}\n"
            f"Human error: {case['human_error'] or 'none — good execution'}\n"
            f"Task: entry_decision + exit_decision review"
        )
        if win:
            assistant = (
                f"ENTER on vol spike | EXIT spike_top | "
                f"REINFORCE: {case['correct']} | label=good_cut"
            )
            cap = "trade_reflex"
        else:
            if case.get("human_error", "").startswith("Entered THIS setup below 80%"):
                assistant = (
                    f"SKIP THIS SETUP | confidence=0.90 | "
                    f"Mistake: {case['human_error']} | "
                    f"Fix: {case['correct']} | "
                    f"ticker_stays_tradeable=true | label=traded_without_edge"
                )
                cap = "decision_text"
            else:
                assistant = (
                    f"LEARN EXECUTION | EXIT earlier on this trip | "
                    f"Mistake: {case['human_error']} | "
                    f"Fix: {case['correct']} | "
                    f"ticker_stays_tradeable=true | label=held_too_long"
                )
                cap = "exit_decision"
        rows.append(_gold_row(
            capability=cap,
            instruction="Review commander trade case — teach algo to beat human.",
            input_text=user,
            output_text=assistant,
            meta={
                "ticker": case["ticker"],
                "sector": case["sector"],
                "outcome_label": "good_cut" if win else "held_too_long",
                "pnl_usd": case["realized_usd"],
                "win": win,
            },
        ))
    return rows


def build_entry_skip_gold() -> List[Dict[str, Any]]:
    """PPO/Halim coevolution-style skip/enter pairs."""
    rows: List[Dict[str, Any]] = []
    scenarios = [
        (
            "spike=2.8x scan=85 profit_prob=0.92 fakeout=0.15 sector=Technology conviction=94%",
            "ENTER | confidence=0.94 | Calculated lottery — commander 90–97% tier. Exit spike_top.",
            True,
        ),
        (
            "spike=2.1x scan=72 profit_prob=0.81 fakeout=0.24 sector=Basic Materials conviction=82%",
            "ENTER | confidence=0.82 | Calculated lottery minimum tier — vol+scan pass.",
            True,
        ),
        (
            "spike=1.3x scan=48 profit_prob=0.42 fakeout=0.58 conviction=45%",
            "SKIP | confidence=0.92 | Below 80% — NOT calculated lottery (SMTK-class mistake).",
            False,
        ),
        (
            "spike=2.3x scan=76 profit_prob=0.83 fakeout=0.20 ticker=PLUG sector=Energy conviction=88%",
            "ENTER | confidence=0.88 | Calculated lottery on PLUG — volatile energy OK when setup passes; EXIT spike_top.",
            True,
        ),
        (
            "spike=1.1x scan=45 profit_prob=0.38 fakeout=0.55 ticker=PLUG conviction=42%",
            "SKIP | confidence=0.88 | Weak THIS setup (<80%) — not a PLUG ban; wait for next spike.",
            False,
        ),
        (
            "open_pnl=-4.2% ticker=CTEV peak_pnl=+1.1% hold_sec=600",
            "EXIT | confidence=0.93 | Hope-hold mistake on CTEV trip — cut now; symbol OK on next 80%+ setup.",
            False,
        ),
        (
            "spike=2.4x scan=78 profit_prob=0.72 fakeout=0.22 sector=Basic Materials attribution=+13.6%",
            "ENTER | confidence=0.84 | Productive turnover — vol + sector tailwind.",
            True,
        ),
        (
            "open_pnl=-2.8% peak_pnl=+0.5% hold_sec=420 exit_reason=none",
            "EXIT | confidence=0.91 | Human hope hold — cut before tail loss.",
            False,
        ),
        (
            "open_pnl=+1.4% peak_pnl=+2.1% vol_fade=true spike_was=2.8x",
            "EXIT | confidence=0.86 | Spike top — lock before giveback.",
            False,
        ),
        (
            "session_loss_streak=3 fee_negative_trips=2",
            "STAND_ASIDE | confidence=0.80 | Pause entries — human would overtrade.",
            False,
        ),
    ]
    for ctx, decision, enter in scenarios:
        user = f"Entry/exit reflex check\n{ctx}\nSource: commander IB lessons"
        rows.append(_gold_row(
            capability="decision_text",
            instruction="Decide enter, skip, exit, or stand aside — beat human errors.",
            input_text=user,
            output_text=decision,
            meta={"enter": enter, "source_detail": "commander_scenario"},
        ))
        rows.append(_gold_row(
            capability="trade_reflex",
            instruction="PPO reflex — correct action from commander failure gold.",
            input_text=user,
            output_text=f"Better action: {enter} — {decision}",
            meta={"enter": enter, "source_detail": "commander_ppo_reflex"},
        ))
    return rows


def _teacher_from_failure_rule(rule: Dict[str, Any]) -> Tuple[int, str]:
    """Map failure rule to PPO teacher label — EXIT mistakes, not symbol SKIP."""
    ba = (rule.get("bot_action") or "").lower()
    if ba.startswith("exit") or "exit " in ba or "cut at" in ba:
        return 2, "SELL"
    if ba.startswith("enter"):
        return 1, "BUY"
    return 0, "SKIP"


def build_experience_records() -> List[Dict[str, Any]]:
    """Teacher-labeled buffer rows for reward-linked PPO."""
    records: List[Dict[str, Any]] = []
    ts = datetime.now(timezone.utc).isoformat()
    for case in TRADE_CASES:
        win = case["outcome"] == "win"
        notional = abs(case["realized_usd"]) / max(abs(case["return_pct"]) / 100, 0.01)
        fee_est = notional * 0.002
        net = case["realized_usd"] - fee_est
        records.append({
            "source": "commander_ib_gold",
            "ticker": case["ticker"],
            "action": "TRADE",
            "sector": case["sector"],
            "pnl_usd": round(case["realized_usd"], 2),
            "net_pnl_usd": round(net, 2),
            "win": win,
            "reward": round(max(-1.0, min(1.0, net / 45.0)), 4),
            "teacher_reward": round(max(-1.0, min(1.0, net / 45.0)), 4),
            "teacher_action": 1 if win else 2,
            "outcome_label": "good_cut" if win else "disaster_tail_loss" if case["contribution_pct"] < -8 else "held_too_long",
            "confidence": 0.85 if win else 0.9,
            "timestamp": ts,
            "learning_day": REPORT_META["period_end"],
            "report": REPORT_META["report"],
        })
    for rule in HUMAN_FAILURE_RULES:
        ta, act = _teacher_from_failure_rule(rule)
        records.append({
            "source": "commander_ib_gold",
            "action": act,
            "ticker": "",
            "win": False,
            "reward": -0.65 if ta != 1 else 0.5,
            "teacher_reward": -0.65 if ta != 1 else 0.5,
            "teacher_action": ta,
            "reason": rule["lesson"][:300],
            "outcome_label": rule["label"],
            "timestamp": ts,
            "learning_day": REPORT_META["period_end"],
        })
    for case in LOTTERY_WIN_CASES:
        records.append({
            "source": "commander_ib_gold",
            "ticker": case["ticker"],
            "action": "BUY",
            "win": True,
            "reward": 0.75,
            "teacher_reward": 0.75,
            "teacher_action": 1,
            "outcome_label": "calculated_lottery_win",
            "confidence": case["conviction_est"] / 100.0,
            "reason": case["setup"],
            "timestamp": ts,
            "learning_day": REPORT_META["period_end"],
        })
    for case in LOTTERY_FAIL_CASES:
        weak = case.get("failure_mode") == "weak_setup"
        records.append({
            "source": "commander_ib_gold",
            "ticker": case["ticker"],
            "action": "SKIP" if weak else "SELL",
            "win": False,
            "reward": -0.55 if weak else -0.75,
            "teacher_reward": -0.55 if weak else -0.75,
            "teacher_action": 0 if weak else 2,
            "outcome_label": "traded_without_edge" if weak else "held_too_long",
            "confidence": 0.9,
            "reason": case["error"],
            "ticker_not_banned": True,
            "timestamp": ts,
            "learning_day": REPORT_META["period_end"],
        })
    return records


def _write_gold_rows(rows: List[Dict[str, Any]], *, force: bool = False) -> Tuple[int, int]:
    if force:
        COMMANDER_GOLD.parent.mkdir(parents=True, exist_ok=True)
        COMMANDER_GOLD.write_text("", encoding="utf-8")
        COMMANDER_HASHES.write_text("", encoding="utf-8")
    hashes = _load_hashes() if not force else set()
    added = skipped = 0
    for row in rows:
        h = _row_key(row)
        if h in hashes:
            skipped += 1
            continue
        hashes.add(h)
        _append(COMMANDER_HASHES, h)
        _append(COMMANDER_GOLD, row)
        added += 1
    return added, skipped


def _seed_experience_buffer(records: List[Dict[str, Any]]) -> int:
    try:
        from core.experience_buffer import append as buffer_append
    except Exception:
        return 0
    count = 0
    for rec in records:
        buffer_append(rec)
        count += 1
    return count


def _append_guidelines() -> None:
    path = _REPO / "models/ai_guidelines.txt"
    block = [
        "",
        f"🧭 COMMANDER IB REPORT GOLD | {datetime.now(timezone.utc).isoformat()}",
        f"Period {REPORT_META['period_start']} → {REPORT_META['period_end']} | +{REPORT_META['cumulative_return_pct']}% TWR",
        "Algo must beat human: cut tail losses, fee-aware turnover, stand aside when edge gone.",
        "• Learn EXECUTION per trip — never blacklist CTEV/PLUG/ENVB; commander profits vol names on other trips",
        "• PLUG loss trip = hope-hold; USEG +3.21% same sector = disciplined enter/exit works",
        "• Max single-trip loss ~3% equity — human had −13% from holding, not from entering",
        "• SKIP only weak setups (<80% conviction), not symbols with prior losses",
        "• Pause entries after loss streak — human overtraded; ended flat in cash",
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(block) + "\n")
    except Exception as exc:
        log.debug(f"Commander guidelines append: {exc}")


def ingest_commander_ib_lessons(
    cfg: Optional[BotConfig] = None,
    *,
    force: bool = False,
    seed_buffer: bool = True,
    append_guidelines: bool = True,
) -> Dict[str, Any]:
    """
    Write commander IB report lessons to commander_gold.jsonl, experience buffer,
    and optional ai_guidelines. Idempotent unless force=True.
    """
    cfg = cfg or BotConfig()
    if not _enabled():
        return {"ok": False, "reason": "COMMANDER_IB_GOLD disabled"}

    all_rows: List[Dict[str, Any]] = []
    all_rows.extend(build_principle_gold())
    all_rows.extend(build_failure_gold())
    all_rows.extend(build_winner_gold())
    all_rows.extend(build_lottery_gold())
    all_rows.extend(build_trade_case_gold())
    all_rows.extend(build_entry_skip_gold())

    added, skipped = _write_gold_rows(all_rows, force=force)

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "commander_ib_gold_ingest",
        "report": REPORT_META["report"],
        "rows_built": len(all_rows),
        "rows_added": added,
        "rows_skipped": skipped,
        "force": force,
    }
    _append(COMMANDER_EVENTS, event)

    buffer_n = 0
    if seed_buffer:
        buffer_n = _seed_experience_buffer(build_experience_records())

    if append_guidelines and added > 0:
        _append_guidelines()

    log.info(
        f"📚 Commander IB gold: +{added} rows ({skipped} skipped) | buffer +{buffer_n}"
    )
    return {
        "ok": True,
        "report": REPORT_META,
        "gold_added": added,
        "gold_skipped": skipped,
        "gold_total_built": len(all_rows),
        "buffer_seeded": buffer_n,
        "path": str(COMMANDER_GOLD),
    }


def export_commander_gold() -> Dict[str, Any]:
    total = 0
    if COMMANDER_GOLD.is_file():
        with open(COMMANDER_GOLD, encoding="utf-8") as fh:
            total = sum(1 for _ in fh)
    return {"ok": True, "total_gold": total, "path": str(COMMANDER_GOLD)}
