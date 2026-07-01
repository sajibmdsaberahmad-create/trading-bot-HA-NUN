#!/usr/bin/env python3
"""
core/halim_json_entry_gold.py — Curated entry_decision JSON gold for Halim v5+ SFT.

Builds train pairs where the assistant is ONE valid JSON object matching the live
entry LM contract (halim_entry_line._build_entry_prompt). Optional Groq/Gemini
teacher pass labels ambiguous spikes for richer v5 curriculum.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log
from core.halim_guardrails import v5_prep_active
from core.training_dataset_paths import council_training_dataset_path

JSON_ENTRY_GOLD = Path("halim/data/training/json_entry_gold.jsonl")
JSON_ENTRY_HASHES = Path("halim/data/training/json_entry_gold_hashes.jsonl")

_ENTRY_USER_PREFIX = (
    "You are M. A. Halim — owned trading mind. Reply JSON only, no markdown.\n"
    '{"enter":true|false,"confidence":0.0-1.0,"reason":"max 10 words"}\n'
)


def build_entry_user_prompt(
    *,
    ticker: str,
    price: float = 0.0,
    spike: float = 1.0,
    scan: float = 0.0,
    ppo_buy: bool = False,
    ppo_conf: float = 0.5,
    ppo_reason: str = "",
    profit_prob: float = 0.0,
    enter_ok: bool = True,
    fakeout_risk: float = 0.0,
    setup_type: str = "",
    ib_context: str = "",
    outcome_hint: str = "",
) -> str:
    """Mirror live Halim entry LM user text (halim_entry_line._build_entry_prompt)."""
    ppo_side = "buy" if ppo_buy else "hold"
    quality_line = ""
    if profit_prob > 0:
        quality_line = (
            f"quality profit_prob={profit_prob:.2f} enter_ok={str(enter_ok).lower()} "
            f"fakeout={fakeout_risk:.2f} setup={setup_type or 'mixed'}\n"
        )
    ib_line = f"{ib_context.strip()}\n" if ib_context else ""
    hint_line = f"{outcome_hint.strip()}\n" if outcome_hint else ""
    body = (
        f"ENTRY {ticker.upper()} price={price:.4f} spike={spike:.2f}x score={scan:.0f}\n"
        f"ppo={ppo_side} conf={ppo_conf:.2f} note={ppo_reason[:50]}\n"
        f"{quality_line}{ib_line}{hint_line}"
        "Reply ONE json object only. No other text.\n"
        '{"enter":true,"confidence":0.72,"reason":"calculated lottery full_bullet"}\n'
        '{"enter":false,"confidence":0.55,"reason":"chop fakeout skip"}\n'
        "enter=true when profit_prob high and momentum clean; false on chop/fakeout."
    )
    return _ENTRY_USER_PREFIX + body


def format_entry_json_assistant(
    *,
    enter: bool,
    confidence: float,
    reason: str,
) -> Optional[str]:
    """Single-line JSON assistant target; None if invalid."""
    conf = float(confidence)
    if conf > 1.0:
        conf /= 100.0
    conf = round(min(0.99, max(0.05, conf)), 2)
    reason_clean = re.sub(r"\s+", " ", (reason or "").strip())[:60]
    if not reason_clean:
        reason_clean = "clean momentum" if enter else "chop skip"
    blob = {"enter": bool(enter), "confidence": conf, "reason": reason_clean}
    text = json.dumps(blob, separators=(",", ":"))
    parsed = parse_entry_json_assistant(text)
    return text if parsed else None


def parse_entry_json_assistant(text: str) -> Optional[Dict[str, Any]]:
    """Validate assistant JSON for SFT inclusion."""
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "enter" not in obj:
        return None
    enter = bool(obj["enter"])
    try:
        conf = float(obj.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if conf > 1.0:
        conf /= 100.0
    if not (0.0 <= conf <= 1.0):
        return None
    reason = str(obj.get("reason", "")).strip()
    if not reason or len(reason) > 80:
        return None
    if re.search(r"agree=False|entry_decision is not", reason, re.I):
        return None
    return {"enter": enter, "confidence": round(conf, 2), "reason": reason}


def _row_hash(user: str, assistant: str) -> str:
    key = f"{user[:500]}|{assistant}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _load_hashes() -> set:
    seen: set = set()
    if not JSON_ENTRY_HASHES.is_file():
        return seen
    for line in JSON_ENTRY_HASHES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            seen.add(line)
    return seen


def _append_hash(h: str) -> None:
    JSON_ENTRY_HASHES.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_ENTRY_HASHES, "a", encoding="utf-8") as fh:
        fh.write(h + "\n")


def _gold_row(
    *,
    user: str,
    assistant: str,
    source: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not parse_entry_json_assistant(assistant):
        return None
    instruction = "Halim entry_decision — reply one JSON object only."
    return {
        "capability": "entry_decision",
        "instruction": instruction,
        "input": user.strip()[:8000],
        "output": assistant.strip(),
        "source": source,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are M. A. Halim — HANOON's owned mind. Trade reasoning, briefings, and "
                    "decisions in concise, actionable language. Respect risk guardrails."
                ),
            },
            {"role": "user", "content": user.strip()[:8000]},
            {"role": "assistant", "content": assistant.strip()},
        ],
        **(meta or {}),
    }


def _parse_spike_score(reason: str) -> Tuple[float, float]:
    spike = 1.0
    score = 0.0
    m = re.search(r"vol=([\d.]+)x", reason or "", re.I)
    if m:
        try:
            spike = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"score=(\d+(?:\.\d+)?)", reason or "", re.I)
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"prob=(\d+(?:\.\d+)?)%", reason or "", re.I)
    profit_prob = float(m.group(1)) / 100.0 if m else 0.0
    return spike, score if score else 0.0


def _parse_profit_prob(reason: str) -> float:
    m = re.search(r"prob=(\d+(?:\.\d+)?)%", reason or "", re.I)
    if m:
        try:
            return float(m.group(1)) / 100.0
        except ValueError:
            pass
    return 0.0


def _outcome_enter_from_labels(labels: List[str], pnl_usd: float = 0.0) -> Optional[bool]:
    if "good_entry_call" in labels or "missed_entry" in labels:
        return True
    if "good_skip_call" in labels or "bad_entry_call" in labels:
        return False
    if pnl_usd > 0.5:
        return True
    if pnl_usd < -0.5:
        return False
    return None


def _reason_from_outcome(labels: List[str], pnl_usd: float) -> str:
    if "good_skip_call" in labels:
        return "chop fakeout skip"
    if "bad_entry_call" in labels:
        return "weak setup skip"
    if "good_entry_call" in labels:
        return "clean momentum scalp"
    if "missed_entry" in labels:
        return "momentum scalp missed"
    if pnl_usd >= 0:
        return "quality momentum"
    return "chop skip"


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _rows_from_council(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    path = council_training_dataset_path()
    if not path.is_file():
        mirror = root / "halim/data/trading/council_training_dataset.jsonl"
        path = mirror if mirror.is_file() else path
    for raw in _iter_jsonl(path):
        ticker = str(raw.get("ticker", "")).upper()
        if not ticker:
            continue
        enter = bool(raw.get("teacher_enter"))
        conf = float(raw.get("teacher_confidence", 0) or 0)
        reason = str(raw.get("teacher_reason", ""))
        spike, score = _parse_spike_score(reason)
        profit_prob = _parse_profit_prob(reason)
        outcome = raw.get("outcome") or {}
        pnl = float(outcome.get("pnl_usd", 0) or 0)
        win = outcome.get("win")

        # Outcome-aware relabel when we know result
        if outcome and win is not None:
            if enter and pnl < -0.25:
                enter, conf = False, max(0.55, conf * 0.85)
            elif not enter and pnl > 0.5:
                enter, conf = True, max(conf, 0.62)

        assistant = format_entry_json_assistant(
            enter=enter,
            confidence=conf,
            reason=reason[:40] or ("momentum" if enter else "skip chop"),
        )
        if not assistant:
            continue
        hint = ""
        if outcome:
            hint = f"outcome pnl=${pnl:+.2f} win={win}"
        user = build_entry_user_prompt(
            ticker=ticker,
            spike=spike,
            scan=score,
            ppo_buy=enter,
            ppo_conf=conf,
            ppo_reason=reason[:50],
            profit_prob=profit_prob,
            outcome_hint=hint,
        )
        row = _gold_row(
            user=user,
            assistant=assistant,
            source="json_entry:council",
            meta={"ticker": ticker, "timestamp": raw.get("timestamp")},
        )
        if row:
            rows.append(row)
    return rows


def _rows_from_outcome_gold(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    path = root / "halim/data/training/outcome_gold.jsonl"
    for raw in _iter_jsonl(path):
        if raw.get("capability") != "enter_skip":
            continue
        inp = str(raw.get("input", ""))
        labels = list(raw.get("outcome_labels") or [])
        if raw.get("outcome_label"):
            labels.append(str(raw.get("outcome_label")))
        pnl = float(raw.get("pnl_usd", 0) or 0)
        enter_opt = _outcome_enter_from_labels(labels, pnl)
        if enter_opt is None:
            continue
        ticker_m = re.search(r"Ticker:\s*([A-Z]+)", inp)
        ticker = ticker_m.group(1) if ticker_m else ""
        spike_m = re.search(r"spike=([\d.]+)x", inp)
        scan_m = re.search(r"scan=(\d+)", inp)
        conf_m = re.search(r"conf\s*(\d+)%", inp)
        spike = float(spike_m.group(1)) if spike_m else 1.0
        scan = float(scan_m.group(1)) if scan_m else 0.0
        conf = float(conf_m.group(1)) / 100.0 if conf_m else (0.65 if enter_opt else 0.55)
        assistant = format_entry_json_assistant(
            enter=enter_opt,
            confidence=conf,
            reason=_reason_from_outcome(labels, pnl),
        )
        if not assistant or not ticker:
            continue
        user = build_entry_user_prompt(
            ticker=ticker,
            spike=spike,
            scan=scan,
            ppo_buy=enter_opt,
            ppo_conf=conf,
            outcome_hint=f"closed pnl=${pnl:+.2f} labels={','.join(labels[:3])}",
        )
        row = _gold_row(
            user=user,
            assistant=assistant,
            source="json_entry:outcome",
            meta={"ticker": ticker, "outcome_labels": labels},
        )
        if row:
            rows.append(row)
    return rows


def _rows_from_experience_buffer(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rel in ("models/experience_buffer.jsonl", "halim/data/trading/experience_buffer.jsonl"):
        path = root / rel
        if not path.is_file():
            continue
        for raw in _iter_jsonl(path):
            if raw.get("source") != "ppo_entry" or raw.get("event") != "entry_fill":
                continue
            ticker = str(raw.get("ticker", "")).upper()
            if not ticker:
                continue
            dec = raw.get("council_decision") or {}
            enter = bool(dec.get("enter"))
            conf = float(dec.get("confidence", raw.get("ppo_conf", 0.5)) or 0.5)
            reason = str(dec.get("reason", ""))
            spike = float(raw.get("spike_ratio", 1) or 1)
            scan = float(raw.get("scan_score", 0) or 0)
            price = float(raw.get("entry_price", dec.get("entry", 0)) or 0)
            assistant = format_entry_json_assistant(
                enter=enter,
                confidence=conf,
                reason=reason[:40] or ("spike entry" if enter else "hold"),
            )
            if not assistant:
                continue
            user = build_entry_user_prompt(
                ticker=ticker,
                price=price,
                spike=spike,
                scan=scan,
                ppo_buy=int(raw.get("ppo_action", 0) or 0) == 1,
                ppo_conf=float(raw.get("ppo_conf", conf) or conf),
                ppo_reason=reason[:50],
            )
            row = _gold_row(
                user=user,
                assistant=assistant,
                source="json_entry:experience",
                meta={"ticker": ticker, "timestamp": raw.get("timestamp")},
            )
            if row:
                rows.append(row)
        break
    return rows


def _teacher_label_prompt(user: str, *, context: str = "") -> str:
    extra = f"\nContext: {context[:400]}" if context else ""
    return (
        f"{user.strip()}\n{extra}\n"
        "Reply with exactly ONE JSON object, no markdown, no explanation:\n"
        '{"enter":true|false,"confidence":0.0-1.0,"reason":"max 10 words"}'
    )


def _teacher_api_label(
    user: str,
    *,
    cfg: BotConfig,
    context: str = "",
    purpose: str = "halim_json_entry_teacher",
) -> Optional[str]:
    if os.getenv("HALIM_JSON_ENTRY_API", "false").lower() not in ("1", "true", "yes"):
        if not v5_prep_active():
            return None
    try:
        from core.council_client import CouncilClient

        client = CouncilClient(cfg)
        if not client.available():
            return None
        system = (
            "You are an expert scalping teacher for HANOON. "
            "Output exactly one JSON object with keys enter, confidence, reason. "
            "enter=false on chop, fakeout, low profit_prob; true on clean momentum only."
        )
        text = client.complete(
            _teacher_label_prompt(user, context=context),
            system=system,
            purpose=purpose,
            fast=True,
            priority=True,
        )
        if not text:
            return None
        # Extract first JSON object from response
        for m in re.finditer(r"\{[^{}]*\"enter\"\s*:\s*(true|false)[^{}]*\}", text, re.I):
            assistant = m.group(0)
            if parse_entry_json_assistant(assistant):
                return assistant
        cleaned = text.strip()
        if parse_entry_json_assistant(cleaned):
            return cleaned
    except Exception as exc:
        log.debug(f"json_entry teacher API: {exc}")
    return None


def export_json_entry_gold(
    *,
    root: Optional[Path] = None,
    use_api: Optional[bool] = None,
    api_max: Optional[int] = None,
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    """
    Append deduped JSON entry gold for Halim v5 SFT.
    Set HALIM_JSON_ENTRY_API=true or pass use_api=True for Groq/Gemini labels.
    """
    root = root or Path(__file__).resolve().parents[1]
    cfg = cfg or BotConfig()
    if use_api is None:
        use_api = os.getenv("HALIM_JSON_ENTRY_API", "false").lower() in ("1", "true", "yes")
    if api_max is None:
        api_max = int(os.getenv("HALIM_JSON_ENTRY_API_MAX", "120"))

    known = _load_hashes()
    candidates: List[Dict[str, Any]] = []
    candidates.extend(_rows_from_council(root))
    candidates.extend(_rows_from_outcome_gold(root))
    candidates.extend(_rows_from_experience_buffer(root))

    JSON_ENTRY_GOLD.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    skipped = 0
    api_calls = 0
    by_source: Dict[str, int] = {}

    def _commit(row: Dict[str, Any]) -> None:
        nonlocal added, skipped
        h = _row_hash(row["input"], row["output"])
        if h in known:
            skipped += 1
            return
        known.add(h)
        _append_hash(h)
        with open(JSON_ENTRY_GOLD, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        added += 1
        src = str(row.get("source", "?"))
        by_source[src] = by_source.get(src, 0) + 1

    for row in candidates:
        _commit(row)

    # API pass: enrich council rows that lack outcome (teacher refines enter/skip)
    if use_api and api_max > 0:
        path = council_training_dataset_path()
        for raw in _iter_jsonl(path):
            if api_calls >= api_max:
                break
            if raw.get("outcome"):
                continue
            ticker = str(raw.get("ticker", "")).upper()
            if not ticker:
                continue
            reason = str(raw.get("teacher_reason", ""))
            spike, score = _parse_spike_score(reason)
            profit_prob = _parse_profit_prob(reason)
            user = build_entry_user_prompt(
                ticker=ticker,
                spike=spike,
                scan=score,
                ppo_buy=bool(raw.get("teacher_enter")),
                ppo_conf=float(raw.get("teacher_confidence", 0.5) or 0.5),
                ppo_reason=reason[:50],
                profit_prob=profit_prob,
            )
            assistant = _teacher_api_label(
                user,
                cfg=cfg,
                context=f"pipeline={raw.get('teacher_pipeline','')} source={raw.get('source','')}",
            )
            if not assistant:
                continue
            api_calls += 1
            row = _gold_row(
                user=user,
                assistant=assistant,
                source="json_entry:teacher_api",
                meta={"ticker": ticker, "timestamp": raw.get("timestamp")},
            )
            if row:
                _commit(row)

    total = sum(1 for _ in _iter_jsonl(JSON_ENTRY_GOLD))
    if added:
        log.info(
            f"🧠 Halim JSON entry gold +{added} (api={api_calls}, skipped dup {skipped}, total {total})"
        )
        try:
            from core.halim_registry import append_registry

            append_registry(
                "export_json_entry_gold",
                {"added": added, "skipped": skipped, "api_calls": api_calls, "total": total},
            )
        except Exception:
            pass

    return {
        "ok": True,
        "added": added,
        "skipped": skipped,
        "api_calls": api_calls,
        "total_gold": total,
        "by_source": by_source,
        "path": str(JSON_ENTRY_GOLD),
    }


_TRADING_TOPIC_HINTS = (
    "trading", "scalp", "market", "stock", "risk", "momentum", "volatility",
    "chart", "candle", "spread", "liquidity", "entry", "exit", "finance",
)


def _learn_cache_files(root: Path) -> List[Path]:
    cache = root / "halim/data/learn_cache"
    if not cache.is_dir():
        return []
    files = sorted(cache.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def export_web_json_drills(
    *,
    root: Optional[Path] = None,
    cfg: Optional[BotConfig] = None,
    api_max: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Turn read-only web learn cache into JSON entry_decision drills via API teacher.
    Only runs when HALIM_JSON_ENTRY_API or HALIM_V5_PREP is enabled.
    """
    root = root or Path(__file__).resolve().parents[1]
    cfg = cfg or BotConfig()
    use_api = (
        os.getenv("HALIM_JSON_ENTRY_API", "false").lower() in ("1", "true", "yes")
        or v5_prep_active()
    )
    if not use_api:
        return {"ok": False, "reason": "api_disabled", "added": 0}
    if api_max is None:
        api_max = int(os.getenv("HALIM_V5_WEB_DRILL_MAX", "80"))

    known = _load_hashes()
    added = 0
    skipped = 0
    api_calls = 0

    for path in _learn_cache_files(root):
        if api_calls >= api_max:
            break
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        topic = str(raw.get("topic", "") or "")
        text = str(raw.get("text", "") or "")[:1200]
        if len(text) < 120:
            continue
        blob = (topic + " " + text).lower()
        if not any(h in blob for h in _TRADING_TOPIC_HINTS):
            continue
        ticker = "EDU"
        user = build_entry_user_prompt(
            ticker=ticker,
            spike=1.4,
            scan=42,
            ppo_buy=False,
            ppo_conf=0.52,
            ppo_reason="web learn drill",
            outcome_hint=f"study: {topic[:60]}",
            ib_context=text[:400],
        )
        assistant = _teacher_api_label(
            user,
            cfg=cfg,
            context=f"web_learn topic={topic[:80]}",
            purpose="halim_v5_web_drill",
        )
        if not assistant:
            continue
        api_calls += 1
        row = _gold_row(
            user=user,
            assistant=assistant,
            source="json_entry:web_drill",
            meta={"topic": topic[:80], "cache": path.name},
        )
        if not row:
            continue
        h = _row_hash(row["input"], row["output"])
        if h in known:
            skipped += 1
            continue
        known.add(h)
        _append_hash(h)
        JSON_ENTRY_GOLD.parent.mkdir(parents=True, exist_ok=True)
        with open(JSON_ENTRY_GOLD, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        added += 1

    if added:
        log.info(f"🧠 Halim web JSON drills +{added} (api={api_calls}, skipped dup {skipped})")
    return {"ok": True, "added": added, "skipped": skipped, "api_calls": api_calls}
