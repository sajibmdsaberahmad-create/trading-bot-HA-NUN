#!/usr/bin/env python3
"""Halim serve — active runtime server (status + reasoning + learn/write). Not inference-only."""

from __future__ import annotations

import sys
from pathlib import Path

_pkg = Path(__file__).resolve().parents[1]
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from halim.active_model import enforce_active_runtime, runtime_envelope
from halim.engine import collect_status, complete_reasoning
from halim.protocol import DEFAULT_HOST, DEFAULT_PORT, MODEL_NAME, PROTOCOL_VERSION


def _with_runtime(body: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(body)
    out["runtime"] = runtime_envelope()
    return out


def _record_action(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        root = os.getenv("HALIM_REPO_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from core.halim_action_learn import record_action
        record_action(
            str(body.get("capability", "reasoning")),
            str(body.get("action", body.get("purpose", "record"))),
            input_text=str(body.get("input", body.get("prompt", "")))[:8000],
            output_text=str(body.get("output", body.get("text", "")))[:8000],
            outcome=str(body.get("outcome", "ok")),
            source=str(body.get("source", "halim_server")),
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else None,
        )
        return {"ok": True, "recorded": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _export_gold() -> Dict[str, Any]:
    try:
        root = os.getenv("HALIM_REPO_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from core.halim_action_learn import export_action_gold
        return {"ok": True, **export_action_gold()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _trigger_evolve() -> Dict[str, Any]:
    try:
        root = os.getenv("HALIM_REPO_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from core.graceful_shutdown import flush_halim_data, flush_owned_brain
        from core.config import BotConfig
        cfg = BotConfig()
        halim = flush_halim_data(cfg, trigger="server_evolve")
        brain = flush_owned_brain(cfg, trigger="server_evolve", push_git=False)
        return {"ok": True, "halim": halim, "evolution": brain}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _unlock_ladder() -> Dict[str, Any]:
    try:
        root = os.getenv("HALIM_REPO_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from core.halim_unlock import unlock_ladder
        return unlock_ladder()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _halim_chat(message: str, *, context: str = "", purpose: str = "chat") -> Dict[str, Any]:
    try:
        root = os.getenv("HALIM_REPO_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from core.halim_chat import halim_chat
        return halim_chat(message, context=context, purpose=purpose)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _halim_generate(kind: str, prompt: str, *, path_hint: str = "") -> Dict[str, Any]:
    try:
        root = os.getenv("HALIM_REPO_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from core.halim_chat import halim_generate
        return halim_generate(kind, prompt, path_hint=path_hint)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


class HalimHandler(BaseHTTPRequestHandler):
    server_version = "HalimServe/1-active"

    def log_message(self, fmt: str, *args) -> None:
        if os.getenv("HALIM_SERVE_QUIET", "true").lower() not in ("1", "true", "yes"):
            super().log_message(fmt, *args)

    def _json(self, code: int, body: Dict[str, Any]) -> None:
        raw = json.dumps(_with_runtime(body), default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Halim-Runtime", "active")
        self.send_header("X-Halim-Inference-Only", "false")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True, "model": MODEL_NAME, "protocol": PROTOCOL_VERSION})
        elif self.path == "/v1/status":
            self._json(200, collect_status())
        elif self.path == "/v1/runtime":
            self._json(200, {"ok": True, "model": MODEL_NAME, **runtime_envelope()})
        elif self.path == "/v1/unlock":
            self._json(200, _unlock_ladder())
        elif self.path == "/v1/manifest":
            st = collect_status()
            self._json(200, {"manifest": st, "protocol": PROTOCOL_VERSION})
        else:
            self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._json(400, {"ok": False, "error": "invalid_json"})
            return

        if self.path == "/v1/complete":
            prompt = str(body.get("prompt", ""))
            purpose = str(body.get("purpose", "reasoning"))
            try:
                root = os.getenv("HALIM_REPO_ROOT", "")
                if root and root not in sys.path:
                    sys.path.insert(0, root)
                from core.trading_focus_guard import halim_lm_blocked_during_trading, trading_focus_message
                if halim_lm_blocked_during_trading(purpose):
                    self._json(503, {
                        "ok": False,
                        "reason": "trading_focus",
                        "message": trading_focus_message(via="cli"),
                        "purpose": purpose,
                    })
                    return
            except Exception:
                pass
            out = complete_reasoning(prompt, purpose=purpose)
            if out.get("ok") and out.get("text"):
                rec = _record_action({
                    "capability": purpose,
                    "action": "complete",
                    "input": prompt,
                    "output": out.get("text"),
                    "source": "halim_lm",
                })
                out["action_recorded"] = rec.get("recorded", False)
            code = 200 if out.get("ok") else 503
            self._json(code, out)
        elif self.path == "/v1/record":
            out = _record_action(body)
            self._json(200 if out.get("ok") else 500, out)
        elif self.path == "/v1/export":
            out = _export_gold()
            self._json(200 if out.get("ok") else 500, out)
        elif self.path == "/v1/evolve":
            out = _trigger_evolve()
            self._json(200 if out.get("ok") else 500, out)
        elif self.path == "/v1/chat":
            msg = str(body.get("message", body.get("prompt", "")))
            ctx = str(body.get("context", ""))
            purpose = str(body.get("purpose", "chat"))
            try:
                root = os.getenv("HALIM_REPO_ROOT", "")
                if root and root not in sys.path:
                    sys.path.insert(0, root)
                from core.trading_focus_guard import halim_lm_blocked_during_trading, trading_focus_message
                if halim_lm_blocked_during_trading(purpose):
                    self._json(503, {
                        "ok": False,
                        "reason": "trading_focus",
                        "text": trading_focus_message(via="cli"),
                        "purpose": purpose,
                    })
                    return
            except Exception:
                pass
            out = _halim_chat(msg, context=ctx, purpose=purpose)
            self._json(200 if out.get("ok") else 503, out)
        elif self.path == "/v1/generate":
            kind = str(body.get("kind", "code"))
            prompt = str(body.get("prompt", ""))
            out = _halim_generate(kind, prompt, path_hint=str(body.get("path", "")))
            self._json(200 if out.get("ok") else 503, out)
        else:
            self._json(404, {"ok": False, "error": "not_found"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{MODEL_NAME} active runtime server")
    parser.add_argument("--host", default=os.getenv("HALIM_SERVE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("HALIM_SERVE_PORT", DEFAULT_PORT)))
    args = parser.parse_args(argv)

    os.environ.setdefault("HALIM_REPO_ROOT", str(_repo_root()))
    ok, msg = enforce_active_runtime(context="halim serve")
    if not ok:
        print(f"⚠️  {msg}")

    httpd = ThreadingHTTPServer((args.host, args.port), HalimHandler)
    env = runtime_envelope()
    print(f"🧠 {MODEL_NAME} serve — http://{args.host}:{args.port}")
    print("   ACTIVE runtime — learns, records actions, writes owned weights (not Ollama read-only)")
    print("   Reflex (PPO/proxy) stays inline in HANOON")
    print("   GET  /health /v1/status /v1/runtime /v1/unlock")
    print("   POST /v1/complete /v1/record /v1/export /v1/evolve /v1/chat /v1/generate")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nHalim serve stopped.")
    return 0


def _repo_root() -> str:
    from pathlib import Path
    p = Path(__file__).resolve().parents[2]
    if (p / "models").is_dir():
        return str(p)
    return str(Path.cwd())


if __name__ == "__main__":
    sys.exit(main())
