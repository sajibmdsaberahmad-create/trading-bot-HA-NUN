#!/usr/bin/env python3
"""
core/ib_client_guard.py — Exclusive IB API client ID lock (prevents 10197 / MD fights).

HANOON reserves client_id=1 (default). Other scripts must use 97+ (reconcile) or 99 (probe).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOCK_DIR = ROOT / "models"
DEFAULT_RESERVED = int(os.getenv("CLIENT_ID", os.getenv("IB_CLIENT_ID", "1")))


def lock_path(client_id: int) -> Path:
    return LOCK_DIR / f".ib_client_{client_id}.lock"


def _read_lock(client_id: int) -> Optional[Dict[str, Any]]:
    p = lock_path(client_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"pid": 0, "stale": True}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(
    client_id: int,
    *,
    pid: Optional[int] = None,
    command: str = "",
    force: bool = False,
) -> Tuple[bool, str]:
    """Claim client_id lock file. Returns (ok, message)."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    pid = int(pid or os.getpid())
    existing = _read_lock(client_id)
    if existing and not force:
        ep = int(existing.get("pid", 0) or 0)
        if _pid_alive(ep) and ep != pid:
            return False, (
                f"IB client_id={client_id} locked by pid {ep} "
                f"({existing.get('command', '?')[:80]})"
            )
    payload = {
        "pid": pid,
        "client_id": client_id,
        "command": (command or " ".join(sys.argv))[:200],
        "ts": time.time(),
    }
    lock_path(client_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True, f"lock acquired client_id={client_id} pid={pid}"


def release_lock(client_id: int, *, pid: Optional[int] = None) -> None:
    pid = int(pid or os.getpid())
    existing = _read_lock(client_id)
    if existing and int(existing.get("pid", 0) or 0) not in (0, pid):
        return
    p = lock_path(client_id)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


def _cmdline(pid: int) -> str:
    try:
        import subprocess
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True)
        return out.strip()
    except Exception:
        return ""


def _scan_conflicting_processes(client_id: int, ib_port: int) -> List[Dict[str, Any]]:
    """Find Python/IB processes that would steal the reserved client ID."""
    import re
    import subprocess

    conflicts: List[Dict[str, Any]] = []
    patterns = [
        re.compile(rf"main\.py\b.*--client-id[=\s]+{client_id}\b"),
        re.compile(rf"download_ib_replay_data\.py\b.*--client-id[=\s]+{client_id}\b"),
        re.compile(rf"clientId[=:\s]+{client_id}\b"),
        re.compile(rf"CLIENT_ID[=:\s]+{client_id}\b"),
        re.compile(rf"IB_CLIENT_ID[=:\s]+{client_id}\b"),
    ]
    # Scripts allowed on other IDs only — never match if they use a different id
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,args="], text=True)
    except Exception:
        return conflicts

    for line in out.splitlines():
        line = line.strip()
        if not line or "grep" in line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if pid == os.getpid():
            continue
        if "ib_insync" not in cmd and "main.py" not in cmd and "tradingbot" not in cmd:
            if not any(x in cmd for x in ("download_ib_replay", "reconcile_ib", "verify_tick")):
                continue
        for pat in patterns:
            if pat.search(cmd):
                conflicts.append({"pid": pid, "cmd": cmd[:160]})
                break

    # Lock file from dead session
    lock = _read_lock(client_id)
    if lock:
        lp = int(lock.get("pid", 0) or 0)
        if lp and _pid_alive(lp):
            if not any(c["pid"] == lp for c in conflicts):
                conflicts.append({"pid": lp, "cmd": lock.get("command", "lock file")})
        elif lp and not _pid_alive(lp):
            release_lock(client_id)

    return conflicts


def check_client_id_available(
    client_id: int = DEFAULT_RESERVED,
    *,
    ib_port: int = 4002,
) -> Tuple[bool, str]:
    conflicts = _scan_conflicting_processes(client_id, ib_port)
    if conflicts:
        lines = [f"IB client_id={client_id} conflict — stop these first:"]
        for c in conflicts[:8]:
            lines.append(f"  pid {c['pid']}: {c['cmd']}")
        lines.append("  Fix: ./stop.sh  or  kill <pid>")
        lines.append(f"  Other tools: use --client-id 97+ (not {client_id})")
        return False, "\n".join(lines)
    return True, f"IB client_id={client_id} available"


def guard_or_exit(client_id: int = DEFAULT_RESERVED, ib_port: int = 4002) -> None:
    ok, msg = check_client_id_available(client_id, ib_port=ib_port)
    if not ok:
        print(msg, file=sys.stderr)
        sys.exit(1)
    print(f"✅ {msg}")
