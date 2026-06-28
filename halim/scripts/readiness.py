#!/usr/bin/env python3
"""Halim toddler readiness — blockers and exact next commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from halim.dataset import count_raw_sources, repo_root, sft_pair_count  # noqa: E402
from halim.device import detect_profile, profile_spec  # noqa: E402
from halim.engine import checkpoint_path, collect_status  # noqa: E402

TODDLER_MIN_PAIRS = int(__import__("os").getenv("HALIM_TODDLER_MIN_PAIRS", "2500"))
COUNCIL_TARGET = int(__import__("os").getenv("HALIM_COUNCIL_TARGET", "5000"))


def _checkpoint_ready() -> bool:
    ckpt = checkpoint_path()
    if not ckpt:
        return False
    if ckpt.with_suffix(".gguf").is_file():
        return True
    if (ckpt / "config.json").is_file():
        return True
    if (ckpt / "adapters.safetensors").is_file():
        return True
    return False


def _next_commands(root: Path, profile: str, blockers: list, recommendations: list | None = None) -> list:
    cmds = []
    if "sft_not_prepared" in blockers:
        cmds.append("./scripts/halim_prepare_train.sh")
    if "no_checkpoint" in blockers:
        if profile in ("m2_16gb", "m2_32gb_plus", "gpu_cloud"):
            cmds.append("./scripts/halim_train_toddler.sh")
        else:
            cmds.append(
                "# 8GB Mac: prepare here, train on 16GB+ Mac or Colab GPU, then register:"
            )
            cmds.append("./scripts/halim_prepare_train.sh")
            cmds.append("# Upload halim/data/training/sft/ + run train on GPU machine")
            cmds.append("python halim/scripts/train_toddler.py --profile gpu_cloud")
            cmds.append("./scripts/halim_register_checkpoint.sh toddler_v1")
    if "backend_not_set" in blockers:
        cmds.append("export HALIM_LM_BACKEND=mlx")
        cmds.append("export HALIM_MODEL_PATH=halim/data/checkpoints/latest")
        cmds.append("./scripts/halim_serve.sh")
    if recommendations and "replay_dataset" in recommendations:
        cmds.append("# Optional — grow dataset while you train:")
        cmds.append("./scripts/start_replay_live.sh turbo")
        cmds.append("./stop_replay.sh   # flush evolution at session end")
    return cmds


def assess(root: Path | None = None) -> dict:
    root = root or repo_root()
    raw = count_raw_sources(root)
    deduped = sft_pair_count(root)
    sft_manifest = root / "halim/data/training/sft/manifest.json"
    profile = detect_profile()
    prof = profile_spec(profile)
    ckpt_ok = _checkpoint_ready()
    backend = __import__("os").getenv("HALIM_LM_BACKEND", "none")

    blockers = []
    if deduped < TODDLER_MIN_PAIRS:
        blockers.append("insufficient_deduped_pairs")
    if not sft_manifest.is_file():
        blockers.append("sft_not_prepared")
    if not ckpt_ok:
        blockers.append("no_checkpoint")
    elif backend in ("", "none"):
        blockers.append("backend_not_set")

    soft_recommendations = []
    if raw.get("council", 0) < COUNCIL_TARGET:
        soft_recommendations.append("replay_dataset")

    ready = len(blockers) == 0 or (ckpt_ok and backend not in ("", "none"))

    phase_hint = "newborn"
    if ckpt_ok:
        ck = checkpoint_path()
        try:
            if ck and (ck / "config.json").is_file():
                meta = json.loads((ck / "config.json").read_text())
                phase_hint = str(meta.get("halim_phase") or "toddler")
            elif ck and "toddler" in str(ck).lower():
                phase_hint = "toddler"
            else:
                phase_hint = "adult"
        except Exception:
            phase_hint = "toddler" if ckpt_ok else "newborn"
    elif deduped >= TODDLER_MIN_PAIRS and raw.get("council", 0) >= 2000:
        phase_hint = "toddler_ready"

    return {
        "ready_for_native_lm": ready,
        "phase_hint": phase_hint,
        "device_profile": profile,
        "lm_on_device": prof.get("lm_enabled", False),
        "raw_sources": raw,
        "deduped_sft_pairs": deduped,
        "toddler_min_pairs": TODDLER_MIN_PAIRS,
        "council_target": COUNCIL_TARGET,
        "council_gap": max(0, COUNCIL_TARGET - raw.get("council", 0)),
        "sft_prepared": sft_manifest.is_file(),
        "checkpoint": str(checkpoint_path()) if ckpt_ok else None,
        "blockers": blockers,
        "recommendations": soft_recommendations,
        "next_commands": _next_commands(root, profile, blockers, soft_recommendations),
        "engine": collect_status(),
    }


def main() -> int:
    report = assess()
    print(json.dumps(report, indent=2))
    return 0 if report.get("ready_for_native_lm") else 1


if __name__ == "__main__":
    raise SystemExit(main())
