#!/usr/bin/env python3
"""Merge Halim LoRA adapter → toddler_v1/merged (run in Colab after training)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _fix_peft_torchao() -> None:
    """Colab often ships torchao 0.10 — peft requires >=0.16 or no torchao."""
    try:
        import torchao  # noqa: F401
        ver = getattr(torchao, "__version__", "0")
        parts = [int(x) for x in ver.split(".")[:2]]
        if parts[0] == 0 and parts[1] < 16:
            print(f"Fixing torchao {ver} (too old for peft)…")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-q", "torchao>=0.16.0"],
            )
    except ImportError:
        pass


def main() -> None:
    _fix_peft_torchao()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    BASE = "Qwen/Qwen2.5-0.5B-Instruct"
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
