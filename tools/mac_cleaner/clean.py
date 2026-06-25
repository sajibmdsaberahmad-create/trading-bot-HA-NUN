#!/usr/bin/env python3
"""
Mac Storage Cleaner — standalone utility (not part of HANOON).

Scan, clean, and unload common macOS cruft: caches, logs, temp, trash,
pip/npm caches, Homebrew, Xcode DerivedData, Ollama RAM, etc.

Examples:
  python3 tools/mac_cleaner/clean.py              # scan only (safe default)
  python3 tools/mac_cleaner/clean.py --unload     # unload Ollama models from RAM
  python3 tools/mac_cleaner/clean.py --clean --yes
  python3 tools/mac_cleaner/clean.py --clean caches logs trash pip --yes
  python3 tools/mac_cleaner/clean.py --clean all --older-than 14 --yes
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

HOME = Path.home()

# Never delete these (even inside broader sweeps)
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
    ]


def _vscode_cache() -> List[Path]:
    base = HOME / "Library" / "Application Support" / "Code"
    return [base / "Cache", base / "CachedData", base / "Code Cache"]


def _ollama_models_dir() -> Path:
    return HOME / ".ollama" / "models"


def _clean_pip(*, dry_run: bool, _older: int) -> int:
    if dry_run:
        cache = _pip_cache()
        return _dir_size(cache) if cache else 0
    code, out = _run_cmd([sys.executable, "-m", "pip", "cache", "purge"], dry_run=False)
    if code != 0:
        cache = _pip_cache()
        return _rm_path(cache, dry_run=False) if cache else 0
    return _dir_size(_pip_cache() or Path("/dev/null"))  # report after


def _clean_npm(*, dry_run: bool, _older: int) -> int:
    cache = _npm_cache()
    before = _dir_size(cache) if cache else 0
    if dry_run:
        return before
    _run_cmd(["npm", "cache", "clean", "--force"], dry_run=False)
    after = _dir_size(cache) if cache else 0
    return max(0, before - after)


def _clean_homebrew(*, dry_run: bool, _older: int) -> int:
    brew = shutil.which("brew")
    if not brew:
        return 0
    if dry_run:
        code, out = _run_cmd([brew, "cleanup", "-n", "-s"], dry_run=False)
        # brew -n prints what would be removed; size estimate is rough
        return 0 if code != 0 else len(out) * 1024  # placeholder — run real cleanup for size
    code, _ = _run_cmd([brew, "cleanup", "-s", "--prune=all"], dry_run=False)
    return 0 if code != 0 else 0  # brew doesn't report bytes; user sees brew output


def _clean_homebrew_with_estimate(*, dry_run: bool, _older: int) -> int:
    brew = shutil.which("brew")
    if not brew:
        return 0
    cellar = Path("/opt/homebrew/Cellar") if (Path("/opt/homebrew")).exists() else Path("/usr/local/Cellar")
    before = _dir_size(cellar)
    if dry_run:
        _run_cmd([brew, "cleanup", "-n", "-s"], dry_run=False)
        return 0
    _run_cmd([brew, "cleanup", "-s", "--prune=all"], dry_run=False)
    after = _dir_size(cellar)
    return max(0, before - after)


def _clean_docker(*, dry_run: bool, _older: int) -> int:
    if not shutil.which("docker"):
        return 0
    if dry_run:
        _run_cmd(["docker", "system", "df"], dry_run=False)
        return 0
    code, out = _run_cmd(["docker", "system", "prune", "-af", "--volumes"], dry_run=False)
    print(out[:2000] if out else "")
    return 0 if code == 0 else 0


def _unload_ollama(*, dry_run: bool) -> None:
    """Unload all models from Ollama RAM (disk blobs stay)."""
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
    """macOS memory purge requires sudo — show hint only."""
    if dry_run:
        print("  [dry-run] sudo purge — frees inactive RAM (requires password)")
        return
    if os.geteuid() == 0:
        subprocess.run(["purge"], check=False)
        print("  Ran purge (root)")
    else:
        print("  Tip: run `sudo purge` to free inactive RAM (optional)")


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
    }


def _clean_ds_store(*, dry_run: bool) -> int:
  roots = [
        HOME / "Downloads",
        HOME / "dev",
        HOME / "Developer",
        HOME / "Projects",
        HOME / "Code",
        Path.cwd(),
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


def _clean_ollama_prune(*, dry_run: bool, _older: int) -> int:
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
    print("  Run with --clean --yes to delete. Add --unload to free Ollama RAM.\n")
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
    parser.add_argument(
        "--clean", action="store_true",
        help="Clean selected categories (requires --yes)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Confirm destructive clean",
    )
    parser.add_argument(
        "--unload", action="store_true",
        help="Unload Ollama models from RAM + memory tips",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="Try sudo purge for inactive RAM (hint if not root)",
    )
    parser.add_argument(
        "--older-than", type=int, default=0, metavar="DAYS",
        help="Only remove files older than N days (logs/temp/downloads/caches)",
    )
    parser.add_argument(
        "categories", nargs="*", default=["all"],
        help=f"Categories to scan/clean, or 'all' (default: all)",
    )
    args = parser.parse_args(argv)

    selected = args.categories
    if not selected or selected == ["all"]:
        # Safe default set — excludes docker, downloads, ollama_disk unless user asks
        selected = [
            "caches", "logs", "trash", "temp", "pip", "npm", "yarn",
            "homebrew", "xcode", "cursor", "vscode", "ds_store",
        ]

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
            freed = cat.clean(dry_run=False, older_than_days=args.older_than)
        except Exception as exc:
            print(f"  ⚠ {name}: {exc}")
            freed = 0
        total_freed += freed
        print(f"  ✓ {name.ljust(12)} freed ~{_fmt_bytes(freed)}")
    print(f"\n  Done — ~{_fmt_bytes(total_freed)} reclaimed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
