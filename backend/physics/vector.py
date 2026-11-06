"""Small 2D vector / geometry helpers shared by physics, rules and the AI.

Pure-Python floats only (no numpy) so results are bit-identical wherever the
interpreter's IEEE-754 doubles are, which the deterministic engine relies on.
Coordinates follow the canvas convention: x right, y DOWN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Vec2:
    x: float
    y: float

    def __add__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, k: float) -> "Vec2":
        return Vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def dot(self, o: "Vec2") -> float:
        return self.x * o.x + self.y * o.y

    def cross(self, o: "Vec2") -> float:
        return self.x * o.y - self.y * o.x

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> "Vec2":
        n = self.length()
        return Vec2(self.x / n, self.y / n) if n > 1e-12 else Vec2(0.0, 0.0)

    def perp(self) -> "Vec2":
        return Vec2(-self.y, self.x)


def angle_to_dir(angle_rad: float) -> Vec2:
    """Shot angle -> unit direction.

    Angle is measured counter-clockwise *as seen on screen* from +x, so
    pi/2 points to the top of the board (y decreasing). This matches the
    `angle_rad` / `aim_angle_deg` fields in the wire protocol.
    """
    return Vec2(math.cos(angle_rad), -math.sin(angle_rad))


def dir_to_angle(d: Vec2) -> float:
    return math.atan2(-d.y, d.x)


def dist(a: Vec2, b: Vec2) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def point_segment_distance(p: Vec2, a: Vec2, b: Vec2) -> float:
    ab = b - a
    denom = ab.dot(ab)
    if denom < 1e-12:
        return dist(p, a)
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return dist(p, a + ab * t)


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
