#!/usr/bin/env python3
"""CLI: strict IB client_id guard — blocks HANOON start if client 1 is taken."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ib_client_guard import (  # noqa: E402
    acquire_lock,
    check_client_id_available,
    release_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard reserved IB API client ID")
    parser.add_argument("--client-id", type=int, default=int(os.getenv("CLIENT_ID", "1")))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "4002")))
    parser.add_argument(
        "--acquire", action="store_true", help="Acquire lock after check (main.py boot)",
    )
    parser.add_argument("--release", action="store_true", help="Release lock on shutdown")
    parser.add_argument("--force", action="store_true", help="Steal stale lock")
    args = parser.parse_args()

    if args.release:
        release_lock(args.client_id)
        print(f"Released IB client_id={args.client_id} lock")
        return 0

    ok, msg = check_client_id_available(args.client_id, ib_port=args.port)
    if not ok:
        print(msg, file=sys.stderr)
        return 1

    if args.acquire:
        got, amsg = acquire_lock(args.client_id, force=args.force)
        if not got:
            print(amsg, file=sys.stderr)
            return 1
        print(f"✅ {amsg}")
        return 0

    print(f"✅ {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
