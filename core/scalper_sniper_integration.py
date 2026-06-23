#!/usr/bin/env python3
import asyncio
from core.sniper import get_sniper, initialize_sniper

class SniperBridge:
    def __init__(self, cfg, scanner=None, worker=None):
        self.cfg = cfg
        self._scanner = scanner
        self._worker = worker
        self.sniper = None
        self.scout = None
        self._enabled = bool(getattr(cfg, "SNIPER_ENABLED", False))
        if not self._enabled: return
        try:
            initialize_sniper(max_targets=getattr(cfg, "SNIPER_MAX_TARGETS", 5), stale_timeout=getattr(cfg, "SNIPER_STALE_TIMEOUT_SEC", 3600))
            self.sniper = get_sniper()
            try:
                from core.sniper_screener import WidenetScout
                self.scout = WidenetScout(cfg=self.cfg, scanner=self._scanner)
            except Exception:
                self.scout = None
        except Exception:
            self._enabled = False

    @property
    def enabled(self):
        return self._enabled and self.sniper is not None and self.scout is not None

    def run_scan(self):
        if not self.enabled or self.scout is None:
            return None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(asyncio.wait_for(self.scout.scan_market(), timeout=10))
            except asyncio.TimeoutError:
                return None
            finally:
                loop.close()
                asyncio.set_event_loop(None)
        except Exception:
            return None
