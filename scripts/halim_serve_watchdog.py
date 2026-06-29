#!/usr/bin/env python3
"""
Standalone Halim serve watchdog — keeps :8765 alive while HANOON/replay trades.

Runs as its own process (started by start_hanoon.sh). If MLX OOM-kills serve or
health fails, restarts via ensure_halim_active.sh without waiting for HANOON's loop.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
halim_pkg = ROOT / "halim"
if str(halim_pkg) not in sys.path:
    sys.path.insert(0, str(halim_pkg))

LOG_PATH = Path(os.getenv("LOG_DIR", str(ROOT / "logs"))) / "halim_watchdog.log"


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC | {msg}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    print(line.rstrip())


def _health() -> bool:
    try:
        from halim.client import health
        return bool(health(timeout=3.0))
    except Exception:
        return False


def _trading_active() -> bool:
    try:
        from core.trading_focus_guard import is_trading_session_active
        return bool(is_trading_session_active())
    except Exception:
        return False


def _shutdown_pending() -> bool:
    try:
        from core.shutdown_control import shutdown_requested
        return bool(shutdown_requested())
    except Exception:
        return Path(os.getenv("HANOON_SHUTDOWN_FILE", str(ROOT / "runtime/shutdown.request"))).is_file()


def _restart_serve() -> bool:
    script = ROOT / "scripts" / "ensure_halim_active.sh"
    if not script.is_file():
        return False
    _log("Halim serve down — watchdog restarting…")
    try:
        proc = subprocess.run(
            ["bash", str(script), "--serve-only", "--restart"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=int(os.getenv("HALIM_WATCHDOG_RESTART_TIMEOUT_SEC", "180")),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if out.strip():
            for ln in out.strip().splitlines()[-6:]:
                _log(f"  {ln}")
        ok = proc.returncode == 0 and _health()
        _log("Halim serve restart OK" if ok else "Halim serve restart FAILED")
        return ok
    except Exception as exc:
        _log(f"Halim serve restart error: {exc}")
        return False


def _sync_serve_pid() -> None:
    try:
        r = subprocess.run(
            ["pgrep", "-f", "halim/halim/serve.py"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode != 0:
            return
        pid = (r.stdout or "").strip().splitlines()[0].strip()
        if pid.isdigit():
            pid_file = Path(os.getenv("LOG_DIR", str(ROOT / "logs"))) / "halim_serve.pid"
            pid_file.write_text(pid + "\n", encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    interval = float(os.getenv("HALIM_WATCHDOG_INTERVAL_SEC", "30"))
    idle_interval = float(os.getenv("HALIM_WATCHDOG_IDLE_SEC", "60"))
    _log(f"Halim serve watchdog started (trading every {interval:.0f}s)")

    fails = 0
    while True:
        try:
            if _trading_active() and not _shutdown_pending():
                if _health():
                    fails = 0
                    _sync_serve_pid()
                else:
                    fails += 1
                    _log(f"health check failed ({fails})")
                    if _restart_serve():
                        fails = 0
                    elif fails >= 3:
                        _log("3 restart failures — backing off 120s")
                        time.sleep(120)
                        fails = 0
                time.sleep(interval)
            else:
                time.sleep(idle_interval)
        except KeyboardInterrupt:
            _log("watchdog stopped (KeyboardInterrupt)")
            return 0
        except Exception as exc:
            _log(f"watchdog loop error: {exc}")
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
