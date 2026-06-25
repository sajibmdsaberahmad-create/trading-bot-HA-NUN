#!/usr/bin/env python3
"""
scripts/git_auto_push.py — Standalone git auto-push daemon.

Runs independently of HANOON. Polls the repo and pushes changes using
credentials from .env (GITHUB_TOKEN, GITHUB_HANOON_REPO).

Usage:
  python3 scripts/git_auto_push.py          # foreground daemon
  ./scripts/start_git_sync.sh             # background daemon
  ./scripts/git_sync_once.sh              # one-shot push
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("GIT_SYNC_STANDALONE", "1")


def main() -> int:
    from core.config import BotConfig
    from core.git_sync import (
        init,
        preflight_check,
        run_standalone_daemon,
        set_standalone_mode,
    )
    from core.notify import log

    cfg = BotConfig()
    set_standalone_mode(True)

    from core.env_secrets import bootstrap_env
    ok_env, env_msg = bootstrap_env(ROOT)
    print(f"Env: {env_msg}")

    init(cfg)

    ok, lines = preflight_check(cfg)
    print("═══ Git sync preflight ═══")
    for line in lines:
        print(line)
    if not ok:
        print("\nFix the items above (.env GITHUB_TOKEN + GITHUB_HANOON_REPO), then retry.")
        return 1

    print("\nGit sync daemon running — Ctrl+C to stop\n")
    try:
        run_standalone_daemon(cfg)
    except KeyboardInterrupt:
        log.info("Git sync daemon interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
