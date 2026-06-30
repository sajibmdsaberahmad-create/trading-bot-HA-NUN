#!/usr/bin/env python3
"""Extracted from scalper_runner — scalper spike loop."""

from __future__ import annotations

from core.scalper_mixin_imports import *  # noqa: F403

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    pass


class ScalperSpikeMixin:
    """Mixin — composed into ScalperRunner."""

    def _on_locked_stream_tick(self, ticker: str, price: float, _ts: Any) -> None:
        """Tick callback — queue spike entry or fast profit/loss exit (debounced)."""
        if price <= 0 or not tick_spike_monitor_enabled(self.cfg):
            return
        now = time.time()
        debounce = tick_spike_debounce_sec(self.cfg)
        if now - self._tick_spike_last_at.get(ticker, 0) < debounce:
            return
        self._tick_spike_last_at[ticker] = now

        if ticker in self._held_tickers():
            if now - self._tick_exit_last_at.get(ticker, 0) < debounce:
                return
            self._tick_exit_last_at[ticker] = now
            self._service_tick_position_exit(ticker, float(price))
            return

        if ticker == self._pending_entry_ticker:
            return
        if not any(t.ticker == ticker for t in self._locked_targets):
            return
        if self._open_position_count() >= self._max_concurrent():
            return
        if now < self._spike_attempt_until.get(ticker, 0):
            return

        min_bars = self._min_bars_for(ticker)
        df, live_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=min_bars)
        if df is None or len(df) < max(3, min_bars // 2):
            return

        is_spike, ratio = self._detect_volume_spike(df, min_period=min(20, max(6, min_bars)))
        is_spike, ratio = apply_micro_spike_boost(
            is_spike, ratio, forecast, cfg=self.cfg,
        )
        if not is_spike and dm:
            burst, br = self._detect_tick_volume_burst(dm, df)
            if burst:
                is_spike, ratio = True, br
        if not is_spike:
            return

        target = next((t for t in self._locked_targets if t.ticker == ticker), None)
        if target is None:
            return
        self._tick_spike_pending[ticker] = {
            "target": target,
            "ratio": ratio,
            "px": float(price or live_px),
            "forecast": forecast,
            "at": now,
        }
    def _service_tick_spike_queue(self) -> None:
        """Drain tick-triggered spike entries (runs on main loop thread)."""
        if not self._tick_spike_pending or self.risk.is_halted():
            return
        now = time.time()
        for ticker, pkt in sorted(
            self._tick_spike_pending.items(),
            key=lambda x: float(x[1].get("ratio", 0)),
            reverse=True,
        ):
            if now - float(pkt.get("at", 0)) > 3.0:
                self._tick_spike_pending.pop(ticker, None)
                continue
            if ticker in self._held_tickers():
                self._tick_spike_pending.pop(ticker, None)
                continue
            if self._pending_entry_ticker and self._pending_entry_ticker != ticker:
                continue
            if now < self._spike_attempt_until.get(ticker, 0):
                self._tick_spike_pending.pop(ticker, None)
                continue

            target = pkt["target"]
            ratio = float(pkt.get("ratio", 1.0))
            px = float(pkt.get("px", 0))
            fc = pkt.get("forecast") or {}
            self._tick_spike_pending.pop(ticker, None)
            self.top_pick = target
            self._last_entry_attempt_at = now
            self._spike_attempt_until[ticker] = now + spike_entry_cooldown_sec(self.cfg)
            log.info(
                f"⚡ TICK SPIKE: {ticker} @ ${px:.2f} | vol={ratio:.1f}x | "
                f"micro={fc.get('spike_likelihood', 0):.0%} pred→${(fc.get('pred_1bar') or px):.2f}"
            )
            result = self._attempt_entry()
            if result in ("entered", "waiting") or ticker in self._held_tickers():
                return
            break
    def _scan_one(self, ticker: str, fast: bool = False) -> Optional[Dict]:
        """
        Scan one ticker. fast=True: 30min 1m bars only (HFT scan pass).
        Full pass adds MTF + AI scoring on refine phase.
        """
        if ticker in self._contract_blacklist:
            return None
        blocked, md_reason = is_market_data_blocked(self.cfg, ticker)
        if blocked:
            log.debug(f"  ⏭ {ticker}: MD blocked — {md_reason[:80]}")
            return None
        cfg_ticker = self.cfg.TICKER
        try:
            self.cfg.TICKER = ticker
            dm = DataManager(self.conn, self.cfg)

            duration = getattr(self.cfg, "SCAN_BAR_DURATION", "1800 S") if fast else "1 D"
            hist_1m = dm.fetch_historical(duration=duration, bar_size="1 min", use_rth=False, quiet=fast)

            df_5m = df_15m = None
            use_mtf = getattr(self.cfg, "USE_MULTI_TIMEFRAME_SCAN", True) and not fast
            if use_mtf:
                try:
                    df_5m = dm.fetch_historical(duration="1 D", bar_size="5 mins", use_rth=False, quiet=True)
                    df_15m = dm.fetch_historical(duration="1 D", bar_size="15 mins", use_rth=False, quiet=True)
                except Exception:
                    pass

            score = None
            if hist_1m is not None and len(hist_1m) >= 20:
                score = self._score_ticker(ticker, hist_1m)
                if score and score.get("total_score", 0) > 0 and use_mtf:
                    mtf_bonus, mtf_note = mtf_score_bonus(hist_1m, df_5m, df_15m)
                    score["total_score"] = round(score["total_score"] + mtf_bonus, 1)
                    if mtf_note:
                        score["reasons"] = f"{score.get('reasons', '')} | {mtf_note}".strip(" |")
                if score and score.get("total_score", 0) > 0 and not fast and not getattr(self.cfg, "AI_FULL_CONTROL", True):
                    ai_adjusted = self._ai_score_ticker(ticker, hist_1m, score["total_score"])
                    score["total_score"] = round(ai_adjusted, 1)
                    score["ai_score"] = round(ai_adjusted, 1)
                if score and score.get("total_score", 0) > 0:
                    self._store_scan_cache(ticker, hist_1m)

            if score and score.get("total_score", 0) > 0:
                log.debug(f"  ✅ {ticker}: score={score['total_score']:.1f} | {score.get('reasons', '')[:60]}")
            else:
                reason = score.get('reasons', 'no_data') if score else 'no_data'
                log.debug(f"  ❌ {ticker}: {reason}")

            return score if score and score.get("total_score", 0) > 0 else None
        except Exception as exc:
            msg = str(exc)
            record_fetch_failure(self.cfg, ticker, exc, bar_size="1 min")
            if "Could not qualify" in msg or "No security definition" in msg:
                if should_permanent_blacklist(self.cfg, "no IB contract"):
                    self._contract_blacklist.add(ticker)
                record_failure_for_learning(
                    self.cfg, ticker=ticker, reason=msg[:200], event="scan_contract",
                )
                log.debug(f"  ⏭ {ticker}: no IB contract (recorded for learning)")
            else:
                log.info(f"  ❌ {ticker}: SCAN ERROR — {exc}")
            return None
        finally:
            self.cfg.TICKER = cfg_ticker
    def _refine_scan_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """Phase-2: MTF + AI refine on top candidates only (fast)."""
        refined = []
        for r in candidates:
            ticker = r["ticker"]
            full = self._scan_one(ticker, fast=False)
            if full:
                refined.append(full)
            else:
                refined.append(r)
        return refined
    def _scan_and_rank(self, startup: bool = False, skip_ib_scanner: bool = False):
        t0 = time.perf_counter()
        from core.startup_log import sinfo
        sinfo(self.cfg, "🔍 HANOON scan: fetching live IB universe…")
        screen_list, universe_source = get_live_scan_universe(
            self.scanner, self.conn, self.cfg,
            startup=startup, skip_ib_scanner=skip_ib_scanner,
        )
        self._last_universe_source = universe_source
        if not screen_list:
            log.warning("⏸ Scan skipped — no tickers in universe")
            return

        if getattr(self.cfg, "FAST_SCANNER_LOCK", True):
            locked = self._scan_and_rank_fast_lock(screen_list, t0)
            if locked or not getattr(self.cfg, "FAST_SCANNER_LOCK_FALLBACK", False):
                return

        fast = getattr(self.cfg, "FAST_SCAN_ENABLED", True)
        mode = "FAST" if fast else "FULL"
        log.info(f"🔍 HANOON SCAN START ({mode}): {len(screen_list)} tickers (live IB only)")
        results: List[Dict] = []
        
        scan_count = 0
        early_exit_n = int(getattr(self.cfg, "SCAN_EARLY_EXIT_QUALIFIED", 18))
        total = len(screen_list)
        for ticker in screen_list:
            scan_count += 1
            if scan_count == 1 or scan_count % 10 == 0 or scan_count == total:
                log.info(f"📊 Scan progress: {scan_count}/{total} tickers ({len(results)} qualified)")
            r = self._scan_one(ticker, fast=fast)
            if r:
                results.append(r)
            if fast and len(results) >= early_exit_n and scan_count >= 15:
                log.info(f"⚡ Early scan exit: {len(results)} qualified in {scan_count} tickers")
                break
        
        if fast and results:
            defer_mtf = not getattr(self.cfg, "SCAN_MTF_DURING_RTH", False)
            market_open = get_market_state() == "open"
            if not (defer_mtf and market_open):
                results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
                top_n = int(getattr(self.cfg, "SCAN_REFINE_TOP_N", 12))
                refine_pool = results[:top_n]
                log.info(f"🔬 Refining top {len(refine_pool)} with MTF + AI...")
                results = self._refine_scan_candidates(refine_pool) + results[top_n:]
            else:
                log.info("⚡ MTF refine deferred during RTH — bars prefetched after lock")
        
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(f"Scan: {len(results)}/{scan_count} qualified in {elapsed_ms:.0f}ms")
        
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            results = self.ai_commander.rank_scan_results(results)
        try:
            from core.war_account import adjust_scan_results
            results = adjust_scan_results(self.cfg, results)
        except Exception:
            pass
        
        # Debug: log score distribution
        if results:
            scores = [r["total_score"] for r in results[:5]]
            log.debug(f"Score distribution: top5={scores}")
        
        self._commit_scan_lock(results, elapsed_ms)
    def _scan_and_rank_fast_lock(self, screen_list: List[str], t0: float) -> bool:
        """
        Lock from IB scanner metadata only (no per-ticker historical fetch).
        Returns True if targets were locked or lock was attempted (skip slow path).
        """
        hits = self.scanner.get_scanner_hits()
        results: List[Dict] = []
        for idx, ticker in enumerate(screen_list):
            if ticker in self._contract_blacklist:
                continue
            hit = hits.get(ticker)
            if hit is None:
                hit = ScannerHit(ticker=ticker, rank=idx, scan_code="live")
            scored = StockScanner.score_scanner_hit(hit, list_index=idx)
            if scored.get("total_score", 0) > 0:
                results.append(scored)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        src = getattr(self, "_last_universe_source", "ib_live")
        src_labels = {
            "ib_live": "IB live scanner",
            "startup_curated": "startup curated list",
            "session_curated": "session curated list",
            "emergency_fallback": "emergency fallback",
        }
        src_label = src_labels.get(src, src)
        from core.startup_log import startup_compact
        lock_line = (
            f"⚡ FAST LOCK: {len(results)}/{len(screen_list)} from {src_label} "
            f"in {elapsed_ms:.0f}ms"
        )
        if startup_compact(self.cfg):
            log.info(lock_line)
        else:
            log.info(
                f"⚡ SCAN FAST LOCK: {len(results)}/{len(screen_list)} ranked "
                f"from {src_label} in {elapsed_ms:.0f}ms (no bar fetch)"
            )

        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander and results:
            results = self.ai_commander.rank_scan_results(results)
        try:
            from core.war_account import adjust_scan_results
            results = adjust_scan_results(self.cfg, results)
        except Exception:
            pass

        min_lock_score = effective_min_lock_score(self.cfg)
        min_candidates = effective_min_lock_candidates(self.cfg)
        qualified = [r for r in results if r.get("total_score", 0) >= min_lock_score]
        if len(qualified) < min_candidates:
            top_hint = ""
            if results:
                best = results[0]
                top_hint = f" | best={best['ticker']}@{best.get('total_score', 0):.0f}"
            log.info(
                f"🔍 Fast lock skipped — {len(qualified)}/{min_candidates} names above "
                f"score {min_lock_score:.0f}{top_hint}"
            )
            return False

        return self._commit_scan_lock(qualified, elapsed_ms, fast_lock=True)
    def _commit_scan_lock(
        self,
        results: List[Dict],
        elapsed_ms: float,
        fast_lock: bool = False,
    ) -> bool:
        """Apply lock pool, notify, stream, and optional bar prefetch."""
        min_lock_score = effective_min_lock_score(self.cfg)
        min_candidates = effective_min_lock_candidates(self.cfg)
        qualified = [r for r in results if r.get("total_score", 0) >= min_lock_score]

        if len(qualified) < min_candidates:
            top_hint = ""
            if results:
                t0 = results[0]
                top_hint = f" | best={t0['ticker']}@{t0.get('total_score', 0):.0f}"
            log.info(
                f"🔍 Lock skipped — {len(qualified)}/{min_candidates} names above "
                f"score {min_lock_score:.0f}{top_hint} (waiting for quality setups)"
            )
            self.top_pick = None
            self._locked_targets = []
            return False
        
        self.scan_results = qualified[: self._max_locked()]

        max_price = getattr(self.cfg, "PENNY_STOCK_MAX_PRICE", 500.0)
        hits = self.scanner.get_scanner_hits()
        from core.universe_filter import passes_profit_hunt_universe
        pool = [
            r for r in qualified
            if r.get("price", 0.0) <= max_price
            and passes_profit_hunt_universe(
                self.cfg,
                r["ticker"],
                str((hits.get(r["ticker"]) or ScannerHit(ticker=r["ticker"])).primary_exchange),
                price=float(r.get("price", 0) or 0),
            )[0]
            and r["ticker"] not in self._contract_blacklist
        ]
        pool.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        tradeable = set(filter_tradeable_tickers(self.cfg, [r["ticker"] for r in pool]))
        pool = [r for r in pool if r["ticker"] in tradeable]
        pool_note = ""
        try:
            from core.scan_lock_pools import build_kill_fit_lock_pool, tier_pool_summary
            locked = build_kill_fit_lock_pool(
                self.cfg, pool, self._max_locked(), hits,
            )
            pool_note = tier_pool_summary(locked, hits, self.cfg)
        except Exception:
            locked = pool[: self._max_locked()]
        try:
            from core.war_account import filter_locked_pool
            locked_objs = [
                ScanResult(
                    ticker=r["ticker"], price=r.get("price", 0.0), volume=r.get("volume", 0),
                    avg_volume=r.get("avg_volume", 0), relative_volume=r.get("rel_vol", 1.0),
                    rank_score=r["total_score"], reason=r.get("reasons", ""),
                )
                for r in locked
            ]
            locked_objs = filter_locked_pool(self.cfg, locked_objs)
            locked_tickers = {p.ticker for p in locked_objs}
            locked = [r for r in locked if r["ticker"] in locked_tickers]
        except Exception:
            pass
        if not locked and qualified:
            locked = sorted(qualified, key=lambda x: x.get("total_score", 0), reverse=True)[:3]

        penny_results = locked
        
        if not penny_results:
            self.top_pick = None
            self._locked_targets = []
            log.info(f"🔍 No setups found in full universe scan ({elapsed_ms:.0f}ms)")
            return False

        self._locked_targets = []
        for r in penny_results:
            hit = hits.get(r["ticker"])
            px = float(r.get("price", 0) or 0)
            if px <= 0 and hit is not None:
                px = float(getattr(hit, "price", 0) or 0)
            pick = ScanResult(
                ticker=r["ticker"], price=px, volume=r.get("volume", 0),
                avg_volume=r.get("avg_volume", 0), relative_volume=r.get("rel_vol", 1.0),
                rank_score=r["total_score"], reason=r.get("reasons", ""),
            )
            self._locked_targets.append(pick)
        self._locked_targets = prioritize_locked_targets(
            self._locked_targets,
            self.cfg,
            self._locked_targets[0].ticker if self._locked_targets else None,
            hits=hits,
        )
        self.top_pick = self._locked_targets[0] if self._locked_targets else None
        self._targets_locked_at = time.time()
        self._focus_target_index = 0
        self._last_focus_rotate = 0.0
        names = ", ".join([p.ticker for p in self._locked_targets])
        lock_tag = "FAST" if fast_lock else "FULL"
        from core.startup_log import startup_compact, sinfo
        log.info(
            f"🎯 LOCKED ({len(self._locked_targets)}): {names} | {lock_tag} {elapsed_ms:.0f}ms{pool_note}"
        )
        self._last_lock_elapsed_ms = elapsed_ms
        if not startup_compact(self.cfg):
            log.info(
                f"🔒 COMMITTED LOCK: scores≥{min_lock_score:.0f} | "
                + (
                    f"priority focus ({warm_priority_count(self.cfg)} warm + "
                    f"{stream_priority_count(self.cfg)} stream)"
                    if ai_fast_execution(self.cfg)
                    else f"rotate every {getattr(self.cfg, 'LOCK_FOCUS_ROTATE_SEC', 0):.0f}s"
                )
                + f" | stale release {getattr(self.cfg, 'LOCK_STALE_RELEASE_SEC', 600):.0f}s"
            )
            if ai_fast_execution(self.cfg):
                priority = self._priority_tickers()
                log.info(
                    f"⚡ AI FAST EXEC: {len(priority)} tickers "
                    f"[{','.join(priority[:8])}{'…' if len(priority) > 8 else ''}] | "
                    f"monitor {fast_monitor_interval(self.cfg):.2f}s"
                )
        self._ensure_locked_streams(quiet=True)
        self._schedule_bar_prefetch([p.ticker for p in self._locked_targets])
        self._bar_warm_due = True
        self._bar_warm_idx = 0

        if getattr(self.cfg, "DEFER_LOCK_AI_REVIEW", True):
            self._lock_review_due = True
            self._lock_review_picks = list(penny_results)
        else:
            self._generative_review_locks(penny_results)
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                send_dynamic_notification(
                    self.notifier, self.autopilot, "targets_locked",
                    self._notify_context({
                        "targets": names,
                        "top_score": self.top_pick.rank_score if self.top_pick else 0,
                        "scan_ms": elapsed_ms,
                    }),
                    f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\nTop score: {self.top_pick.rank_score:.0f}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            elif not getattr(self.cfg, "DEFER_LOCK_AI_REVIEW", True):
                self.notifier.info(
                    f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\n"
                    f"Top score: {self.top_pick.rank_score:.0f}"
                )

        if getattr(self.cfg, "SCAN_BOOTSTRAP_ENTRY", True):
            self._bootstrap_entry_due = True

        try:
            buffer_append({
                "source": "scan_pick",
                "ticker": self.top_pick.ticker,
                "action": "SCAN_PICK",
                "scan_score": self.top_pick.rank_score,
                "confidence": 0.5,
                "features": [],
            })
        except Exception:
            pass
        return True
    def _schedule_bar_prefetch(self, tickers: List[str]):
        """Queue 1-min bar prefetch — priority names at front of queue."""
        priority = self._priority_tickers() if self._locked_targets else []
        priority_set = {t.upper() for t in priority}
        ordered = [t for t in priority if t in tickers]
        ordered += [t for t in tickers if t.upper() not in priority_set]
        for ticker in ordered:
            if (
                ticker
                and ticker not in self._scan_data_cache
                and ticker not in self._bar_prefetch_queue
            ):
                blocked, _ = is_market_data_blocked(self.cfg, ticker)
                if blocked:
                    continue
                self._bar_prefetch_queue.append(ticker)
        if self._bar_prefetch_queue:
            log.debug(f"Bar prefetch queued: {self._bar_prefetch_queue[:12]}")
    def _prefetch_one_ticker_bars(self, ticker: str, quiet: bool = True) -> Optional[pd.DataFrame]:
        """Fetch bars for one ticker — live stream first; HMDS only when allowed."""
        if not ticker or ticker in self._contract_blacklist:
            return None
        blocked, _ = is_market_data_blocked(self.cfg, ticker)
        if blocked:
            return None
        need = self._min_bars_for(ticker)
        cached = self._scan_data_cache.get(ticker)
        if cached is not None and len(cached) >= need:
            return cached

        if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
            live_df = self._bars_from_stream(ticker, need)
            if live_df is not None:
                return live_df

        if skip_historical_prefetch(self.cfg) and self._stream_has_price(ticker):
            return None
        soft_skip = (
            bool(getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True))
            and ticker in self._target_monitors
        )
        try:
            from core.sniper_execution import sniper_active, sniper_force_bar_prefetch
            if (
                soft_skip
                and sniper_force_bar_prefetch(self.cfg)
                and sniper_active(self.cfg)
            ):
                prio = {n.upper() for n in (self._priority_tickers() or [])}
                if ticker.upper() in prio:
                    soft_skip = False
        except Exception:
            pass
        if soft_skip:
            return None
        try:
            from core.rth_session import historical_prefetch_allowed
            if not historical_prefetch_allowed(self.cfg):
                return None
        except Exception:
            return None

        cfg_ticker = self.cfg.TICKER
        try:
            self.cfg.TICKER = ticker
            dm = DataManager(self.conn, self.cfg)
            duration = getattr(self.cfg, "SCAN_BAR_DURATION", "1800 S")
            if getattr(self.cfg, "PAPER_TRADING", False):
                duration = getattr(self.cfg, "PAPER_SCAN_BAR_DURATION", "420 S")
            fresh = dm.fetch_historical(
                duration=duration, bar_size="1 min", use_rth=False, quiet=quiet,
            )
            min_accept = need if ai_fast_execution(self.cfg) else 20
            if fresh is not None and len(fresh) >= min_accept:
                self._store_scan_cache(ticker, fresh)
                if fresh["close"].iloc[-1] > 0:
                    for target in self._locked_targets:
                        if target.ticker == ticker and target.price <= 0:
                            target.price = float(fresh["close"].iloc[-1])
                return fresh
            if fresh is not None and len(fresh) >= 3 and ai_fast_execution(self.cfg):
                self._store_scan_cache(ticker, fresh)
                return fresh
        except Exception as exc:
            record_fetch_failure(self.cfg, ticker, exc, bar_size="1 min")
            log.debug(f"Bar prefetch {ticker}: {exc}")
        finally:
            self.cfg.TICKER = cfg_ticker
        return None
    def _drain_bar_prefetch_queue(self):
        """Non-blocking bar prefetch — priority names first when fast execution on."""
        per_loop = prefetch_per_loop(self.cfg)
        if ai_fast_execution(self.cfg) and self._locked_targets:
            priority = self._priority_tickers()
            for ticker in priority:
                cached = self._scan_data_cache.get(ticker)
                need = self._min_bars_for(ticker)
                if cached is not None and len(cached) >= need:
                    continue
                self._prefetch_one_ticker_bars(ticker, quiet=True)
                per_loop -= 1
                if per_loop <= 0:
                    return
        for _ in range(max(1, per_loop)):
            if not self._bar_prefetch_queue:
                return
            ticker = self._bar_prefetch_queue.pop(0)
            if ticker in self._scan_data_cache:
                continue
            self._prefetch_one_ticker_bars(ticker, quiet=True)
    def _warm_locked_bar_cache(self):
        """Fetch 1-min bars for ALL priority locked names — spike monitor ready ASAP."""
        budget = warm_budget_sec(self.cfg)
        t0 = time.perf_counter()
        warmed = 0
        priority = self._priority_tickers()
        for ticker in priority:
            if time.perf_counter() - t0 > budget:
                break
            if self._prefetch_one_ticker_bars(ticker, quiet=True) is not None:
                warmed += 1
        remaining = [
            t.ticker for t in self._locked_targets
            if t.ticker not in self._scan_data_cache
        ]
        if remaining:
            self._schedule_bar_prefetch(remaining)
        priority_ready = sum(
            1 for t in priority
            if t in self._scan_data_cache
            and len(self._scan_data_cache[t]) >= self._min_bars_for(t)
        )
        total_ready = sum(
            1 for t in self._locked_targets
            if t.ticker in self._scan_data_cache
            and len(self._scan_data_cache[t.ticker]) >= self._min_bars_for(t.ticker)
        )
        log.info(
            f"📊 Bar cache: {priority_ready}/{len(priority)} priority ready | "
            f"{total_ready}/{len(self._locked_targets)} total locked"
        )
    def _tick_bar_warm_on_main(self) -> None:
        """Prefetch IB bars on main loop — multiple tickers per tick when configured."""
        if not self._bar_warm_due or not self._locked_targets:
            return
        priority = self._priority_tickers()
        idx = self._bar_warm_idx
        per_loop = int(getattr(self.cfg, "BAR_WARM_PER_LOOP", 4))
        warmed = 0
        while idx < len(priority) and warmed < per_loop:
            ticker = priority[idx]
            need = self._min_bars_for(ticker)
            cached = self._scan_data_cache.get(ticker)
            if cached is not None and len(cached) >= need:
                idx += 1
                continue
            if self._stream_has_price(ticker):
                self._bars_from_stream(ticker, need)
                idx += 1
                warmed += 1
                continue
            self._prefetch_one_ticker_bars(ticker, quiet=True)
            idx += 1
            warmed += 1
        self._bar_warm_idx = idx
        if idx >= len(priority):
            self._bar_warm_due = False
            self._bar_warm_idx = 0
            priority_ready = sum(
                1 for t in priority
                if t in self._scan_data_cache
                and len(self._scan_data_cache[t]) >= self._min_bars_for(t)
            )
            total_ready = sum(
                1 for t in self._locked_targets
                if t.ticker in self._scan_data_cache
                and len(self._scan_data_cache[t.ticker]) >= self._min_bars_for(t.ticker)
            )
            log.info(
                f"📊 Bar cache: {priority_ready}/{len(priority)} priority ready | "
                f"{total_ready}/{len(self._locked_targets)} total locked"
            )
    def _locked_target_rows(self) -> List[Dict]:
        return [
            {
                "ticker": t.ticker,
                "price": t.price,
                "volume": t.volume,
                "avg_volume": t.avg_volume,
                "rel_vol": t.relative_volume,
                "total_score": t.rank_score,
                "reasons": t.reason,
            }
            for t in self._locked_targets
        ]
    def _apply_lock_row_merge(
        self,
        merged_rows: List[Dict],
        added: List[str],
        removed: List[str],
        tag: str = "MERGE",
    ) -> bool:
        if not merged_rows:
            return False
        hits = self.scanner.get_scanner_hits()
        self._locked_targets = []
        for r in merged_rows:
            hit = hits.get(r["ticker"])
            px = float(r.get("price", 0) or 0)
            if px <= 0 and hit is not None:
                px = float(getattr(hit, "price", 0) or 0)
            self._locked_targets.append(
                ScanResult(
                    ticker=r["ticker"],
                    price=px,
                    volume=r.get("volume", 0),
                    avg_volume=r.get("avg_volume", 0),
                    relative_volume=r.get("rel_vol", 1.0),
                    rank_score=r["total_score"],
                    reason=r.get("reasons", ""),
                )
            )
        self._locked_targets = prioritize_locked_targets(
            self._locked_targets, self.cfg, hits=hits,
        )
        self.top_pick = self._locked_targets[0] if self._locked_targets else None
        names = ", ".join(t.ticker for t in self._locked_targets)
        from core.scan_lock_pools import tier_pool_summary
        pool_note = tier_pool_summary(merged_rows, hits, self.cfg)
        change = ""
        if added or removed:
            change = f" +{','.join(added)}" if added else ""
            if removed:
                change += f" -{','.join(removed)}"
        log.info(f"🔄 LOCK {tag}: {names}{pool_note}{change}")
        for tk in removed:
            if tk in self._target_monitors:
                self._stop_target_stream(tk)
        self._ensure_locked_streams(quiet=True)
        self._schedule_bar_prefetch([p.ticker for p in self._locked_targets])
        return True
    def _maybe_soft_rotate_lock(self, now: float) -> bool:
        """Drop weakest stale tail slots — keeps top names; opens room for scanner merge."""
        rotate_sec = float(os.getenv("SCAN_SOFT_ROTATE_SEC", "180"))
        if rotate_sec <= 0 or self._in_any_position() or not self._locked_targets:
            return False
        if now - self._last_soft_rotate < rotate_sec:
            return False
        self._last_soft_rotate = now

        drop_n = int(os.getenv("SCAN_SOFT_ROTATE_DROP", "2"))
        protect_n = int(os.getenv("SCAN_SOFT_ROTATE_PROTECT", "5"))
        if drop_n <= 0:
            return False

        hits = self.scanner.get_scanner_hits()
        from core.scan_lock_pools import kill_fit_score

        scored: List[Tuple[float, ScanResult, bool]] = []
        for target in self._locked_targets:
            row = {
                "ticker": target.ticker,
                "price": target.price,
                "total_score": target.rank_score,
            }
            kfs = kill_fit_score(row, hits, self.cfg)
            last_touch = self._lock_spike_touch_at.get(target.ticker, 0.0)
            stale = (now - last_touch) > rotate_sec if last_touch > 0 else True
            scored.append((kfs, target, stale))
        scored.sort(key=lambda x: x[0], reverse=True)

        dropped: List[ScanResult] = []
        for kfs, target, stale in scored[protect_n:]:
            if len(dropped) >= drop_n:
                break
            if not stale:
                continue
            dropped.append(target)
        if not dropped:
            return False

        drop_set = {t.ticker for t in dropped}
        self._locked_targets = [t for t in self._locked_targets if t.ticker not in drop_set]
        for t in dropped:
            self._stop_target_stream(t.ticker)
        self.top_pick = self._locked_targets[0] if self._locked_targets else None
        log.info(
            f"🔄 Soft rotate — dropped [{', '.join(t.ticker for t in dropped)}] | "
            f"keeping {len(self._locked_targets)}"
        )
        self._soft_merge_due = True
        return True
    def _maybe_merge_lock_from_scanner(self, now: float) -> bool:
        """Light IB scanner refresh — fill open slots or upgrade weak tail without full rescan."""
        merge_sec = float(os.getenv("SCAN_MERGE_SEC", "120"))
        slots_open = len(self._locked_targets) < self._max_locked()
        if not self._soft_merge_due:
            if self._in_any_position() or not self._locked_targets:
                return False
            if not slots_open and now - self._last_merge_scan < merge_sec:
                return False
        if now - self._last_merge_scan < 15.0:
            return False

        self._last_merge_scan = now
        self._soft_merge_due = False

        screen_list = self.scanner.get_dynamic_universe(self.conn, force=False)
        if not screen_list:
            return False

        hits = self.scanner.get_scanner_hits()
        fresh: List[Dict] = []
        for idx, ticker in enumerate(screen_list[:50]):
            if ticker in self._contract_blacklist:
                continue
            hit = hits.get(ticker)
            if hit is None:
                hit = ScannerHit(ticker=ticker, rank=idx, scan_code="live")
            scored = StockScanner.score_scanner_hit(hit, list_index=idx)
            if scored.get("total_score", 0) > 0:
                fresh.append(scored)
        if not fresh:
            return False

        from core.scan_lock_pools import merge_kill_fit_lock_pool
        merged, added, removed = merge_kill_fit_lock_pool(
            self.cfg,
            self._locked_target_rows(),
            fresh,
            self._max_locked(),
            hits,
        )
        if not added and not removed and not slots_open:
            return False
        return self._apply_lock_row_merge(merged, added, removed)
    def _maybe_release_stale_lock(self, now: float) -> bool:
        """Last resort — full clear only after long quiet (soft rotate handles churn)."""
        if not self._locked_targets or self._in_any_position():
            return False
        stale_sec = float(getattr(self.cfg, "LOCK_STALE_RELEASE_SEC", 900.0))
        if stale_sec <= 0:
            return False
        locked_for = now - self._targets_locked_at
        if locked_for < stale_sec:
            return False
        names = ", ".join(t.ticker for t in self._locked_targets)
        log.info(
            f"🔓 Stale lock release — no entry in {locked_for:.0f}s | "
            f"clearing [{names}] → rescan"
        )
        for t in list(self._target_monitors.keys()):
            self._stop_target_stream(t)
        self._locked_targets = []
        self.top_pick = None
        self._targets_locked_at = 0.0
        self._bar_prefetch_queue.clear()
        self._last_scan_time = 0.0
        return True
    def _maybe_rotate_locked_focus(self, now: float):
        """Rotate live tick stream across locked names — disabled when all priority watched."""
        if ai_fast_execution(self.cfg) or not focus_rotation_enabled(self.cfg):
            return
        if len(self._locked_targets) < 2:
            return
        rotate_sec = float(getattr(self.cfg, "LOCK_FOCUS_ROTATE_SEC", 60.0))
        if rotate_sec <= 0:
            return
        if now - self._last_focus_rotate < rotate_sec:
            return
        self._last_focus_rotate = now
        self._focus_target_index = (self._focus_target_index + 1) % len(self._locked_targets)
        pick = self._locked_targets[self._focus_target_index]
        if not getattr(self.cfg, "FOCUS_PIN_TOP_PICK", False):
            self.top_pick = pick
        self._ensure_locked_streams(quiet=True)
        log.info(
            f"🔄 Focus rotate → {pick.ticker} "
            f"({self._focus_target_index + 1}/{len(self._locked_targets)})"
        )
    def _generative_review_locks(self, picks: List[Dict]):
        """AI council ranks and comments on locked targets."""
        if not picks or not getattr(self.cfg, "GENERATIVE_THINKING_ENABLED", True):
            return
        if is_ai_council_mode(self.cfg) and self.ai_commander:
            try:
                review = self.ai_commander.review_lock_watchlist(picks)
                thought = review.get("commentary", "")
                if not thought and not review.get("pending"):
                    thought = f"Gut pick: {review.get('gut_pick', '')}"
                if thought:
                    log.info(f"🧠 COUNCIL watchlist: {thought[:400]}")
            except Exception:
                pass
            return
        names = ", ".join(r["ticker"] for r in picks[:5])
        log.info(f"🎯 LOCKED watchlist (no ambient API): {names}")
        return
    def _focused_ticker(self) -> Optional[str]:
        """Best-ranked pick for entry context — NOT the only monitored ticker."""
        if self.top_pick:
            return self.top_pick.ticker
        if not self._locked_targets:
            return None
        priority = self._priority_tickers()
        return priority[0] if priority else self._locked_targets[0].ticker
    def _service_stream_repairs(self) -> None:
        """Restart streams outside IB error callbacks (avoids nested event loop)."""
        if self._md_suspended or not self._stream_repair:
            return
        for ticker, mode in list(self._stream_repair.items()):
            self._stream_repair.pop(ticker, None)
            if mode == "realtime" and self._stream_modes.get(ticker) == "realtime":
                dm = self._target_monitors.get(ticker)
                if dm is not None and dm.has_live_stream():
                    continue
            if ticker in self._target_monitors:
                self._stop_target_stream(ticker)
            log.debug(f"  📡 {ticker}: switching to 5s bars")
            self._start_target_stream(ticker, quiet=True, stream_mode=mode)
    def _ensure_focus_stream(self, quiet: bool = False):
        """Backward-compatible alias — starts all locked streams when enabled."""
        self._ensure_locked_streams(quiet=quiet)
    def _ensure_locked_streams(self, quiet: bool = False):
        """
        Keep live data on priority locked tickers.
        Top N get tick-by-tick (IB cap ~5); rest get 5-second real-time bars.
        """
        if not self._locked_targets:
            return
        watch_all = getattr(self.cfg, "WATCH_ALL_LOCKED_STREAMS", True)
        if watch_all and ai_fast_execution(self.cfg):
            ordered = prioritize_locked_targets(
                self._locked_targets,
                self.cfg,
                hits=self.scanner.get_scanner_hits(),
            )
            wanted = [t.ticker for t in ordered[: self._max_locked()]]
        elif watch_all:
            wanted = [
                t.ticker for t in self._locked_targets[: self._max_locked()]
            ]
        else:
            wanted = self._priority_tickers()[: stream_priority_count(self.cfg)]
        wanted = filter_tradeable_tickers(self.cfg, wanted)

        if not watch_all and not ai_fast_execution(self.cfg):
            focus = self._focused_ticker()
            for t in list(self._target_monitors.keys()):
                if t != focus:
                    self._stop_target_stream(t)
            if focus:
                self._ensure_target_stream(focus, mode="tick", quiet=quiet)
            return

        for t in list(self._target_monitors.keys()):
            if t not in wanted:
                self._stop_target_stream(t)

        held = set(self._held_tickers())
        modes = assign_stream_modes(
            wanted, self.cfg, held=held, tick_denied=self._tick_limit_denied,
        )
        n_tick = n_rt = n_skip = 0
        for ticker, mode in modes.items():
            if mode == "skip":
                n_skip += 1
                if ticker in self._target_monitors:
                    self._stop_target_stream(ticker)
                continue
            self._ensure_target_stream(ticker, mode=mode, quiet=quiet)
            if mode == "tick":
                n_tick += 1
            else:
                n_rt += 1

        if wanted:
            tickers = ",".join(wanted[:8]) + ("…" if len(wanted) > 8 else "")
            body = (
                f"{n_tick} tick + {n_rt} 5s-bars"
                + (f" ({n_skip} deferred)" if n_skip else "")
                + f" [{tickers}]"
            )
            if body != getattr(self, "_last_stream_log_body", ""):
                self._last_stream_log_body = body
                prefix = "📡 Streams:" if quiet else "  📡 PRIORITY STREAMS:"
                log.info(f"{prefix} {body}")
                try:
                    from core.sniper_execution import sniper_tick_streams_enabled
                    if sniper_tick_streams_enabled(self.cfg):
                        tick_names = [t for t, m in modes.items() if m == "tick"]
                        if tick_names:
                            log.info(f"  🎯 Sniper tick sensors: {', '.join(tick_names)}")
                except Exception:
                    pass
    def _ensure_target_stream(self, ticker: str, mode: str = "realtime", quiet: bool = False):
        """Start or switch stream mode for one locked ticker."""
        current = self._stream_modes.get(ticker)
        if ticker in self._target_monitors and current == mode:
            return
        if ticker in self._target_monitors and current != mode:
            self._stop_target_stream(ticker)
        if ticker not in self._target_monitors:
            self._start_target_stream(ticker, quiet=quiet, stream_mode=mode)
    def _start_target_stream(
        self, ticker: str, quiet: bool = False, stream_mode: str = "tick",
    ):
        """Start live stream for a locked target."""
        blocked, reason = is_market_data_blocked(self.cfg, ticker)
        if blocked:
            log.debug(f"  ⏭ stream skip {ticker}: {reason[:80]}")
            return
        if ticker in self._target_monitors:
            return
        if stream_mode == "tick":
            if ticker.upper() in self._tick_limit_denied:
                stream_mode = "realtime"
            elif self._active_tick_stream_count() >= tick_stream_count(self.cfg):
                stream_mode = "realtime"
        try:
            cfg = BotConfig(TICKER=ticker)
            dm = DataManager(self.conn, cfg)
            cached = self._scan_data_cache.get(ticker)
            n_cached = len(cached) if cached is not None else 0
            if cached is not None and n_cached > 0:
                dm.seed_buffer_from_dataframe(cached, n_bars=60)
            dm.start_tick_stream(realtime_only=(stream_mode == "realtime"), quiet=quiet)
            if tick_spike_monitor_enabled(self.cfg):
                sym = ticker
                dm.on_tick(lambda px, ts, t=sym: self._on_locked_stream_tick(t, px, ts))
            self._target_monitors[ticker] = dm
            self._stream_modes[ticker] = stream_mode
            self._target_last_bar_count[ticker] = n_cached
            kind = "5s" if stream_mode == "realtime" else "tick"
            warm = "warming" if n_cached < self._min_bars_for(ticker) else f"{n_cached} bars"
            msg = f"  📡 LIVE STREAM {kind} {ticker} ({warm})"
            (log.debug if quiet else log.info)(msg)
        except Exception as exc:
            record_fetch_failure(self.cfg, ticker, exc, bar_size=f"stream:{stream_mode}")
            log.warning(f"  Stream start failed for {ticker}: {exc}")
    def _log_flat_heartbeat(self):
        """One-line alive pulse while flat — confirms watch loop without stream spam."""
        if self._in_any_position() or not self._locked_targets:
            return
        now = time.time()
        pulse_sec = float(getattr(self.cfg, "FLAT_PULSE_SEC", 15.0))
        if now - self._last_flat_pulse < pulse_sec:
            return
        self._last_flat_pulse = now
        focus = self._focused_ticker() or "?"
        locked = ",".join(t.ticker for t in self._locked_targets[: self._max_locked()])
        n_streams = len(self._target_monitors)
        nxt = self._next_best_pick.ticker if self._next_best_pick else "-"
        priority = self._priority_tickers()
        bars_ready = sum(
            1 for t in priority
            if t in self._scan_data_cache
            and len(self._scan_data_cache[t]) >= self._min_bars_for(t)
        )
        priced = sum(1 for t in priority if self._stream_has_price(t))
        if priced > 0:
            self.conn._10197_reclaim_attempts = 0
            self.conn._10197_storm_until = 0.0
        warm_note = ""
        if bars_ready < len(priority) and priced > 0:
            warm_note = f" | bars {bars_ready}/{len(priority)} warming from live streams"
        quality = ""
        if capital_discipline_enabled(self.cfg):
            quality = " | full AI — no entry caps"
        log.info(
            f"👁 WATCHING: {n_streams} streams | priced {priced}/{len(priority)} | "
            f"priority=[{','.join(priority[:10]) or focus}] | pool=[{locked}] | "
            f"next_best={nxt}{warm_note}{quality}"
        )
    def _detect_tick_volume_burst(self, dm: DataManager, df: pd.DataFrame) -> Tuple[bool, float]:
        """Detect volume burst from live tick prints or 5s bar accumulation."""
        ticks = list(getattr(dm, "_tick_buffer", []))
        if len(ticks) >= 5:
            recent_vol = sum(int(t.get("size", 0)) for t in ticks[-100:])
            avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else 1.0
            if avg_vol > 0:
                ratio = recent_vol / avg_vol
                return ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO, ratio
        fast = dm.get_fast_bar_dataframe(n=12)
        if fast is not None and len(fast) >= 3:
            recent_vol = float(fast["volume"].tail(3).sum())
            avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else 1.0
            if avg_vol > 0:
                ratio = recent_vol / avg_vol
                return ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO, ratio
        return False, 1.0
    def _stop_all_target_streams(self) -> None:
        """Stop every live tick/bar stream (used on shutdown and IB session reclaim)."""
        for ticker in list(self._target_monitors.keys()):
            self._stop_target_stream(ticker)
    def _stop_target_stream(self, ticker: str):
        """Stop live tick stream for a target."""
        dm = self._target_monitors.pop(ticker, None)
        self._stream_modes.pop(ticker, None)
        if dm:
            try:
                dm.stop_tick_stream()
            except Exception:
                pass
        self._target_last_bar_count.pop(ticker, None)
        if self._active_stream_ticker == ticker:
            self._active_stream_ticker = None
    def _refresh_locked_bars(self, quiet: bool = False):
        """Refresh 1min bars for priority targets so volume/uptrend checks stay current."""
        if ai_fast_execution(self.cfg):
            targets = [
                t for t in self._locked_targets
                if t.ticker.upper() in self._priority_ticker_set()
            ]
        else:
            targets = self._locked_targets
        for target in targets:
            ticker = target.ticker
            blocked, _ = is_market_data_blocked(self.cfg, ticker)
            if blocked:
                continue
            need = self._min_bars_for(ticker)
            if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
                if self._bars_from_stream(ticker, need) is not None:
                    continue
            if ticker in self._target_monitors and bool(
                getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True),
            ):
                continue
            if skip_historical_prefetch(self.cfg) and self._stream_has_price(ticker):
                continue
            try:
                from core.rth_session import historical_prefetch_allowed
                if not historical_prefetch_allowed(self.cfg):
                    continue
            except Exception:
                continue
            cfg_ticker = self.cfg.TICKER
            try:
                self.cfg.TICKER = ticker
                dm = DataManager(self.conn, self.cfg)
                fresh = dm.fetch_historical(
                    duration="1800 S", bar_size="1 min", use_rth=False, quiet=quiet,
                )
                min_bars = self._min_bars_for(ticker)
                if fresh is not None and len(fresh) >= min_bars:
                    self._store_scan_cache(ticker, fresh)
            except Exception as exc:
                record_fetch_failure(self.cfg, ticker, exc, bar_size="1 min")
            finally:
                self.cfg.TICKER = cfg_ticker
    def _silent_background_watch(self):
        """Rank other locked targets for next entry — no log noise while holding."""
        if not self._in_any_position() or len(self._locked_targets) < 2:
            return
        holding = self._held_tickers()
        best: Optional[ScanResult] = None
        best_opp = 0.0
        cfg_ticker = self.cfg.TICKER
        try:
            for target in self._locked_targets:
                if target.ticker in holding:
                    continue
                ticker = target.ticker
                blocked, _ = is_market_data_blocked(self.cfg, ticker)
                if blocked:
                    continue
                need = self._min_bars_for(ticker)
                if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
                    fresh = self._bars_from_stream(ticker, need)
                    if fresh is not None and len(fresh) >= need:
                        pass
                    elif skip_historical_prefetch(self.cfg):
                        continue
                    else:
                        fresh = None
                else:
                    fresh = None
                try:
                    if fresh is None and not skip_historical_prefetch(self.cfg):
                        self.cfg.TICKER = ticker
                        dm = DataManager(self.conn, self.cfg)
                        fresh = dm.fetch_historical(
                            duration="1800 S", bar_size="1 min", use_rth=False, quiet=True,
                        )
                    if fresh is None or len(fresh) < need:
                        continue
                    self._store_scan_cache(ticker, fresh)
                    px = float(fresh["close"].iloc[-1])
                    if not only_uptrend(fresh.tail(60), px):
                        continue
                    is_spike, vol = self._detect_volume_spike(fresh.tail(60))
                    opp = float(target.rank_score) * (vol if is_spike else 0.6)
                    if is_spike:
                        opp *= 1.4
                    if opp > best_opp:
                        best_opp = opp
                        best = target
                except Exception:
                    pass
            if best and best_opp > 0:
                self._next_best_pick = best
                self._next_best_score = best_opp
        finally:
            self.cfg.TICKER = cfg_ticker
    def _prefetch_live_ai_hotline(self):
        """Council prefetch — off in nanny mode to preserve RPM for live entries."""
        from core.council_nanny import prefetch_enabled
        if not prefetch_enabled(self.cfg):
            return
        if not self.ai_commander or not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        targets = self._locked_targets or []
        top_n = effective_prefetch_top_n(self.cfg)
        for target in targets[:top_n]:
            ticker = target.ticker if hasattr(target, "ticker") else target.get("ticker")
            if not ticker:
                continue
            df = self._scan_data_cache.get(ticker)
            if df is None or len(df) < 20:
                continue
            try:
                live_px = float(df["close"].iloc[-1])
                dm = self._target_monitors.get(ticker)
                if dm:
                    lp = dm.get_latest_price()
                    if lp and lp > 0:
                        live_px = float(lp)
                _, spike = self._detect_volume_spike(df)
                scan = target.rank_score if hasattr(target, "rank_score") else float(target.get("total_score", 0))
                bid, ask = self._get_bid_ask(ticker)
                spread = (ask - bid) / live_px if bid and ask and live_px > 0 else 0.0
                self.ai_commander.prefetch_entry_decision(
                    ticker, live_px, spike, scan,
                    market_ctx={
                        "bid": bid, "ask": ask, "spread_pct": spread,
                        "avg_volume": float(df["volume"].tail(20).mean()),
                        "recent_volume": float(df["volume"].iloc[-1]),
                    },
                    df=df,
                )
            except Exception as exc:
                log.debug(f"Prefetch {ticker}: {exc}")
    def _fast_monitor_locked(self, scout_only: bool = False):
        """
        Scan all locked tickers for spikes.
        scout_only=True: track next_best while holding a position (no new entry).
        """
        if not self._locked_targets:
            return
        if self._open_position_count() >= self._max_concurrent() and not scout_only:
            return
        now = time.time()
        refresh_sec = float(getattr(self.cfg, "LOCK_BAR_REFRESH_SEC", 180.0))
        if now - getattr(self, '_last_bar_refresh', 0) > refresh_sec:
            self._last_bar_refresh = now
            self._refresh_locked_bars(quiet=True)

        # Keep all priority streams alive — simultaneous monitor (no single-ticker rotation)
        if ai_fast_execution(self.cfg) or getattr(self.cfg, "WATCH_ALL_LOCKED_STREAMS", True):
            self._ensure_locked_streams(quiet=True)
        elif self.top_pick:
            self._ensure_locked_streams(quiet=True)

        best_spike: Optional[Tuple[ScanResult, float, float, pd.DataFrame]] = None
        spike_candidates: List[Tuple[float, ScanResult, float, float, pd.DataFrame]] = []
        best_priority = 0.0

        holding = self._held_tickers()
        priority_names = self._priority_ticker_set()
        scan_targets = self._locked_targets[: self._max_locked()]

        for target in scan_targets:
            ticker = target.ticker
            if ticker in self._entry_poll_states or ticker in holding:
                continue
            min_bars = self._min_bars_for(ticker)
            df, live_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=min_bars)
            min_ok = min_bars
            if dm and live_px > 0 and bool(getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True)):
                min_ok = max(3, min_bars // 2)
            if df is None or len(df) < min_ok:
                if dm and ticker.upper() in priority_names:
                    burst, burst_ratio = self._detect_tick_volume_burst(dm, df if df is not None else pd.DataFrame())
                    if burst:
                        if live_px <= 0:
                            live_px = float(dm.get_latest_price() or 0)
                        if live_px > 0:
                            priority = float(target.rank_score) * float(burst_ratio) * 1.5
                            work_df = df.tail(60).copy() if df is not None and len(df) else pd.DataFrame()
                            spike_candidates.append((priority, target, live_px, burst_ratio, work_df))
                continue

            if live_px <= 0:
                live_px = float(df["close"].iloc[-1])

            try:
                from core.fill_tracker import sanitize_quote_price
                bar_close = float(df["close"].iloc[-1])
                fc0 = self._last_micro_forecast.get(ticker, {})
                pred0 = float(fc0.get("pred_1bar") or 0)
                live_px = sanitize_quote_price(
                    live_px, ref_px=bar_close, pred_px=pred0, symbol=ticker,
                )
            except Exception:
                bar_close = float(df["close"].iloc[-1])

            if bar_close > 0 and live_px > 0 and abs(live_px / bar_close - 1.0) > 0.35:
                log.info(
                    f"  ⏭ QUOTE veto {ticker}: live ${live_px:.2f} vs bar ${bar_close:.2f}"
                )
                continue

            work_df = df.tail(60).copy()

            if forecast.get("dir", 0) < 0 and not forecast.get("breakout"):
                continue

            spike_fast_ok = should_spike_fast_entry(
                self.cfg, 1.0, float(target.rank_score),
            )
            uptrend_ok = only_uptrend(work_df, live_px, min_bars=min_bars)
            if not uptrend_ok and not (
                ai_fast_execution(self.cfg)
                and ticker.upper() in priority_names
                and spike_fast_ok
            ):
                if forecast.get("spike_likelihood", 0) < 0.5:
                    continue

            is_spike, spike_ratio = self._detect_volume_spike(work_df, min_period=min(20, max(6, min_bars)))
            min_spike = float(getattr(self.cfg, "LOCKED_SPIKE_MIN_RATIO", 1.15))
            if not is_spike and spike_ratio >= min_spike:
                is_spike, spike_ratio = True, spike_ratio

            is_spike, spike_ratio = apply_micro_spike_boost(
                is_spike, spike_ratio, forecast,
                cfg=self.cfg, scan_score=float(target.rank_score), live_px=float(live_px),
            )

            if dm and ticker.upper() in priority_names:
                burst, burst_ratio = self._detect_tick_volume_burst(dm, work_df)
                if burst:
                    is_spike, spike_ratio = True, burst_ratio

            # Momentum breakout: price clearing recent high with elevated volume
            if not is_spike and len(work_df) >= 6:
                high5 = float(work_df["high"].tail(5).max())
                vol_ratio = float(work_df["volume"].tail(3).mean()) / (
                    float(work_df["volume"].tail(20).mean()) + 1e-9
                )
                if live_px > high5 * 1.001 and vol_ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO:
                    is_spike, spike_ratio = True, vol_ratio

            if not is_spike and target.rank_score >= 20:
                vol_ratio = float(work_df["volume"].tail(3).mean()) / (
                    float(work_df["volume"].tail(20).mean()) + 1e-9
                )
                if vol_ratio >= 1.15:
                    is_spike, spike_ratio = True, vol_ratio

            if not is_spike:
                continue

            self._lock_spike_touch_at[ticker] = now
            boost = 1.0 + float(forecast.get("spike_likelihood", 0)) * float(
                getattr(self.cfg, "MICRO_SPIKE_BOOST", 0.35)
            )
            priority = float(target.rank_score) * float(spike_ratio) * boost
            spike_candidates.append((priority, target, live_px, spike_ratio, work_df))
            if priority > best_priority:
                best_priority = priority
                best_spike = (target, live_px, spike_ratio, work_df)

        if best_spike is None and not spike_candidates:
            return

        if scout_only:
            if best_spike:
                target, live_px, spike_ratio, work_df = best_spike
                self._next_best_pick = target
                self._next_best_score = best_priority
                if int(time.time()) % 30 == 0:
                    log.debug(
                        f"  👀 Scout while holding: next={target.ticker} "
                        f"vol={spike_ratio:.1f}x score={target.rank_score:.0f}"
                    )
            return

        spike_candidates.sort(key=lambda x: x[0], reverse=True)
        max_attempts = max_spike_attempts_per_cycle(self.cfg)
        attempted = 0

        for priority, target, live_px, spike_ratio, work_df in spike_candidates[:max_attempts]:
            ticker = target.ticker
            if time.time() < self._spike_attempt_until.get(ticker, 0):
                continue
            if time.time() < self._spike_skip_until.get(ticker, 0):
                continue
            if time.time() < self._entry_cooldown_until.get(ticker, 0):
                continue
            if self.risk.is_halted():
                return
            if self._open_position_count() >= self._max_concurrent():
                return
            if self._pending_entry_ticker and time.time() < self._pending_entry_until:
                if self._pending_entry_ticker == ticker:
                    continue

            self._store_scan_cache(ticker, work_df)
            self.top_pick = target
            self._last_entry_attempt_at = time.time()
            self._spike_attempt_until[ticker] = time.time() + spike_entry_cooldown_sec(self.cfg)
            fc = self._last_micro_forecast.get(ticker, {})
            q_prob = fc.get("profit_probability", "")
            q_setup = fc.get("setup_type", "")
            q_extra = ""
            if q_prob != "":
                q_extra = f" | profit_prob={float(q_prob):.0%} setup={q_setup}"
            log.info(
                f"⚡ SPIKE: {ticker} @ ${live_px:.2f} | vol={spike_ratio:.1f}x | "
                f"score={target.rank_score:.0f} | micro={fc.get('spike_likelihood', 0):.0%} "
                f"pred→${(fc.get('pred_1bar') or live_px):.2f}{q_extra} | attempting entry..."
            )
            from core.entry_quality import (
                assess_entry_quality, quality_blocks_entry, regime_blocks_entry, mtf_blocks_entry,
            )
            quality = assess_entry_quality(
                self.cfg, fc,
                spike_ratio=spike_ratio,
                scan_score=float(target.rank_score),
                live_px=live_px,
            )
            fc.update(quality)
            self._last_micro_forecast[ticker] = fc
            from core.entry_quality import profit_prob_blocks_entry
            if profit_prob_blocks_entry(self.cfg, quality):
                log.info(
                    f"  ⏭ PROFIT PROB veto {ticker}: {quality.get('reason', '')[:100]}"
                )
                self._spike_skip_until[ticker] = time.time() + float(
                    getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                )
                continue
            if not quality.get("enter_ok", True):
                log.info(
                    f"  📊 QUALITY advisory {ticker}: {quality.get('reason', '')[:100]}"
                )
            spike_regime = "momentum_spike"
            fast_df, _, _, _ = self._resolve_live_bars(ticker, min_bars=10)
            if fast_df is not None and len(fast_df) >= 5:
                try:
                    _, spike_regime = resolve_regime(
                        self.regime_detector, fast_df,
                        spike_ratio=float(spike_ratio),
                        vol_ratio=float(spike_ratio),
                    )
                except Exception:
                    pass
            df_5m = df_15m = None
            from core.entry_quality import mtf_fetch_skipped
            if not mtf_fetch_skipped(
                self.cfg,
                scan_score=float(target.rank_score),
                spike_ratio=float(spike_ratio),
            ):
                df_5m, df_15m = self._resolve_mtf_bars(
                    ticker, float(target.rank_score), float(spike_ratio),
                )
            try:
                from core.smart_stack import (
                    collect_spike_gate_advisories,
                    mechanical_gates_advisory_only,
                )
                gate_adv = collect_spike_gate_advisories(
                    self.cfg,
                    ticker=ticker,
                    quality=quality,
                    spike_regime=spike_regime,
                    df_5m=df_5m,
                    df_15m=df_15m,
                    scan_score=float(target.rank_score),
                    spike_ratio=float(spike_ratio),
                )
                self._smart_gate_context[ticker.upper()] = gate_adv
                if mechanical_gates_advisory_only(self.cfg):
                    for gkey, gval in gate_adv.items():
                        if gkey == "ticker" or not isinstance(gval, dict):
                            continue
                        if not gval.get("ok", True):
                            log.info(
                                f"  📊 GATE advisory {ticker}: {gkey} — "
                                f"{gval.get('reason', '')[:80]}"
                            )
                else:
                    if quality_blocks_entry(self.cfg, quality):
                        log.info(
                            f"  ⏭ QUALITY veto {ticker}: {quality.get('reason', '')[:100]}"
                        )
                        self._spike_skip_until[ticker] = time.time() + float(
                            getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                        )
                        continue
                    if regime_blocks_entry(self.cfg, spike_regime):
                        log.info(
                            f"  ⏭ REGIME block {ticker}: {spike_regime} — skip new entry"
                        )
                        self._spike_skip_until[ticker] = time.time() + float(
                            getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                        )
                        continue
                    if mtf_blocks_entry(
                        self.cfg, df_5m, df_15m,
                        scan_score=float(target.rank_score),
                        spike_ratio=float(spike_ratio),
                    ):
                        log.info(
                            f"  ⏭ MTF block {ticker}: 5m/15m not aligned — skip entry"
                        )
                        self._spike_skip_until[ticker] = time.time() + float(
                            getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                        )
                        continue
            except Exception:
                if quality_blocks_entry(self.cfg, quality):
                    log.info(
                        f"  ⏭ QUALITY veto {ticker}: {quality.get('reason', '')[:100]}"
                    )
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                    )
                    continue
                if regime_blocks_entry(self.cfg, spike_regime):
                    log.info(
                        f"  ⏭ REGIME block {ticker}: {spike_regime} — skip new entry"
                    )
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                    )
                    continue
                if mtf_blocks_entry(
                    self.cfg, df_5m, df_15m,
                    scan_score=float(target.rank_score),
                    spike_ratio=float(spike_ratio),
                ):
                    log.info(f"  ⏭ MTF block {ticker}: 5m/15m not aligned — skip entry")
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                    )
                    continue
            result = self._attempt_entry()
            attempted += 1
            if ticker in self._held_tickers():
                self._active_stream_ticker = ticker
                self._ensure_position_stream(ticker)
                return
            if result == "entered":
                return
            if result in ("permanent_skip", "learn_skip"):
                self._locked_targets = [t for t in self._locked_targets if t.ticker != ticker]
                self._stop_target_stream(ticker)
                if not self._locked_targets:
                    self._last_scan_time = 0
                    log.info("🔓 All locked targets cleared — will rescan universe")
                elif self.top_pick and self.top_pick.ticker == ticker:
                    self.top_pick = self._locked_targets[0]
                    self._ensure_focus_stream(quiet=True)
            if result == "waiting" and attempted >= max_attempts:
                break
    def _detect_volume_spike(self, df: pd.DataFrame, min_period: int = 20) -> Tuple[bool, float]:
        """
        Detect volume spike: current volume vs recent average.
        Uses shorter window when fewer bars available (fast execution).
        """
        n = len(df)
        if n < 6:
            return False, 1.0
        period = min(min_period, n - 1)
        volumes = df["volume"].values[-period:]
        avg_vol = np.mean(volumes[:-1]) if len(volumes) > 1 else float(volumes[0])
        current_vol = volumes[-1]
        if avg_vol <= 0:
            return False, 1.0
        spike_ratio = current_vol / avg_vol
        threshold = getattr(self.cfg, "VOLUME_SPIKE_MIN_RATIO", 1.25)
        if ai_fast_execution(self.cfg):
            threshold = min(threshold, float(getattr(self.cfg, "AI_SPIKE_FAST_MIN_RATIO", 1.15)))
        return spike_ratio >= threshold, spike_ratio
    def _predict_slippage(self, df: pd.DataFrame, current_px: float) -> float:
        """
        Predict slippage risk based on spread, momentum divergence, and order flow.
        Returns 0.0 (no slippage) to 1.0 (high slippage)
        """
        if len(df) < 10:
            return 0.5
        closes = df["close"].values[-10:]
        volumes = df["volume"].values[-10:]
        
        # Momentum divergence: price up but volume down = exhaustion
        price_up = closes[-1] > closes[-3]
        vol_down = volumes[-1] < np.mean(volumes[-5:-1])
        divergence = 0.3 if (price_up and vol_down) else 0.0
        
        # High volatility = higher slippage
        atr = compute_atr(df, period=5)
        vol_ratio = atr / current_px if current_px > 0 else 0.01
        vol_slippage = min(0.3, vol_ratio * 2.0)
        
        # Thin volume = higher slippage
        avg_vol = np.mean(volumes[-5:])
        thin_penalty = 0.2 if avg_vol < 50000 else 0.0
        
        total_slippage = min(1.0, divergence + vol_slippage + thin_penalty)
        return total_slippage
    def _store_scan_cache(self, ticker: str, df: pd.DataFrame) -> None:
        """Bounded LRU-style scan bar cache — avoids unbounded DataFrame RAM."""
        key = str(ticker or "").upper()
        if not key:
            return
        try:
            slim = df.tail(self._scan_cache_max_bars).copy()
        except Exception:
            slim = df
        self._scan_data_cache[key] = slim
        if len(self._scan_data_cache) <= self._scan_cache_max_tickers:
            return
        locked = set()
        if self.current_ticker:
            locked.add(str(self.current_ticker).upper())
        for t in getattr(self, "_locked_target_names", []) or []:
            locked.add(str(t).upper())
        if self.top_pick and getattr(self.top_pick, "ticker", None):
            locked.add(str(self.top_pick.ticker).upper())
        for cache_key in list(self._scan_data_cache.keys()):
            if len(self._scan_data_cache) <= self._scan_cache_max_tickers:
                break
            if cache_key not in locked:
                self._scan_data_cache.pop(cache_key, None)
    def _score_ticker(self, ticker: str, df: pd.DataFrame) -> Dict:
        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        if not only_uptrend(df, current_px):
            return {"ticker": ticker, "total_score": 0, "price": current_px, "volume": int(volumes[-1]), "avg_volume": int(np.mean(volumes[-20:])), "rel_vol": 1.0, "reasons": "not_uptrend"}
        score = 1.0
        reasons = ["uptrend"]
        weights = self._load_weights()
        w_mom = float(weights.get("momentum", 2.0))
        w_vol = float(weights.get("volume", 15.0))
        w_inst = float(weights.get("institutional", 20.0))
        w_vwap = float(weights.get("vwap_slope", 5.0))
        w_atr = float(weights.get("atr_bonus", 5.0))
        w_mr = float(weights.get("mean_reversion", 5.0))
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0
        ret_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0
        mom_score = ret_5 * 0.5 + ret_10 * 0.3 + ret_20 * 0.2
        score += mom_score * w_mom
        if mom_score > 2:
            reasons.append(f"strong_mom_{mom_score:.1f}")
        vol_avg20 = np.mean(volumes[-20:])
        vol_avg5 = np.mean(volumes[-5:])
        vol_ratio = vol_avg5 / (vol_avg20 + 1e-9)
        score += max(0, vol_ratio - 1.0) * w_vol
        if vol_ratio > 1.3:
            reasons.append(f"vol_{vol_ratio:.1f}x")
        inst = InstitutionalDetector()
        for i in range(-20, 0):
            inst.feed_bar(float(volumes[i]), float(closes[i]))
        sig = inst.scan()
        if sig.direction == "accumulating" and sig.strength > 0.5:
            score += sig.strength * w_inst
            reasons.append(f"inst_{sig.strength:.1f}")
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        try:
            vwap_hist = np.array([
                safe_vwap(typical[max(0, i - 19):i + 1], volumes[max(0, i - 19):i + 1])
                for i in range(19, len(typical))
            ])
            vwap_slope = (vwap_hist[-1] - vwap_hist[-5]) / (vwap_hist[-5] + 1e-9) * 100
        except Exception:
            vwap_slope = 0
        score += max(0, vwap_slope) * w_vwap
        if vwap_slope > 0.5:
            reasons.append(f"vwap_up_{vwap_slope:.2f}%")
        atr = compute_atr(df, period=10)
        atr_pct = (atr / current_px) * 100
        if 0.3 < atr_pct < 3.0:
            score += w_atr
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1]
        dist = (current_px - ema9) / (pd.Series(closes).diff().rolling(20).std().iloc[-1] + 1e-9)
        if abs(dist) < 1.5:
            score += w_mr
        rule_result = {
            "ticker": ticker, "price": current_px, "volume": int(volumes[-1]),
            "avg_volume": int(vol_avg20), "rel_vol": round(vol_ratio, 2),
            "total_score": round(score, 1), "reasons": " | ".join(reasons[:3]) if reasons else "balanced",
            "ai_score": None,
        }
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            return self.ai_commander.score_ticker(ticker, df, hints=rule_result)
        return rule_result
    def _ai_score_ticker(self, ticker: str, df: pd.DataFrame, rule_score: float) -> float:
        """
        AI validates/overrides rule-based score.
        Returns AI-adjusted score (0-100 scale).
        """
        if not self.cfg.USE_ENHANCED_AI or self.model is None or self._model_fresh:
            return rule_score
        try:
            self._ai_update_buffers(df, float(df["close"].iloc[-1]))
            if len(self._feature_buffer) < self.cfg.WINDOW_SIZE:
                return rule_score
            window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
            total = self.bot_cash + self.shares * float(df["close"].iloc[-1])
            c_rat = self.bot_cash / (total + 1e-9)
            p_rat = (self.shares * float(df["close"].iloc[-1])) / (total + 1e-9) if self.shares > 0 else 0.0
            obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            from core.agent import predict_with_reasoning
            bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
            action, confidence, reasoning = predict_with_reasoning(
                self.model, obs, self.cfg, self.ai_components,
                bar_df=bar_df,
                recent_rewards=getattr(self.perf, 'recent_rewards', None) if hasattr(self, 'perf') else None,
            )
            ai_score = rule_score
            if action == 1 and confidence >= self.cfg.CONFIDENCE_THRESHOLD:
                ai_score = rule_score * (1.0 + confidence * 0.5)
            elif action == 2:
                ai_score = rule_score * 0.3
            buffer_append({
                "source": "ai_scan",
                "ticker": ticker,
                "action": "EVALUATE",
                "scan_score": rule_score,
                "ai_score": ai_score,
                "confidence": confidence,
                "features": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return ai_score
        except Exception:
            return rule_score
