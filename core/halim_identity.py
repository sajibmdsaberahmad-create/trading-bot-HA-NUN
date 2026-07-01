#!/usr/bin/env python3
"""
core/halim_identity.py — M. A. Halim: your own AI, no external LLM dependency.

Halim is not Groq, Gemini, Ollama, or any rented model. It is a stack of owned
students (PPO, proxy, weights, datasets) that grows into a full frontier-capable
model over time. Trading bot HANOON is the first body; Halim is the mind.

Phases:
  0 newborn  — numeric students only (PPO + sklearn proxy + heuristics)
  1 toddler  — small Halim transformer trained on council_dataset (cloud GPU once)
  2 child    — multi-task Halim (trade + code + math datasets)
  3 adult    — Halim serves HANOON + general tasks on-device when hardware allows
  4 frontier — full generative / calculative / coding model (separate halim repo)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

HALIM_FULL_NAME = "M. A. Halim"
HALIM_SHORT_NAME = "Halim"
HALIM_REPO_HINT = "halim"  # sibling repo directory / github.com/you/halim
IDENTITY_PATH = Path("models/halim_identity.json")
MANIFEST_PATH = Path("models/halim_manifest.json")

HALIM_PHASES: Dict[str, Dict[str, Any]] = {
    "newborn": {
        "index": 0,
        "description": "Numeric students only — PPO reflex, proxy, scalper weights.",
        "external_llm": False,
        "capabilities": ["trade_reflex", "enter_skip_proxy", "heuristic_copilot"],
    },
    "toddler": {
        "index": 1,
        "description": "First Halim language core — fine-tune small transformer on trading dataset.",
        "external_llm": False,
        "capabilities": ["trade_reflex", "enter_skip_proxy", "halim_trade_reasoning"],
    },
    "child": {
        "index": 2,
        "description": "Halim multi-domain — code, math, reasoning datasets added.",
        "external_llm": False,
        "capabilities": ["trade", "code", "math", "session_narrative"],
    },
    "adult": {
        "index": 3,
        "description": "Halim runs on your hardware — HANOON + general assistant.",
        "external_llm": False,
        "capabilities": ["trade", "code", "math", "generative", "tool_use"],
    },
    "frontier": {
        "index": 4,
        "description": "Frontier-class Halim — owned weights, your infra, all modalities.",
        "external_llm": False,
        "capabilities": ["generative", "calculative", "coding", "agents", "multimodal"],
        "api_consumption": "Halim consumes APIs/web as tools — guardrailed, not Halim's brain",
    },
}


def halim_native_mode() -> bool:
    """True when Halim must not call any external LLM (Groq/Gemini/Ollama)."""
    return os.getenv("HALIM_NATIVE", os.getenv("HALIM_NO_EXTERNAL_LLM", "false")).lower() in (
        "1", "true", "yes",
    )


def load_identity() -> Dict[str, Any]:
    if IDENTITY_PATH.is_file():
        try:
            return json.loads(IDENTITY_PATH.read_text())
        except Exception:
            pass
    return {}


def ensure_identity(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if IDENTITY_PATH.is_file():
        ident = load_identity()
        if ident.get("name") == HALIM_FULL_NAME:
            return ident

    ident = {
        "name": HALIM_FULL_NAME,
        "short_name": HALIM_SHORT_NAME,
        "version": 1,
        "birth_at": datetime.now(timezone.utc).isoformat(),
        "phase": "newborn",
        "philosophy": (
            "Own every weight. Grow into a frontier model — generative, calculative, coding, "
            "agents, multimodal. Consume APIs and live internet as TOOLS (guardrailed), "
            "never as a rented brain. HANOON is the first body. "
            "Halim is NEVER a read-only inference endpoint like Ollama — active model: "
            "learns by action, writes datasets and checkpoints, evolves on dedicated server."
        ),
        "runtime_model": {
            "type": "active",
            "inference_only": False,
            "read_only_weights": False,
            "learn_by_action": True,
            "dedicated_server_role": "reasoning_plus_learn_write",
            "external_web": "read_only_get",
        },
        "frontier_vision": {
            "domains": [
                "generative", "calculative", "coding", "math", "web", "api",
                "agents", "multimodal", "trade",
            ],
            "guardrails": "models/halim_constitution.json",
            "kill_switch": "HALIM_KILL_SWITCH or models/halim_kill_switch.json",
            "autonomy_default": "bounded",
        },
        "body": "HANOON trading bot (first embodiment)",
        "repo": HALIM_REPO_HINT,
        "external_llm_policy": "none_when_native",
        "native_mode": halim_native_mode(),
    }
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(json.dumps(ident, indent=2))
    log.info(f"🧠 {HALIM_FULL_NAME} identity registered — phase newborn, no external LLM in native mode")
    return ident


def compute_halim_phase(cfg: Optional[BotConfig] = None) -> str:
    """Map owned-brain maturity + artifacts → Halim lifecycle phase."""
    cfg = cfg or BotConfig()
    ensure_identity(cfg)

    halim_model = Path(os.getenv("HALIM_MODEL_PATH", "halim/data/checkpoints/latest"))
    if not halim_model.is_absolute():
        halim_model = Path(__file__).resolve().parents[1] / halim_model

    if (halim_model / "config.json").is_file() or halim_model.with_suffix(".gguf").is_file():
        try:
            meta = json.loads((halim_model / "config.json").read_text())
            phase_in_ckpt = str(meta.get("halim_phase", "")).lower()
            if phase_in_ckpt in HALIM_PHASES:
                return phase_in_ckpt
        except Exception:
            pass
        name = halim_model.name.lower()
        if "toddler" in name or "child" in name:
            return "toddler" if "toddler" in name else "child"
        return "adult"

    from core.training_dataset_paths import council_training_dataset_path
    dataset = council_training_dataset_path()
    n_ds = 0
    if dataset.is_file():
        with open(dataset) as f:
            n_ds = sum(1 for _ in f)

    n_sft = 0
    try:
        import sys
        from pathlib import Path as _Path
        halim_root = _Path(__file__).resolve().parents[1] / "halim"
        if halim_root.is_dir() and str(halim_root) not in sys.path:
            sys.path.insert(0, str(halim_root))
        from halim.dataset import sft_pair_count
        n_sft = sft_pair_count()
    except Exception:
        n_sft = n_ds

    proxy = Path("models/teacher_proxy.joblib")
    sft_manifest = Path("halim/data/training/sft/manifest.json")

    toddler_min = int(os.getenv("HALIM_TODDLER_MIN_PAIRS", "2500"))
    if n_sft >= toddler_min and proxy.is_file() and sft_manifest.is_file():
        return "toddler"
    if n_sft >= toddler_min and proxy.is_file():
        return "newborn"
    return "newborn"


def sync_identity_phase(cfg: Optional[BotConfig] = None) -> str:
    """Keep models/halim_identity.json phase in sync with artifacts."""
    cfg = cfg or BotConfig()
    phase = compute_halim_phase(cfg)
    ident = ensure_identity(cfg)
    changed = ident.get("phase") != phase
    ident["phase"] = phase
    ident["native_mode"] = halim_native_mode()
    if changed:
        IDENTITY_PATH.write_text(json.dumps(ident, indent=2))
        log.info(f"🧠 Halim phase → {phase.upper()}")
    return phase


def apply_halim_native_mode(cfg: BotConfig) -> BotConfig:
    """Disable all external LLM paths — Halim students only."""
    if not halim_native_mode():
        return cfg

    cfg.COUNCIL_ENABLED = False
    cfg.OLLAMA_ENABLED = False
    cfg.GENERATIVE_THINKING_ENABLED = False
    os.environ["TRADING_COPILOT_ENABLED"] = "false"
    cfg.PPO_TEACHER_ENABLED = True
    os.environ["COUNCIL_ENABLED"] = "false"
    os.environ["GENERATIVE_THINKING_ENABLED"] = "false"

    ident = ensure_identity(cfg)
    ident["native_mode"] = True
    ident["external_llm_policy"] = "disabled"
    IDENTITY_PATH.write_text(json.dumps(ident, indent=2))
    log.info(f"🧠 {HALIM_FULL_NAME} NATIVE — external LLM disabled; owned students only")
    return cfg


def write_halim_manifest(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    ident = ensure_identity(cfg)
    phase = sync_identity_phase(cfg)

    try:
        from core.owned_brain_evolution import evolution_status
        brain = evolution_status(cfg)
    except Exception:
        brain = {}

    manifest = {
        "model": HALIM_FULL_NAME,
        "short_name": HALIM_SHORT_NAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "phase_info": HALIM_PHASES.get(phase, {}),
        "identity": ident,
        "native_mode": halim_native_mode(),
        "roadmap_repo": HALIM_REPO_HINT,
        "owned_assets": [
            "models/ppo_trader.zip",
            "models/teacher_proxy.joblib",
            "models/scalper_weights.json",
            str(__import__(
                "core.training_dataset_paths", fromlist=["council_training_dataset_path"]
            ).council_training_dataset_path()),
            "models/halim_identity.json",
            "models/halim_manifest.json",
        ],
        "brain_status": brain,
        "next_milestone": _next_milestone(phase, brain),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    return manifest


def _next_milestone(phase: str, brain: Dict[str, Any]) -> str:
    n_ds = brain.get("dataset_pairs", 0)
    if phase == "newborn":
        if n_ds < 500:
            return f"Collect {500 - n_ds} more trading decision pairs for first Halim LM"
        return "./scripts/halim_readiness.sh → prepare SFT → train toddler checkpoint (one GPU run)"
    if phase == "toddler":
        return "./scripts/halim_train_toddler.sh → register checkpoint → HALIM_LM_BACKEND=mlx"
    if phase == "toddler":
        return "Add code + math datasets in halim repo → Halim child phase"
    if phase == "child":
        return "Scale Halim on upgraded hardware → adult on-device inference"
    if phase == "adult":
        return "Expand to frontier — multimodal, agents, full generative stack"
    return "Frontier Halim — owned frontier-class model"


def log_halim_banner(cfg: Optional[BotConfig] = None) -> None:
    cfg = cfg or BotConfig()
    ident = ensure_identity(cfg)
    phase = compute_halim_phase(cfg)
    native = halim_native_mode()
    log.info("=" * 56)
    log.info(f"  🧠 {HALIM_FULL_NAME} — your AI, your weights, your future")
    log.info(f"  Phase: {phase.upper()} · Native (no external LLM): {native}")
    log.info(f"  {_next_milestone(phase, {})}")
    log.info(f"  Repo: ./{HALIM_REPO_HINT}/ (frontier model home)")
    try:
        from core.halim_guardrails import log_guardrail_banner
        log_guardrail_banner(cfg)
    except Exception:
        log.info("=" * 56)
