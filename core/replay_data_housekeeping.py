#!/usr/bin/env python3
"""
core/replay_data_housekeeping.py — One replay source: data/replay/intraday only.

After IB download: dedupe bars, trim to retention window, remove redundant
daily hanoon/ duplicates and orphan CSVs so replay never picks the wrong file.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd

from core.notify import log
from core.replay_data import resolve_replay_dir

# Scalper replay uses intraday 1-min only
INTRADAY_SUFFIX = "_1min.csv"

# Daily yfinance folder — redundant when intraday exists
HANOON_SUBDIR = "hanoon"

# Optional: drop intraday files for tickers outside this set (empty = keep all)
_DEFAULT_UNIVERSE = frozenset({
    "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB", "ASTS", "QS", "LCID",
    "RIVN", "NVDA", "TSLA", "SPY", "QQQ",
})


def _progress(msg: str) -> None:
    print(msg, flush=True)


def normalize_intraday_csv(
    path: Path,
    *,
    retention_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Dedupe, sort, trim one intraday file in place."""
    out: Dict[str, Any] = {"path": str(path), "ok": False}
    if not path.is_file():
        out["reason"] = "missing"
        return out
    try:
        df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
        before = len(df)
        df.index = pd.to_datetime(df.index, utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).sort_index()
        df = df[~df.index.duplicated(keep="last")]

        if retention_days and retention_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(retention_days))
            df = df[df.index >= pd.Timestamp(cutoff)]

        if len(df) < 20:
            out["reason"] = f"too_few_rows_{len(df)}"
            return out

        df.to_csv(path, index_label="datetime")
        out.update({
            "ok": True,
            "before": before,
            "after": len(df),
            "removed": before - len(df),
            "start": str(df.index[0]),
            "end": str(df.index[-1]),
        })
        return out
    except Exception as exc:
        out["reason"] = str(exc)[:120]
        return out


def _intraday_tickers(intraday_dir: Path) -> Set[str]:
    out: Set[str] = set()
    for p in intraday_dir.glob(f"*{INTRADAY_SUFFIX}"):
        sym = p.stem.replace("_1min", "").replace("_1MIN", "").upper()
        if sym:
            out.add(sym)
    return out


def remove_redundant_replay_sources(
    root: Optional[Path] = None,
    *,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Drop duplicate/unused replay sources — intraday IB farm is the only training source.
    Removes: hanoon daily dupes, archive/SP500 daily folders, stray root CSVs.
    """
    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "error": "no_replay_dir"}

    result: Dict[str, Any] = {
        "ok": True,
        "root": str(root),
        "deleted_dirs": [],
        "deleted_files": [],
        "bytes_freed": 0,
    }
    intraday_syms = _intraday_tickers(root / "intraday") if (root / "intraday").is_dir() else set()

    def _rm_path(p: Path, bucket: str) -> None:
        try:
            if p.is_file():
                result["bytes_freed"] += p.stat().st_size
                p.unlink()
                result["deleted_files"].append(str(p.relative_to(root)))
            elif p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                import shutil
                shutil.rmtree(p, ignore_errors=True)
                result["bytes_freed"] += size
                result["deleted_dirs"].append(str(p.relative_to(root)))
            if verbose and bucket:
                _progress(f"  🗑  removed unused {p.relative_to(root)}")
        except Exception as exc:
            log.debug(f"remove redundant {p}: {exc}")

    # Legacy daily folders — never used when intraday exists
    for sub in ("archive", "SP500_Data_10Y", "hanoon"):
        d = root / sub
        if d.is_dir():
            if sub == "hanoon":
                for p in sorted(d.glob("*.csv")):
                    if p.stem.upper() in intraday_syms:
                        _rm_path(p, "hanoon_dupe")
            elif any(d.iterdir()):
                _rm_path(d, "legacy_dir")

    for p in sorted(root.glob("*.csv")):
        sym = p.stem.upper()
        if sym in intraday_syms:
            _rm_path(p, "stray_root")

    if verbose:
        n = len(result["deleted_files"]) + len(result["deleted_dirs"])
        mb = result["bytes_freed"] / (1024 * 1024)
        if n:
            _progress(f"🧹 Removed {n} redundant replay source(s) · ~{mb:.1f} MB freed")
    return result


def clean_replay_farm(
    root: Optional[Path] = None,
    *,
    retention_days: Optional[int] = None,
    remove_hanoon_duplicates: bool = True,
    remove_orphan_intraday: bool = True,
    universe: Optional[Set[str]] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Organize replay data under REPLAY_DATA_DIR:
      - Normalize every intraday/*_1min.csv
      - Delete hanoon/{TICKER}.csv when intraday exists (duplicate source)
      - Delete intraday files not in universe (optional)
      - Delete empty/corrupt CSVs
    """
    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "error": "no_replay_dir"}

    retention = retention_days
    if retention is None:
        retention = int(os.getenv("REPLAY_RETENTION_DAYS", "60"))

    intraday_dir = root / "intraday"
    hanoon_dir = root / HANOON_SUBDIR
    intraday_dir.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Any] = {
        "ok": True,
        "root": str(root),
        "retention_days": retention,
        "normalized": [],
        "deleted_hanoon": [],
        "deleted_orphans": [],
        "deleted_invalid": [],
    }

    tickers = _intraday_tickers(intraday_dir)
    allowed = universe if universe is not None else _DEFAULT_UNIVERSE
    use_universe = os.getenv("REPLAY_CLEAN_STRICT_UNIVERSE", "false").lower() in (
        "1", "true", "yes",
    )

    if verbose:
        _progress(f"🧹 Replay farm clean — {intraday_dir} ({len(tickers)} intraday tickers)")

    try:
        remove_redundant_replay_sources(root, verbose=verbose)
    except Exception as exc:
        log.debug(f"Redundant replay source cleanup: {exc}")

    # Normalize intraday files
    for p in sorted(intraday_dir.glob(f"*{INTRADAY_SUFFIX}")):
        sym = p.stem.replace("_1min", "").replace("_1MIN", "").upper()
        if use_universe and allowed and sym not in allowed:
            try:
                p.unlink()
                result["deleted_orphans"].append(sym)
                if verbose:
                    _progress(f"  🗑  removed orphan intraday {sym} (not in universe)")
            except Exception:
                pass
            continue

        norm = normalize_intraday_csv(p, retention_days=retention)
        if norm.get("ok"):
            result["normalized"].append({
                "ticker": sym,
                "bars": norm.get("after"),
                "removed_dupes": norm.get("removed", 0),
            })
            if verbose:
                _progress(
                    f"  ✅ {sym}: {norm.get('after', 0):,} bars "
                    f"({norm.get('removed', 0)} dupes trimmed)"
                )
        else:
            try:
                p.unlink()
                result["deleted_invalid"].append(sym)
                if verbose:
                    _progress(f"  🗑  removed invalid intraday {sym}: {norm.get('reason')}")
            except Exception:
                pass

    # Remove duplicate daily hanoon CSVs when intraday exists
    if remove_hanoon_duplicates and hanoon_dir.is_dir():
        active = _intraday_tickers(intraday_dir)
        for p in sorted(hanoon_dir.glob("*.csv")):
            sym = p.stem.upper()
            if sym in active:
                try:
                    p.unlink()
                    result["deleted_hanoon"].append(sym)
                    if verbose:
                        _progress(f"  🗑  removed duplicate daily hanoon/{p.name} (intraday exists)")
                except Exception as exc:
                    log.debug(f"hanoon delete {p}: {exc}")

    # Remove stray daily CSVs at replay root (not intraday/)
    for p in sorted(root.glob("*.csv")):
        sym = p.stem.upper()
        if sym in _intraday_tickers(intraday_dir) or (hanoon_dir / f"{sym}.csv").is_file():
            try:
                p.unlink()
                result.setdefault("deleted_root_stray", []).append(p.name)
                if verbose:
                    _progress(f"  🗑  removed stray {p.name} from replay root")
            except Exception:
                pass

    result["intraday_tickers"] = len(_intraday_tickers(intraday_dir))
    if verbose:
        _progress(
            f"🧹 Done — {result['intraday_tickers']} intraday tickers · "
            f"hanoon dupes removed: {len(result['deleted_hanoon'])}"
        )
    return result


def should_purge_replay_data() -> bool:
    """
    True when session end should wipe all replay CSVs (learning stays in models/).
    Default: trim consumed bars only (REPLAY_TRIM_CONSUMED_ON_STOP=true).
    Full wipe when REPLAY_PURGE_ALL_ON_STOP=true, farm fully consumed, or legacy mode.
    """
    if os.getenv("REPLAY_PURGE_DATA_ON_STOP", "true").lower() not in ("1", "true", "yes"):
        return False
    if os.getenv("REPLAY_PURGE_ALL_ON_STOP", "false").lower() in ("1", "true", "yes"):
        return True
    trim_on = os.getenv("REPLAY_TRIM_CONSUMED_ON_STOP", "true").lower() in ("1", "true", "yes")
    if trim_on:
        try:
            from core.replay_consumption import farm_fully_consumed
            if not farm_fully_consumed():
                return False
        except Exception:
            pass
    if os.getenv("REPLAY_KEEP_CSV_BETWEEN_EPOCHS", "false").lower() in ("1", "true", "yes"):
        if os.getenv("WEEKEND_REPLAY_LOOP", "").lower() in ("1", "true", "yes"):
            return False
    return not trim_on


def purge_replay_farm(
    root: Optional[Path] = None,
    *,
    verbose: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Delete all replay CSV training data after session ends.
    Learning artifacts (models/, halim/data/training/) are untouched.
    """
    if not force and not should_purge_replay_data():
        return {"ok": True, "skipped": True, "reason": "purge_disabled"}

    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "error": "no_replay_dir"}

    result: Dict[str, Any] = {
        "ok": True,
        "root": str(root),
        "deleted_intraday": [],
        "deleted_hanoon": [],
        "deleted_stray": [],
        "deleted_other": [],
        "bytes_freed": 0,
    }

    def _unlink(p: Path, bucket: str) -> None:
        try:
            if not p.is_file():
                return
            size = p.stat().st_size
            p.unlink()
            result["bytes_freed"] = int(result.get("bytes_freed", 0)) + size
            result[bucket].append(p.name if bucket != "deleted_other" else str(p.relative_to(root)))
        except Exception as exc:
            log.debug(f"purge skip {p}: {exc}")

    intraday_dir = root / "intraday"
    hanoon_dir = root / HANOON_SUBDIR

    if verbose:
        _progress(f"🗑  Purging replay CSV farm under {root} (models/ learning kept)…")

    # Intraday IB historicals — primary storage consumer
    if intraday_dir.is_dir():
        for pattern in ("*.csv", "*.tmp", "*.parquet", "*.jsonl"):
            for p in sorted(intraday_dir.glob(pattern)):
                _unlink(p, "deleted_intraday")
        for p in sorted(intraday_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".csv", ".tmp", ".parquet", ".bak"):
                rel = str(p.relative_to(intraday_dir))
                if rel not in result["deleted_intraday"]:
                    _unlink(p, "deleted_intraday")

    if hanoon_dir.is_dir():
        for p in sorted(hanoon_dir.rglob("*")):
            if p.is_file():
                _unlink(p, "deleted_hanoon")

    for p in sorted(root.glob("*.csv")):
        _unlink(p, "deleted_stray")

    # Staging / partial download artifacts anywhere under replay root
    for pattern in ("*.tmp", "*.partial", "*.download", "*_partial.csv"):
        for p in sorted(root.rglob(pattern)):
            if p.is_file():
                _unlink(p, "deleted_other")

    # Keep folder structure
    intraday_dir.mkdir(parents=True, exist_ok=True)
    hanoon_dir.mkdir(parents=True, exist_ok=True)
    keep = intraday_dir / ".gitkeep"
    if not keep.is_file():
        try:
            keep.write_text("")
        except Exception:
            pass

    n = (
        len(result["deleted_intraday"])
        + len(result["deleted_hanoon"])
        + len(result["deleted_stray"])
        + len(result["deleted_other"])
    )
    if verbose:
        mb = result.get("bytes_freed", 0) / (1024 * 1024)
        _progress(
            f"🗑  Purged {n} file(s) · freed ~{mb:.1f} MB · "
            f"re-download next session via IB or weekend start"
        )
    result["files_deleted"] = n

    try:
        from datetime import datetime, timezone
        import json
        journal = Path("models/replay_purge.jsonl")
        journal.parent.mkdir(parents=True, exist_ok=True)
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "files_deleted": n,
                "bytes_freed": result.get("bytes_freed", 0),
                "root": str(root),
            }, separators=(",", ":")) + "\n")
    except Exception:
        pass

    return result


def maybe_purge_replay_farm(*, reason: str = "session_end", verbose: bool = True) -> Optional[Dict[str, Any]]:
    """Purge replay CSV farm when env allows."""
    if not should_purge_replay_data():
        if verbose:
            log.info(f"Replay farm purge skipped ({reason}) — REPLAY_KEEP_CSV_BETWEEN_EPOCHS=true")
        return None
    return purge_replay_farm(verbose=verbose)


def farm_status(root: Optional[Path] = None) -> Dict[str, Any]:
    """Quick health check for scripts/verify_replay_farm."""
    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "error": "no_replay_dir"}
    intraday = root / "intraday"
    hanoon = root / HANOON_SUBDIR
    intraday_syms = _intraday_tickers(intraday) if intraday.is_dir() else set()
    hanoon_dupes: List[str] = []
    if hanoon.is_dir():
        for p in hanoon.glob("*.csv"):
            if p.stem.upper() in intraday_syms:
                hanoon_dupes.append(p.name)
    counts = {}
    for sym in intraday_syms:
        p = intraday / f"{sym}_1min.csv"
        try:
            counts[sym] = sum(1 for _ in open(p, encoding="utf-8", errors="ignore")) - 1
        except Exception:
            counts[sym] = 0
    vals = list(counts.values()) or [0]
    return {
        "ok": True,
        "root": str(root),
        "intraday_tickers": len(intraday_syms),
        "hanoon_duplicate_daily": hanoon_dupes,
        "min_bars": min(vals),
        "max_bars": max(vals),
        "per_ticker": counts,
    }
