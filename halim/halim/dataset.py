"""Merge Halim gold sources into unified SFT JSONL for toddler training."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

SYSTEM_PROMPT = (
    "You are M. A. Halim — HANOON's owned mind. Trade reasoning, briefings, and "
    "decisions in concise, actionable language. Respect risk guardrails."
)

DEFAULT_PATHS = {
    "council": "models/council_training_dataset.jsonl",
    "action_gold": "halim/data/training/action_gold.jsonl",
    "coevolution": "halim/data/training/coevolution_gold.jsonl",
    "dialogue": "halim/data/training/dialogue_gold.jsonl",
}


def repo_root() -> Path:
    env = os.getenv("HALIM_REPO_ROOT", "").strip()
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2]
    if (here / "models").is_dir():
        return here
    return Path.cwd()


def _row_hash(row: Dict[str, Any]) -> str:
    key = "|".join(
        str(row.get(k, ""))[:400]
        for k in ("capability", "instruction", "input", "output")
    )
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _messages_row(instruction: str, user: str, assistant: str) -> Dict[str, Any]:
    user_text = user.strip()
    if instruction and instruction not in user_text[:120]:
        user_text = f"{instruction.strip()}\n\n{user_text}"
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text[:8000]},
            {"role": "assistant", "content": assistant.strip()[:8000]},
        ]
    }


def _gold_row(
    *,
    capability: str,
    instruction: str,
    input_text: str,
    output_text: str,
    source: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    out = (output_text or "").strip()
    inp = (input_text or "").strip()
    if len(out) < 8 or len(inp) < 4:
        return None
    if source == "test" and len(out) < 40:
        return None
    row = {
        "capability": capability,
        "instruction": instruction,
        "input": inp,
        "output": out,
        "source": source,
        **(meta or {}),
    }
    row["messages"] = _messages_row(instruction, inp, out)["messages"]
    return row


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def council_to_gold(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ticker = str(raw.get("ticker", "")).upper()
    if not ticker:
        return None
    reason = str(raw.get("teacher_reason", "")).strip()
    if len(reason) < 6:
        return None
    ctx = raw.get("market_context") or {}
    enter = bool(raw.get("teacher_enter"))
    conf = float(raw.get("teacher_confidence", 0) or 0)
    pipeline = str(raw.get("teacher_pipeline", ""))
    outcome = raw.get("outcome") or {}

    ctx_bits = []
    if ctx:
        ctx_bits.append(json.dumps(ctx, separators=(",", ":")))
    user = (
        f"Ticker: {ticker}\nPipeline: {pipeline}\n"
        f"Signals: {reason[:500]}"
    )
    if ctx_bits:
        user += f"\nMarket: {ctx_bits[0][:400]}"

    decision = "ENTER" if enter else "SKIP"
    assistant = f"{decision} | confidence={conf:.2f} | {reason[:400]}"
    if outcome:
        assistant += (
            f"\nOutcome: pnl={outcome.get('pnl_usd')} "
            f"win={outcome.get('win')} exit={outcome.get('exit_reason', '')}"
        )

    return _gold_row(
        capability="decision_text",
        instruction="Decide enter or skip with concise reasoning.",
        input_text=user,
        output_text=assistant,
        source="council",
        meta={"ticker": ticker, "timestamp": raw.get("timestamp")},
    )


def standard_gold(raw: Dict[str, Any], *, default_source: str) -> Optional[Dict[str, Any]]:
    if raw.get("messages"):
        return raw
    cap = str(raw.get("capability", "reasoning"))
    instruction = str(raw.get("instruction", "")).strip() or "Complete the Halim task."
    inp = str(raw.get("input", raw.get("input_excerpt", "")))
    out = str(raw.get("output", raw.get("output_excerpt", "")))
    source = str(raw.get("source", default_source))
    if default_source == "coevolution" and inp and len(out) < 120:
        out = f"Setup: {inp[:500]}\nReconcile: {out}"
    return _gold_row(
        capability=cap,
        instruction=instruction,
        input_text=inp,
        output_text=out,
        source=source,
        meta={"timestamp": raw.get("timestamp"), "capability": cap},
    )


def iter_source_rows(root: Path) -> Iterator[Tuple[str, Dict[str, Any]]]:
    council = root / DEFAULT_PATHS["council"]
    for raw in _iter_jsonl(council):
        row = council_to_gold(raw)
        if row:
            yield "council", row

    for key in ("action_gold", "coevolution", "dialogue"):
        path = root / DEFAULT_PATHS[key]
        for raw in _iter_jsonl(path):
            row = standard_gold(raw, default_source=key)
            if row:
                yield key, row


def count_raw_sources(root: Optional[Path] = None) -> Dict[str, int]:
    root = root or repo_root()
    counts: Dict[str, int] = {}
    for key, rel in DEFAULT_PATHS.items():
        path = root / rel
        counts[key] = sum(1 for _ in _iter_jsonl(path)) if path.is_file() else 0
    counts["total_raw"] = sum(counts.values())
    return counts


def prepare_sft_dataset(
    *,
    root: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    val_ratio: float = 0.05,
    min_pairs: int = 2500,
    max_pairs: int = 100_000,
) -> Dict[str, Any]:
    """Write deduped train/valid JSONL under halim/data/training/sft/."""
    root = root or repo_root()
    out_dir = out_dir or (root / "halim/data/training/sft")
    out_dir.mkdir(parents=True, exist_ok=True)

    seen: set = set()
    rows: List[Dict[str, Any]] = []
    by_source: Dict[str, int] = {}

    for source, row in iter_source_rows(root):
        h = _row_hash(row)
        if h in seen:
            continue
        seen.add(h)
        rows.append(row)
        by_source[source] = by_source.get(source, 0) + 1
        if len(rows) >= max_pairs:
            break

    if len(rows) < min_pairs:
        return {
            "ok": False,
            "reason": "insufficient_pairs",
            "pairs": len(rows),
            "min_pairs": min_pairs,
            "by_source": by_source,
        }

    split = max(1, int(len(rows) * val_ratio))
    train_rows = rows[:-split] if split < len(rows) else rows
    valid_rows = rows[-split:] if split < len(rows) else rows[:1]

    train_path = out_dir / "train.jsonl"
    valid_path = out_dir / "valid.jsonl"
    manifest_path = out_dir / "manifest.json"

    def _write(path: Path, chunk: List[Dict[str, Any]]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in chunk:
                fh.write(json.dumps({"messages": row["messages"]}, separators=(",", ":")) + "\n")

    _write(train_path, train_rows)
    _write(valid_path, valid_rows)

    manifest = {
        "pairs_total": len(rows),
        "train_pairs": len(train_rows),
        "valid_pairs": len(valid_rows),
        "by_source": by_source,
        "train_path": str(train_path.relative_to(root)) if train_path.is_relative_to(root) else str(train_path),
        "valid_path": str(valid_path.relative_to(root)) if valid_path.is_relative_to(root) else str(valid_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return {"ok": True, **manifest, "manifest_path": str(manifest_path)}


def sft_pair_count(root: Optional[Path] = None) -> int:
    """Fast estimate: deduped pair count without rewriting files."""
    root = root or repo_root()
    manifest = root / "halim/data/training/sft/manifest.json"
    if manifest.is_file():
        try:
            return int(json.loads(manifest.read_text()).get("pairs_total", 0))
        except Exception:
            pass
    seen: set = set()
    n = 0
    for _, row in iter_source_rows(root):
        h = _row_hash(row)
        if h in seen:
            continue
        seen.add(h)
        n += 1
    return n
