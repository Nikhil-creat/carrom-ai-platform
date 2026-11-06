"""Server-side shot validation.

The real anti-cheat is *architectural*: the server owns the physics, so a
modified client can only ever submit a shot request, never a result. This
module makes sure that request is sane (ranges, baseline, timing, replay,
rate) and counts violations so repeat offenders get kicked.

`client_checksum` is an integrity/tamper-evidence check against casual
payload editing and accidental corruption. It is NOT a secret-keyed MAC (a
browser cannot hold a secret), so it must never be the only defence.
"""
from __future__ import annotations

import hashlib
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from config import settings
from physics.engine import PhysicsConfig, Shot


class CheatViolation(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def compute_checksum(room_id: str, player_id: str, timestamp: int, payload: dict[str, Any]) -> str:
    """Canonical checksum the client must compute: sha256(...)[:8]."""
    sp = payload.get("spin", {}) or {}
    pos = payload.get("striker_position", {}) or {}
    canon = "|".join([
        str(room_id), str(player_id), str(int(timestamp)),
        f"{float(pos.get('x', 0.0)):.2f}",
        f"{float(payload.get('angle_rad', 0.0)):.6f}",
        f"{float(payload.get('impulse_force', 0.0)):.3f}",
        f"{float(sp.get('top_spin', 0.0)):.3f}",
        f"{float(sp.get('side_spin', 0.0)):.3f}",
    ])
    return hashlib.sha256(canon.encode()).hexdigest()[:8]


@dataclass
class _PlayerTrack:
    last_ts: int = 0
    shots: deque = field(default_factory=lambda: deque(maxlen=64))
    strikes: int = 0


class AntiCheat:
    def __init__(self, cfg: PhysicsConfig | None = None, require_checksum: bool = True) -> None:
        self.cfg = cfg or PhysicsConfig()
        self.require_checksum = require_checksum
        self._tracks: dict[str, _PlayerTrack] = defaultdict(_PlayerTrack)

    def _num(self, v: Any, name: str) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise CheatViolation("BAD_PAYLOAD", f"{name} must be a finite number")
        return float(v)

    def validate_shot(self, room_id: str, user_id: str, slot: str, baseline_y: float,
                      message: dict[str, Any], now_ms: int | None = None) -> Shot:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        c = self.cfg
        t = self._tracks[user_id]
        if message.get("player_id") != user_id:
            self._flag(t, "SPOOFED_PLAYER", "player_id does not match connection")
        if message.get("room_id") != room_id:
            self._flag(t, "WRONG_ROOM", "room_id mismatch")
        ts = int(self._num(message.get("timestamp"), "timestamp"))
        if abs(now_ms - ts) > settings.max_clock_skew_ms:
            self._flag(t, "STALE_TIMESTAMP", "timestamp outside allowed clock skew")
        if ts <= t.last_ts:
            self._flag(t, "REPLAY", "timestamp not newer than previous shot")
        while t.shots and now_ms - t.shots[0] > 60_000:
            t.shots.popleft()
        if len(t.shots) >= settings.max_shots_per_minute:
            self._flag(t, "RATE_LIMIT", "too many shots per minute")

        p = message.get("payload")
        if not isinstance(p, dict):
            raise CheatViolation("BAD_PAYLOAD", "payload missing")
        pos = p.get("striker_position") or {}
        x = self._num(pos.get("x"), "striker_position.x")
        y = self._num(pos.get("y", baseline_y), "striker_position.y")
        angle = self._num(p.get("angle_rad"), "angle_rad")
        force = self._num(p.get("impulse_force"), "impulse_force")
        spin = p.get("spin") or {}
        top = self._num(spin.get("top_spin", 0.0), "top_spin")
        side = self._num(spin.get("side_spin", 0.0), "side_spin")

        if not (0.0 <= force <= c.max_impulse):
            self._flag(t, "FORCE_RANGE", f"impulse_force must be within 0..{c.max_impulse}")
        if abs(top) > 1.0 or abs(side) > 1.0:
            self._flag(t, "SPIN_RANGE", "spin must be within -1..1")
        if abs(y - baseline_y) > 0.5:
            self._flag(t, "BASELINE_Y", "striker must be on your baseline")
        if not (c.baseline_min_x <= x <= c.baseline_max_x):
            self._flag(t, "BASELINE_X", "striker outside baseline limits")
        if abs(angle) > 4 * math.pi:
            self._flag(t, "ANGLE_RANGE", "angle out of range")

        if self.require_checksum:
            expect = compute_checksum(room_id, user_id, ts, p)
            if str(p.get("client_checksum", "")).lower() != expect:
                self._flag(t, "BAD_CHECKSUM", "client checksum mismatch")

        t.last_ts = ts
        t.shots.append(now_ms)
        return Shot(x, baseline_y, angle, force, top, side)

    def _flag(self, track: _PlayerTrack, code: str, message: str) -> None:
        # Rate/replay/stale can be honest (lag, double click); all count toward a kick anyway.
        track.strikes += 1
        raise CheatViolation(code, message)

    def strikes(self, user_id: str) -> int:
        return self._tracks[user_id].strikes

    def should_kick(self, user_id: str) -> bool:
        return self._tracks[user_id].strikes >= settings.cheat_strikes_before_kick

    def reset_room(self) -> None:
        self._tracks.clear()
