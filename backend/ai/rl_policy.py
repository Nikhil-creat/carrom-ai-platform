"""Reinforcement-learning shot policy (PPO, contextual-bandit form).

A carrom shot is close to a one-step decision (state -> (striker_x, angle,
force) -> immediate reward), so the policy is trained with clipped-PPO on
single-step episodes against the real physics. In production it acts as a
*residual* policy: the Analyst proposes geometric candidates and the policy
nudges angle/force around them (bounded), which keeps it safe and sample
efficient. With no trained weights it is the identity, so the agent works
out of the box.

torch is optional at import time; everything except the network/trainer
works without it.
"""
from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass
from typing import Any

import numpy as np

from physics.engine import Shot
from physics.rules import CarromGame

try:  # optional dependency
    import torch
    from torch import nn

    TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    TORCH = False

N_COINS = 19
OBS_DIM = N_COINS * 4 + 6
MAX_ANGLE_DELTA = math.radians(3.0)
MAX_FORCE_DELTA = 0.10  # fraction of the candidate force


def encode_state(game: CarromGame, slot: str) -> np.ndarray:
    """Fixed-length observation: per-coin (x, y, alive, mine) + 6 scalars."""
    cfg = game.cfg
    me = game.players[slot].color
    coins = [b for b in game.physics.bodies if b.kind != "striker"]
    v = np.zeros(OBS_DIM, dtype=np.float32)
    for i, b in enumerate(coins[:N_COINS]):
        v[i * 4 + 0] = b.x / cfg.board_size
        v[i * 4 + 1] = b.y / cfg.board_size
        v[i * 4 + 2] = 0.0 if b.pocketed else 1.0
        v[i * 4 + 3] = 1.0 if b.kind == me else (0.5 if b.kind == "queen" else 0.0)
    other = "p2" if slot == "p1" else "p1"
    base = N_COINS * 4
    v[base + 0] = (game.players[slot].score - game.players[other].score) / 10.0
    v[base + 1] = 1.0 if slot == "p1" else 0.0
    v[base + 2] = 1.0 if game.queen_state == "board" else 0.0
    v[base + 3] = 1.0 if game.queen_state == "pending" else 0.0
    v[base + 4] = 1.0 if game.queen_state == "covered" else 0.0
    v[base + 5] = game.strike_count / 100.0
    return v


if TORCH:

    class PolicyNet(nn.Module):  # type: ignore[misc]
        """Actor-critic MLP. Action = [x_norm, angle_norm, force_norm] in [-1, 1]."""

        def __init__(self, obs_dim: int = OBS_DIM, hidden: int = 128) -> None:
            super().__init__()
            self.body = nn.Sequential(nn.Linear(obs_dim, hidden), nn.Tanh(),
                                      nn.Linear(hidden, hidden), nn.Tanh())
            self.mu = nn.Linear(hidden, 3)
            self.v = nn.Linear(hidden, 1)
            self.log_std = nn.Parameter(torch.full((3,), -1.0))

        def forward(self, obs):
            h = self.body(obs)
            return torch.tanh(self.mu(h)), self.log_std.exp().expand_as(self.mu(h)), self.v(h).squeeze(-1)

        def dist(self, obs):
            mu, std, v = self(obs)
            return torch.distributions.Normal(mu, std), v


def action_to_shot(a: np.ndarray, game: CarromGame, slot: str) -> Shot:
    """Absolute action (used for training rollouts from scratch)."""
    cfg = game.cfg
    x = cfg.baseline_min_x + (float(a[0]) * 0.5 + 0.5) * (cfg.baseline_max_x - cfg.baseline_min_x)
    base = math.pi / 2 if slot == "p1" else -math.pi / 2
    angle = base + float(a[1]) * math.radians(80)
    force = (float(a[2]) * 0.5 + 0.5) * cfg.max_impulse
    return Shot(x, game.baseline_y(slot), angle, force)


class RLShotPolicy:
    """Residual policy wrapper used by the Executor agent."""

    def __init__(self, weights_path: str | None = None) -> None:
        self.net = None
        path = weights_path or os.getenv("CARROM_POLICY_WEIGHTS")
        if TORCH and path and os.path.exists(path):
            self.net = PolicyNet()
            self.net.load_state_dict(torch.load(path, map_location="cpu"))
            self.net.eval()

    @property
    def trained(self) -> bool:
        return self.net is not None

    def refine(self, shot: Shot, game: CarromGame, slot: str) -> Shot:
        if self.net is None:
            return shot
        with torch.no_grad():
            obs = torch.from_numpy(encode_state(game, slot))[None]
            mu, _, _ = self.net(obs)
        da, df = float(mu[0, 1]), float(mu[0, 2])
        return Shot(shot.striker_x, shot.striker_y,
                    shot.angle_rad + da * MAX_ANGLE_DELTA,
                    max(0.5, shot.impulse_force * (1.0 + df * MAX_FORCE_DELTA)),
                    shot.top_spin, shot.side_spin)


# ---------------------------------------------------------------------- training
class CarromShotEnv:
    """Single-agent training env: random-ish mid-game boards, reward = shot outcome."""

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)
        self.game: CarromGame | None = None

    def reset(self) -> np.ndarray:
        g = CarromGame("train", ("a", "A"), ("b", "B"))
        coins = [b for b in g.physics.bodies if b.kind != "striker"]
        for b in coins:  # remove a random subset to create varied mid-game positions
            if self.rng.random() < 0.45:
                b.pocketed = True
        for b in coins:  # jitter survivors (keeps non-overlap by trying small moves)
            if b.pocketed:
                continue
            for _ in range(5):
                nx, ny = b.x + self.rng.uniform(-90, 90), b.y + self.rng.uniform(-90, 90)
                if 60 < nx < 680 and 60 < ny < 680 and all(
                        math.hypot(nx - o.x, ny - o.y) > 2 * b.radius + 1
                        for o in coins if o is not b and not o.pocketed):
                    b.x, b.y = nx, ny
                    break
        g.record_frames = False
        self.game = g
        return encode_state(g, "p1")

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, dict[str, Any]]:
        assert self.game is not None
        out = self.game.play_shot("p1", action_to_shot(action, self.game, "p1"))
        reward = float(out.score_delta) - 0.5 * out.opp_pocketed - (2.0 if out.foul else 0.0)
        reward += 0.3 if out.next_turn == "p1" else 0.0
        return encode_state(self.game, "p1"), reward, True, {"outcome": out.summary()}


def train_ppo(updates: int = 50, batch: int = 64, epochs: int = 4, clip: float = 0.2,
              lr: float = 3e-4, seed: int = 0, save_path: str | None = None) -> "PolicyNet":
    """Clipped-PPO on one-step episodes. CPU-friendly; ~10 ms per physics rollout."""
    if not TORCH:
        raise RuntimeError("torch is required for training")
    torch.manual_seed(seed)
    env = CarromShotEnv(seed)
    net = PolicyNet()
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    for u in range(updates):
        obs_l, act_l, logp_l, rew_l = [], [], [], []
        for _ in range(batch):
            obs = env.reset()
            with torch.no_grad():
                d, _ = net.dist(torch.from_numpy(obs)[None])
                a = d.sample().clamp(-1, 1)
                logp = d.log_prob(a).sum(-1)
            _, r, _, _ = env.step(a[0].numpy())
            obs_l.append(obs), act_l.append(a[0].numpy()), logp_l.append(float(logp)), rew_l.append(r)
        obs_t = torch.tensor(np.array(obs_l))
        act_t = torch.tensor(np.array(act_l))
        old_logp = torch.tensor(logp_l)
        rew = torch.tensor(rew_l, dtype=torch.float32)
        for _ in range(epochs):
            d, v = net.dist(obs_t)
            adv = rew - v.detach()
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)
            ratio = (d.log_prob(act_t).sum(-1) - old_logp).exp()
            pol_loss = -torch.min(ratio * adv, ratio.clamp(1 - clip, 1 + clip) * adv).mean()
            val_loss = 0.5 * (v - rew).pow(2).mean()
            loss = pol_loss + val_loss - 0.001 * d.entropy().sum(-1).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
    if save_path:
        torch.save(net.state_dict(), save_path)
    return net


if __name__ == "__main__":  # python -m ai.rl_policy
    train_ppo(updates=int(os.getenv("PPO_UPDATES", "20")), save_path=os.getenv("CARROM_POLICY_WEIGHTS", "policy.pt"))
