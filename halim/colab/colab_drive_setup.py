#!/usr/bin/env python3
"""
Auto-prepare Halim Colab train from Google Drive folder.

Upload to My Drive/Halim/ (browser only):
  - halim_toddler_v*.zip  (latest checkpoint — optional if toddler_v1/ already on Drive)
  - halim_sft.zip         (new SFT batch from Mac)

Then in Colab:
  os.environ["HALIM_WORK"] = "/content/drive/MyDrive/Halim"
  !python colab_drive_setup.py
  !python train_toddler_colab.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

WORK = Path(os.environ.get("HALIM_WORK", "/content/drive/MyDrive/Halim"))
OUT_DIR = WORK / "toddler_v1"
STATE_PATH = WORK / "halim_colab_state.json"
CONTENT = Path("/content")


def _version_from_name(name: str) -> int:
    m = re.search(r"v(\d+)", name, re.I)
    return int(m.group(1)) if m else 0


def _all_toddler_zips() -> list[Path]:
    return sorted(WORK.glob("halim_toddler_v*.zip"), key=lambda p: _version_from_name(p.name))


def _latest_toddler_zip() -> Path | None:
    zips = _all_toddler_zips()
    return zips[-1] if zips else None


def audit_drive(work: Path | None = None) -> dict:
    """List Drive assets — latest halim_toddler_vN.zip wins over v2 when adapter missing."""
    base = work or WORK
    sft_cands = list(base.glob("halim_sft*.zip"))
    sft = max(sft_cands, key=lambda p: p.stat().st_mtime) if sft_cands else None
    zips = sorted(base.glob("halim_toddler_v*.zip"), key=lambda p: _version_from_name(p.name))
    out_dir = base / "toddler_v1"
    adp = out_dir / "lora_adapter" / "adapter_model.safetensors"
    latest = zips[-1] if zips else None
    return {
        "work_dir": str(base),
        "sft_zip": sft.name if sft else None,
        "toddler_zips": [
            {"name": p.name, "version": _version_from_name(p.name), "size_mb": round(p.stat().st_size / (1024 * 1024), 1)}
            for p in zips
        ],
        "latest_toddler_zip": latest.name if latest else None,
        "adapter_on_drive": adp.is_file(),
        "adapter_path": str(adp) if adp.is_file() else str(adp),
        "train_source": (
            "toddler_v1/ on Drive (continue LoRA)"
            if adp.is_file()
            else (f"extract {latest.name}" if latest else "missing — upload halim_toddler_v*.zip")
        ),
    }


def _latest_sft_zip() -> Path | None:
    explicit = os.environ.get("HALIM_SFT_ZIP", "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise FileNotFoundError(f"HALIM_SFT_ZIP not found: {p}")

    # Colab direct upload (faster than Drive for ~15–50 MB zip)
    content_cands = list(CONTENT.glob("halim_sft*.zip"))
    if content_cands:
        return max(content_cands, key=lambda p: p.stat().st_mtime)

    candidates = list(WORK.glob("halim_sft*.zip"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _train_out_dir() -> Path:
    return Path(os.environ.get("HALIM_OUT_DIR", str(CONTENT / "toddler_v1")))


def _adapter_ready_at(path: Path | None = None) -> bool:
    base = path or _train_out_dir()
    return (base / "lora_adapter" / "adapter_model.safetensors").is_file()


def _adapter_ready() -> bool:
    return _adapter_ready_at(OUT_DIR) or _adapter_ready_at(_train_out_dir())


def _load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _midrun_checkpoint() -> Path | None:
    adp_dir = _train_out_dir() / "lora_adapter"
    if not adp_dir.is_dir():
        return None
    cks = sorted(adp_dir.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
    if not cks:
        return None
    latest = cks[-1]
    trainer = latest / "trainer_state.json"
    if not trainer.is_file():
        return latest
    try:
        data = json.loads(trainer.read_text())
        if data.get("epoch", 0) < float(data.get("num_train_epochs", 0) or 0):
            return latest
    except Exception:
        return latest
    return None


def _clear_finished_checkpoints() -> list[str]:
    removed = []
    adp_dir = _train_out_dir() / "lora_adapter"
    for p in adp_dir.glob("checkpoint-*"):
        shutil.rmtree(p)
        removed.append(p.name)
    return removed


def _extract_toddler_if_needed(toddler_zip: Path | None) -> None:
    local_out = CONTENT / "toddler_v1"
    local_adp = local_out / "lora_adapter" / "adapter_model.safetensors"
    drive_adp = OUT_DIR / "lora_adapter" / "adapter_model.safetensors"
    train_out = Path(os.environ.get("HALIM_OUT_DIR", str(local_out)))

    if _adapter_ready_at(train_out):
        print("toddler weights: already at", train_out)
        return
    if local_adp.is_file() and train_out != local_out:
        print("copying toddler weights /content →", train_out)
        shutil.copytree(local_out, train_out, dirs_exist_ok=True)
        return
    if drive_adp.is_file():
        print("copying toddler weights Drive →", train_out)
        shutil.copytree(OUT_DIR, train_out, dirs_exist_ok=True)
        return
    if toddler_zip and toddler_zip.is_file():
        print("extracting toddler zip:", toddler_zip.name, "→", train_out)
        with zipfile.ZipFile(toddler_zip, "r") as zf:
            zf.extractall(train_out.parent)
        return
    raise FileNotFoundError(
        f"No adapter at {train_out} and no halim_toddler_v*.zip in {WORK}. "
        "Upload your latest toddler zip to Drive."
    )


def _extract_sft(sft_zip: Path) -> dict:
    print("extracting SFT:", sft_zip.name)
    with zipfile.ZipFile(sft_zip, "r") as zf:
        zf.extractall(CONTENT)
    manifest_path = CONTENT / "sft" / "colab_manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text())
    return {}


def _next_output_zip_name() -> str:
    existing = [_version_from_name(p.name) for p in WORK.glob("halim_toddler_v*.zip")]
    n = max(existing + [2]) + 1
    return f"halim_toddler_v{n}.zip"


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    audit = audit_drive(WORK)
    print("=== Halim Drive audit ===")
    print(json.dumps(audit, indent=2))
    if audit["toddler_zips"] and len(audit["toddler_zips"]) > 1:
        names = [z["name"] for z in audit["toddler_zips"]]
        print(f"NOTE: multiple toddler zips — using highest version only if extract needed: {audit['latest_toddler_zip']}")
        print(f"      (not v2 unless it is the only zip): {names}")

    toddler_zip = _latest_toddler_zip()
    sft_zip = _latest_sft_zip()
    if not sft_zip:
        raise FileNotFoundError(
            f"Upload halim_sft.zip to Colab /content/ (fast) or {WORK} (Drive)"
        )

    state = _load_state()
    os.environ.setdefault("HALIM_OUT_DIR", str(CONTENT / "toddler_v1"))
    manifest = _extract_sft(sft_zip)
    build_id = str(manifest.get("build_id", ""))
    sft_mode = str(manifest.get("sft_mode", manifest.get("mode", "full")))

    need_weights = (
        _adapter_ready_at(OUT_DIR)
        or _adapter_ready_at(_train_out_dir())
        or toddler_zip is not None
        or sft_mode == "core_delta"
    )
    if need_weights:
        _extract_toddler_if_needed(toddler_zip)
    else:
        print("fresh train: no prior toddler weights — base Qwen + new LoRA")

    script = CONTENT / "train_toddler_colab.py"
    if not script.is_file():
        bundled = CONTENT / "colab_drive_setup.py"
        raise FileNotFoundError(
            "halim_sft.zip missing train_toddler_colab.py — rebuild on Mac: "
            "./scripts/halim_prepare_train_incremental.sh"
        )

    midrun = _midrun_checkpoint()
    same_build = build_id and build_id == state.get("last_trained_build_id")

    os.environ["HALIM_SFT_DIR"] = str(CONTENT / "sft")
    train_out = _train_out_dir()

    if midrun and not same_build:
        os.environ["HALIM_CONTINUE_LORA"] = "auto"
        os.environ["HALIM_RESUME"] = "true"
        os.environ["HALIM_RESUME_MIDRUN"] = "true"
        mode = "resume_midrun"
        print("mode: resume mid-run checkpoint", midrun.name)
    elif _adapter_ready_at(train_out) and sft_mode == "core_delta":
        removed = _clear_finished_checkpoints()
        os.environ["HALIM_CONTINUE_LORA"] = "auto"
        os.environ["HALIM_RESUME"] = "false"
        mode = "incremental"
        print("mode: incremental continue LoRA | cleared checkpoints:", removed or "none")
    elif _adapter_ready_at(train_out):
        os.environ["HALIM_CONTINUE_LORA"] = "auto"
        os.environ["HALIM_RESUME"] = "false"
        mode = "continue_full_pack"
        print("mode: continue LoRA on full SFT pack")
    else:
        os.environ["HALIM_FRESH_TRAIN"] = "true"
        os.environ["HALIM_CONTINUE_LORA"] = "false"
        os.environ["HALIM_RESUME"] = "false"
        mode = "fresh"

    out_zip = _next_output_zip_name()
    summary = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "work_dir": str(WORK),
        "out_dir": str(train_out),
        "drive_toddler_dir": str(OUT_DIR),
        "mode": mode,
        "sft_zip": sft_zip.name,
        "toddler_zip": toddler_zip.name if toddler_zip else None,
        "build_id": build_id,
        "sft_mode": sft_mode,
        "output_zip": out_zip,
        "train_script": str(script),
    }
    _save_state({**state, "last_prepared_build_id": build_id, "last_mode": mode, "next_output_zip": out_zip})
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
