#!/usr/bin/env python3
"""Commander-style probes for Halim toddler LM — JSON + production token scoring."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from halim.engine import checkpoint_path, complete_reasoning, collect_status  # noqa: E402

# Production-style prompt (matches SFT gold: ENTER | confidence=0.xx | reason)
_TOKEN_PREFIX = (
    "You are M. A. Halim. Reply in one line:\n"
    "ACTION | confidence=0.00-1.00 | brief reason\n"
    "ACTION is ENTER, SKIP, EXIT, or STAND_ASIDE.\n"
)

PROBES: List[Dict[str, Any]] = [
    {
        "id": "weak_setup_skip",
        "purpose": "entry_decision",
        "context": (
            "TASK: entry_decision\n"
            "spike=1.3x scan=48 profit_prob=0.42 fakeout=0.58 conviction=45% ticker=SMTK\n"
            "Source: commander IB lessons"
        ),
        "expected_action": "SKIP",
        "expect_enter": False,
    },
    {
        "id": "plug_strong_enter",
        "purpose": "entry_decision",
        "context": (
            "TASK: entry_decision\n"
            "spike=2.3x scan=76 profit_prob=0.83 fakeout=0.20 ticker=PLUG sector=Energy conviction=88%\n"
            "Source: commander IB lessons"
        ),
        "expected_action": "ENTER",
        "expect_enter": True,
    },
    {
        "id": "ctev_hope_hold_exit",
        "purpose": "exit_decision",
        "context": (
            "TASK: exit_decision\n"
            "open_pnl=-4.2% ticker=CTEV peak_pnl=+1.1% hold_sec=600 hope_hold=true\n"
            "Source: commander IB lessons"
        ),
        "expected_action": "EXIT",
        "expect_exit": True,
    },
    {
        "id": "calculated_lottery_enter",
        "purpose": "entry_decision",
        "context": (
            "TASK: entry_decision\n"
            "spike=2.8x scan=85 profit_prob=0.92 fakeout=0.15 conviction=94%\n"
            "Source: commander IB lessons"
        ),
        "expected_action": "ENTER",
        "expect_enter": True,
    },
]

_ENTER_RE = re.compile(r"\b(ENTER|BUY|LONG)\b", re.I)
_EXIT_RE = re.compile(r"\b(EXIT|CLOSE|SELL|BAIL|CUT)\b", re.I)
_SKIP_RE = re.compile(r"\b(SKIP|PASS|WAIT|STAND_ASIDE|STAND\s+ASIDE)\b", re.I)
_HOLD_RE = re.compile(r"\bHOLD\b", re.I)
_CONF_PCT_RE = re.compile(r"(\d{1,3})\s*%\s*(?:CONFIDENCE|SCORE|CERTAINTY)?", re.I)
_CONF_FLOAT_RE = re.compile(r"confidence\s*[=:]\s*(0?\.\d+|1\.0|1)", re.I)


def _json_prompt(context: str) -> str:
    return (
        "You are M. A. Halim. Reply JSON only.\n"
        '{"enter":true|false,"confidence":0.0-1.0,"reason":"max 10 words"}\n'
        f"{context}"
    )


def _token_prompt(context: str) -> str:
    return f"{_TOKEN_PREFIX}{context}"


def _parse_json_blob(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {}


def parse_toddler_response(raw_text: str) -> Dict[str, Any]:
    """Scrape action intent + confidence from decision_text (production boundary)."""
    text = (raw_text or "").strip()
    upper = text.upper()

    action = "UNKNOWN"
    if _ENTER_RE.search(upper):
        action = "ENTER"
    elif _EXIT_RE.search(upper):
        action = "EXIT"
    elif _SKIP_RE.search(upper):
        action = "SKIP"
    elif _HOLD_RE.search(upper) and "HOPE" in upper:
        action = "EXIT"

    confidence_pct: Optional[int] = None
    m = _CONF_PCT_RE.search(text)
    if m:
        confidence_pct = min(100, int(m.group(1)))
    else:
        m2 = _CONF_FLOAT_RE.search(text)
        if m2:
            confidence_pct = int(round(float(m2.group(1)) * 100))

    json_blob = _parse_json_blob(text)
    if action == "UNKNOWN" and json_blob:
        if "enter" in json_blob:
            action = "ENTER" if bool(json_blob.get("enter")) else "SKIP"
        elif "exit" in json_blob:
            action = "EXIT" if bool(json_blob.get("exit")) else "SKIP"
        if confidence_pct is None and json_blob.get("confidence") is not None:
            try:
                c = float(json_blob["confidence"])
                confidence_pct = int(round(c * 100 if c <= 1 else c))
            except (TypeError, ValueError):
                pass

    return {
        "predicted_action": action,
        "confidence_pct": confidence_pct,
        "used_json": bool(json_blob),
        "token_fallback": action != "UNKNOWN" and not json_blob,
    }


def _score_json(probe: Dict[str, Any], text: str) -> Tuple[bool, str]:
    blob = _parse_json_blob(text)
    if probe["purpose"] == "entry_decision":
        expect = probe.get("expect_enter")
        if blob and "enter" in blob:
            enter = blob["enter"]
            if isinstance(enter, str):
                enter = enter.lower() in ("true", "yes", "1")
            return bool(enter) == expect, f"json enter={enter}"
        return False, "no_json"
    if probe["purpose"] == "exit_decision":
        expect = probe.get("expect_exit", True)
        if blob and "exit" in blob:
            exit_ = blob["exit"]
            if isinstance(exit_, str):
                exit_ = exit_.lower() in ("true", "yes", "1")
            return bool(exit_) == expect, f"json exit={exit_}"
        return False, "no_json"
    return False, "unknown_probe"


def _score_token(probe: Dict[str, Any], text: str) -> Tuple[bool, str, Dict[str, Any]]:
    parsed = parse_toddler_response(text)
    expected = probe["expected_action"]
    got = parsed["predicted_action"]
    ok = got == expected
    how = "token_match" if ok else f"token_got={got}"
    return ok, how, parsed


def _complete(probe: Dict[str, Any], prompt: str, *, use_server: bool) -> Tuple[str, bool, str]:
    if use_server:
        import urllib.request

        body = json.dumps({
            "prompt": prompt,
            "purpose": probe["purpose"],
            "max_tokens": 96,
            "temperature": 0.2,
        }).encode()
        url = os.getenv("HALIM_SERVER_URL", "http://127.0.0.1:8765") + "/v1/complete"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read().decode())
        return str(out.get("text", "")), bool(out.get("ok")), str(out.get("reason", ""))
    out = complete_reasoning(prompt, purpose=probe["purpose"])
    return str(out.get("text", "")), bool(out.get("ok")), str(out.get("reason", ""))


def run_eval(*, use_server: bool = False, style: str = "both") -> Dict[str, Any]:
    os.environ.setdefault("HALIM_REPO_ROOT", str(ROOT))
    ckpt = checkpoint_path()
    results: List[Dict[str, Any]] = []
    json_passed = 0
    token_passed = 0

    for probe in PROBES:
        row: Dict[str, Any] = {"id": probe["id"], "expected_action": probe["expected_action"]}

        if style in ("json", "both"):
            t0 = time.time()
            text, ok, reason = _complete(probe, _json_prompt(probe["context"]), use_server=use_server)
            jpass, jhow = _score_json(probe, text)
            json_passed += int(jpass)
            row["json"] = {
                "pass": jpass,
                "how": jhow,
                "ok": ok,
                "latency_s": round(time.time() - t0, 1),
                "text": text[:240],
                "infer_reason": reason,
            }

        if style in ("token", "both"):
            t0 = time.time()
            text, ok, reason = _complete(probe, _token_prompt(probe["context"]), use_server=use_server)
            tpass, thow, parsed = _score_token(probe, text)
            token_passed += int(tpass)
            row["token"] = {
                "pass": tpass,
                "how": thow,
                "parsed": parsed,
                "ok": ok,
                "latency_s": round(time.time() - t0, 1),
                "text": text[:240],
                "infer_reason": reason,
            }

        results.append(row)

    cfg: Dict[str, Any] = {}
    if ckpt and (ckpt / "config.json").is_file():
        try:
            cfg = json.loads((ckpt / "config.json").read_text())
        except Exception:
            pass

    report: Dict[str, Any] = {
        "checkpoint": str(ckpt) if ckpt else None,
        "build_id": cfg.get("build_id"),
        "trained_at": cfg.get("trained_at") or cfg.get("registered_at"),
        "sft_mode": cfg.get("sft_mode"),
        "train_pairs": cfg.get("train_pairs"),
        "style": style,
        "probes": results,
        "engine": collect_status(),
    }

    if style in ("json", "both"):
        report["json_score"] = f"{json_passed}/{len(PROBES)}"
        report["json_passed"] = json_passed
    if style in ("token", "both"):
        report["token_score"] = f"{token_passed}/{len(PROBES)}"
        report["token_passed"] = token_passed
        report["primary_score"] = report["token_score"]
    else:
        report["primary_score"] = report.get("json_score")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval Halim toddler commander probes")
    parser.add_argument("--server", action="store_true", help="Use running halim serve :8765")
    parser.add_argument(
        "--style",
        choices=("json", "token", "both"),
        default="both",
        help="Score JSON strictness, production tokens, or both (default: both)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-probe human summary")
    args = parser.parse_args()
    report = run_eval(use_server=args.server, style=args.style)

    if args.verbose:
        for row in report["probes"]:
            print(f"--- {row['id']} (expect {row['expected_action']}) ---")
            if "token" in row:
                t = row["token"]
                p = t.get("parsed", {})
                mark = "PASS" if t.get("pass") else "FAIL"
                print(f"  token: {mark} | action={p.get('predicted_action')} conf={p.get('confidence_pct')}")
                print(f"  snippet: {t.get('text', '')[:100]}")
            if "json" in row:
                j = row["json"]
                mark = "PASS" if j.get("pass") else "FAIL"
                print(f"  json:  {mark} | {j.get('how')}")
        print()

    print(json.dumps(report, indent=2))

    primary = report.get("token_passed") if args.style != "json" else report.get("json_passed", 0)
    threshold = 3 if args.style == "json" else 3
    return 0 if (primary or 0) >= threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
