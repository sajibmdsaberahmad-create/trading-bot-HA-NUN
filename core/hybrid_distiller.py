#!/usr/bin/env python3
"""
core/hybrid_distiller.py — Automatic Halim↔PPO knowledge distillation.

Phase 0 (R&D): Cloud council + PPO run together; every decision is logged.
Phase 1 (distill): Once enough closed trades exist, train a fast TeacherProxy
    (sklearn) that maps numeric market state → council-style enter/confidence.
Phase 2 (fast path): When proxy accuracy is high enough, live entry decisions
    skip cloud council (milliseconds) and use PPO + TeacherProxy instead.

Council stays active for Telegram, journaling, and off-hours training.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.config import BotConfig
from core.notify import log

MODELS_DIR = Path("models")
DECISION_LOG = MODELS_DIR / "ai_decision_log.jsonl"
BUFFER_PATH = MODELS_DIR / "experience_buffer.jsonl"
PROXY_PATH = MODELS_DIR / "teacher_proxy.joblib"
_proxy_bundle_cache: Optional[Dict[str, Any]] = None
_proxy_bundle_mtime: float = 0.0
STATE_PATH = MODELS_DIR / "hybrid_distill_state.json"

# Extra numeric channels beyond flat PPO obs (spike, scan, spread, volume)
N_EXTRA = 5


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def distillation_status(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Current phase and metrics for dashboards / logs."""
    cfg = cfg or BotConfig()
    state = _load_state()
    closed = _count_closed_trades()
    min_trades = int(getattr(cfg, "HYBRID_DISTILL_MIN_TRADES", 100))
    full_trades = int(getattr(cfg, "HYBRID_DISTILL_FULL_TRADES", 500))
    phase = "collecting"
    if closed >= full_trades and state.get("fast_path"):
        phase = "fast_path"
    elif closed >= min_trades and PROXY_PATH.exists():
        phase = "distilled"
    elif closed >= min_trades:
        phase = "ready_to_distill"
    return {
        "phase": phase,
        "closed_trades": closed,
        "min_trades": min_trades,
        "full_trades": full_trades,
        "fast_path": bool(state.get("fast_path")),
        "proxy_accuracy": state.get("proxy_accuracy"),
        "last_train": state.get("last_train"),
        "proxy_exists": PROXY_PATH.exists(),
    }


def _load_buffer_records(max_records: int = 800) -> List[Dict[str, Any]]:
    from core.experience_buffer import load_recent
    return load_recent(max_records)


def _count_closed_trades() -> int:
    state = _load_state()
    cached = state.get("closed_trade_count")
    if cached is not None:
        return int(cached)
    if not BUFFER_PATH.exists():
        return 0
    n = 0
    for rec in _load_buffer_records(1200):
        if rec.get("source") in ("live_trade", "replay_live") and rec.get("action") in (
            "SELL", "TRADE", None,
        ):
            n += 1
    state["closed_trade_count"] = n
    _save_state(state)
    return n


def note_closed_trade_for_distill() -> None:
    state = _load_state()
    state["closed_trade_count"] = int(state.get("closed_trade_count", 0)) + 1
    _save_state(state)


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _load_entry_decisions(max_records: int = 600) -> List[Dict[str, Any]]:
    if not DECISION_LOG.exists():
        return []
    out = []
    with open(DECISION_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("category") != "ENTRY_DECISION":
                continue
            data = r.get("data") or {}
            if not data.get("ticker"):
                continue
            out.append({
                "ts": _parse_ts(r.get("timestamp", "")),
                "ticker": data["ticker"],
                "enter": bool(data.get("enter")),
                "confidence": float(data.get("confidence", 0.5) or 0.5),
                "reason": str(data.get("reason", ""))[:120],
            })
    return out[-max_records:]


def _build_feature_matrix(
    cfg: BotConfig,
    decisions: List[Dict[str, Any]],
    buffer: List[Dict[str, Any]],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Join Ollama entry decisions with experience-buffer feature snapshots.
    Returns X, y_enter (0/1), sample_weights.
    """
    entries_by_ticker: Dict[str, List[Dict]] = {}
    trades_by_ticker: Dict[str, List[Dict]] = {}
    all_feats_by_ticker: Dict[str, List[Dict]] = {}
    for r in buffer:
        t = r.get("ticker", "")
        if not t:
            continue
        if r.get("source") in ("live_entry", "replay_live", "ppo_entry") and r.get("features"):
            entries_by_ticker.setdefault(t, []).append(r)
        if r.get("features"):
            all_feats_by_ticker.setdefault(t, []).append(r)
        if r.get("source") in ("live_trade", "replay_live"):
            trades_by_ticker.setdefault(t, []).append(r)

    rows_x: List[np.ndarray] = []
    rows_y: List[int] = []
    weights: List[float] = []

    obs_dim = cfg.WINDOW_SIZE * cfg.N_FEATURES + 2 + N_EXTRA

    def _pick_features(ticker: str, ts: float, max_dt: float = 600.0) -> Optional[Dict]:
        best, best_dt = None, 999999.0
        for pool in (entries_by_ticker.get(ticker, []), all_feats_by_ticker.get(ticker, [])):
            for c in pool:
                dt = abs(_parse_ts(c.get("timestamp", "")) - ts)
                if dt < best_dt and dt < max_dt:
                    best_dt = dt
                    best = c
        return best

    for dec in decisions:
        ticker = dec["ticker"]
        ts = dec["ts"]
        feat_rec = _pick_features(ticker, ts)
        if feat_rec is None and dec["enter"]:
            continue
        if feat_rec is None:
            # Skip decisions: use zero base + council confidence channel
            base = np.zeros(cfg.WINDOW_SIZE * cfg.N_FEATURES, dtype=np.float32)
            cash, pos, spike, scan, spread, vol = 0.9, 0.0, 1.0, 0.0, 0.0, 1.0
        else:
            flat = feat_rec.get("features") or []
            if len(flat) < cfg.WINDOW_SIZE * cfg.N_FEATURES:
                if dec["enter"]:
                    continue
                base = np.zeros(cfg.WINDOW_SIZE * cfg.N_FEATURES, dtype=np.float32)
                cash, pos = 0.9, 0.0
                spike = float(feat_rec.get("spike_ratio", 1.0))
                scan = float(feat_rec.get("scan_score", 0.0))
                spread = float(feat_rec.get("spread_pct", 0.0))
                vol = float(feat_rec.get("vol_ratio", 1.0))
            else:
                base = np.array(flat[: cfg.WINDOW_SIZE * cfg.N_FEATURES], dtype=np.float32)
                cash = float(feat_rec.get("cash_ratio", 0.9))
                pos = float(feat_rec.get("pos_ratio", 0.0))
                spike = float(feat_rec.get("spike_ratio", 1.0))
                scan = float(feat_rec.get("scan_score", 0.0))
                spread = float(feat_rec.get("spread_pct", 0.0))
                vol = float(feat_rec.get("vol_ratio", 1.0))
        vec = np.concatenate([
            base,
            [cash, pos, spike, scan, spread, vol, float(dec.get("confidence", 0.5))],
        ]).astype(np.float32)
        if vec.shape[0] != obs_dim:
            vec = np.resize(vec, obs_dim)

        w = 1.0
        for tr in trades_by_ticker.get(ticker, []):
            if abs(_parse_ts(tr.get("timestamp", "")) - ts) < 3600:
                pnl = float(tr.get("pnl_usd", 0) or 0)
                w = 1.0 + min(abs(pnl) / 25.0, 3.0)
                if dec["enter"] and pnl > 0:
                    w *= 1.5
                elif dec["enter"] and pnl < 0:
                    w *= 0.7
                break

        rows_x.append(vec)
        rows_y.append(1 if dec["enter"] else 0)
        weights.append(w)

    if not rows_x:
        return np.zeros((0, obs_dim)), np.zeros(0), np.zeros(0)
    return np.vstack(rows_x), np.array(rows_y, dtype=np.int32), np.array(weights, dtype=np.float32)


def _obs_to_vector(
    obs: np.ndarray,
    spike_ratio: float,
    scan_score: float,
    market_ctx: Optional[Dict[str, Any]],
) -> np.ndarray:
    """Build proxy input from live PPO observation + market context."""
    base = np.asarray(obs, dtype=np.float32).flatten()
    mctx = market_ctx or {}
    spread = float(mctx.get("spread_pct", 0) or 0)
    vol = float(mctx.get("recent_volume", 0) or 0) / (float(mctx.get("avg_volume", 1) or 1) + 1e-9)
    extra = np.array([spike_ratio, scan_score, spread, vol, 0.0], dtype=np.float32)
    return np.concatenate([base, extra]).astype(np.float32)


def train_teacher_proxy(cfg: BotConfig) -> Dict[str, Any]:
    """Step A: distill Ollama entry decisions into a fast numeric proxy."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        import joblib
    except ImportError as exc:
        log.warning(f"Hybrid distillation skipped — sklearn/joblib missing: {exc}")
        return {"ok": False, "reason": "missing_sklearn"}

    decisions = _load_entry_decisions()
    buffer = _load_buffer_records()
    X, y, sample_w = _build_feature_matrix(cfg, decisions, buffer)

    min_samples = int(getattr(cfg, "HYBRID_DISTILL_MIN_SAMPLES", 30))
    if len(y) < min_samples:
        return {
            "ok": False,
            "reason": f"need_{min_samples}_paired_samples",
            "paired": int(len(y)),
            "decisions": len(decisions),
        }

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    X_train, X_test, y_train, y_test, w_train, _ = train_test_split(
        Xs, y, sample_w, test_size=0.2, random_state=42, stratify=y if len(np.unique(y)) > 1 else None,
    )

    clf = LogisticRegression(max_iter=500, class_weight="balanced")
    try:
        clf.fit(X_train, y_train, sample_weight=w_train)
    except ValueError as exc:
        if "needs samples of at least 2 classes" in str(exc):
            return {
                "ok": False,
                "reason": "single_class",
                "error": str(exc)[:120],
                "paired": int(len(y)),
                "class_counts": {"enter": int(y.sum()), "skip": int(len(y) - y.sum())},
            }
        raise
    accuracy = float(clf.score(X_test, y_test))

    bundle = {
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": clf.coef_.tolist(),
        "intercept": clf.intercept_.tolist(),
        "obs_dim": int(X.shape[1]),
        "accuracy": accuracy,
        "samples": int(len(y)),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    MODELS_DIR.mkdir(exist_ok=True)
    joblib.dump(bundle, PROXY_PATH)

    state = _load_state()
    state["last_train"] = bundle["trained_at"]
    state["proxy_accuracy"] = accuracy
    state["proxy_samples"] = len(y)
    min_acc = float(getattr(cfg, "HYBRID_DISTILL_MIN_ACCURACY", 0.62))
    full_trades = int(getattr(cfg, "HYBRID_DISTILL_FULL_TRADES", 500))
    closed = _count_closed_trades()
    auto_fast = getattr(cfg, "HYBRID_DISTILL_AUTO_FAST_PATH", True)
    if auto_fast and accuracy >= min_acc and closed >= full_trades:
        state["fast_path"] = True
        log.info(
            f"⚡ Hybrid fast path ENABLED — proxy acc={accuracy:.0%} | "
            f"closed_trades={closed} (Ollama skipped on entry, still used for alerts)"
        )
    _save_state(state)

    log.info(
        f"🎓 Teacher proxy trained — acc={accuracy:.0%} | samples={len(y)} | "
        f"phase={distillation_status(cfg)['phase']}"
    )
    try:
        from core.brain_notify import notify_brain_development
        from core.brain_maturity import maturity_snapshot
        snap = maturity_snapshot(cfg)
        notify_brain_development(
            cfg,
            "brain_proxy_trained",
            {
                "accuracy": accuracy,
                "samples": len(y),
                "stage": snap.get("stage"),
                "fast_path": bool(state.get("fast_path")),
                "summary": f"Teacher proxy acc={accuracy:.0%} ({len(y)} samples)",
            },
        )
    except Exception:
        pass
    return {"ok": True, "accuracy": accuracy, "samples": len(y)}


def is_fast_path_enabled(cfg: BotConfig) -> bool:
    if not getattr(cfg, "HYBRID_DISTILLATION_ENABLED", True):
        return False
    if getattr(cfg, "HYBRID_DISTILL_FAST_PATH", False):
        return True
    state = _load_state()
    return bool(state.get("fast_path"))


def _load_proxy_bundle() -> Optional[Dict[str, Any]]:
    """Load teacher proxy once — reload only when joblib file changes."""
    global _proxy_bundle_cache, _proxy_bundle_mtime
    if not PROXY_PATH.exists():
        _proxy_bundle_cache = None
        return None
    try:
        mtime = PROXY_PATH.stat().st_mtime
    except OSError:
        return None
    if _proxy_bundle_cache is not None and mtime == _proxy_bundle_mtime:
        return _proxy_bundle_cache
    try:
        import joblib
        bundle = joblib.load(PROXY_PATH)
    except Exception:
        return None
    _proxy_bundle_cache = bundle
    _proxy_bundle_mtime = mtime
    return bundle


def predict_teacher_proxy(
    obs: np.ndarray,
    spike_ratio: float,
    scan_score: float,
    market_ctx: Optional[Dict[str, Any]],
    cfg: Optional[BotConfig] = None,
) -> Optional[Dict[str, Any]]:
    """Microsecond inference — distilled Ollama entry signal."""
    bundle = _load_proxy_bundle()
    if bundle is None:
        return None

    vec = _obs_to_vector(obs, spike_ratio, scan_score, market_ctx)
    obs_dim = int(bundle.get("obs_dim", len(vec)))
    if len(vec) != obs_dim:
        vec = np.resize(vec, obs_dim)

    mean = np.array(bundle["scaler_mean"], dtype=np.float32)
    scale = np.array(bundle["scaler_scale"], dtype=np.float32)
    scale = np.where(scale < 1e-9, 1.0, scale)
    xs = (vec - mean) / scale

    coef = np.array(bundle["coef"], dtype=np.float32)
    intercept = float(bundle["intercept"][0])
    logit = float(np.dot(coef, xs) + intercept)
    prob = 1.0 / (1.0 + np.exp(-logit))
    threshold = float(getattr(cfg or BotConfig(), "HYBRID_DISTILL_ENTER_THRESHOLD", 0.45))

    return {
        "enter": prob >= threshold,
        "confidence": prob,
        "gut_feel": prob,
        "reason": f"Distilled teacher proxy conf={prob:.0%}",
        "journal": f"Fast path — proxy acc={bundle.get('accuracy', 0):.0%}",
        "_proxy": True,
    }


def proxy_entry_decision(
    obs: np.ndarray,
    spike_ratio: float,
    scan_score: float,
    market_ctx: Optional[Dict[str, Any]],
    cfg: BotConfig,
) -> Optional[Dict[str, Any]]:
    """Full entry JSON shape for AICommander when student proxy is active."""
    if not PROXY_PATH.exists():
        return None
    try:
        from core.brain_maturity import should_use_student_entry
        if should_use_student_entry(cfg) or is_fast_path_enabled(cfg):
            return predict_teacher_proxy(obs, spike_ratio, scan_score, market_ctx, cfg)
    except Exception:
        if is_fast_path_enabled(cfg):
            return predict_teacher_proxy(obs, spike_ratio, scan_score, market_ctx, cfg)
    return None


def maybe_run_hybrid_distillation(cfg: BotConfig) -> Dict[str, Any]:
    """
    Auto-called after closed trades. Trains proxy when thresholds are met.
    """
    if not getattr(cfg, "HYBRID_DISTILLATION_ENABLED", True):
        return {"skipped": True, "reason": "disabled"}
    try:
        from core.learning_coordinator import should_defer_heavy_learning, memory_pressure_high
        if should_defer_heavy_learning(cfg) or memory_pressure_high(cfg):
            return {"skipped": True, "reason": "deferred_or_memory"}
    except Exception:
        pass

    closed = _count_closed_trades()
    min_trades = int(getattr(cfg, "HYBRID_DISTILL_MIN_TRADES", 100))
    every_n = int(getattr(cfg, "HYBRID_DISTILL_CHECK_EVERY_N_TRADES", 5))

    status = distillation_status(cfg)
    if closed < min_trades:
        if closed % max(every_n, 1) == 0:
            log.info(
                f"🎓 Hybrid distill collecting — {closed}/{min_trades} closed trades "
                f"(Halim+PPO teaching each other; distill auto-starts at {min_trades})"
            )
        return {"phase": "collecting", "closed_trades": closed}

    state = _load_state()
    last_train = state.get("last_train", "")
    min_retrain_hours = float(getattr(cfg, "HYBRID_DISTILL_RETRAIN_HOURS", 24))
    if last_train:
        try:
            age_h = (time.time() - _parse_ts(last_train)) / 3600
            if age_h < min_retrain_hours and PROXY_PATH.exists():
                return {"phase": status["phase"], "skipped": True, "reason": "recent_train"}
        except Exception:
            pass

    result = train_teacher_proxy(cfg)
    if result.get("ok"):
        try:
            from core.git_sync import push_learning_checkpoint_async
            push_learning_checkpoint_async("hybrid_distill_proxy")
        except Exception:
            pass
    return result
