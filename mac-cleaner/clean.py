#!/usr/bin/env python3
"""
Mac Storage Cleaner — standalone utility (not part of HANOON).

Scan, clean, and unload common macOS cruft: caches, logs, temp, trash,
pip/npm caches, Homebrew, Xcode DerivedData, Ollama RAM, etc.

Double-click: Start Mac Cleaner.command
CLI: ./mac-clean.sh
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

HOME = Path.home()

PROTECTED_PREFIXES: Tuple[Path, ...] = tuple(
    Path(p).expanduser().resolve()
    for p in (
        "~/Library/Keychains",
        "~/Library/Mobile Documents",
        "~/Library/MobileSync",
        "~/Library/CloudStorage",
        "~/Library/Application Support/MobileSync",
        "~/Pictures",
        "~/Movies",
        "~/Music",
        "~/Documents",
        "~/Desktop",
    )
)

PROTECTED_CACHE_NAMES: Set[str] = {
    "com.apple.bird",
    "com.apple.Safari",
    "com.apple.Safari.SafeBrowsing",
    "CloudKit",
}


def _fmt_bytes(n: int) -> str:
    n = max(0, int(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def _dir_size(path: Path, *, max_depth: int = 12) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for root, dirs, files in os.walk(path, topdown=True):
            depth = root.replace(str(path), "").count(os.sep)
            if depth > max_depth:
                dirs.clear()
                continue
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _is_protected(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    for prefix in PROTECTED_PREFIXES:
        try:
            resolved.relative_to(prefix)
            return True
        except ValueError:
            continue
    return False


def _rm_path(path: Path, *, dry_run: bool) -> int:
    if not path.exists() or _is_protected(path):
        return 0
    try:
        size = _dir_size(path)
    except OSError:
        size = 0
    if dry_run:
        return size
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path, ignore_errors=True)
        return size
    except OSError:
        return 0


def _prune_old_files(
    root: Path,
    *,
    older_than_days: int,
    dry_run: bool,
    max_files: int = 50_000,
) -> int:
    if not root.exists() or _is_protected(root):
        return 0
    cutoff = time.time() - older_than_days * 86400
    freed = 0
    count = 0
    try:
        for p in root.rglob("*"):
            count += 1
            if count > max_files:
                break
            if not p.is_file():
                continue
            try:
                if p.stat().st_mtime >= cutoff:
                    continue
                sz = p.stat().st_size
                if dry_run:
                    freed += sz
                else:
                    p.unlink(missing_ok=True)
                    freed += sz
            except OSError:
                pass
    except OSError:
        pass
    return freed


def _run_cmd(cmd: List[str], *, dry_run: bool) -> Tuple[int, str]:
    if dry_run:
        return 0, f"[dry-run] would run: {' '.join(cmd)}"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, str(exc)


@dataclass
class Category:
    name: str
    description: str
    scan: Callable[[], int]
    clean: Callable[[bool, int], int]


def _scan_paths(paths: Iterable[Path]) -> int:
    return sum(_dir_size(p) for p in paths if p.exists())


def _clean_paths(paths: Iterable[Path], *, dry_run: bool) -> int:
    return sum(_rm_path(p, dry_run=dry_run) for p in paths if p.exists())


def _user_caches_root() -> Path:
    return HOME / "Library" / "Caches"


def _scan_user_caches() -> int:
    root = _user_caches_root()
    if not root.exists():
        return 0
    total = 0
    try:
        for child in root.iterdir():
            if child.name in PROTECTED_CACHE_NAMES:
                continue
            if child.name.startswith("com.apple.") and child.name not in (
                "com.apple.python",
            ):
                continue
            total += _dir_size(child)
    except OSError:
        pass
    return total


def _clean_user_caches(*, dry_run: bool, older_than_days: int) -> int:
    root = _user_caches_root()
    if not root.exists():
        return 0
    freed = 0
    try:
        for child in root.iterdir():
            if child.name in PROTECTED_CACHE_NAMES:
                continue
            if child.name.startswith("com.apple.") and child.name not in (
                "com.apple.python",
            ):
                continue
            if older_than_days > 0 and child.is_dir():
                freed += _prune_old_files(
                    child, older_than_days=older_than_days, dry_run=dry_run,
                )
                if _dir_size(child) == 0:
                    freed += _rm_path(child, dry_run=dry_run)
            else:
                freed += _rm_path(child, dry_run=dry_run)
    except OSError:
        pass
    return freed


def _user_logs_paths() -> List[Path]:
    return [HOME / "Library" / "Logs"]


def _temp_paths() -> List[Path]:
    paths = [Path("/tmp")]
    tmp = os.environ.get("TMPDIR")
    if tmp:
        paths.append(Path(tmp))
    return paths


def _pip_cache() -> Optional[Path]:
    try:
        code, out = _run_cmd(
            [sys.executable, "-m", "pip", "cache", "dir"], dry_run=False,
        )
        if code == 0 and out.strip():
            return Path(out.strip())
    except Exception:
        pass
    return HOME / "Library" / "Caches" / "pip"


def _npm_cache() -> Optional[Path]:
    code, out = _run_cmd(["npm", "config", "get", "cache"], dry_run=False)
    if code == 0 and out.strip() and out.strip() != "undefined":
        return Path(out.strip())
    return HOME / ".npm"


def _yarn_cache() -> Path:
    return HOME / "Library" / "Caches" / "Yarn"


def _xcode_derived() -> Path:
    return HOME / "Library" / "Developer" / "Xcode" / "DerivedData"


def _cursor_cache() -> List[Path]:
    base = HOME / "Library" / "Application Support" / "Cursor"
    return [
        base / "Cache",
        base / "CachedData",
        base / "Code Cache",
        base / "GPUCache",
        HOME / "Library" / "Caches" / "com.todesktop.230313mzl4w4u92",
        _cursor_shipit_cache(),
    ]


def _vscode_cache() -> List[Path]:
    base = HOME / "Library" / "Application Support" / "Code"
    return [base / "Cache", base / "CachedData", base / "Code Cache"]


def _cursor_shipit_cache() -> Path:
    return HOME / "Library" / "Caches" / "com.todesktop.230313mzl4w4u92.ShipIt"


def _ide_hog_junk_paths() -> List[Path]:
    """Leftovers from Amazon Q, CodeWhisperer, Gemini Code Assist, Cloud Code CLI."""
    cursor_gs = HOME / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage"
    vscode_gs = HOME / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
    paths = [
        HOME / "Library" / "Application Support" / "amazon-q",
        HOME / "Library" / "Application Support" / "cloud-code",
        HOME / "Library" / "WebKit" / "com.amazon.codewhisperer",
        HOME / ".codewhisperer",
        HOME / ".amazonq",
        HOME / "Library" / "Preferences" / "com.amazon.codewhisperer.plist",
        HOME / "Library" / "Preferences" / "com.amazon.aws.codewhisperer.plist",
        HOME / "Library" / "Caches" / "com.amazon.codewhisperer",
        HOME / "Library" / "Caches" / "aws.toolkit.kit",
        cursor_gs / "amazonwebservices.amazon-q-vscode",
        cursor_gs / "google.geminicodeassist",
        vscode_gs / "amazonwebservices.amazon-q-vscode",
        vscode_gs / "google.geminicodeassist",
    ]
    for ext_root in (
        HOME / ".cursor" / "extensions",
        HOME / ".vscode" / "extensions",
    ):
        if ext_root.is_dir():
            for prefix in (
                "amazonwebservices.amazon-q-vscode-",
                "amazonwebservices.codewhisperer-for-command-line-companion-",
                "google.geminicodeassist-",
            ):
                paths.extend(ext_root.glob(f"{prefix}*"))
    return paths


def _scan_ide_hog_junk() -> int:
    return _scan_paths(_ide_hog_junk_paths())


def _clean_ide_hog_junk(dry_run: bool, older_than_days: int = 0) -> int:
    freed = _clean_paths(_ide_hog_junk_paths(), dry_run=dry_run)
    for pattern in (
        "Amazon Q Helper",
        "cloudcode_cli duet",
        "geminicodeassist.*/agent/a2a-server",
        "codewhisperer",
    ):
        if dry_run:
            continue
        try:
            subprocess.run(
                ["pkill", "-KILL", "-f", pattern],
                capture_output=True, timeout=3, check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
    return freed


def _cursor_logs_root() -> Path:
    return HOME / "Library" / "Application Support" / "Cursor" / "logs"


def _scan_cursor_ext_logs() -> int:
    total = 0
    root = _cursor_logs_root()
    if not root.is_dir():
        return 0
    stale_names = (
        "amazonwebservices.amazon-q-vscode",
        "google.geminicodeassist",
        "amazonwebservices.codewhisperer-for-command-line-companion",
    )
    try:
        for session in root.iterdir():
            exthost = session / "window1" / "exthost"
            if not exthost.is_dir():
                for wh in session.glob("window*"):
                    exthost = wh / "exthost"
                    if exthost.is_dir():
                        for name in stale_names:
                            total += _dir_size(exthost / name)
            else:
                for name in stale_names:
                    total += _dir_size(exthost / name)
        cutoff = time.time() - 7 * 86400
        for session in root.iterdir():
            try:
                if session.stat().st_mtime < cutoff:
                    total += _dir_size(session)
            except OSError:
                pass
    except OSError:
        pass
    return total


def _clean_cursor_ext_logs(dry_run: bool, older_than_days: int = 0) -> int:
    freed = 0
    root = _cursor_logs_root()
    if not root.is_dir():
        return 0
    stale_names = (
        "amazonwebservices.amazon-q-vscode",
        "google.geminicodeassist",
        "amazonwebservices.codewhisperer-for-command-line-companion",
    )
    try:
        for session in root.iterdir():
            for wh in session.glob("window*"):
                exthost = wh / "exthost"
                if not exthost.is_dir():
                    continue
                for name in stale_names:
                    freed += _rm_path(exthost / name, dry_run=dry_run)
        days = older_than_days or 7
        cutoff = time.time() - days * 86400
        for session in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
            try:
                if session.stat().st_mtime >= cutoff:
                    continue
                freed += _rm_path(session, dry_run=dry_run)
            except OSError:
                pass
        keep = sorted(
            [p for p in root.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in keep[2:]:
            freed += _rm_path(old, dry_run=dry_run)
    except OSError:
        pass
    return freed


def _ollama_models_dir() -> Path:
    return HOME / ".ollama" / "models"


def _clean_pip(dry_run: bool, older_than_days: int = 0) -> int:
    if dry_run:
        cache = _pip_cache()
        return _dir_size(cache) if cache else 0
    code, _out = _run_cmd([sys.executable, "-m", "pip", "cache", "purge"], dry_run=False)
    if code != 0:
        cache = _pip_cache()
        return _rm_path(cache, dry_run=False) if cache else 0
    return 0


def _clean_npm(dry_run: bool, older_than_days: int = 0) -> int:
    cache = _npm_cache()
    before = _dir_size(cache) if cache else 0
    if dry_run:
        return before
    _run_cmd(["npm", "cache", "clean", "--force"], dry_run=False)
    after = _dir_size(cache) if cache else 0
    return max(0, before - after)


def _clean_homebrew_with_estimate(dry_run: bool, older_than_days: int = 0) -> int:
    brew = shutil.which("brew")
    if not brew:
        return 0
    cellar = (
        Path("/opt/homebrew/Cellar")
        if Path("/opt/homebrew").exists()
        else Path("/usr/local/Cellar")
    )
    before = _dir_size(cellar)
    if dry_run:
        _run_cmd([brew, "cleanup", "-n", "-s"], dry_run=False)
        return 0
    _run_cmd([brew, "cleanup", "-s", "--prune=all"], dry_run=False)
    after = _dir_size(cellar)
    return max(0, before - after)


def _clean_docker(dry_run: bool, older_than_days: int = 0) -> int:
    if not shutil.which("docker"):
        return 0
    if dry_run:
        _run_cmd(["docker", "system", "df"], dry_run=False)
        return 0
    code, out = _run_cmd(["docker", "system", "prune", "-af", "--volumes"], dry_run=False)
    print(out[:2000] if out else "")
    return 0 if code == 0 else 0


def _unload_ollama(*, dry_run: bool) -> None:
    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/ps", timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        print("  Ollama not reachable — skip unload")
        return
    models = [m.get("name") or m.get("model") for m in data.get("models", [])]
    models = [m for m in models if m]
    if not models:
        print("  Ollama: no models loaded in RAM")
        return
    for model in models:
        payload = json.dumps({"model": model, "keep_alive": 0}).encode()
        if dry_run:
            print(f"  [dry-run] would unload RAM: {model}")
            continue
        try:
            req = urllib.request.Request(
                f"{base}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            print(f"  Unloaded from RAM: {model}")
        except Exception as exc:
            print(f"  Unload {model}: {exc}")


def _purge_memory_hint(*, dry_run: bool) -> None:
    if dry_run:
        print("  [dry-run] sudo purge — frees inactive RAM (requires password)")
        return
    if os.geteuid() == 0:
        subprocess.run(["purge"], check=False)
        print("  Ran purge (root)")
    else:
        print("  Tip: run `sudo purge` to free inactive RAM (optional)")


def _hanoon_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _hanoon_log_paths() -> List[Path]:
    root = _hanoon_root()
    return [
        root / "HANOON.log",
        root / "logs" / "REPLAY_SCALPER.log",
        root / "logs" / "halim_serve.log",
        root / "logs" / "WEEKEND_REPLAY.log",
    ]


def _scan_hanoon_duplicates() -> int:
    total = 0
    rel = _hanoon_root() / "halim-release"
    if rel.is_dir():
        total += _dir_size(rel)
    dl = HOME / "Downloads"
    for name in (
        "halim_toddler_v1.zip",
        "halim_toddler_v2.zip",
        "halim_toddler_v3",
        "halim_toddler_v3.zip",
    ):
        p = dl / name
        if p.exists():
            total += _dir_size(p)
    stale = HOME / "Downloads" / "venv"
    if stale.is_dir() and stale != _hanoon_root() / "venv":
        total += _dir_size(stale)
    return total


def _scan_hanoon_cruft() -> int:
    root = _hanoon_root()
    total = 0
    try:
        for p in root.rglob("__pycache__"):
            total += _dir_size(p)
        for p in root.rglob("*.pyc"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    ckpt = root / "halim" / "data" / "checkpoints" / "toddler_v1" / "lora_adapter"
    if ckpt.is_dir():
        for d in ckpt.glob("checkpoint-*"):
            for name in ("optimizer.pt", "scheduler.pt", "rng_state.pth"):
                f = d / name
                if f.is_file():
                    total += f.stat().st_size
    for name in ("toddler_v1_test", "toddler_v1_test2"):
        p = root / "halim" / "data" / "checkpoints" / name
        if p.is_dir():
            total += _dir_size(p)
    for lp in _hanoon_log_paths():
        if lp.is_file() and lp.stat().st_size > 500_000:
            total += lp.stat().st_size - 500_000
    return total


def _clean_hanoon_duplicates(dry_run: bool, older_than_days: int = 0) -> int:
    freed = 0
    rel = _hanoon_root() / "halim-release"
    if rel.is_dir():
        freed += _rm_path(rel, dry_run=dry_run)
    dl = HOME / "Downloads"
    for name in (
        "halim_toddler_v1.zip",
        "halim_toddler_v2.zip",
        "halim_toddler_v3",
        "halim_toddler_v3.zip",
    ):
        freed += _rm_path(dl / name, dry_run=dry_run)
    stale = HOME / "Downloads" / "venv"
    if stale.is_dir() and stale != _hanoon_root() / "venv":
        freed += _rm_path(stale, dry_run=dry_run)
    return freed


def _clean_hanoon_cruft(dry_run: bool, older_than_days: int = 0) -> int:
    root = _hanoon_root()
    freed = 0
    try:
        for p in list(root.rglob("__pycache__")):
            freed += _rm_path(p, dry_run=dry_run)
        for p in list(root.rglob("*.pyc")):
            if p.is_file():
                try:
                    sz = p.stat().st_size
                    if dry_run:
                        freed += sz
                    else:
                        p.unlink(missing_ok=True)
                        freed += sz
                except OSError:
                    pass
    except OSError:
        pass

    ckpt = root / "halim" / "data" / "checkpoints" / "toddler_v1" / "lora_adapter"
    keep_steps: List[int] = []
    if ckpt.is_dir():
        steps = []
        for d in ckpt.glob("checkpoint-*"):
            try:
                steps.append(int(d.name.split("-")[-1]))
            except ValueError:
                continue
        steps.sort()
        keep_steps = steps[-2:] if len(steps) > 2 else steps
        for d in ckpt.glob("checkpoint-*"):
            try:
                step = int(d.name.split("-")[-1])
            except ValueError:
                continue
            if step not in keep_steps:
                freed += _rm_path(d, dry_run=dry_run)
            else:
                for name in ("optimizer.pt", "scheduler.pt", "rng_state.pth"):
                    f = d / name
                    if f.is_file():
                        try:
                            sz = f.stat().st_size
                            if dry_run:
                                freed += sz
                            else:
                                f.unlink(missing_ok=True)
                                freed += sz
                        except OSError:
                            pass
    for name in ("toddler_v1_test", "toddler_v1_test2"):
        freed += _rm_path(root / "halim" / "data" / "checkpoints" / name, dry_run=dry_run)

    for lp in _hanoon_log_paths():
        if lp.name == "halim_serve.log":
            continue  # keep crash traces — MLX segfaults are diagnosed from this log
        if not lp.is_file() or lp.stat().st_size <= 500_000:
            continue
        if dry_run:
            freed += lp.stat().st_size - 500_000
        else:
            try:
                lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()
                lp.write_text("\n".join(lines[-2000:]) + "\n", encoding="utf-8")
                freed += max(0, lp.stat().st_size)
            except OSError:
                pass
    return freed


def _clean_git_gc(dry_run: bool, older_than_days: int = 0) -> int:
    root = _hanoon_root()
    git = root / ".git"
    if not git.is_dir():
        return 0
    before = _dir_size(git)
    if dry_run:
        return max(0, before // 10)
    try:
        subprocess.run(
            ["git", "-C", str(root), "gc", "--prune=now"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0
    after = _dir_size(git)
    return max(0, before - after)


def _clean_cursor_shipit(dry_run: bool, older_than_days: int = 0) -> int:
    return _rm_path(_cursor_shipit_cache(), dry_run=dry_run)


def build_categories() -> Dict[str, Category]:
    return {
        "caches": Category(
            "caches",
            "User Library/Caches (skips Safari/iCloud)",
            _scan_user_caches,
            lambda dry, old: _clean_user_caches(dry_run=dry, older_than_days=old),
        ),
        "logs": Category(
            "logs",
            "User Library/Logs",
            lambda: _scan_paths(_user_logs_paths()),
            lambda dry, old: sum(
                _prune_old_files(p, older_than_days=old or 7, dry_run=dry)
                if old else _rm_path(p, dry_run=dry)
                for p in _user_logs_paths()
            ),
        ),
        "trash": Category(
            "trash",
            "Trash (~/.Trash)",
            lambda: _dir_size(HOME / ".Trash"),
            lambda dry, _old: _rm_path(HOME / ".Trash", dry_run=dry),
        ),
        "temp": Category(
            "temp",
            "Temp folders (/tmp, $TMPDIR)",
            lambda: _scan_paths(_temp_paths()),
            lambda dry, old: sum(
                _prune_old_files(p, older_than_days=old or 1, dry_run=dry)
                for p in _temp_paths()
            ),
        ),
        "pip": Category(
            "pip",
            "pip download cache",
            lambda: _dir_size(_pip_cache() or Path()),
            _clean_pip,
        ),
        "npm": Category(
            "npm",
            "npm cache",
            lambda: _dir_size(_npm_cache() or Path()),
            _clean_npm,
        ),
        "yarn": Category(
            "yarn",
            "Yarn cache",
            lambda: _dir_size(_yarn_cache()),
            lambda dry, _old: _rm_path(_yarn_cache(), dry_run=dry),
        ),
        "homebrew": Category(
            "homebrew",
            "Homebrew old kegs and caches",
            lambda: 0,
            _clean_homebrew_with_estimate,
        ),
        "xcode": Category(
            "xcode",
            "Xcode DerivedData",
            lambda: _dir_size(_xcode_derived()),
            lambda dry, _old: _rm_path(_xcode_derived(), dry_run=dry),
        ),
        "cursor": Category(
            "cursor",
            "Cursor IDE caches",
            lambda: _scan_paths(_cursor_cache()),
            lambda dry, _old: _clean_paths(_cursor_cache(), dry_run=dry),
        ),
        "vscode": Category(
            "vscode",
            "VS Code caches",
            lambda: _scan_paths(_vscode_cache()),
            lambda dry, _old: _clean_paths(_vscode_cache(), dry_run=dry),
        ),
        "ds_store": Category(
            "ds_store",
            ".DS_Store under home dev folders (Downloads, projects)",
            lambda: 0,
            lambda dry, _old: _clean_ds_store(dry_run=dry),
        ),
        "ollama_disk": Category(
            "ollama_disk",
            "Ollama unused model blobs (ollama prune)",
            lambda: _dir_size(_ollama_models_dir()),
            _clean_ollama_prune,
        ),
        "docker": Category(
            "docker",
            "Docker images/containers/volumes (aggressive)",
            lambda: 0,
            _clean_docker,
        ),
        "downloads": Category(
            "downloads",
            "Old files in ~/Downloads (use --older-than)",
            lambda: _dir_size(HOME / "Downloads"),
            lambda dry, old: _prune_old_files(
                HOME / "Downloads",
                older_than_days=old or 90,
                dry_run=dry,
            ),
        ),
        "cursor_shipit": Category(
            "cursor_shipit",
            "Cursor updater ShipIt cache (~1GB)",
            lambda: _dir_size(_cursor_shipit_cache()),
            _clean_cursor_shipit,
        ),
        "hanoon_duplicates": Category(
            "hanoon_duplicates",
            "Regenerable halim-release + duplicate toddler zips in Downloads",
            _scan_hanoon_duplicates,
            _clean_hanoon_duplicates,
        ),
        "hanoon_cruft": Category(
            "hanoon_cruft",
            "HANOON __pycache__, old LoRA checkpoints, trim logs (keeps venv + active model)",
            _scan_hanoon_cruft,
            _clean_hanoon_cruft,
        ),
        "ide_hog_junk": Category(
            "ide_hog_junk",
            "Amazon Q / Gemini Code Assist / Cloud Code leftovers + kill sidecars",
            _scan_ide_hog_junk,
            _clean_ide_hog_junk,
        ),
        "cursor_ext_logs": Category(
            "cursor_ext_logs",
            "Cursor stale session logs + removed extension exthost junk",
            _scan_cursor_ext_logs,
            _clean_cursor_ext_logs,
        ),
        "git_gc": Category(
            "git_gc",
            "git gc in tradingbot repo (safe — history stays on remote)",
            lambda: _dir_size(_hanoon_root() / ".git") // 10,
            _clean_git_gc,
        ),
    }


DEFAULT_CATEGORIES = [
    "caches", "logs", "trash", "temp", "pip", "npm", "yarn",
    "homebrew", "xcode", "cursor", "cursor_shipit", "vscode", "ds_store",
]

HANOON_CATEGORIES = [
    "ide_hog_junk", "cursor_ext_logs", "hanoon_duplicates", "hanoon_cruft", "git_gc",
]

ALL_PRESET = DEFAULT_CATEGORIES + HANOON_CATEGORIES


def _clean_ds_store(*, dry_run: bool) -> int:
    roots = [
        HOME / "Downloads",
        HOME / "dev",
        HOME / "Developer",
        HOME / "Projects",
        HOME / "Code",
        Path(__file__).resolve().parent,
    ]
    freed = 0
    for root in roots:
        if not root.exists() or _is_protected(root):
            continue
        try:
            for p in root.rglob(".DS_Store"):
                if p.is_file():
                    try:
                        sz = p.stat().st_size
                        if dry_run:
                            freed += sz
                        else:
                            p.unlink(missing_ok=True)
                            freed += sz
                    except OSError:
                        pass
        except OSError:
            pass
    return freed


def _clean_ollama_prune(dry_run: bool, older_than_days: int = 0) -> int:
    ollama = shutil.which("ollama")
    if not ollama:
        return 0
    before = _dir_size(_ollama_models_dir())
    if dry_run:
        print("  [dry-run] would run: ollama prune")
        return 0
    code, out = _run_cmd([ollama, "prune", "-f"], dry_run=False)
    if out:
        print(f"  {out[:500]}")
    after = _dir_size(_ollama_models_dir())
    return max(0, before - after) if code == 0 else 0


def _print_scan(categories: Dict[str, Category], selected: List[str]) -> int:
    print("\n  Mac Storage Cleaner — scan\n")
    total = 0
    rows: List[Tuple[str, str, int]] = []
    for name in selected:
        cat = categories[name]
        try:
            size = cat.scan()
        except Exception as exc:
            print(f"  ⚠ {name}: scan failed ({exc})")
            size = 0
        rows.append((name, cat.description, size))
        total += size
    width = max(len(r[0]) for r in rows) if rows else 10
    for name, desc, size in rows:
        mark = _fmt_bytes(size).rjust(10)
        print(f"  {name.ljust(width)}  {mark}  {desc}")
    print(f"\n  Estimated reclaimable (scan): ~{_fmt_bytes(total)}")
    print("  Run Clean Safe.command or menu option 2 to delete.\n")
    return total


def main(argv: Optional[List[str]] = None) -> int:
    categories = build_categories()
    all_names = list(categories.keys())

    parser = argparse.ArgumentParser(
        description="Mac Storage Cleaner — standalone, safe scan/clean/unload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Categories: " + ", ".join(all_names) + "\n"
            "Default is scan-only. Use --clean --yes to actually delete."
        ),
    )
    parser.add_argument("--clean", action="store_true", help="Clean selected categories")
    parser.add_argument("--yes", "-y", action="store_true", help="Confirm destructive clean")
    parser.add_argument("--unload", action="store_true", help="Unload Ollama models from RAM")
    parser.add_argument("--purge", action="store_true", help="RAM purge hint / sudo purge")
    parser.add_argument(
        "--older-than", type=int, default=0, metavar="DAYS",
        help="Only remove files older than N days",
    )
    parser.add_argument(
        "categories", nargs="*", default=["all"],
        help="Categories to scan/clean, or 'all'",
    )
    args = parser.parse_args(argv)

    selected = args.categories
    if not selected or selected == ["all"]:
        selected = list(ALL_PRESET)
    elif selected == ["hanoon"]:
        selected = list(HANOON_CATEGORIES)
    elif selected == ["safe"]:
        selected = list(DEFAULT_CATEGORIES)

    unknown = [c for c in selected if c not in categories]
    if unknown:
        print(f"Unknown categories: {', '.join(unknown)}", file=sys.stderr)
        print(f"Valid: {', '.join(all_names)}", file=sys.stderr)
        return 2

    dry_run = not (args.clean and args.yes)

    if args.unload or args.purge:
        print("\n  Memory unload\n")
        _unload_ollama(dry_run=dry_run and not args.yes)
        if args.purge:
            _purge_memory_hint(dry_run=dry_run and not args.yes)
        print()

    if not args.clean:
        _print_scan(categories, selected)
        return 0

    if not args.yes:
        print("Refusing to clean without --yes (dry-run scan instead):\n")
        _print_scan(categories, selected)
        return 1

    print("\n  Mac Storage Cleaner — cleaning\n")
    total_freed = 0
    for name in selected:
        cat = categories[name]
        try:
            freed = cat.clean(False, args.older_than)
        except Exception as exc:
            print(f"  ⚠ {name}: {exc}")
            freed = 0
        total_freed += freed
        print(f"  ✓ {name.ljust(12)} freed ~{_fmt_bytes(freed)}")
    print(f"\n  Done — ~{_fmt_bytes(total_freed)} reclaimed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
