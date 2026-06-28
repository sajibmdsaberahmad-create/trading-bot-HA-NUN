#!/usr/bin/env python3
"""
core/halim_learn_browse.py — Off-hours knowledge harvest for Halim action gold.

Read-only: Wikipedia, investopedia/SEC/docs, RSS news, market-hours brief, Google snippets.
Never runs during live/replay trading unless HALIM_LEARN_DURING_TRADING=true.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.halim_learn_catalog import build_learn_topic_pool, pick_google_queries
from core.notify import log

_TOPIC_OFFSET_PATH = Path("models/halim_learn_topic_offset.txt")
_LEARN_LOOP_PID_PATH = Path("models/halim_learn_loop.pid")


def _pick_rotated_topics(cap: int) -> List[str]:
    """Rotate through the full topic pool so each batch reads different pages."""
    all_topics = build_learn_topic_pool()
    if not all_topics:
        return []
    offset = 0
    if _TOPIC_OFFSET_PATH.is_file():
        try:
            offset = int(_TOPIC_OFFSET_PATH.read_text().strip()) % len(all_topics)
        except Exception:
            offset = 0
    picked = [all_topics[(offset + i) % len(all_topics)] for i in range(min(cap, len(all_topics)))]
    new_offset = (offset + len(picked)) % len(all_topics)
    try:
        _TOPIC_OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOPIC_OFFSET_PATH.write_text(str(new_offset), encoding="utf-8")
    except Exception:
        pass
    return picked


def is_learn_loop_active() -> bool:
    """True while LEARN_START / learn browse loop is running."""
    if not _LEARN_LOOP_PID_PATH.is_file():
        return False
    try:
        pid = int(_LEARN_LOOP_PID_PATH.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        try:
            _LEARN_LOOP_PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _learn_loop_mark_active() -> None:
    try:
        _LEARN_LOOP_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LEARN_LOOP_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def _learn_loop_mark_inactive() -> None:
    try:
        _LEARN_LOOP_PID_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _flush_deferred_retrain(cfg: BotConfig) -> None:
    try:
        from core.halim_auto_lm import finalize_learn_session
        finalize_learn_session(cfg)
    except Exception as exc:
        log.debug(f"Halim learn finalize: {exc}")


def _trading_blocks_learn(force: bool = False) -> Optional[Dict[str, Any]]:
    try:
        from core.trading_focus_guard import is_trading_session_active
        if is_trading_session_active() and not force:
            if os.getenv("HALIM_LEARN_DURING_TRADING", "false").lower() not in ("1", "true", "yes"):
                return {
                    "ok": False,
                    "reason": "trading_active",
                    "message": "Trading/replay is running — learn paused (full focus on algo). "
                    "Stop trading or set HALIM_LEARN_DURING_TRADING=true.",
                }
    except Exception:
        pass
    return None


def _fetch_one(topic: str, cfg: BotConfig) -> Dict[str, Any]:
    from core.halim_web_learn import (
        fetch_learn_page,
        fetch_market_hours_learn,
        fetch_rss_learn,
        fetch_wikipedia_summary,
    )

    topic = topic.strip()
    if not topic:
        return {"ok": False, "reason": "empty_topic", "topic": topic}
    if topic.startswith("local:commander_ib_report"):
        from core.halim_commander_report_learn import fetch_commander_report_learn
        return {**fetch_commander_report_learn(cfg), "topic": topic}
    if topic.startswith("local:market_hours"):
        return {**fetch_market_hours_learn(cfg), "topic": topic}
    if topic.startswith("rss:"):
        url = topic[4:].strip()
        return {**fetch_rss_learn(url, cfg, topic=topic), "topic": topic}
    if topic.startswith("wiki:"):
        return {**fetch_wikipedia_summary(topic[5:], cfg), "topic": topic}
    if topic.startswith("http://") or topic.startswith("https://"):
        return {**fetch_learn_page(topic, cfg, topic=topic), "topic": topic}
    return {**fetch_wikipedia_summary(topic, cfg), "topic": f"wiki:{topic}"}


def _google_snippet(query: str, cfg: BotConfig) -> Dict[str, Any]:
    if os.getenv("HALIM_GOOGLE_AI_SEARCH", "true").lower() not in ("1", "true", "yes"):
        return {"ok": False, "reason": "google_search_disabled", "query": query}
    try:
        from core.halim_google_ai_search import query_google_ai_answer
        return query_google_ai_answer(query, cfg=cfg)
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:80], "query": query}


def run_learn_browse_cycle(
    cfg: Optional[BotConfig] = None,
    *,
    topics: Optional[List[str]] = None,
    max_pages: Optional[int] = None,
    google_queries: Optional[List[str]] = None,
    export_gold: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Browse allowlisted web + record action gold. Best during IB maintenance / market closed.
    """
    cfg = cfg or BotConfig()

    blocked = _trading_blocks_learn(force=force)
    if blocked:
        return blocked

    if os.getenv("HALIM_WEB_LEARN", "true").lower() not in ("1", "true", "yes"):
        return {"ok": False, "reason": "HALIM_WEB_LEARN_disabled"}

    try:
        from core.halim_guardrails import apply_operator_frontier_settings
        apply_operator_frontier_settings(cfg)
    except Exception:
        pass

    cap = max_pages or int(os.getenv("HALIM_LEARN_BATCH_MAX", "8"))
    pause = float(os.getenv("HALIM_LEARN_BATCH_PAUSE_SEC", "2.0"))
    if topics is not None:
        topic_list = topics[:cap]
    else:
        topic_list = _pick_rotated_topics(cap)

    fetched: List[Dict[str, Any]] = []
    ok_count = 0
    total_chars = 0

    log.info(f"📚 Halim learn browse — {len(topic_list)} source(s), multi-domain allowlist")

    for i, topic in enumerate(topic_list):
        if i > 0 and pause > 0:
            time.sleep(pause)
        r = _fetch_one(topic, cfg)
        fetched.append(r)
        if r.get("ok"):
            ok_count += 1
            total_chars += int(r.get("text_chars") or 0)

    google_results: List[Dict[str, Any]] = []
    if google_queries is None and os.getenv("HALIM_LEARN_GOOGLE_SNIPPETS", "true").lower() in (
        "1", "true", "yes",
    ):
        g_cap = int(os.getenv("HALIM_LEARN_GOOGLE_MAX", "3"))
        google_queries = pick_google_queries(g_cap)
    g_cap = int(os.getenv("HALIM_LEARN_GOOGLE_MAX", "3"))
    for q in (google_queries or [])[:g_cap]:
        gr = _google_snippet(q, cfg)
        google_results.append(gr)
        if gr.get("ok") and gr.get("answer"):
            try:
                from core.halim_action_learn import record_action
                record_action(
                    "read_understand",
                    "google_ai_overview",
                    input_text=q[:500],
                    output_text=str(gr.get("answer", ""))[:2000],
                    outcome="ok",
                    source="google_ai_search",
                    cfg=cfg,
                )
            except Exception:
                pass
        time.sleep(pause)

    gold: Dict[str, Any] = {}
    if export_gold:
        try:
            from core.halim_action_learn import export_action_gold
            gold = export_action_gold(include_learn_cache=True)
        except Exception as exc:
            gold = {"error": str(exc)[:80]}

    summary = {
        "ok": ok_count > 0 or bool(gold.get("added")),
        "pages_attempted": len(topic_list),
        "pages_ok": ok_count,
        "total_chars": total_chars,
        "google_attempted": len(google_results),
        "google_ok": sum(1 for g in google_results if g.get("ok")),
        "export_gold": gold,
        "topics": topic_list,
        "fetched": [
            {
                "topic": f.get("topic"),
                "ok": f.get("ok"),
                "url": f.get("final_url") or f.get("url"),
                "chars": f.get("text_chars"),
                "reason": f.get("reason"),
            }
            for f in fetched
        ],
    }
    cap_hits = sum(1 for f in fetched if f.get("reason") == "learn_fetch_daily_cap")
    if cap_hits and cap_hits == len(fetched):
        summary["reason"] = "learn_fetch_daily_cap"
        summary["ok"] = False
        msg = (
            "Daily learn fetch cap reached — restart tomorrow or raise "
            "HALIM_LEARN_FETCH_DAILY_CAP in scripts/halim_env.sh"
        )
        log.warning(f"📚 Halim learn browse — {msg}")
        print(f"⚠️  {msg}", flush=True)
    log.info(
        f"📚 Halim learn browse done — {ok_count}/{len(topic_list)} pages, "
        f"{total_chars} chars, gold+{gold.get('added', 0)}"
    )
    return summary


def run_learn_browse_loop(
    cfg: Optional[BotConfig] = None,
    *,
    max_cycles: Optional[int] = None,
) -> None:
    """
    Continuous maintenance learn — rotates topics each cycle until trading starts or Ctrl+C.
    """
    cfg = cfg or BotConfig()
    pause = float(os.getenv("HALIM_LEARN_LOOP_PAUSE_SEC", "0"))
    cycle = 0
    total_added = 0
    total_pages = 0

    _learn_loop_mark_active()
    try:
        from core.halim_guardrails import learn_uncapped_active, effective_learn_fetch_daily_cap, learn_gold_budget_remaining
        if learn_uncapped_active():
            log.info(
                f"📚 Learn UNCAPPED today — fetch cap {effective_learn_fetch_daily_cap()}, "
                f"gold budget left {learn_gold_budget_remaining()}"
            )
    except Exception:
        pass
    log.info(
        f"📚 Halim learn loop started — batch={os.getenv('HALIM_LEARN_BATCH_MAX', '8')}, "
        f"{'back-to-back batches' if pause <= 0 else f'pause={pause:.0f}s between batches'} "
        "(Ctrl+C to stop)"
    )

    try:
        while True:
            blocked = _trading_blocks_learn()
            if blocked:
                log.info(f"📚 Halim learn loop stopped — {blocked.get('reason')}")
                print(blocked.get("message", "Learn loop stopped."))
                break

            cycle += 1
            if max_cycles is not None and cycle > max_cycles:
                break

            print(f"\n── Cycle {cycle} ──", flush=True)
            r = run_learn_browse_cycle(cfg)
            if r.get("reason") == "learn_fetch_daily_cap":
                print("Learn loop stopping — daily fetch cap reached.", flush=True)
                break
            try:
                from core.halim_guardrails import learn_gold_budget_remaining, learn_uncapped_active
                if learn_uncapped_active() and learn_gold_budget_remaining() <= 0:
                    print(
                        "Learn loop stopping — daily gold export cap reached (dedup + anti-overfit).",
                        flush=True,
                    )
                    break
            except Exception:
                pass
            if not r.get("ok") and r.get("reason") not in (None, "trading_active"):
                log.warning(f"📚 Halim learn cycle {cycle} issue: {r.get('reason')}")
                break

            added = int((r.get("export_gold") or {}).get("added", 0))
            pages = int(r.get("pages_ok", 0))
            total_added += added
            total_pages += pages
            print(
                f"Cycle {cycle}: {pages} pages, gold +{added} "
                f"(session total: {total_pages} pages, +{total_added} gold)",
                flush=True,
            )

            if max_cycles is not None and cycle >= max_cycles:
                break

            if pause > 0:
                log.info(f"📚 Halim learn loop — sleeping {pause:.0f}s before next batch…")
                slept = 0.0
                while slept < pause:
                    chunk = min(15.0, pause - slept)
                    try:
                        time.sleep(chunk)
                    except KeyboardInterrupt:
                        raise
                    slept += chunk
                    if _trading_blocks_learn():
                        log.info("📚 Halim learn loop stopped — trading started")
                        print("Trading started — learn loop stopping (algo has full focus).")
                        return
            elif _trading_blocks_learn():
                log.info("📚 Halim learn loop stopped — trading started")
                print("Trading started — learn loop stopping (algo has full focus).")
                return

        print(f"\n✅ Learn loop finished — {cycle} cycle(s), {total_pages} pages, gold +{total_added}")

    except KeyboardInterrupt:
        print(
            f"\n⏹ Learn loop stopped (Ctrl+C) — {cycle} cycle(s), "
            f"{total_pages} pages, gold +{total_added}",
            flush=True,
        )
        log.info("📚 Halim learn loop interrupted by user")
    finally:
        _learn_loop_mark_inactive()
        if total_added > 0 or cycle > 0:
            print("📦 Rebuilding halim_sft.zip for Colab…", flush=True)
        _flush_deferred_retrain(cfg)


def parse_learn_command(text: str) -> Dict[str, Any]:
    """Parse /learn or natural-language learn requests."""
    low = (text or "").strip().lower()
    if low.startswith("/learn"):
        rest = text.strip()[6:].strip()
        if not rest or rest.lower() in ("batch", "all", "browse", "start"):
            return {"mode": "batch"}
        if rest.startswith("wiki:") or rest.startswith("rss:") or rest.startswith("local:"):
            return {"mode": "single", "topic": rest}
        if rest.startswith("http"):
            return {"mode": "single", "topic": rest}
        return {"mode": "single", "topic": f"wiki:{rest.replace(' ', '_')}"}
    triggers = (
        "browse", "learn about", "gain knowledge", "read wikipedia",
        "study ", "research ", "harvest gold", "earn gold",
    )
    if any(t in low for t in triggers):
        for prefix in ("learn about ", "study ", "research ", "browse "):
            if prefix in low:
                subject = low.split(prefix, 1)[-1].strip(" .!?")
                if subject and subject not in ("the internet", "internet", "everything"):
                    return {"mode": "single", "topic": f"wiki:{subject.replace(' ', '_')}"}
        return {"mode": "batch"}
    return {"mode": "none"}
