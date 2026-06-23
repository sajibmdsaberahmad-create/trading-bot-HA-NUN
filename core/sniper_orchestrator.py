#!/usr/bin/env python3
"""
core/sniper_orchestrator.py — Async Orchestration Engine

Coordinates the two-phase Sniper-Lock architecture:
  1. Spawns Wide-Net Scout (screener loop) in background
  2. Spawns Strike Squad (heartbeat loop) in foreground
  3. Manages lifecycle, graceful shutdown, error recovery
"""

import asyncio
import logging
import signal
from typing import Optional
from datetime import datetime

from core.notify import log
from core.config import BotConfig
from core.sniper import initialize_sniper, get_sniper, save_sniper_state
from core.sniper_screener import run_screener, WidenetScout
from core.sniper_heartbeat import run_heartbeat, SniperHeartbeat
from core.connector import IBConnector
from core.broker import IBBroker


class SniperOrchestrator:
    """
    Manages the full async lifecycle of the Sniper-Lock architecture.
    
    Responsibilities:
      - Initialize sniper components
      - Spawn screener and heartbeat tasks
      - Handle graceful shutdown
      - Monitor task health
      - Save state on exit
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.sniper = None
        self.screener_task = None
        self.heartbeat_task = None
        self.stop_event = asyncio.Event()
        self.tasks_started = False
        
    async def initialize(self):
        """Initialize sniper system."""
        log.info("🎯 Initializing Sniper-Lock Architecture...")
        
        # Initialize target lock system
        self.sniper = initialize_sniper(
            max_targets=self.cfg.SNIPER_MAX_TARGETS,
            stale_timeout=self.cfg.SNIPER_STALE_TIMEOUT_SEC
        )
        
        log.info(
            f"✅ Sniper Initialized\n"
            f"   Max Targets: {self.cfg.SNIPER_MAX_TARGETS}\n"
            f"   Scout Interval: {self.cfg.SNIPER_SCREENER_INTERVAL_SEC}s\n"
            f"   Heartbeat: {self.cfg.SNIPER_HEARTBEAT_INTERVAL_MS}ms ({1000/self.cfg.SNIPER_HEARTBEAT_INTERVAL_MS:.0f} Hz)"
        )
    
    async def start_screener(self, ib_connector=None):
        """Start the Wide-Net Scout (screener loop)."""
        if not self.cfg.SNIPER_ENABLED:
            log.warning("🎯 Sniper disabled in config")
            return
        
        log.info("🔍 Starting Wide-Net Scout...")
        self.screener_task = asyncio.create_task(
            run_screener(
                cfg=self.cfg,
                scan_interval=self.cfg.SNIPER_SCREENER_INTERVAL_SEC,
                ib_connector=ib_connector
            ),
            name="sniper_screener"
        )
        
        log.info("🔍 Wide-Net Scout spawned (runs in background)")
    
    async def start_heartbeat(
        self,
        ib_connector: IBConnector,
        ib_broker: IBBroker,
        ai_model=None,
        features=None
    ):
        """Start the Strike Squad (heartbeat loop)."""
        if not self.cfg.SNIPER_ENABLED:
            return
        
        log.info("⚡ Starting Strike Squad Heartbeat...")
        self.heartbeat_task = asyncio.create_task(
            run_heartbeat(
                ib_connector=ib_connector,
                ib_broker=ib_broker,
                ai_model=ai_model,
                features=features,
                cfg=self.cfg,
                pulse_interval_ms=self.cfg.SNIPER_HEARTBEAT_INTERVAL_MS
            ),
            name="sniper_heartbeat"
        )
        
        log.info("⚡ Strike Squad Heartbeat spawned (ultra-low-latency)")
        self.tasks_started = True
    
    async def run_until_signal(self):
        """
        Run sniper loops until shutdown signal (Ctrl+C).
        
        Monitors both loops and keeps them alive.
        """
        if not self.tasks_started:
            log.warning("Tasks not started yet")
            return
        
        log.info("🎯 Sniper-Lock Architecture LIVE")
        log.info("   Press Ctrl+C to gracefully shutdown...")
        
        try:
            # Wait for stop event (set by signal handlers)
            await self.stop_event.wait()
        except KeyboardInterrupt:
            log.info("\n⏸️  Shutdown signal received")
            self.stop_event.set()
        
        await self.shutdown()
    
    async def shutdown(self):
        """Graceful shutdown of sniper system."""
        log.info("🛑 Initiating Sniper shutdown...")
        
        # Signal tasks to stop
        self.stop_event.set()
        
        # Cancel tasks
        if self.screener_task:
            self.screener_task.cancel()
            try:
                await self.screener_task
            except asyncio.CancelledError:
                pass
        
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Save state
        if self.cfg.SNIPER_SAVE_STATE:
            await save_sniper_state(self.cfg.SNIPER_STATE_PATH)
        
        # Print final stats
        if self.sniper:
            stats = self.sniper.get_stats()
            log.info(
                f"📊 Final Sniper Stats:\n"
                f"   Total Updates: {stats.get('total_updates', 0)}\n"
                f"   Total Cycles: {stats.get('total_cycles', 0)}\n"
                f"   Current Targets: {stats.get('current_targets', [])}"
            )
        
        log.info("✅ Sniper shutdown complete")
    
    def setup_signal_handlers(self):
        """Setup graceful shutdown on SIGINT/SIGTERM."""
        def signal_handler(sig, frame):
            log.info(f"Signal {sig} received, initiating graceful shutdown...")
            self.stop_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)


async def run_sniper_live(
    cfg: BotConfig,
    ib_connector: IBConnector,
    ib_broker: IBBroker,
    ai_model=None,
    features=None
) -> bool:
    """
    Main entry point for running Sniper-Lock architecture in live mode.
    
    Usage:
        success = await run_sniper_live(cfg, ib, broker, model, features)
    
    Returns:
        True if successful, False on error
    """
    orchestrator = SniperOrchestrator(cfg)
    
    try:
        # Initialize
        await orchestrator.initialize()
        orchestrator.setup_signal_handlers()
        
        # Start screener loop (background) - pass connector for real data
        await orchestrator.start_screener(ib_connector=ib_connector)
        
        # Start heartbeat loop (foreground)
        await orchestrator.start_heartbeat(
            ib_connector=ib_connector,
            ib_broker=ib_broker,
            ai_model=ai_model,
            features=features
        )
        
        # Run until signal
        await orchestrator.run_until_signal()
        
        return True
        
    except Exception as exc:
        log.error(f"Sniper orchestrator error: {exc}")
        await orchestrator.shutdown()
        return False


def run_sniper_sync(
    cfg: BotConfig,
    ib_connector: IBConnector,
    ib_broker: IBBroker,
    ai_model=None,
    features=None
) -> bool:
    """
    Synchronous wrapper to run sniper architecture.
    
    Usage:
        success = run_sniper_sync(cfg, ib, broker, model, features)
    """
    try:
        asyncio.run(
            run_sniper_live(
                cfg=cfg,
                ib_connector=ib_connector,
                ib_broker=ib_broker,
                ai_model=ai_model,
                features=features
            )
        )
        return True
    except Exception as exc:
        log.error(f"Sniper sync wrapper error: {exc}")
        return False
