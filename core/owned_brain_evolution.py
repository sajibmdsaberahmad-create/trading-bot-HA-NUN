#!/usr/bin/env python3
"""
core/owned_brain_evolution.py — Your models, your data, daily improvement (no local Ollama required).

The flywheel:
  Live API (Groq/Gemini) = expensive TEACHER — best reasoning, rate-limited
  Everything logged       = YOUR training gold (models/*.jsonl, *.zip, *.joblib)
  Students on disk        = YOURS — improve every session, API use drops over time

Students (all run on your Mac, milliseconds):
  1. ppo_trader_replay.zip     — reflex policy (PPO micro-train on fills)
  2. teacher_proxy.joblib      — distilled council enter/skip (sklearn)
  3. scalper_weights.json      — heuristic scanner weights
  4. copilot_state.json        — session reasoning cache
  5. council_dataset.jsonl     — (exported) prompt→decision→outcome for future LLM student

You do NOT need Ollama on-device. API teaches; students compress knowledge into owned weights.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

MODELS = Path("models")
STATE_PATH = MODELS / "owned_brain_state.json"
DATASET_PATH = MODELS / "council_training_dataset.jsonl"
DECISION_LOG = MODELS / "ai_decision_log.jsonl"
BUFFER_PATH = MODELS / "experience_buffer.jsonl"


def _file_info(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path)}
    st = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size_kb": round(st.st_size / 1024, 1),
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def evolution_status(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Single dashboard: what you own, how mature each student is, API dependency level."""
    cfg = cfg or BotConfig()
    out: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "philosophy": "API teaches → students on disk learn → API calls shrink over time",
        "assets": {},
        "phases": {},
        "api_dependency": "high",
        "next_milestone": "",
    }

    # Owned weight files
    ppo_live = Path(cfg.MODEL_PATH)
    ppo_replay = Path(os.getenv("REPLAY_MODEL_PATH", "models/ppo_trader_replay.zip"))
    out["assets"]["ppo_live"] = _file_info(ppo_live)
    out["assets"]["ppo_replay"] = _file_info(ppo_replay)
    out["assets"]["teacher_proxy"] = _file_info(MODELS / "teacher_proxy.joblib")
    out["assets"]["scalper_weights"] = _file_info(MODELS / "scalper_weights.json")
    out["assets"]["copilot_state"] = _file_info(MODELS / "copilot_state.json")
    out["assets"]["experience_buffer"] = _file_info(BUFFER_PATH)
    out["assets"]["decision_log"] = _file_info(DECISION_LOG)
    out["assets"]["council_dataset"] = _file_info(DATASET_PATH)

    # Trade stats
    try:
        from core.ppo_teacher_training import trade_stats
        ts = trade_stats(n=500)
        out["trade_stats"] = {
            "count": ts.get("count", 0),
            "win_rate": round(float(ts.get("win_rate", 0)), 3),
            "avg_pnl": round(float(ts.get("avg_pnl", 0)), 2),
        }
    except Exception:
        out["trade_stats"] = {}

    # PPO phase
    ppo_ok = out["assets"]["ppo_replay"].get("exists") or out["assets"]["ppo_live"].get("exists")
    out["phases"]["ppo"] = "active" if ppo_ok else "missing"

    # Teacher proxy phase
    try:
        from core.hybrid_distiller import distillation_status, is_fast_path_enabled
        ds = distillation_status(cfg)
        out["phases"]["teacher_proxy"] = ds.get("phase", "collecting")
        out["phases"]["proxy_accuracy"] = ds.get("proxy_accuracy")
        out["phases"]["fast_path"] = is_fast_path_enabled(cfg)
        if ds.get("fast_path"):
            out["api_dependency"] = "medium"
    except Exception:
        out["phases"]["teacher_proxy"] = "unknown"

    # Teacher sessions
    teacher_log = MODELS / "ppo_teacher_sessions.jsonl"
    if teacher_log.is_file():
        out["assets"]["ppo_teacher_sessions"] = _file_info(teacher_log)
        out["phases"]["ppo_teacher"] = "active"
    else:
        out["phases"]["ppo_teacher"] = "not_started"

    # Copilot
    out["phases"]["copilot"] = "active" if out["assets"]["copilot_state"].get("exists") else "warming"

    # Buffer richness
    buf_lines = 0
    if BUFFER_PATH.is_file():
        with open(BUFFER_PATH) as f:
            buf_lines = sum(1 for _ in f)
    out["buffer_records"] = buf_lines

    # API dependency ladder
    closed = out["trade_stats"].get("count", 0)
    proxy_acc = out["phases"].get("proxy_accuracy") or 0
    if out["phases"].get("fast_path"):
        out["api_dependency"] = "low-medium"
        out["next_milestone"] = "Export council dataset; fine-tune small LLM student on cheap GPU"
    elif closed >= 100 and proxy_acc and float(proxy_acc) >= 0.55:
        out["api_dependency"] = "medium"
        out["next_milestone"] = "Enable hybrid fast_path when proxy acc ≥ 62% and 500 closes"
    elif closed >= 30:
        out["api_dependency"] = "high"
        out["next_milestone"] = f"Keep replay running — {100 - closed} trades until proxy distill"
    else:
        out["next_milestone"] = "Run replay/live to collect 100+ closed trades for first distill"

    if DATASET_PATH.is_file():
        with open(DATASET_PATH) as f:
            n_ds = sum(1 for _ in f)
        out["dataset_pairs"] = n_ds
        if n_ds >= 500:
            out["next_milestone"] = "Optional: fine-tune 1-3B model on council_dataset (Modal/Colab once)"

    try:
        from core.brain_maturity import maturity_snapshot
        out["maturity"] = maturity_snapshot(cfg)
        out["phases"]["growth_stage"] = out["maturity"].get("stage", "newborn")
    except Exception:
        pass

    return out


def export_council_dataset(
    *,
    min_records: int = 10,
    max_records: int = 50_000,
) -> Dict[str, Any]:
    """
    Build YOUR council training dataset: (context features + council decision + trade outcome).
    This is the asset that lets you train a small LLM student later on cheap cloud — once.
    """
    if not DECISION_LOG.is_file():
        return {"ok": False, "reason": "no_decision_log", "exported": 0}

    buffer: List[Dict] = []
    if BUFFER_PATH.is_file():
        with open(BUFFER_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        buffer.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    trades_by_ticker: Dict[str, List[Dict]] = {}
    features_by_ticker: Dict[str, List[Dict]] = {}
    for r in buffer:
        t = str(r.get("ticker", "")).upper()
        if not t:
            continue
        if r.get("source") in ("live_trade", "replay_live") and r.get("pnl_usd") is not None:
            trades_by_ticker.setdefault(t, []).append(r)
        if r.get("features") and r.get("source") in ("live_entry", "replay_live", "ppo_entry"):
            features_by_ticker.setdefault(t, []).append(r)

    exported = 0
    MODELS.mkdir(parents=True, exist_ok=True)
    with open(DATASET_PATH, "w", encoding="utf-8") as out_f:
        with open(DECISION_LOG) as f:
            for line in f:
                if exported >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("category") != "ENTRY_DECISION":
                    continue
                data = rec.get("data") or {}
                ticker = str(data.get("ticker", "")).upper()
                if not ticker:
                    continue
                ts = rec.get("timestamp", "")
                outcome = None
                for tr in trades_by_ticker.get(ticker, []):
                    if abs(_ts_diff(ts, tr.get("timestamp", ""))) < 7200:
                        outcome = {
                            "pnl_usd": tr.get("pnl_usd"),
                            "win": tr.get("win"),
                            "exit_reason": tr.get("exit_reason", tr.get("reason")),
                        }
                        break
                feat_snap = None
                for fe in features_by_ticker.get(ticker, []):
                    if abs(_ts_diff(ts, fe.get("timestamp", ""))) < 180:
                        feat_snap = {
                            "spike_ratio": fe.get("spike_ratio"),
                            "scan_score": fe.get("scan_score"),
                            "regime": fe.get("regime"),
                        }
                        break
                row = {
                    "timestamp": ts,
                    "ticker": ticker,
                    "teacher_enter": bool(data.get("enter")),
                    "teacher_confidence": float(data.get("confidence", 0) or 0),
                    "teacher_reason": str(data.get("reason", ""))[:300],
                    "teacher_pipeline": str(data.get("pipeline", "")),
                    "market_context": feat_snap,
                    "outcome": outcome,
                    "source": "groq_gemini_council",
                }
                out_f.write(json.dumps(row, separators=(",", ":")) + "\n")
                exported += 1

    log.info(f"📦 Exported {exported} council training pairs → {DATASET_PATH}")
    state = {}
    if STATE_PATH.is_file():
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    state["last_export"] = datetime.now(timezone.utc).isoformat()
    state["dataset_pairs"] = exported
    STATE_PATH.write_text(json.dumps(state, indent=2))
    return {"ok": exported >= min_records, "exported": exported, "path": str(DATASET_PATH)}


def _ts_diff(a: str, b: str) -> float:
    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00")).timestamp()
        tb = datetime.fromisoformat(b.replace("Z", "+00:00")).timestamp()
        return ta - tb
    except Exception:
        return 999999.0


def log_evolution_summary(cfg: Optional[BotConfig] = None) -> None:
    st = evolution_status(cfg)
    log.info("=" * 60)
    log.info("  OWNED BRAIN EVOLUTION — your models, improving daily")
    log.info(f"  API dependency: {st.get('api_dependency', '?')} | next: {st.get('next_milestone', '')}")
    ts = st.get("trade_stats") or {}
    log.info(f"  Trades: {ts.get('count', 0)} | WR={ts.get('win_rate', 0):.0%} | buffer={st.get('buffer_records', 0):,}")
    for name, phase in (st.get("phases") or {}).items():
        if name != "proxy_accuracy":
            log.info(f"  {name}: {phase}")
    for key in ("ppo_replay", "teacher_proxy", "council_dataset"):
        info = (st.get("assets") or {}).get(key, {})
        if info.get("exists"):
            log.info(f"  ✓ {key} ({info.get('size_kb', 0)} KB)")
    log.info("=" * 60)


MANIFEST_PATH = MODELS / "owned_brain_manifest.json"
DEVICE_PROFILE_PATH = MODELS / "device_profile.json"

DEVICE_PROFILES: Dict[str, Dict[str, Any]] = {
    "m2_8gb": {
        "ram_mb_max": 10240,
        "ram_tier": "compact",
        "ppo_micro_steps": 512,
        "ppo_teacher_enabled": True,
        "proxy_train_enabled": True,
        "heavy_training": False,
        "council_model_hint": "llama-3.1-8b-instant",
        "notes": "No local LLM. PPO+proxy+copilot on CPU. Groq 8B teacher via API.",
    },
    "m2_16gb": {
        "ram_mb_max": 20480,
        "ram_tier": "balanced",
        "ppo_micro_steps": 1024,
        "ppo_teacher_enabled": True,
        "proxy_train_enabled": True,
        "heavy_training": True,
        "council_model_hint": "llama-3.3-70b-versatile",
        "notes": "Optional MLX 3B student later.",
    },
    "m2_32gb_plus": {
        "ram_mb_max": 999999,
        "ram_tier": "standard",
        "ppo_micro_steps": 2048,
        "ppo_teacher_enabled": True,
        "proxy_train_enabled": True,
        "heavy_training": True,
        "council_model_hint": "llama-3.3-70b-versatile",
        "notes": "Can run MLX 3-7B local copilot when ready.",
    },
}

_last_evolution_ts = 0.0


def detect_device_profile() -> str:
    override = os.getenv("OWNED_BRAIN_DEVICE", "").strip().lower()
    if override in DEVICE_PROFILES:
        return override
    try:
        from core.memory_guard import total_ram_mb
        ram = total_ram_mb()
    except Exception:
        ram = 8192
    if ram <= 10240:
        return "m2_8gb"
    if ram <= 20480:
        return "m2_16gb"
    return "m2_32gb_plus"


def device_limits(profile: Optional[str] = None) -> Dict[str, Any]:
    profile = profile or detect_device_profile()
    base = dict(DEVICE_PROFILES.get(profile, DEVICE_PROFILES["m2_8gb"]))
    base["profile"] = profile
    try:
        from core.memory_guard import total_ram_mb
        base["detected_ram_mb"] = total_ram_mb()
    except Exception:
        base["detected_ram_mb"] = 0
    return base


def write_device_profile() -> Dict[str, Any]:
    profile = detect_device_profile()
    limits = device_limits(profile)
    payload = {
        "profile": profile,
        "detected_ram_mb": limits.get("detected_ram_mb"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **limits,
    }
    MODELS.mkdir(parents=True, exist_ok=True)
    DEVICE_PROFILE_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def write_manifest(cfg: BotConfig, evolution_result: Dict[str, Any]) -> None:
    st = evolution_status(cfg)
    manifest = {
        "name": "M. A. Halim",
        "legacy_name": "HANOON Owned Brain",
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "device": write_device_profile(),
        "evolution": evolution_result,
        "status": st,
        "portable_assets": [
            "models/ppo_trader_replay.zip",
            "models/ppo_trader.zip",
            "models/teacher_proxy.joblib",
            "models/scalper_weights.json",
            "models/council_training_dataset.jsonl",
            "models/experience_buffer.jsonl",
            "models/copilot_state.json",
            "models/owned_brain_manifest.json",
        ],
        "docs": "docs/OWNED_BRAIN.md",
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def run_post_session_evolution(
    cfg: Optional[BotConfig] = None,
    *,
    model: Any = None,
    trigger: str = "session_end",
    push_git: bool = True,
) -> Dict[str, Any]:
    """End-of-session flywheel: export, train proxy, manifest, git push."""
    global _last_evolution_ts
    import time as _time

    min_gap = float(os.getenv("OWNED_BRAIN_MIN_EVOLUTION_SEC", "120"))
    if _time.time() - _last_evolution_ts < min_gap:
        return {"skipped": True, "reason": "recent_evolution"}
    _last_evolution_ts = _time.time()

    cfg = cfg or BotConfig()
    limits = device_limits()
    profile = limits["profile"]

    try:
        from core.brain_maturity import (
            apply_maturity_to_config,
            evolution_progress,
            log_maturity_banner,
            maturity_limits,
            record_evolution,
        )
        mat = apply_maturity_to_config(cfg)
        mat_lim = maturity_limits(cfg)
        log.info(
            f"🧬 OWNED BRAIN evolution ({trigger}) — device={profile} "
            f"stage={mat.get('stage', '?')}"
        )
    except Exception:
        mat_lim = {}
        evolution_progress = lambda s: log.info(f"  🧬 evolution step: {s}…")  # noqa: E731
        record_evolution = lambda: None  # noqa: E731

    result: Dict[str, Any] = {
        "trigger": trigger,
        "device_profile": profile,
        "stage": mat_lim.get("stage") if mat_lim else "unknown",
        "steps": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        evolution_progress("export council dataset")
        result["steps"]["export_dataset"] = export_council_dataset()
    except Exception as exc:
        result["steps"]["export_dataset"] = {"ok": False, "error": str(exc)[:120]}

    proxy_min = int(mat_lim.get("proxy_min_trades", getattr(cfg, "HYBRID_DISTILL_MIN_TRADES", 30)))
    if limits.get("proxy_train_enabled", True):
        try:
            evolution_progress("train teacher proxy (sklearn)")
            from core.hybrid_distiller import train_teacher_proxy, distillation_status
            ds_before = distillation_status(cfg)
            if ds_before.get("closed_trades", 0) >= proxy_min:
                result["steps"]["train_proxy"] = train_teacher_proxy(cfg)
            else:
                result["steps"]["train_proxy"] = {
                    "skipped": True,
                    "closed": ds_before.get("closed_trades", 0),
                    "need": proxy_min,
                }
        except Exception as exc:
            result["steps"]["train_proxy"] = {"ok": False, "error": str(exc)[:120]}

    if limits.get("ppo_teacher_enabled", True):
        try:
            evolution_progress("PPO teacher (local-first)")
            from core.ppo_teacher_training import run_ppo_teacher_session
            result["steps"]["ppo_teacher"] = run_ppo_teacher_session(
                cfg, model=model, trigger=trigger, force=False,
            )
        except Exception as exc:
            result["steps"]["ppo_teacher"] = {"ok": False, "error": str(exc)[:120]}

    try:
        evolution_progress("update scalper weights")
        from core.online_trainer import _update_weights_from_buffer
        _update_weights_from_buffer()
        result["steps"]["weights"] = {"ok": True}
    except Exception as exc:
        result["steps"]["weights"] = {"ok": False, "error": str(exc)[:120]}

    evolution_progress("write manifest")
    write_manifest(cfg, result)
    try:
        from core.halim_identity import write_halim_manifest
        write_halim_manifest(cfg)
    except Exception:
        pass

    try:
        from core.halim_gold_pipeline import run_halim_gold_pipeline
        is_replay = "replay" in str(trigger).lower()
        result["steps"]["halim_gold_pipeline"] = run_halim_gold_pipeline(
            cfg,
            trigger=trigger,
            prepare_sft=is_replay or os.getenv("HALIM_PREPARE_SFT_ON_EVOLUTION", "true").lower() in ("1", "true", "yes"),
            package_colab=is_replay or os.getenv("HALIM_AUTO_PACKAGE_COLAB", "true").lower() in ("1", "true", "yes"),
        )
    except Exception as exc:
        result["steps"]["halim_gold_pipeline"] = {"ok": False, "error": str(exc)[:120]}

    try:
        from core.halim_registry import append_evolution_registry
        append_evolution_registry(result)
    except Exception:
        pass

    try:
        from core.halim_ppo_coevolution import run_coevolution_cycle
        result["steps"]["coevolution"] = run_coevolution_cycle(
            cfg, model=model, trigger=trigger,
        )
    except Exception as exc:
        result["steps"]["coevolution"] = {"ok": False, "error": str(exc)[:120]}

    state: Dict[str, Any] = {}
    if STATE_PATH.is_file():
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    state["last_evolution"] = result["timestamp"]
    state["last_trigger"] = trigger
    state["device_profile"] = profile
    state["stage"] = result.get("stage")
    try:
        record_evolution()
        state = json.loads(STATE_PATH.read_text())
    except Exception:
        state["evolution_count"] = int(state.get("evolution_count", 0)) + 1
        STATE_PATH.write_text(json.dumps(state, indent=2))

    log_evolution_summary(cfg)

    if push_git and os.getenv("OWNED_BRAIN_GIT_PUSH", "true").lower() in ("1", "true", "yes"):
        try:
            from core.halim_developer import run_halim_developer_cycle
            dev = run_halim_developer_cycle(cfg, trigger=f"post_{trigger}", push_git=True)
            result["steps"]["halim_developer"] = dev
            result["git_push"] = (dev.get("steps") or {}).get("git_push", "queued")
        except Exception as exc:
            result["steps"]["halim_developer"] = {"ok": False, "error": str(exc)[:120]}
            result["git_push"] = f"failed:{exc}"

    result["ok"] = True
    log.info(f"🧬 Owned brain evolution complete — {MANIFEST_PATH}")

    try:
        from core.brain_notify import notify_evolution_complete
        notify_evolution_complete(cfg, result)
    except Exception as exc:
        log.debug(f"Brain evolution notify: {exc}")

    return result
