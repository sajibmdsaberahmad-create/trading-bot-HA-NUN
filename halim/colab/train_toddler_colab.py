#!/usr/bin/env python3
"""
Train Halim toddler on Google Colab (free T4 GPU).
SCRIPT_VERSION = halim-toddler-v4  (Drive resume + core_delta + continue LoRA)

Env (set in Colab before running):
  HALIM_OUT_DIR          default toddler_v1 — use Drive path to survive disconnects:
                         /content/drive/MyDrive/Halim/toddler_v1
  HALIM_RESUME           auto|true|false — resume mid-run checkpoint (default auto)
  HALIM_FRESH_TRAIN      true — wipe adapter, train from base Qwen (first v3 full)
  HALIM_CONTINUE_LORA    auto|true|false — load existing LoRA weights on new SFT zip
  HALIM_SAVE_STEPS       0=epoch only; e.g. 200 for step saves during long runs
  HALIM_SAVE_TOTAL_LIMIT keep last N checkpoints (default 3)
  HALIM_RESUME_CHECKPOINT explicit path to checkpoint-N folder
  HALIM_FAST_PATH        auto (default) — T4: batch 8, no AMP
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE_MODEL = os.getenv("HALIM_BASE_MODEL", os.getenv("HALIM_SCAFFOLD_HF", "Qwen/Qwen2.5-0.5B-Instruct"))
SFT_DIR = Path(os.getenv("HALIM_SFT_DIR", "sft"))
OUT_DIR = Path(os.getenv("HALIM_OUT_DIR", "toddler_v1"))
MAX_SEQ_LENGTH = int(os.getenv("HALIM_MAX_SEQ_LENGTH", "1024"))
EPOCHS = float(os.getenv("HALIM_EPOCHS", "0"))
LORA_R = int(os.getenv("HALIM_LORA_R", "16"))
LORA_ALPHA = int(os.getenv("HALIM_LORA_ALPHA", "32"))
SAVE_STEPS = int(os.getenv("HALIM_SAVE_STEPS", "0"))
SAVE_TOTAL_LIMIT = int(os.getenv("HALIM_SAVE_TOTAL_LIMIT", "3"))


def _resolve_training_knobs() -> tuple[int, int, bool, bool, str]:
    """Pick batch / precision for Colab T4 (high VRAM use, no AMP) unless env overrides."""
    import torch

    batch_raw = os.getenv("HALIM_BATCH_SIZE", "").strip()
    accum_raw = os.getenv("HALIM_GRAD_ACCUM", "").strip()
    fp16_raw = os.getenv("HALIM_FP16", "").strip().lower()
    bf16_raw = os.getenv("HALIM_BF16", "").strip().lower()
    fast = os.getenv("HALIM_FAST_PATH", "auto").lower()
    max_power = os.getenv("HALIM_MAX_POWER", "false").lower() in ("1", "true", "yes")

    batch = int(batch_raw) if batch_raw else 0
    accum = int(accum_raw) if accum_raw else 0
    profile = "manual" if batch_raw or accum_raw else "legacy"

    if fast not in ("0", "false", "off", "no") and torch.cuda.is_available():
        name = torch.cuda.get_device_name(0).upper()
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        if fast in ("1", "true", "yes", "auto") and ("T4" in name or vram_gb <= 16.5):
            if not batch_raw:
                batch = 16 if max_power else 8
            if not accum_raw:
                accum = 1
            profile = "colab_t4_max" if max_power else "colab_t4_fast"

    if batch <= 0:
        batch = 2
    if accum <= 0:
        accum = 4
    if profile == "legacy" and not batch_raw and not accum_raw:
        profile = "legacy_default"

    if fp16_raw in ("1", "true", "yes"):
        fp16, bf16 = True, False
    elif bf16_raw in ("1", "true", "yes"):
        fp16, bf16 = False, True
    elif fp16_raw in ("0", "false", "no"):
        fp16, bf16 = False, bf16_raw not in ("0", "false", "no")
    elif profile in ("colab_t4_fast", "colab_t4_max"):
        # T4 QLoRA + fp16 GradScaler crashes (bf16 unscale) — speed from large batch, fp32 LoRA
        fp16, bf16 = False, False
    else:
        fp16, bf16 = False, True

    return batch, accum, fp16, bf16, profile


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _auto_epochs(train_n: int) -> float:
    if EPOCHS > 0:
        return EPOCHS
    if train_n < 2000:
        return 2.0
    if train_n < 4000:
        return 2.5
    if train_n < 8000:
        return 3.0
    return 3.5


def _load_colab_manifest() -> dict:
    path = SFT_DIR / "colab_manifest.json"
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


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


def _checkpoint_dirs(adapter_dir: Path) -> list[Path]:
    out = []
    for p in adapter_dir.glob("checkpoint-*"):
        suffix = p.name.split("-")[-1]
        if suffix.isdigit():
            out.append(p)
    return sorted(out, key=lambda p: int(p.name.split("-")[-1]))


def _find_resume_checkpoint(adapter_dir: Path, *, target_epochs: float = 0.0) -> str | None:
    explicit = os.getenv("HALIM_RESUME_CHECKPOINT", "").strip()
    if explicit:
        ep = Path(explicit)
        return str(ep) if ep.is_dir() else None

    mode = os.getenv("HALIM_RESUME", "auto").lower()
    if mode in ("0", "false", "no"):
        return None
    if mode == "auto" and _env_bool("HALIM_FRESH_TRAIN"):
        return None

    cps = _checkpoint_dirs(adapter_dir)
    if not cps:
        return None

    last = cps[-1]
    # Finished v2 checkpoint-3955 is not a crash — don't resume trainer state on incremental.
    if _env_bool("HALIM_CONTINUE_LORA", "auto") and not _env_bool("HALIM_RESUME_MIDRUN"):
        print(
            f"Skip trainer resume ({last.name}): CONTINUE_LORA uses weights only — "
            "set HALIM_RESUME_MIDRUN=true only after a disconnect mid-run"
        )
        return None

    state_path = last / "trainer_state.json"
    if state_path.is_file() and target_epochs > 0:
        try:
            state = json.loads(state_path.read_text())
            done_epoch = float(state.get("epoch", 0) or 0)
            if done_epoch >= target_epochs - 0.01:
                print(f"Skip resume: {last.name} already at epoch {done_epoch} (target {target_epochs})")
                return None
        except Exception:
            pass

    print(f"Resume: found {last.name} under {adapter_dir}")
    return str(last)


def _should_continue_lora(adapter_dir: Path, *, fresh_train: bool) -> bool:
    if fresh_train:
        return False
    mode = os.getenv("HALIM_CONTINUE_LORA", "auto").lower()
    if mode in ("0", "false", "no"):
        return False
    has_adapter = (adapter_dir / "adapter_model.safetensors").is_file()
    has_ckpt = bool(_checkpoint_dirs(adapter_dir))
    if mode in ("1", "true", "yes"):
        return has_adapter or has_ckpt
    return has_adapter


def _prepare_adapter_dir(adapter_dir: Path, *, fresh_train: bool, resume_ckpt: str | None, continue_lora: bool) -> None:
    if resume_ckpt:
        print(f"Keeping adapter dir for resume: {adapter_dir}")
        adapter_dir.mkdir(parents=True, exist_ok=True)
        return
    if continue_lora:
        print(f"Continue LoRA: keeping existing weights in {adapter_dir}")
        adapter_dir.mkdir(parents=True, exist_ok=True)
        return
    if fresh_train and adapter_dir.exists():
        print(f"FRESH_TRAIN: removing {adapter_dir}")
        shutil.rmtree(adapter_dir)
    adapter_dir.mkdir(parents=True, exist_ok=True)


def _build_sft_config(
    SFTConfig, *, adapter_dir: Path, epochs: float,
    batch_size: int, grad_accum: int, fp16: bool, bf16: bool,
) -> object:
    import inspect

    save_strategy = "steps" if SAVE_STEPS > 0 else "epoch"
    save_steps = SAVE_STEPS if SAVE_STEPS > 0 else 500

    kwargs = {
        "output_dir": str(adapter_dir),
        "num_train_epochs": epochs,
        "per_device_train_batch_size": batch_size,
        "per_device_eval_batch_size": batch_size,
        "gradient_accumulation_steps": grad_accum,
        "learning_rate": 2e-4,
        "logging_steps": 25,
        "eval_strategy": "epoch",
        "save_strategy": save_strategy,
        "save_steps": save_steps,
        "save_total_limit": SAVE_TOTAL_LIMIT,
        "fp16": fp16,
        "bf16": bf16,
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


def _record_trained_hashes(train_rows: list, build_id: str) -> None:
    try:
        import sys
        repo = Path.cwd()
        if str(repo / "halim") not in sys.path:
            sys.path.insert(0, str(repo / "halim"))
        from halim.dataset import record_trained_from_sft
        record_trained_from_sft(root=repo, build_id=build_id, train_pairs=len(train_rows))
        print("Recorded trained hashes → models/halim_sft_trained_hashes.jsonl")
    except Exception as exc:
        print(f"Note: could not record trained hashes on Colab ({exc}) — run record_sft_trained.py on Mac")


def main() -> None:
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    _require_files()
    print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE — enable GPU in Colab!")
    if not torch.cuda.is_available():
        raise RuntimeError("No GPU. In Colab: Runtime → Change runtime type → T4 GPU")

    batch_size, grad_accum, fp16, bf16, train_profile = _resolve_training_knobs()
    eff_batch = batch_size * grad_accum

    train_rows = _load_rows(SFT_DIR / "train.jsonl")
    valid_rows = _load_rows(SFT_DIR / "valid.jsonl")
    colab_manifest = _load_colab_manifest()
    sft_mode = "full"
    manifest_path = SFT_DIR / "manifest.json"
    if manifest_path.is_file():
        try:
            sft_mode = json.loads(manifest_path.read_text()).get("mode", "full")
        except Exception:
            pass

    adapter_dir = OUT_DIR / "lora_adapter"
    fresh_train = _env_bool("HALIM_FRESH_TRAIN")
    epochs = _auto_epochs(len(train_rows))
    if sft_mode == "core_delta":
        epochs = min(epochs, float(os.getenv("HALIM_CORE_DELTA_EPOCHS", "2.5")))
    resume_ckpt = _find_resume_checkpoint(adapter_dir, target_epochs=epochs)
    continue_lora = _should_continue_lora(adapter_dir, fresh_train=fresh_train)

    build_id = colab_manifest.get("build_id", "unknown")
    created = colab_manifest.get("created_at", "")
    print(f"OUT_DIR: {OUT_DIR.resolve()}")
    print(
        f"Train: {len(train_rows)} | Valid: {len(valid_rows)} | Epochs: {epochs} | SFT mode: {sft_mode}"
    )
    print(
        f"Knobs: batch={batch_size} grad_accum={grad_accum} eff_batch={eff_batch} "
        f"fp16={fp16} bf16={bf16} profile={train_profile}"
    )
    print(f"Halim SFT build_id: {build_id}  packaged_at: {created}")
    print(f"CONTINUE_LORA: {continue_lora} | RESUME_CKPT: {resume_ckpt or 'none'}")
    if build_id == "unknown":
        print("WARNING: colab_manifest.json missing — re-run ./scripts/halim_colab_ready.sh on your Mac")
    if colab_manifest.get("by_source"):
        print("Source mix:", colab_manifest["by_source"])

    _prepare_adapter_dir(adapter_dir, fresh_train=fresh_train, resume_ckpt=resume_ckpt, continue_lora=continue_lora)

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # 4bit matmul dtype — independent of Trainer AMP (T4 fast uses fp32 train + fp16 bnb)
    if fp16:
        compute_dtype = torch.float16
    elif bf16:
        compute_dtype = torch.bfloat16
    else:
        compute_dtype = torch.float16
    print(f"bnb_4bit_compute_dtype: {compute_dtype} (trainer fp16={fp16} bf16={bf16})")

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    if continue_lora and (adapter_dir / "adapter_model.safetensors").is_file():
        print("Loading existing LoRA adapter for continued training…")
        model = PeftModel.from_pretrained(model, str(adapter_dir), is_trainable=True)

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

    training_args = _build_sft_config(
        SFTConfig, adapter_dir=adapter_dir, epochs=epochs,
        batch_size=batch_size, grad_accum=grad_accum, fp16=fp16, bf16=bf16,
    )

    trainer_kwargs = {
        "model": model,
        "args": training_args,
        "train_dataset": train_ds,
        "eval_dataset": valid_ds,
        "processing_class": tokenizer,
    }
    if not continue_lora:
        trainer_kwargs["peft_config"] = lora

    trainer = SFTTrainer(**trainer_kwargs)

    print("Starting Halim toddler training…")
    if resume_ckpt:
        print(f"Resuming from checkpoint: {resume_ckpt}")
        trainer.train(resume_from_checkpoint=resume_ckpt)
    else:
        trainer.train()
    trainer.save_model(str(adapter_dir))

    print("Merging LoRA into base model for easy Mac download…")
    merged_dir = OUT_DIR / "merged"
    if merged_dir.exists():
        shutil.rmtree(merged_dir)
    merged_dir.mkdir(parents=True, exist_ok=True)

    try:
        import subprocess
        import sys
        try:
            import torchao
            ver = getattr(torchao, "__version__", "0")
            parts = [int(x) for x in ver.split(".")[:2]]
            if parts[0] == 0 and parts[1] < 16:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-q", "torchao>=0.16.0"],
                )
        except ImportError:
            pass
    except Exception:
        pass

    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16,
        device_map="cpu",
        trust_remote_code=True,
    )
    peft_model = PeftModel.from_pretrained(base, str(adapter_dir))
    merged = peft_model.merge_and_unload()
    merged.save_pretrained(str(merged_dir), safe_serialization=True)
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
        "epochs": epochs,
        "sft_mode": sft_mode,
        "by_source": colab_manifest.get("by_source") or {},
        "raw_sources": colab_manifest.get("raw_sources") or {},
        "trained_on": "google_colab",
        "package_version": colab_manifest.get("version", 1),
        "build_id": build_id,
        "packaged_at": colab_manifest.get("created_at"),
        "resume_checkpoint": resume_ckpt,
        "continued_lora": continue_lora,
        "out_dir": str(OUT_DIR),
    }
    (OUT_DIR / "config.json").write_text(json.dumps(cfg, indent=2))

    _record_trained_hashes(train_rows, build_id)

    work = os.getenv("HALIM_WORK", "").strip()
    if work:
        try:
            state_path = Path(work) / "halim_colab_state.json"
            state = {}
            if state_path.is_file():
                state = json.loads(state_path.read_text())
            state["last_trained_build_id"] = build_id
            state["last_trained_at"] = datetime.now(timezone.utc).isoformat()
            state["train_pairs"] = len(train_rows)
            state_path.write_text(json.dumps(state, indent=2))
            print(f"Updated Drive state: {state_path}")
        except Exception as exc:
            print(f"Note: could not update Drive state ({exc})")

    print(f"Done. Download folder: {OUT_DIR.resolve()}")
    print("  toddler_v1/merged/  ← zip and install on Mac")


if __name__ == "__main__":
    main()
