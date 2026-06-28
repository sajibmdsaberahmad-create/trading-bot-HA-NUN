#!/usr/bin/env python3
"""
core/ppo_reward_trainer.py — Reward-linked PPO training from experience buffer.

Builds short labeled episodes from closed trades, PPO entry evals, and teacher
labels — each step uses recorded rewards (PnL, council, teacher) instead of a
fake tiled feature block. Runs off-hours / coordinator batches only by default.
"""

from __future__ import annotations

import os
import random
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.config import BotConfig
from core.notify import log

try:
    import gymnasium as gym
except ImportError:
    gym = None  # type: ignore

from core.env import TradingEnv

REWARD_SOURCES = frozenset({
    "live_trade", "replay_live", "replay_sim", "shadow_trade",
    "ppo_entry", "ppo_entry_eval", "teacher_ppo",
    "deferred_council", "halim_ppo_coevolution", "live_entry",
    "backtest", "scan_pick", "ppo_led", "commander_ib_gold",
})

_deferred_lock = threading.Lock()
_deferred_records: List[Dict[str, Any]] = []


def _feat_vector(raw: Any, cfg: BotConfig) -> Optional[np.ndarray]:
    if raw is None:
        return None
    n_feat = int(getattr(cfg, "N_FEATURES", 18))
    try:
        arr = np.array(raw, dtype=np.float32).flatten()
    except Exception:
        return None
    if arr.size == n_feat:
        return arr
    if arr.size >= n_feat:
        return arr[-n_feat:]
    return None


def _commander_teacher_features(rec: Dict[str, Any], cfg: BotConfig) -> np.ndarray:
    """Synthetic obs for commander_ib_gold teacher rows (no live bar features)."""
    n = int(getattr(cfg, "N_FEATURES", 18))
    v = np.zeros(n, dtype=np.float32)
    v[0] = float(rec.get("confidence", 0.5) or 0.5)
    try:
        v[1] = float(int(rec.get("teacher_action", 0))) / 2.0
    except (TypeError, ValueError):
        v[1] = 0.0
    v[2] = 1.0 if rec.get("win") else 0.0
    v[3] = float(rec.get("teacher_reward", rec.get("reward", 0)) or 0)
    label = str(rec.get("outcome_label", ""))
    v[4] = 1.0 if label == "held_too_long" else 0.0
    v[5] = 1.0 if label == "calculated_lottery_win" else 0.0
    v[6] = 1.0 if label == "traded_without_edge" else 0.0
    v[7] = 1.0 if rec.get("ticker_not_banned") else 0.0
    return v


def defer_reward_records(records: Sequence[Dict[str, Any]]) -> None:
    """Queue records for off-hours reward-linked training (hot path — no PPO learn)."""
    if not records:
        return
    cap = int(os.getenv("PPO_REWARD_DEFER_MAX", "200"))
    with _deferred_lock:
        _deferred_records.extend(records)
        if len(_deferred_records) > cap:
            del _deferred_records[:-cap]


def drain_deferred_records() -> List[Dict[str, Any]]:
    with _deferred_lock:
        out = list(_deferred_records)
        _deferred_records.clear()
    return out


def _net_pnl_usd(rec: Dict[str, Any], cfg: BotConfig) -> Optional[float]:
    pnl = rec.get("pnl_usd")
    if pnl is None:
        return None
    try:
        gross = float(pnl)
    except (TypeError, ValueError):
        return None
    if not getattr(cfg, "PPO_REWARD_FEE_AWARE", True):
        return gross
    cost_pct = float(getattr(cfg, "TRANSACTION_COST_PCT", 0.001))
    entry = float(rec.get("entry_fill") or rec.get("entry", 0) or 0)
    exit_px = float(rec.get("exit_fill") or rec.get("exit", 0) or 0)
    shares = float(rec.get("shares", 0) or 0)
    if entry > 0 and exit_px > 0 and shares > 0:
        fees = shares * (entry + exit_px) * cost_pct
        return gross - fees
    return gross


def _episode_reward(rec: Dict[str, Any], cfg: BotConfig) -> float:
    for key in ("teacher_reward", "reward", "reward_delta"):
        if rec.get(key) is not None:
            try:
                v = float(rec.get(key))
                if abs(v) > 1e-9:
                    return float(max(-1.0, min(1.0, v)))
            except (TypeError, ValueError):
                pass
    pnl = _net_pnl_usd(rec, cfg)
    if pnl is not None:
        try:
            return float(np.tanh(float(pnl) / float(os.getenv("PPO_REWARD_PNL_SCALE", "45"))))
        except (TypeError, ValueError):
            pass
    if rec.get("win"):
        return 0.35
    if rec.get("win") is False or rec.get("result") == "loss":
        return -0.35
    return 0.0


def _target_action(rec: Dict[str, Any]) -> Optional[int]:
    if rec.get("teacher_action") is not None:
        try:
            return int(rec.get("teacher_action"))
        except (TypeError, ValueError):
            pass
    if rec.get("action") is not None and str(rec.get("source", "")) == "teacher_ppo":
        try:
            return int(rec.get("action"))
        except (TypeError, ValueError):
            pass
    if rec.get("should_have_entered") is False:
        return 0
    if rec.get("should_have_entered") is True:
        return 1
    pnl = rec.get("pnl_usd")
    if pnl is not None:
        return 1 if float(pnl) > 0 else 0
    if rec.get("ppo_action") is not None:
        try:
            return int(rec.get("ppo_action"))
        except (TypeError, ValueError):
            pass
    return None


@dataclass
class LabeledEpisode:
    features: np.ndarray
    prices: np.ndarray
    step_rewards: np.ndarray
    target_action: Optional[int] = None
    ticker: str = ""
    source: str = ""


def record_to_episode(rec: Dict[str, Any], cfg: BotConfig) -> Optional[LabeledEpisode]:
    feat = _feat_vector(rec.get("features") or rec.get("obs"), cfg)
    if feat is None and str(rec.get("source", "")) == "commander_ib_gold":
        feat = _commander_teacher_features(rec, cfg)
    if feat is None:
        return None

    ws = int(getattr(cfg, "WINDOW_SIZE", 30))
    T = ws + 3
    entry = float(rec.get("entry_price") or rec.get("entry") or 100.0)
    exit_p = float(rec.get("exit_price") or rec.get("exit") or entry)
    if abs(exit_p - entry) < 1e-6:
        # Entry-only records — small favorable drift if reward positive
        terminal_r = _episode_reward(rec, cfg)
        drift = 0.002 * (1.0 if terminal_r >= 0 else -1.0)
        exit_p = entry * (1.0 + drift)

    prices = np.linspace(entry, exit_p, T, dtype=np.float32)
    features = np.tile(feat.reshape(1, -1), (T, 1)).astype(np.float32)

    terminal_r = _episode_reward(rec, cfg)
    step_rewards = np.zeros(T, dtype=np.float32)
    decision_idx = ws
    step_rewards[decision_idx] = terminal_r * 0.45
    step_rewards[-1] = terminal_r

    return LabeledEpisode(
        features=features,
        prices=prices,
        step_rewards=step_rewards,
        target_action=_target_action(rec),
        ticker=str(rec.get("ticker", "")),
        source=str(rec.get("source", "")),
    )


def build_episodes(cfg: BotConfig, records: Sequence[Dict[str, Any]]) -> List[LabeledEpisode]:
    max_ep = int(os.getenv("PPO_REWARD_MAX_EPISODES", "96"))
    if os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes"):
        max_ep = min(max_ep, int(os.getenv("PPO_REWARD_REPLAY_MAX_EPISODES", "24")))
    episodes: List[LabeledEpisode] = []
    seen = set()
    for rec in reversed(list(records)):
        key = (
            rec.get("entry_id"),
            rec.get("timestamp"),
            rec.get("ticker"),
            rec.get("source"),
        )
        if key in seen:
            continue
        ep = record_to_episode(rec, cfg)
        if ep is None:
            continue
        seen.add(key)
        episodes.append(ep)
        if len(episodes) >= max_ep:
            break
    episodes.reverse()
    return episodes


def collect_training_records(
    cfg: BotConfig,
    *,
    extra: Optional[Sequence[Dict[str, Any]]] = None,
    n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    from core.experience_buffer import load_recent

    cap = n or int(os.getenv("PPO_REWARD_BUFFER_LOOKBACK", "600"))
    raw = load_recent(cap)
    if extra:
        raw = list(raw) + list(extra)
    out: List[Dict[str, Any]] = []
    for rec in raw:
        src = str(rec.get("source", ""))
        if src == "commander_ib_gold" and rec.get("teacher_action") is not None:
            out.append(rec)
            continue
        if not (rec.get("features") or rec.get("obs")):
            continue
        if src in REWARD_SOURCES or rec.get("reward") is not None or rec.get("pnl_usd") is not None:
            out.append(rec)
    return out


class LabeledTradingEnv(TradingEnv):
    """TradingEnv with injected step rewards from recorded trade / teacher labels."""

    def __init__(
        self,
        episode: LabeledEpisode,
        cfg: BotConfig,
    ) -> None:
        self._inject_rewards = episode.step_rewards
        self._target_action = episode.target_action
        self._episode_meta = episode
        super().__init__(
            episode.features,
            episode.prices,
            cfg.INITIAL_CASH,
            cfg.TRANSACTION_COST_PCT,
            cfg.WINDOW_SIZE,
            cfg.DEFAULT_MAX_POSITION_PCT,
        )

    def step(self, action: int):
        obs, env_r, terminated, truncated, info = super().step(int(action))
        idx = min(max(self._step_idx - 1, 0), len(self._inject_rewards) - 1)
        reward = float(self._inject_rewards[idx])
        if idx == self.window_size and self._target_action is not None:
            if int(action) == int(self._target_action):
                reward += abs(reward) * 0.2 + 0.08
            else:
                reward -= abs(reward) * 0.2 + 0.06
        reward += 0.12 * float(env_r)
        info = dict(info or {})
        info["injected_reward"] = float(self._inject_rewards[idx])
        info["ticker"] = self._episode_meta.ticker
        return obs, float(reward), terminated, truncated, info


class EpisodesPoolEnv(gym.Env):
    """Rotates labeled episodes — one mini-episode per reset."""

    metadata = {"render_modes": []}

    def __init__(self, episodes: Sequence[LabeledEpisode], cfg: BotConfig) -> None:
        if not episodes:
            raise ValueError("EpisodesPoolEnv requires at least one episode")
        self._episodes = list(episodes)
        self._cfg = cfg
        self._inner: Optional[LabeledTradingEnv] = None
        self.observation_space = self._spawn_inner().observation_space
        self.action_space = self._spawn_inner().action_space

    def _spawn_inner(self) -> LabeledTradingEnv:
        ep = random.choice(self._episodes)
        return LabeledTradingEnv(ep, self._cfg)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._inner = self._spawn_inner()
        return self._inner.reset(seed=seed, options=options)

    def step(self, action):
        if self._inner is None:
            raise RuntimeError("EpisodesPoolEnv.reset() must be called first")
        return self._inner.step(action)

    def render(self):
        if self._inner:
            self._inner.render()


def _compute_train_steps(cfg: BotConfig, n_episodes: int, requested: Optional[int] = None) -> int:
    base = requested or int(getattr(cfg, "PPO_REWARD_TRAIN_STEPS", 2048))
    try:
        from core.learning_coordinator import heavy_learning_steps_scale
        scale = heavy_learning_steps_scale(cfg)
        base = max(256, int(base * scale))
    except Exception:
        pass
    per_ep = int(os.getenv("PPO_REWARD_STEPS_PER_EPISODE", "24"))
    return min(int(os.getenv("PPO_REWARD_TRAIN_STEPS_MAX", "8192")), max(base, n_episodes * per_ep))


def run_reward_linked_ppo_train(
    cfg: BotConfig,
    *,
    model: Any = None,
    steps: Optional[int] = None,
    extra_records: Optional[Sequence[Dict[str, Any]]] = None,
    force: bool = False,
    live: bool = False,
) -> bool:
    """
    Train PPO on labeled episodes with real buffer rewards.

    force=True  — full train (shutdown, teacher, unified pipeline)
    live=True   — bounded in-session train during RTH/replay (default on)
    neither     — full train off-hours only; RTH records queue until live/flush batch
    """
    if not force:
        if live:
            try:
                from core.learning_coordinator import allow_live_micro_ppo
                if not allow_live_micro_ppo(cfg):
                    if extra_records:
                        defer_reward_records(list(extra_records))
                    return False
            except Exception:
                if extra_records:
                    defer_reward_records(list(extra_records))
                return False
        else:
            try:
                from core.learning_coordinator import memory_pressure_high, should_defer_heavy_learning
                if should_defer_heavy_learning(cfg) or memory_pressure_high(cfg):
                    if extra_records:
                        defer_reward_records(list(extra_records))
                    return False
            except Exception:
                pass

    deferred = drain_deferred_records()
    records = collect_training_records(cfg, extra=extra_records)
    if deferred:
        records.extend(deferred)

    episodes = build_episodes(cfg, records)
    min_ep = int(os.getenv("PPO_REWARD_MIN_EPISODES", "4"))
    if len(episodes) < min_ep:
        log.debug(f"Reward-linked PPO: {len(episodes)} episodes < {min_ep} — re-queuing")
        if records:
            defer_reward_records(records)
        return False

    if model is None:
        try:
            from core.ppo_entry_learning import get_ppo_model
            model = get_ppo_model()
        except Exception:
            model = None

    train_steps = _compute_train_steps(cfg, len(episodes), steps)
    if live and not force:
        try:
            from core.learning_coordinator import live_micro_ppo_steps
            train_steps = live_micro_ppo_steps(cfg, train_steps)
        except Exception:
            train_steps = min(train_steps, int(os.getenv("PPO_LIVE_MICRO_STEPS_MAX", "64")))

    vec_env = None
    try:
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        import gc
        from stable_baselines3.common.vec_env import DummyVecEnv

        ep_snapshot = list(episodes)

        def _factory() -> EpisodesPoolEnv:
            return EpisodesPoolEnv(ep_snapshot, cfg)

        vec_env = DummyVecEnv([_factory])

        if model is None:
            from core.agent import build_ppo_agent
            probe = EpisodesPoolEnv(ep_snapshot, cfg)
            model = build_ppo_agent(probe, cfg, model_path=cfg.MODEL_PATH)
        model.set_env(vec_env)

        model.learn(total_timesteps=train_steps, reset_num_timesteps=False, progress_bar=False)

        try:
            from core.ppo_entry_learning import set_ppo_model
            set_ppo_model(model)
        except Exception:
            pass

        saved = False
        try:
            from core.learning_coordinator import should_save_ppo_now, note_ppo_saved
            if should_save_ppo_now(cfg, force=force):
                model.save(cfg.MODEL_PATH)
                note_ppo_saved()
                saved = True
        except Exception:
            model.save(cfg.MODEL_PATH)
            saved = True

        pos = sum(1 for e in episodes if (e.step_rewards[-1] if len(e.step_rewards) else 0) > 0)
        mode = "live" if live and not force else "full"
        log.info(
            f"  🧠 Reward-linked PPO ({mode}): {len(episodes)} episodes ({pos} positive) | "
            f"{train_steps} steps | saved={saved} → {cfg.MODEL_PATH}"
        )
        return True
    except Exception as exc:
        log.warning(f"Reward-linked PPO train failed: {exc}")
        if records:
            defer_reward_records(records)
        return False
    finally:
        if vec_env is not None:
            try:
                vec_env.close()
            except Exception:
                pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass
