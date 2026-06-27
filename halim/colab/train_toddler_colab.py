#!/usr/bin/env python3
"""
Train Halim toddler on Google Colab (free T4 GPU).
SCRIPT_VERSION = halim-toddler-v2  (uses _build_sft_config — NOT raw max_seq_length)

Expects:
  sft/train.jsonl
  sft/valid.jsonl

Writes:
  toddler_v1/          merged small model (~1GB)
  toddler_v1/config.json   Halim metadata for your Mac
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE_MODEL = os.getenv("HALIM_BASE_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
SFT_DIR = Path(os.getenv("HALIM_SFT_DIR", "sft"))
OUT_DIR = Path(os.getenv("HALIM_OUT_DIR", "toddler_v1"))
MAX_SEQ_LENGTH = int(os.getenv("HALIM_MAX_SEQ_LENGTH", "1024"))
EPOCHS = float(os.getenv("HALIM_EPOCHS", "2"))
BATCH_SIZE = int(os.getenv("HALIM_BATCH_SIZE", "2"))
GRAD_ACCUM = int(os.getenv("HALIM_GRAD_ACCUM", "4"))
LORA_R = int(os.getenv("HALIM_LORA_R", "16"))
LORA_ALPHA = int(os.getenv("HALIM_LORA_ALPHA", "32"))


def _require_files() -> None:
    for name in ("train.jsonl", "valid.jsonl"):
        p = SFT_DIR / name
        if not p.is_file():
            raise FileNotFoundError(
                f"Missing {p}. Upload halim_sft.zip and unzip so sft/train.jsonl exists."
            )


def _load_rows(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _build_sft_config(SFTConfig, *, adapter_dir: Path) -> object:
    """TRL API changed: max_seq_length → max_length in v0.16+."""
    import inspect

    kwargs = {
        "output_dir": str(adapter_dir),
        "num_train_epochs": EPOCHS,
        "per_device_train_batch_size": BATCH_SIZE,
        "per_device_eval_batch_size": BATCH_SIZE,
        "gradient_accumulation_steps": GRAD_ACCUM,
        "learning_rate": 2e-4,
        "logging_steps": 25,
        "eval_strategy": "epoch",
        "save_strategy": "epoch",
        "fp16": False,
        "bf16": True,
        "report_to": "none",
        "dataset_text_field": "text",
    }
    sig = inspect.signature(SFTConfig.__init__)
    params = set(sig.parameters.keys())
    if "max_length" in params:
        kwargs["max_length"] = MAX_SEQ_LENGTH
    elif "max_seq_length" in params:
        kwargs["max_seq_length"] = MAX_SEQ_LENGTH
    if "evaluation_strategy" in params and "eval_strategy" not in params:
        kwargs["evaluation_strategy"] = kwargs.pop("eval_strategy")
    return SFTConfig(**{k: v for k, v in kwargs.items() if k in params})


def main() -> None:
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    _require_files()
    print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE — enable GPU in Colab!")
    if not torch.cuda.is_available():
        raise RuntimeError("No GPU. In Colab: Runtime → Change runtime type → T4 GPU")

    train_rows = _load_rows(SFT_DIR / "train.jsonl")
    valid_rows = _load_rows(SFT_DIR / "valid.jsonl")
    print(f"Train: {len(train_rows)} | Valid: {len(valid_rows)}")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
    )

    def to_text(messages: list) -> str:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)

    train_ds = Dataset.from_dict({"text": [to_text(r["messages"]) for r in train_rows]})
    valid_ds = Dataset.from_dict({"text": [to_text(r["messages"]) for r in valid_rows]})

    lora = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )

    adapter_dir = OUT_DIR / "lora_adapter"
    if adapter_dir.exists():
        shutil.rmtree(adapter_dir)
    adapter_dir.mkdir(parents=True, exist_ok=True)

    training_args = _build_sft_config(SFTConfig, adapter_dir=adapter_dir)

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=valid_ds,
        peft_config=lora,
        processing_class=tokenizer,
    )

    print("Starting Halim toddler training… (~15–30 min on T4)")
    trainer.train()
    trainer.save_model(str(adapter_dir))

    print("Merging LoRA into base model for easy Mac download…")
    merged_dir = OUT_DIR / "merged"
    if merged_dir.exists():
        shutil.rmtree(merged_dir)
    merged_dir.mkdir(parents=True, exist_ok=True)

    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )
    peft_model = PeftModel.from_pretrained(base, str(adapter_dir))
    merged = peft_model.merge_and_unload()
    merged.save_pretrained(str(merged_dir), safe_serialization=True)
    # Always copy full tokenizer from base — merged save can miss files Colab needs
    tok_save = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tok_save.pad_token is None:
        tok_save.pad_token = tok_save.eos_token
    tok_save.save_pretrained(str(merged_dir))

    cfg = {
        "halim_phase": "toddler",
        "model": "M. A. Halim",
        "base_model": BASE_MODEL,
        "backend": "hf",
        "merged_path": "merged",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_pairs": len(train_rows),
        "valid_pairs": len(valid_rows),
        "trained_on": "google_colab",
    }
    (OUT_DIR / "config.json").write_text(json.dumps(cfg, indent=2))

    print(f"Done. Download folder: {OUT_DIR.resolve()}")
    print("  toddler_v1/merged/  ← copy this into halim/data/checkpoints/toddler_v1/merged/")


if __name__ == "__main__":
    main()
