#!/usr/bin/env python3
"""
core/device_optimizer.py — Maximize the device's calculative ability.

This module ensures the AI can use every CPU core, all available RAM,
and run parallel inference/training/optimization without bottlenecks.
"""

import os
import sys
import json
import time
import math
import multiprocessing
import logging
import threading
from typing import Optional, Dict, List, Callable, Any, Tuple
from collections import deque
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("DEVICE_OPTIMIZER")


@dataclass
class DeviceProfile:
    """Current device capabilities."""
    cpu_cores: int = 1
    cpu_threads: int = 1
    total_ram_mb: int = 512
    available_ram_mb: int = 512
    gpu_available: bool = False
    gpu_name: str = ""
    gpu_memory_mb: int = 0
    storage_free_mb: int = 0
    platform: str = ""
    python_version: str = ""

    def to_dict(self) -> Dict:
        return {
            "cpu_cores": self.cpu_cores,
            "cpu_threads": self.cpu_threads,
            "total_ram_mb": self.total_ram_mb,
            "available_ram_mb": self.available_ram_mb,
            "gpu_available": self.gpu_available,
            "gpu_name": self.gpu_name,
            "gpu_memory_mb": self.gpu_memory_mb,
            "storage_free_mb": self.storage_free_mb,
            "platform": self.platform,
        }


class DeviceOptimizer:
    """
    Monitor and optimize for maximum device utilization.
    
    The AI can query this module to:
    - Get optimal thread counts for parallel work
    - Check available RAM before loading large models
    - Schedule heavy work when resources are free
    - Use all CPU cores for scanning/backtesting
    """

    def __init__(self):
        self.profile = self._detect_device()
        self._optimal_threads = self._calculate_optimal_threads()
        self._resource_history = deque(maxlen=5000)
        self._lock = threading.Lock()
        self._monitoring = False
        self._monitor_thread = None

    def _detect_device(self) -> DeviceProfile:
        profile = DeviceProfile()
        profile.platform = sys.platform
        profile.python_version = sys.version.split()[0]

        # CPU
        try:
            profile.cpu_cores = multiprocessing.cpu_count()
            profile.cpu_threads = max(2, profile.cpu_cores)
        except Exception:
            profile.cpu_cores = 4
            profile.cpu_threads = 4

        # RAM
        try:
            import resource
            mem_kb = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
            profile.total_ram_mb = mem_kb // (1024 * 1024)
        except Exception:
            try:
                import psutil
                mem = psutil.virtual_memory()
                profile.total_ram_mb = mem.total // (1024 * 1024)
                profile.available_ram_mb = mem.available // (1024 * 1024)
            except Exception:
                profile.total_ram_mb = 8192
                profile.available_ram_mb = 4096

        # GPU
        try:
            import torch
            if torch.cuda.is_available():
                profile.gpu_available = True
                profile.gpu_name = torch.cuda.get_device_name(0)
                profile.gpu_memory_mb = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
        except ImportError:
            pass

        # Storage
        try:
            stat = os.statvfs('.')
            profile.storage_free_mb = (stat.f_bavail * stat.f_frsize) // (1024 * 1024)
        except Exception:
            profile.storage_free_mb = 10_000

        logger.info(f"Device: {profile.cpu_cores} cores, {profile.total_ram_mb}MB RAM, "
                    f"GPU={profile.gpu_available}, Free storage={profile.storage_free_mb}MB")
        return profile

    def _calculate_optimal_threads(self) -> int:
        return max(2, self.profile.cpu_cores - 1)

    def get_optimal_workers(self, task_type: str = "default") -> int:
        """Get optimal worker count for a task."""
        if task_type == "io_intensive":
            return self.profile.cpu_cores * 4
        elif task_type == "cpu_intensive":
            return self._optimal_threads
        elif task_type == "memory_heavy":
            return max(1, self._optimal_threads // 2)
        return self._optimal_threads

    def parallel_map(self, fn: Callable, items: List[Any],
                     task_type: str = "default") -> List[Any]:
        """Execute function on all items in parallel."""
        workers = self.get_optimal_workers(task_type)
        results = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fn, item): item for item in items}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.debug(f"Parallel task failed: {exc}")
                    results.append(None)
        return results

    def can_load_model(self, estimated_size_mb: float) -> bool:
        """Check if we have enough RAM for a model."""
        needed = estimated_size_mb * 2.5  # overhead for inference
        return self.profile.available_ram_mb >= needed

    def start_monitoring(self, interval_sec: float = 5.0):
        """Start background resource monitoring."""
        if self._monitoring:
            return
        try:
            import psutil
            self._monitoring = True
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, args=(interval_sec,), daemon=True
            )
            self._monitor_thread.start()
        except ImportError:
            logger.debug("psutil not available, skipping monitoring")

    def _monitor_loop(self, interval_sec: float):
        import psutil
        while self._monitoring:
            try:
                mem = psutil.virtual_memory()
                self.profile.available_ram_mb = mem.available // (1024 * 1024)
                with self._lock:
                    self._resource_history.append({
                        "ts": time.time(),
                        "avail_ram_mb": self.profile.available_ram_mb,
                        "cpu_pct": psutil.cpu_percent(interval=0.1),
                    })
            except Exception:
                pass
            time.sleep(interval_sec)

    def stop_monitoring(self):
        self._monitoring = False

    def get_status(self) -> Dict:
        return {
            "profile": self.profile.to_dict(),
            "optimal_threads": self._optimal_threads,
            "monitoring": self._monitoring,
            "history_samples": len(self._resource_history),
        }
