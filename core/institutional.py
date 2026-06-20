#!/usr/bin/env python3
"""
core/institutional.py — Institutional footprint detection engine.

Detects when institutions are accumulating/distributing a stock by
analyzing tick-level tape data: block trades, volume clusters,
bid/ask imbalances, large-print detection, and cumulative delta.

This is the "smell the money" layer — it sits ABOVE the PPO agent
and can override its decision to BUY/SELL when institutional activity
is detected or when the tape shows dangerous patterns.

Key concepts:
- BLOCK TRADE: A single print > 2x the average trade size
- VOLUME CLUSTER: 3+ consecutive bars with volume > 1.5x avg
- BID/ASK IMBALANCE: When one side of the book is significantly heavier
- CUMULATIVE DELTA: Running sum of (trades at ask - trades at bid)
- LARGE PRINT RATIO: % of total volume coming from > 2x avg trades
- TICK VELOCITY: Rate of price changes per second (acceleration)
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass, field


@dataclass
class InstitutionalSignal:
    """Output of one scan cycle — strength and direction of institutional activity."""
    detected: bool = False
    direction: str = "neutral"  # "accumulating", "distributing", "neutral"
    strength: float = 0.0       # 0.0 to 1.0
    confidence: float = 0.0     # 0.0 to 1.0
    
    # Component signals
    block_trade_detected: bool = False
    volume_cluster_detected: bool = False
    bid_ask_imbalance: float = 0.0   # positive = bullish, negative = bearish
    cumulative_delta_z: float = 0.0  # z-score of cum delta
    large_print_ratio: float = 0.0
    tick_velocity: float = 0.0
    relative_volume: float = 0.0     # current vol / avg vol
    
    # Alert text for Telegram
    alert: str = ""


class InstitutionalDetector:
    """
    Detects institutional activity from tick-level and bar data.
    Runs on every new bar (1-min) during live trading.
    """
    
    def __init__(self):
        # Tick buffers for tape reading
        self._tick_prices: List[float] = []
        self._tick_sizes: List[float] = []
        self._tick_sides: List[str] = []     # "buy", "sell", "unknown"
        self._tick_timestamps: List[float] = []
        
        # Bar-level buffers
        self._volume_buffer: List[float] = []
        self._close_buffer: List[float] = []
        
        # Running state
        self._cumulative_delta: float = 0.0
        self._cumulative_delta_history: List[float] = []
        self._avg_trade_size: float = 0.0
        self._large_trade_count: int = 0
        self._total_trade_count: int = 0
        
        # Configuration
        self.BLOCK_TRADE_MULTIPLIER = 2.5      # >2.5x avg trade size = block trade
        self.VOLUME_CLUSTER_MULTIPLIER = 1.5   # >1.5x avg vol = cluster bar
        self.VOLUME_CLUSTER_BARS = 3           # 3+ consecutive bars
        self.LARGE_PRINT_THRESHOLD = 2.0       # >2x avg trade size = large print
        self.TICK_BUFFER_SIZE = 1000           # keep last 1000 ticks
    
    def feed_tick(self, price: float, size: float, side: str = "unknown", timestamp: Optional[float] = None):
        """
        Feed a single tick into the detector.
        side: "buy" (at ask), "sell" (at bid), "unknown"
        """
        ts = timestamp or pd.Timestamp.utcnow().timestamp()
        
        self._tick_prices.append(price)
        self._tick_sizes.append(size)
        self._tick_sides.append(side)
        self._tick_timestamps.append(ts)
        
        # Trim buffer
        if len(self._tick_prices) > self.TICK_BUFFER_SIZE:
            self._tick_prices = self._tick_prices[-self.TICK_BUFFER_SIZE:]
            self._tick_sizes = self._tick_sizes[-self.TICK_BUFFER_SIZE:]
            self._tick_sizes = self._tick_sizes[-self.TICK_BUFFER_SIZE:]
            self._tick_timestamps = self._tick_timestamps[-self.TICK_BUFFER_SIZE:]
        
        # Update cumulative delta
        if side == "buy":
            self._cumulative_delta += size
        elif side == "sell":
            self._cumulative_delta -= size
        
        # Update average trade size (running)
        self._total_trade_count += 1
        n = self._total_trade_count
        if n == 1:
            self._avg_trade_size = size
        else:
            self._avg_trade_size = self._avg_trade_size * ((n - 1) / n) + size / n
        
        # Count large prints
        if size > self._avg_trade_size * self.LARGE_PRINT_THRESHOLD and self._avg_trade_size > 0:
            self._large_trade_count += 1
    
    def feed_bar(self, volume: float, close: float):
        """Feed completed bar data."""
        self._volume_buffer.append(volume)
        self._close_buffer.append(close)
        self._cumulative_delta_history.append(self._cumulative_delta)
        
        # Keep last 100 bars
        if len(self._volume_buffer) > 100:
            self._volume_buffer = self._volume_buffer[-100:]
            self._close_buffer = self._close_buffer[-100:]
            self._cumulative_delta_history = self._cumulative_delta_history[-100:]
    
    def scan(self) -> InstitutionalSignal:
        """
        Run full institutional detection scan on current state.
        Returns an InstitutionalSignal with all component scores.
        """
        signal = InstitutionalSignal()
        
        # If not enough data, return neutral
        if len(self._volume_buffer) < 20 or len(self._tick_prices) < 10:
            return signal
        
        # 1. Relative Volume
        recent_vol = self._volume_buffer[-1] if self._volume_buffer else 0
        avg_vol = np.mean(self._volume_buffer[-21:-1]) if len(self._volume_buffer) >= 21 else np.mean(self._volume_buffer[:-1]) if len(self._volume_buffer) > 1 else recent_vol
        signal.relative_volume = float(recent_vol / (avg_vol + 1e-9))
        
        # 2. Volume Cluster Detection
        if len(self._volume_buffer) >= self.VOLUME_CLUSTER_BARS + 1:
            recent_bars = self._volume_buffer[-self.VOLUME_CLUSTER_BARS:]
            vol_avg = np.mean(self._volume_buffer[-(self.VOLUME_CLUSTER_BARS + 10):-self.VOLUME_CLUSTER_BARS]) if len(self._volume_buffer) >= self.VOLUME_CLUSTER_BARS + 10 else avg_vol
            cluster_count = sum(1 for v in recent_bars if v > vol_avg * self.VOLUME_CLUSTER_MULTIPLIER)
            signal.volume_cluster_detected = cluster_count >= 2
        
        # 3. Block Trade Detection
        if self._avg_trade_size > 0 and self._tick_sizes:
            recent_sizes = self._tick_sizes[-50:]
            block_count = sum(1 for s in recent_sizes if s > self._avg_trade_size * self.BLOCK_TRADE_MULTIPLIER)
            signal.block_trade_detected = block_count >= 2
        
        # 4. Large Print Ratio
        if self._total_trade_count > 0:
            recent_large = sum(1 for s in self._tick_sizes[-100:] if s > self._avg_trade_size * self.LARGE_PRINT_THRESHOLD)
            recent_total = min(len(self._tick_sizes[-100:]), 100)
            signal.large_print_ratio = float(recent_large / (recent_total + 1e-9))
        
        # 5. Cumulative Delta Z-Score
        if len(self._cumulative_delta_history) >= 20:
            cd_values = self._cumulative_delta_history[-20:]
            cd_mean = np.mean(cd_values)
            cd_std = np.std(cd_values) + 1e-9
            signal.cumulative_delta_z = float((self._cumulative_delta - cd_mean) / cd_std)
        
        # 6. Tick Velocity (price acceleration)
        if len(self._tick_prices) >= 10 and len(self._tick_timestamps) >= 10:
            recent_prices = self._tick_prices[-10:]
            recent_times = self._tick_timestamps[-10:]
            time_span = recent_times[-1] - recent_times[0]
            if time_span > 0:
                price_change = (recent_prices[-1] - recent_prices[0]) / (recent_prices[0] + 1e-9)
                signal.tick_velocity = float(price_change / (time_span + 1e-9))
        
        # 7. Bid/Ask Imbalance (from tick sides)
        if len(self._tick_sides) >= 50:
            recent_sides = self._tick_sides[-50:]
            buys = sum(1 for s in recent_sides if s == "buy")
            sells = sum(1 for s in recent_sides if s == "sell")
            total_known = buys + sells
            if total_known > 0:
                signal.bid_ask_imbalance = float((buys - sells) / total_known)
        
        # ── Aggregate signal ─────────────────────────────────────────────
        score = 0.0
        confidence = 0.0
        direction = "neutral"
        
        # Volume cluster (+)
        if signal.volume_cluster_detected:
            score += 0.3
            confidence += 0.2
        
        # Block trades (+ if on bid side or neutral)
        if signal.block_trade_detected:
            score += 0.2
            confidence += 0.15
        
        # Cumulative delta (bullish if positive z-score)
        if signal.cumulative_delta_z > 1.0:
            score += 0.2 * min(signal.cumulative_delta_z / 3.0, 1.0)
            confidence += 0.15
        elif signal.cumulative_delta_z < -1.0:
            score -= 0.2 * min(abs(signal.cumulative_delta_z) / 3.0, 1.0)
            confidence += 0.15
        
        # Tick velocity
        if signal.tick_velocity > 0.001:
            score += min(signal.tick_velocity * 10, 0.2)
            confidence += 0.1
        elif signal.tick_velocity < -0.001:
            score -= min(abs(signal.tick_velocity) * 10, 0.2)
            confidence += 0.1
        
        # Large print ratio
        if signal.large_print_ratio > 0.3:
            score += 0.15
            confidence += 0.1
        
        # Relative volume
        if signal.relative_volume > 1.5:
            score += 0.1
            confidence += 0.1
        
        # Bid/ask imbalance
        if abs(signal.bid_ask_imbalance) > 0.2:
            score += signal.bid_ask_imbalance * 0.3
            confidence += 0.1
        
        # Normalize and set
        signal.strength = float(np.clip(abs(score), 0.0, 1.0))
        signal.confidence = float(np.clip(confidence, 0.0, 1.0))
        signal.detected = signal.strength > 0.4 and signal.confidence > 0.3
        
        if score > 0.3:
            direction = "accumulating"
        elif score < -0.3:
            direction = "distributing"
        
        signal.direction = direction
        
        # Build alert text
        if signal.detected:
            parts = []
            if signal.block_trade_detected:
                parts.append("Block trades detected")
            if signal.volume_cluster_detected:
                parts.append(f"Volume cluster ({signal.relative_volume:.1f}x avg)")
            if signal.large_print_ratio > 0.3:
                parts.append(f"Large prints: {signal.large_print_ratio:.0%} of volume")
            if abs(signal.cumulative_delta_z) > 1.5:
                delta_dir = "bullish" if signal.cumulative_delta_z > 0 else "bearish"
                parts.append(f"Cum delta {delta_dir} (z={signal.cumulative_delta_z:.1f})")
            if abs(signal.bid_ask_imbalance) > 0.3:
                imb_dir = "buying" if signal.bid_ask_imbalance > 0 else "selling"
                parts.append(f"Bid/ask imbalance: {imb_dir}")
            
            dir_emoji = "🟢" if direction == "accumulating" else "🔴" if direction == "distributing" else "⚪"
            signal.alert = f"{dir_emoji} Institutional {direction.upper()}\n" + "\n".join(parts)
        
        return signal
    
    def get_scalp_confidence(self) -> float:
        """
        Returns a scalping confidence score 0.0-1.0.
        Used by the PPO agent to bias its decision toward aggressive entries
        when the tape is hot.
        """
        signal = self.scan()
        if not signal.detected or signal.direction == "distributing":
            return 0.0
        return signal.strength * signal.confidence
    
    def should_override_buy(self) -> Tuple[bool, str]:
        """Returns (override_to_hold, reason) if tape is dangerous."""
        signal = self.scan()
        if signal.direction == "distributing" and signal.strength > 0.6:
            return True, f"Institutional distribution detected (strength={signal.strength:.2f})"
        if signal.cumulative_delta_z < -2.0:
            return True, f"Heavy selling pressure (cum delta z={signal.cumulative_delta_z:.1f})"
        return False, ""


def compute_vwap_bands(df: pd.DataFrame, width: float = 2.0) -> Tuple[float, float, float]:
    """
    Compute VWAP and standard deviation bands from an OHLCV dataframe.
    Returns (vwap, upper_band, lower_band).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df["volume"].cumsum()
    cum_tpv = (typical * df["volume"]).cumsum()
    vwap = cum_tpv / (cum_vol + 1e-9)
    
    # VWAP standard deviation
    vwap_dev = (typical - vwap) ** 2
    cum_vwap_dev = (vwap_dev * df["volume"]).cumsum()
    vwap_std = np.sqrt(cum_vwap_dev / (cum_vol + 1e-9))
    
    vwap_val = float(vwap.iloc[-1])
    upper = float(vwap_val + width * vwap_std.iloc[-1])
    lower = float(vwap_val - width * vwap_std.iloc[-1])
    
    return vwap_val, upper, lower


def compute_gap_percentage(prev_close: float, open_price: float) -> float:
    """Computes gap up/down as a percentage."""
    if prev_close <= 0:
        return 0.0
    return (open_price - prev_close) / prev_close * 100.0


def compute_relative_volume(current_vol: float, vol_history: List[float], period: int = 65) -> float:
    """Compute current volume relative to average of last N periods."""
    if len(vol_history) < period:
        return 1.0
    avg_vol = np.mean(vol_history[-period:])
    if avg_vol <= 0:
        return 1.0
    return float(current_vol / avg_vol)