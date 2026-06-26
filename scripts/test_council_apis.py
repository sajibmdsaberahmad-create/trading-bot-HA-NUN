#!/usr/bin/env python3
"""
scripts/test_council_apis.py — Verify Groq + Gemini keys and pick working models.

Usage:
  python3 scripts/test_council_apis.py
  python3 scripts/test_council_apis.py --quick
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.config import BotConfig
from core.groq_pool import parse_groq_keys

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

PROMPT = (
    'Reply ONLY JSON: {"enter": false, "confidence": 0.55, '
    '"reason": "api_test", "gut_feel": 0.5}'
)


def _test_groq(key: str, model: str) -> dict:
    """Test Groq via official SDK (urllib gets Cloudflare 403 on some networks)."""
    t0 = time.time()
    try:
        from groq import Groq

        client = Groq(api_key=key)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "HANOON council — JSON only."},
                {"role": "user", "content": PROMPT},
            ],
            temperature=0.3,
            max_completion_tokens=80,
            timeout=20.0,
        )
        ms = (time.time() - t0) * 1000
        text = (completion.choices[0].message.content or "").strip()
        if text:
            return {"ok": True, "ms": round(ms), "sample": text[:120], "error": ""}
        return {"ok": False, "ms": round(ms), "sample": "", "error": "empty_response"}
    except ImportError:
        return {"ok": False, "ms": 0, "sample": "", "error": "pip install groq"}
    except Exception as exc:
        return {"ok": False, "ms": 0, "sample": "", "error": str(exc)[:200]}


def _test_gemini(key: str, model: str) -> dict:
    url = f"{GEMINI_BASE}/{model}:generateContent?key={key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 80},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode())
        ms = (time.time() - t0) * 1000
        parts = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = " ".join(p.get("text", "") for p in parts).strip()
        return {"ok": bool(text), "ms": round(ms), "sample": text[:120], "error": ""}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return {"ok": False, "ms": 0, "sample": "", "error": f"HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"ok": False, "ms": 0, "sample": "", "error": str(exc)[:200]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Test default models only")
    args = parser.parse_args()

    groq_keys = parse_groq_keys(BotConfig())
    gem_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

    print("=" * 60)
    print("HANOON Council API Test")
    print("=" * 60)

    groq_models = GROQ_MODELS[:1] if args.quick else GROQ_MODELS
    gem_models = GEMINI_MODELS[:1] if args.quick else GEMINI_MODELS

    best_groq = None
    if groq_keys:
        print(f"\nGroq ({len(groq_keys)} key(s)):")
        for i, groq_key in enumerate(groq_keys, 1):
            suffix = groq_key[-4:] if len(groq_key) > 4 else "?"
            print(f"  Key {i} [...{suffix}]:")
            for model in groq_models:
                r = _test_groq(groq_key, model)
                status = "OK" if r["ok"] else "FAIL"
                print(f"    [{status}] {model} — {r['ms']}ms")
                if r["ok"]:
                    print(f"         {r['sample'][:80]}")
                    if best_groq is None:
                        best_groq = (model, r["ms"])
                else:
                    print(f"         {r['error']}")
    else:
        print("\nGroq: SKIP (no GROQ_API_KEY)")

    best_gem = None
    if gem_key:
        print("\nGemini:")
        for model in gem_models:
            r = _test_gemini(gem_key, model)
            status = "OK" if r["ok"] else "FAIL"
            print(f"  [{status}] {model} — {r['ms']}ms")
            if r["ok"]:
                print(f"       {r['sample'][:80]}")
                if best_gem is None:
                    best_gem = (model, r["ms"])
            else:
                print(f"       {r['error']}")
    else:
        print("\nGemini: SKIP (no GEMINI_API_KEY)")

    print("\n" + "=" * 60)
    print("Recommendation for .env:")
    if best_groq:
        print(f"  GROQ_MODEL={best_groq[0]}  # {best_groq[1]}ms")
    else:
        print("  GROQ_MODEL=  # fix Groq key or pick working model above")
    if best_gem:
        print(f"  GEMINI_MODEL={best_gem[0]}")
        print(f"  GEMINI_VISION_MODEL={best_gem[0]}")
    else:
        print("  GEMINI_API_KEY=  # use AI Studio key (AIza...) if AQ key fails")

    if not best_groq and not best_gem:
        print("\nNo working API — bot will run PPO-only until keys fixed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
