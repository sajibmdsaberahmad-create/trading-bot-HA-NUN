#!/usr/bin/env bash
# Monitor replay + PPO↔Halim coevolution health while session runs.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
MON_LOG="$LOG_DIR/replay_monitor.log"
INTERVAL="${MONITOR_INTERVAL_SEC:-45}"

mkdir -p "$LOG_DIR"
if [[ -d venv ]]; then source venv/bin/activate; fi

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$MON_LOG"; }

log "═══ Replay health monitor started (interval=${INTERVAL}s) ═══"

while true; do
  replay_up="no"
  if [[ -f "$LOG_DIR/replay.pid" ]]; then
    pid=$(cat "$LOG_DIR/replay.pid" 2>/dev/null || echo 0)
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      replay_up="yes pid=$pid"
    fi
  fi

  halim_up="no"
  for halim_health in "http://127.0.0.1:8765/health" "http://127.0.0.1:8765/v1/health"; do
    if curl -sf --max-time 2 "$halim_health" >/dev/null 2>&1; then
      halim_up="yes"
      break
    fi
  done

  v2=0 agree=0 disagree=0 comp=0 ppo_fix=0 halim_fix=0
  if [[ -f halim/data/coevolution/correction_log.jsonl ]]; then
    read -r v2 agree disagree comp ppo_fix halim_fix < <(
      python3 - <<'PY'
import json
from pathlib import Path
v2=ag=dis=comp=cpp=hfix=0
p=Path("halim/data/coevolution/correction_log.jsonl")
if p.is_file():
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-800:]:
        try: ev=json.loads(line)
        except: continue
        if ev.get("event"): continue
        if int(ev.get("label_version",1))>=2: v2+=1
        cmp=ev.get("comparison")or{}
        if cmp.get("ppo_halim_agree") is True: ag+=1
        elif cmp.get("ppo_halim_agree") is False: dis+=1
        if "halim_complement" in str(ev.get("pipeline")or""): comp+=1
        cf=cmp.get("correction_for")
        if cf=="ppo": cpp+=1
        elif cf=="halim": hfix+=1
print(v2, ag, dis, comp, cpp, hfix)
PY
    )
  fi

  err_tail=""
  if [[ -f "$LOG_DIR/REPLAY_SCALPER.log" ]]; then
    err_tail=$(tail -n 120 "$LOG_DIR/REPLAY_SCALPER.log" 2>/dev/null | grep -iE 'traceback|error|exception|OOM|failed' | tail -3 || true)
  fi

  log "replay=$replay_up halim_serve=$halim_up | v2=$v2 agree=$agree disagree=$disagree complement=$comp fix_ppo=$ppo_fix fix_halim=$halim_fix"
  if [[ -n "$err_tail" ]]; then
    log "  ⚠️ recent errors: $err_tail"
  fi

  if [[ "$v2" -gt 0 ]]; then
    log "  ✓ label_version=2 coevolution rows detected"
  fi

  if [[ "$replay_up" == no* ]]; then
    # idle until replay starts
    sleep "$INTERVAL"
    continue
  fi

  sleep "$INTERVAL"
done
