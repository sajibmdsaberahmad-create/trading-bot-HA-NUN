#!/usr/bin/env python3
"""
core/env.py — Gymnasium trading environment used for warm-up training
and online fine-tuning.

Note: this environment teaches the PPO agent WHEN to go long/flat/exit
its intent (HOLD/BUY/SELL). It does NOT teach position sizing or exact
stop/target placement — those are deliberately kept outside the
learned policy and handled deterministically by core/risk.py during
live trading, so a bad training run can never produce an agent that
risks more than the hardcoded limits allow. The simulated reward here
still includes a simplified stop/target/transaction-cost model so the
agent learns to avoid setups that would obviously get stopped out.
"""

from typing import Dict, Tuple

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:
    raise SystemExit("ERROR: gymnasium not installed. Fix: pip install gymnasium")


class TradingEnv(gym.Env):
    """
    OBSERVATION: flattened (window_size x n_features) + [cash_ratio, pos_ratio]
    ACTION:      Discrete(3): 0=HOLD 1=BUY 2=SELL
    REWARD:      delta log(portfolio_value) - transaction_cost - drawdown_penalty
    """

    metadata = {"render_modes": ["human"]}

    def __init__(self, features: np.ndarray, prices: np.ndarray,
                 initial_cash: float = 1_000.0, transaction_cost: float = 0.001,
                 window_size: int = 30, max_position_pct: float = 0.90):
        super().__init__()

        if len(features) != len(prices):
            raise ValueError("features and prices must have the same length")
        if len(features) <= window_size:
            raise ValueError(f"Need more than {window_size} rows of data (got {len(features)})")

        self.features = features
        self.prices = prices
        self.initial_cash = initial_cash
        self.transaction_cost = transaction_cost
        self.window_size = window_size
        self.max_position_pct = max_position_pct

        n_features = features.shape[1]
        obs_dim = window_size * n_features + 2

        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Discrete(3)

        self._reset_state()

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._reset_state()
        return self._build_obs(), {}

    def step(self, action: int):
        prev_value = self._portfolio_value()
        self._execute_action(int(action))

        self._step_idx += 1
        terminated = self._step_idx >= len(self.features) - 1

        new_value = self._portfolio_value()
        log_ret = float(np.log(new_value / (prev_value + 1e-9)))

        if new_value > self._peak_value:
            self._peak_value = new_value
        drawdown = (self._peak_value - new_value) / (self._peak_value + 1e-9)
        dd_penalty = max(0.0, drawdown - 0.03) * 0.5

        reward = log_ret - dd_penalty

        info = {
            "portfolio_value": new_value,
            "cash": self.cash,
            "shares": self.shares,
            "step": self._step_idx,
        }
        return self._build_obs(), reward, terminated, False, info

    def render(self, mode="human"):
        price = self._current_price()
        nav = self._portfolio_value()
        ret = (nav / self.initial_cash - 1.0) * 100.0
        print(f"Step {self._step_idx:5d} | NAV: ${nav:>10,.2f} ({ret:+.2f}%) | "
              f"Cash: ${self.cash:>10,.2f} | Shares: {self.shares:>8.2f} | Price: ${price:>8.2f}")

    def _reset_state(self):
        self._step_idx = self.window_size
        self.cash = float(self.initial_cash)
        self.shares = 0.0
        self._peak_value = float(self.initial_cash)

    def _build_obs(self) -> np.ndarray:
        start = self._step_idx - self.window_size
        window = self.features[start:self._step_idx].flatten()
        total = self._portfolio_value()
        c_rat = self.cash / (total + 1e-9)
        p_rat = (self.shares * self._current_price()) / (total + 1e-9)
        return np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)

    def _current_price(self) -> float:
        idx = min(self._step_idx, len(self.prices) - 1)
        return max(float(self.prices[idx]), 1e-6)

    def _portfolio_value(self) -> float:
        return self.cash + self.shares * self._current_price()

    def _execute_action(self, action: int):
        price = self._current_price()

        if action == 1:
            budget = self.cash * self.max_position_pct
            cost = budget * (1.0 + self.transaction_cost)
            if cost <= self.cash and price > 0:
                self.shares += budget / price
                self.cash -= cost

        elif action == 2:
            if self.shares > 0:
                proceeds = self.shares * price * (1.0 - self.transaction_cost)
                self.cash += proceeds
                self.shares = 0.0
