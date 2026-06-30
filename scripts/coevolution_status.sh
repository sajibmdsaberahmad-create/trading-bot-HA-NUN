#!/usr/bin/env bash
# PPO ↔ Halim coevolution health — label quality, agree ratio, recent corrections.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

python3 - <<'PY'
import json
import sys

from core.halim_ppo_coevolution import coevolution_status_report

r = coevolution_status_report(recent=10)
all_t = r.get("all_time") or {}
v2 = r.get("since_label_v2") or {}

print("══════════════════════════════════════════════════════════════")
print("  PPO ↔ HALIM COEVOLUTION STATUS")
print("══════════════════════════════════════════════════════════════")
print(f"  Last cycle: {r.get('last_cycle') or 'never'}")
print(f"  Cycles run: {r.get('cycles', 0)}")
print()
print("  ALL-TIME (includes legacy mislabels before label_version=2)")
print(f"    Agree: {all_t.get('agreements', 0)}  Disagree: {all_t.get('disagreements', 0)}"
      f"  ratio={r.get('agree_ratio_all')}")
print(f"    Correct PPO: {all_t.get('corrections_for_ppo', 0)}"
      f"  Correct Halim: {all_t.get('corrections_for_halim', 0)}")
print(f"    Complements: {all_t.get('complements', 0)}"
      f"  legacy-skipped: {all_t.get('legacy_mislabeled_skipped', 0)}")
print(f"    Market proved PPO: {all_t.get('market_proved_ppo', 0)}"
      f"  Halim: {all_t.get('market_proved_halim', 0)}")
print()
print("  SINCE LABEL v2 (honest independent signals — trust this)")
print(f"    v2 events: {v2.get('label_v2_events', 0)}")
print(f"    Agree: {v2.get('agreements', 0)}  Disagree: {v2.get('disagreements', 0)}"
      f"  ratio={r.get('agree_ratio_v2')}")
print(f"    Correct PPO: {v2.get('corrections_for_ppo', 0)}"
      f"  Correct Halim: {v2.get('corrections_for_halim', 0)}")
print(f"    Complements: {v2.get('complements', 0)}")
print()
print("  RECENT EVENTS")
for row in r.get("recent") or []:
    if row.get("kind") == "decision":
        print(
            f"    [{row.get('time', '')[:19]}] {row.get('ticker')} {row.get('task')} "
            f"ppo={row.get('ppo')} halim={row.get('halim')} "
            f"agree={row.get('agree')} fix→{row.get('correction_for')} "
            f"src={row.get('halim_source')} v{row.get('label_version', 1)}"
        )
    else:
        print(
            f"    [{row.get('time', '')[:19]}] OUTCOME {row.get('ticker')} "
            f"proved={row.get('market_proved')} {row.get('outcome')} pnl={row.get('pnl')}"
        )
print()
if (v2.get("label_v2_events") or 0) < 5:
    print("  ⚠️  Few v2 labels yet — run replay/live so new honest coevolution rows accumulate.")
if (all_t.get("corrections_for_ppo") or 0) > 10 * max(all_t.get("corrections_for_halim") or 0, 1):
    if (v2.get("corrections_for_ppo") or 0) <= 3 * max(v2.get("corrections_for_halim") or 0, 1):
        print("  ✓ Legacy skew detected but v2 ratio looks healthier.")
    else:
        print("  ⚠️  PPO still over-corrected — check halim_lm serve + entry ring latency.")
print("══════════════════════════════════════════════════════════════")

if "--json" in sys.argv:
    print(json.dumps(r, indent=2))
PY
