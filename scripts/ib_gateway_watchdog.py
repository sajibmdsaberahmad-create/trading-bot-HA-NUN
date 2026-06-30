#!/usr/bin/env python3
"""
Standalone IB Gateway port watchdog — monitors localhost API socket during live trading.

Log-only sidecar (Telegram alerts come from IBConnector in HANOON). Writes
models/ib_connectivity.jsonl for port-up/port-down events.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG_PATH = Path(os.getenv("LOG_DIR", str(ROOT / "logs"))) / "ib_gateway_watchdog.log"
FLAG_DIR = Path(os.getenv("RUNTIME_DIR", str(ROOT / "runtime")))
DOWN_FLAG = FLAG_DIR / "ib_gateway_down.flag"


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC | {msg}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    print(line.rstrip())


def _journal(event: str, **fields) -> None:
    try:
        from core.ib_connectivity_journal import log_ib_connectivity
        log_ib_connectivity(event, source="gateway_watchdog", **fields)
    except Exception as exc:
        _log(f"{event} | {fields} (journal skipped: {exc})")


def _port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        _port_open.last_error = str(exc)  # type: ignore[attr-defined]
        return False


def _hanoon_running() -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-f", "main.py --mode scalper"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return r.returncode == 0
    except Exception:
        return False


def _shutdown_pending() -> bool:
    try:
        from core.shutdown_control import shutdown_requested
        return bool(shutdown_requested())
    except Exception:
        return Path(
            os.getenv("HANOON_SHUTDOWN_FILE", str(ROOT / "runtime/shutdown.request"))
        ).is_file()


def main() -> int:
    host = os.getenv("IB_HOST", "127.0.0.1")
    port = int(os.getenv("IB_PORT", "4002"))
    interval = float(os.getenv("IB_GATEWAY_WATCHDOG_INTERVAL_SEC", "30"))
    idle_interval = float(os.getenv("IB_GATEWAY_WATCHDOG_IDLE_SEC", "60"))
    ok_log_every = max(1, int(os.getenv("IB_GATEWAY_WATCHDOG_OK_EVERY", "20")))

    FLAG_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"IB Gateway watchdog started ({host}:{port}, active probe every {interval:.0f}s)")
    _journal("watchdog_start", host=host, port=port, interval_sec=interval)

    port_up = True
    down_since: float | None = None
    down_checks = 0
    ok_checks = 0

    while True:
        try:
            hanoon = _hanoon_running() and not _shutdown_pending()
            if hanoon:
                open_now = _port_open(host, port)
                if open_now:
                    if not port_up and down_since is not None:
                        duration = time.time() - down_since
                        _log(
                            f"Gateway port {host}:{port} UP after {duration:.0f}s "
                            f"({down_checks} failed probe(s))"
                        )
                        _journal(
                            "gateway_port_up",
                            host=host,
                            port=port,
                            down_sec=round(duration, 1),
                            failed_probes=down_checks,
                            level="info",
                        )
                    port_up = True
                    down_since = None
                    down_checks = 0
                    ok_checks += 1
                    if ok_checks == 1 or ok_checks % ok_log_every == 0:
                        _log(f"Gateway port {host}:{port} OK (HANOON active, probe #{ok_checks})")
                    try:
                        DOWN_FLAG.unlink(missing_ok=True)
                    except OSError:
                        pass
                else:
                    down_checks += 1
                    err = getattr(_port_open, "last_error", "connection refused")
                    if port_up:
                        down_since = time.time()
                        _log(
                            f"Gateway port {host}:{port} DOWN — "
                            f"HANOON IBConnector will wait ({err})"
                        )
                        _journal(
                            "gateway_port_down",
                            host=host,
                            port=port,
                            error=err[:200],
                            level="warning",
                        )
                    elif down_checks % 10 == 0:
                        elapsed = time.time() - (down_since or time.time())
                        _log(
                            f"Gateway port still DOWN ({elapsed:.0f}s, "
                            f"{down_checks} probes, last: {err})"
                        )
                    port_up = False
                    try:
                        DOWN_FLAG.write_text(
                            f"{datetime.now(timezone.utc).isoformat()} {host}:{port} {err}\n",
                            encoding="utf-8",
                        )
                    except OSError:
                        pass
                time.sleep(interval)
            else:
                if not port_up:
                    _log(f"Gateway port {host}:{port} idle check — HANOON not running")
                port_up = _port_open(host, port)
                down_since = None
                down_checks = 0
                ok_checks = 0
                try:
                    DOWN_FLAG.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(idle_interval)
        except KeyboardInterrupt:
            _log("IB Gateway watchdog stopped")
            _journal("watchdog_stop", level="info")
            return 0
        except Exception as exc:
            _log(f"watchdog error: {exc}")
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
