#!/usr/bin/env bash
# Interactive menu for double-click .command launchers.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
CLEAN="$DIR/mac-clean.sh"

confirm_yes() {
  local answer
  read -r -p "  Continue? [y/N]: " answer
  answer="$(echo "$answer" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  [[ "$answer" == "y" || "$answer" == "yes" ]]
}

run_scan() {
  "$CLEAN"
}

run_clean_safe() {
  echo ""
  echo "  This removes caches, logs, trash, temp, pip/npm, IDE caches."
  echo "  Documents, Desktop, and Photos are NEVER touched."
  echo ""
  if confirm_yes; then
    "$CLEAN" --clean --yes
  else
    echo "  Cancelled."
  fi
}

run_unload() {
  "$CLEAN" --unload --yes
}

run_clean_and_unload() {
  echo ""
  echo "  Clean disk (safe categories) and unload Ollama from RAM."
  if confirm_yes; then
    "$CLEAN" --unload --yes
    "$CLEAN" --clean --yes
  else
    echo "  Cancelled."
  fi
}

run_hanoon_clean() {
  echo ""
  echo "  HANOON project: duplicate releases, old checkpoints, git gc."
  echo "  Keeps venv, active Halim model, and git remote history."
  echo ""
  if confirm_yes; then
    "$CLEAN" --clean hanoon --yes
  else
    echo "  Cancelled."
  fi
}

run_full_clean() {
  echo ""
  echo "  Full clean: safe system caches + HANOON project cruft."
  echo ""
  if confirm_yes; then
    "$CLEAN" --clean all --yes
  else
    echo "  Cancelled."
  fi
}

run_aggressive() {
  echo ""
  echo "  AGGRESSIVE: docker prune, ollama prune, old Downloads (90+ days)."
  if confirm_yes; then
    "$CLEAN" --clean docker ollama_disk downloads --older-than 90 --yes
  else
    echo "  Cancelled."
  fi
}

show_menu() {
  clear
  echo ""
  echo "  ╔══════════════════════════════════════╗"
  echo "  ║       Mac Storage Cleaner            ║"
  echo "  ║   (standalone — not the trading bot) ║"
  echo "  ╚══════════════════════════════════════╝"
  echo ""
  echo "    1) Scan — show reclaimable space (safe)"
  echo "    2) Clean Safe — caches, logs, trash, temp…"
  echo "    3) Unload RAM — free Ollama from memory"
  echo "    4) Clean + Unload — disk + RAM"
    echo "    5) Aggressive — docker, ollama prune, old Downloads"
    echo "    6) HANOON — duplicate models, old checkpoints, git gc"
    echo "    7) Full — safe system + HANOON project clean"
    echo "    0) Quit"
  echo ""
}

MODE="${1:-}"

case "$MODE" in
  scan)       run_scan ;;
  clean)      run_clean_safe ;;
  unload)     run_unload ;;
  both)       run_clean_and_unload ;;
  aggressive) run_aggressive ;;
  hanoon)     run_hanoon_clean ;;
  full)       run_full_clean ;;
  "")
    while true; do
      show_menu
      read -r -p "  Choose [0-7]: " choice
      case "$choice" in
        1) run_scan; read -r -p "  Press Enter…" _ ;;
        2) run_clean_safe; read -r -p "  Press Enter…" _ ;;
        3) run_unload; read -r -p "  Press Enter…" _ ;;
        4) run_clean_and_unload; read -r -p "  Press Enter…" _ ;;
        5) run_aggressive; read -r -p "  Press Enter…" _ ;;
        6) run_hanoon_clean; read -r -p "  Press Enter…" _ ;;
        7) run_full_clean; read -r -p "  Press Enter…" _ ;;
        0|q|Q) echo "  Bye."; exit 0 ;;
        *) echo "  Invalid option."; sleep 1 ;;
      esac
    done
    ;;
  *)
    echo "Unknown mode: $MODE"
    exit 1
    ;;
esac
