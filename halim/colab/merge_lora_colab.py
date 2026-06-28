#!/usr/bin/env python3
"""Merge Halim LoRA adapter → toddler_v1/merged (run in Colab after training)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _fix_peft_torchao() -> None:
    """Colab ships torchao 0.10 — peft 0.14+ crashes unless removed or upgraded."""
    # Uninstall beats upgrade on Colab (preinstalled wheel often wins pip install -U)
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", "torchao"],
        check=False,
        capture_output=True,
    )
    # Optional upgrade if user wants torchao later
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "torchao>=0.16.0"],
        check=False,
        capture_output=True,
    )


def main() -> None:
    _fix_peft_torchao()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # HF scaffold registry id — required by transformers; product name is M. A. Halim.
    BASE = os.getenv("HALIM_BASE_MODEL", os.getenv("HALIM_SCAFFOLD_HF", "Qwen/Qwen2.5-0.5B-Instruct"))
    adapter = Path("toddler_v1/lora_adapter")
    if not (adapter / "adapter_model.safetensors").is_file():
        adapter = Path("toddler_v1/lora_adapter/checkpoint-614")
    merged = Path("toddler_v1/merged")
    merged.mkdir(parents=True, exist_ok=True)

    print("Using adapter:", adapter)
    print("1/4 Loading base model (CPU)…")
    base = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.float16, device_map="cpu", trust_remote_code=True,
    )
    print("2/4 Loading LoRA…")
    peft_model = PeftModel.from_pretrained(base, str(adapter))
    print("3/4 Merging…")
    merged_model = peft_model.merge_and_unload()
    print("4/4 Saving merged (~1 GB)…")
    merged_model.save_pretrained(str(merged), safe_serialization=True)

    tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.save_pretrained(str(merged))

    cfg = {
        "halim_phase": "toddler",
        "model": "M. A. Halim",
        "base_model": BASE,
        "backend": "hf",
        "merged_path": "merged",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "trained_on": "google_colab_merge",
    }
    Path("toddler_v1/config.json").write_text(json.dumps(cfg, indent=2))

    size_gb = sum(f.stat().st_size for f in merged.rglob("*") if f.is_file()) / 1e9
    print("✅ Done:", [p.name for p in merged.iterdir()])
    print(f"Size: {size_gb:.2f} GB")


if __name__ == "__main__":
    main()
