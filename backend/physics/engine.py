"""Deterministic 2D rigid-body physics for Carrom.

Design notes
------------
* Fixed timestep: 60 ticks/s, each split into `substeps` sub-steps so the
  fastest striker (~2600 units/s) never moves more than ~11 units per
  sub-step (coin radius is 15.1) -> no tunnelling.
* Determinism: no randomness, no wall-clock, no dict/set iteration order
  dependence. Bodies live in a list with a fixed order and pairs are always
  visited i < j. Same inputs -> same outputs on the same platform, which is
  what the authoritative server and the AI simulator rely on.
* Units are board units: the 74 cm board is 740 x 740 units (1 unit = 1 mm).
* Impulse resolution (coin<->coin, coin<->wall) handles restitution plus
  tangential (Coulomb-limited) friction that exchanges linear and angular
  momentum, which is where rotational spin comes from.
* Friction: constant dynamic deceleration (mu_k * G) plus light viscous
  damping. Spin decays exponentially and bends the path slightly (Magnus-like
  curve term) so side-spin shots feel different from clean ones.

Angle convention: see vector.angle_to_dir (pi/2 == towards top of the screen).
"""
from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import Any

from .vector import angle_to_dir, clamp

TICK_RATE = 60
TICK_DT = 1.0 / TICK_RATE


@dataclass
class PhysicsConfig:
    board_size: float = 740.0
    coin_radius: float = 15.1
    striker_radius: float = 18.0
    coin_mass: float = 5.5
    striker_mass: float = 15.0
    pocket_radius: float = 30.0
    pocket_inset: float = 35.0
    e_coin_coin: float = 0.85   # coin <-> coin / striker <-> coin
    e_border: float = 0.78      # border <-> coin
    friction: float = 0.015     # mu_k, dimensionless
    friction_scale: float = 40000.0  # converts mu_k to units/s^2 (decel = 600 by default)
    damping: float = 0.35       # viscous velocity damping, 1/s
    spin_decay: float = 1.6     # 1/s
    spin_curve: float = 0.0009  # lateral accel per (rad/s * unit/s)
    contact_mu: float = 0.12    # Coulomb friction between bodies / border
    rest_speed: float = 3.0     # units/s below which a body is stopped
    rest_spin: float = 0.5      # rad/s
    substeps: int = 4
    solver_iterations: int = 2
    max_shot_speed: float = 2600.0  # speed at impulse_force == max_impulse
    max_impulse: float = 20.0
    side_spin_scale: float = 40.0   # rad/s per unit of side_spin
    max_sim_ticks: int = TICK_RATE * 25
    baseline_p1_y: float = 620.0
    baseline_p2_y: float = 120.0
    baseline_min_x: float = 150.0
    baseline_max_x: float = 590.0


@dataclass
class Body:
    id: str
    kind: str  # "queen" | "white" | "black" | "striker"
    x: float
    y: float
    radius: float
    mass: float
    vx: float = 0.0
    vy: float = 0.0
    omega: float = 0.0
    pocketed: bool = False
    pocket_id: str | None = None

    @property
    def inertia(self) -> float:
        return 0.5 * self.mass * self.radius * self.radius  # solid disc

    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)


@dataclass
class Shot:
    striker_x: float
    striker_y: float
    angle_rad: float
    impulse_force: float
    top_spin: float = 0.0
    side_spin: float = 0.0


@dataclass
class PhysEvent:
    tick: int
    type: str  # "striker_hit" | "coin_hit" | "border" | "pocket"
    a: str
    b: str | None = None
    intensity: float = 0.0
    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick, "type": self.type, "a": self.a, "b": self.b,
            "intensity": round(self.intensity, 2),
            "x": round(self.x, 1), "y": round(self.y, 1),
        }


@dataclass
class ShotResult:
    ticks: int
    settled: bool
    events: list[PhysEvent]
    pocketed: list[Body]            # in pocketing order (includes striker if it fell)
    frames: list[list[float]]       # per tick: [x0, y0, x1, y1, ...] in body order
    body_order: list[str]
    first_contact: str | None       # id of the first body the striker touched
    final_hash: str
    start_tick: int = 0          # engine tick before the first recorded frame


class CarromPhysics:
    def __init__(self, config: PhysicsConfig | None = None) -> None:
        self.cfg = config or PhysicsConfig()
        self.bodies: list[Body] = []
        self.tick = 0
        s, i, r = self.cfg.board_size, self.cfg.pocket_inset, self.cfg.pocket_radius
        self.pockets = [
            {"id": "p_top_left", "x": i, "y": i, "radius": r},
            {"id": "p_top_right", "x": s - i, "y": i, "radius": r},
            {"id": "p_bot_left", "x": i, "y": s - i, "radius": r},
            {"id": "p_bot_right", "x": s - i, "y": s - i, "radius": r},
        ]
        self.reset_layout()

    # ------------------------------------------------------------------ layout
    def reset_layout(self) -> None:
        """Standard opening rosette: queen centre, 6 inner, 12 outer coins."""
        c = self.cfg
        mid = c.board_size / 2.0
        gap = 2 * c.coin_radius + 0.2
        coins: list[tuple[str, float, float]] = [("queen", mid, mid)]
        inner = []
        for k in range(6):
            a = math.radians(60 * k + 90)
            inner.append((mid + gap * math.cos(a), mid + gap * math.sin(a)))
        outer = []
        for k in range(12):
            if k % 2 == 0:  # on the inner coins' axes, two gaps out
                a, d = math.radians(30 * k + 90), 2 * gap
            else:           # nestled between two inner coins
                a, d = math.radians(30 * k + 90), math.sqrt(3) * gap
            outer.append((mid + d * math.cos(a), mid + d * math.sin(a)))
        for k, (x, y) in enumerate(inner):
            coins.append(("white" if k % 2 == 0 else "black", x, y))
        for k, (x, y) in enumerate(outer):
            coins.append(("white" if (k // 2) % 2 == 0 else "black", x, y))
        self.bodies = []
        n = {"queen": 0, "white": 0, "black": 0}
        for kind, x, y in coins:
            n[kind] += 1
            cid = {"queen": "q1"}.get(kind, f"{kind[0]}{n[kind]}")
            self.bodies.append(Body(cid, kind, x, y, c.coin_radius, c.coin_mass))
        self.bodies.append(Body("striker", "striker", mid, c.baseline_p1_y,
                                c.striker_radius, c.striker_mass))
        self.tick = 0

    # ---------------------------------------------------------------- accessors
    @property
    def striker(self) -> Body:
        return self.bodies[-1]

    def body(self, bid: str) -> Body:
        for b in self.bodies:
            if b.id == bid:
                return b
        raise KeyError(bid)

    def active(self) -> list[Body]:
        return [b for b in self.bodies if not b.pocketed]

    def is_at_rest(self) -> bool:
        return all(b.speed() == 0.0 and b.omega == 0.0 for b in self.bodies if not b.pocketed)

    def clone(self) -> "CarromPhysics":
        other = CarromPhysics.__new__(CarromPhysics)
        other.cfg = self.cfg
        other.pockets = self.pockets
        other.tick = self.tick
        other.bodies = [Body(b.id, b.kind, b.x, b.y, b.radius, b.mass, b.vx, b.vy,
                             b.omega, b.pocketed, b.pocket_id) for b in self.bodies]
        return other

    # -------------------------------------------------------------------- hash
    def state_hash(self) -> str:
        h = hashlib.sha256()
        for b in self.bodies:
            h.update(struct.pack("<dddd?", b.x, b.y, b.vx, b.vy, b.pocketed))
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------- shots
    def place_striker(self, x: float, y: float) -> None:
        s = self.striker
        s.x, s.y, s.vx, s.vy, s.omega = x, y, 0.0, 0.0, 0.0
        s.pocketed, s.pocket_id = False, None

    def apply_shot(self, shot: Shot) -> None:
        c = self.cfg
        self.place_striker(shot.striker_x, shot.striker_y)
        d = angle_to_dir(shot.angle_rad)
        force = clamp(shot.impulse_force, 0.0, c.max_impulse)
        speed = (force / c.max_impulse) * c.max_shot_speed * (1.0 + 0.15 * clamp(shot.top_spin, -1, 1))
        s = self.striker
        s.vx, s.vy = d.x * speed, d.y * speed
        s.omega = clamp(shot.side_spin, -1.0, 1.0) * c.side_spin_scale

    def simulate_shot(self, shot: Shot, record_frames: bool = True) -> ShotResult:
        """Apply `shot` and step until everything stops (or max_sim_ticks)."""
        self.apply_shot(shot)
        events: list[PhysEvent] = []
        pocketed: list[Body] = []
        frames: list[list[float]] = []
        first_contact: list[str | None] = [None]
        order = [b.id for b in self.bodies]
        start_tick = self.tick
        settled = False
        while self.tick - start_tick < self.cfg.max_sim_ticks:
            self.step(events, pocketed, first_contact)
            if record_frames:
                frames.append(self._frame())
            if self.is_at_rest():
                settled = True
                break
        return ShotResult(self.tick - start_tick, settled, events, pocketed, frames,
                          order, first_contact[0], self.state_hash(), start_tick)

    def _frame(self) -> list[float]:
        out: list[float] = []
        for b in self.bodies:
            out.append(round(b.x, 2))
            out.append(round(b.y, 2))
        return out

    # -------------------------------------------------------------------- step
    def step(self, events: list[PhysEvent] | None = None,
             pocketed: list[Body] | None = None,
             first_contact: list[str | None] | None = None) -> None:
        events = events if events is not None else []
        pocketed = pocketed if pocketed is not None else []
        first_contact = first_contact if first_contact is not None else [None]
        c = self.cfg
        vmax = 0.0
        for b in self.bodies:
            if not b.pocketed:
                sp = abs(b.vx) + abs(b.vy)  # cheap upper bound of |v|
                if sp > vmax:
                    vmax = sp
        # keep per-substep travel under 0.75 coin radii: no tunnelling, fewer steps when slow
        n = max(1, min(c.substeps, math.ceil(vmax * TICK_DT / (0.75 * c.coin_radius))))
        h = TICK_DT / n
        for _ in range(n):
            self._substep(h, events, pocketed, first_contact)
        self.tick += 1

    def _substep(self, h: float, events: list[PhysEvent], pocketed: list[Body],
                 first_contact: list[str | None]) -> None:
        c = self.cfg
        live = [b for b in self.bodies if not b.pocketed]

        for b in live:
            # Magnus-like curve: spin deflects the path perpendicular to velocity.
            if b.omega != 0.0:
                b.vx += -b.vy * b.omega * c.spin_curve * h * 60.0
                b.vy += b.vx * b.omega * c.spin_curve * h * 60.0
            b.x += b.vx * h
            b.y += b.vy * h

        for b in live:
            self._walls(b, events)

        for _ in range(c.solver_iterations):
            moving = [(b.vx != 0.0 or b.vy != 0.0) for b in live]
            if not any(moving):
                break
            for i in range(len(live)):
                a = live[i]
                am = moving[i]
                for j in range(i + 1, len(live)):
                    if not (am or moving[j]):
                        continue  # two resting bodies never start overlapping
                    self._pair(a, live[j], events, first_contact)
            for b in live:
                self._walls(b, events, quiet=True)

        for b in live:
            self._pockets(b, events, pocketed)

        decel = c.friction * c.friction_scale
        damp = math.exp(-c.damping * h)
        spin_damp = math.exp(-c.spin_decay * h)
        for b in live:
            if b.pocketed:
                continue
            sp = b.speed()
            if sp > 0.0:
                ns = max(0.0, sp - decel * h) * damp
                if ns < c.rest_speed:
                    ns = 0.0
                k = ns / sp
                b.vx *= k
                b.vy *= k
            b.omega *= spin_damp
            if abs(b.omega) < c.rest_spin:
                b.omega = 0.0

    # --------------------------------------------------------------- collisions
    def _walls(self, b: Body, events: list[PhysEvent], quiet: bool = False) -> None:
        s, r = self.cfg.board_size, b.radius
        if b.x - r < 0.0:
            b.x = r
            self._wall_bounce(b, 1.0, 0.0, events, quiet)
        elif b.x + r > s:
            b.x = s - r
            self._wall_bounce(b, -1.0, 0.0, events, quiet)
        if b.y - r < 0.0:
            b.y = r
            self._wall_bounce(b, 0.0, 1.0, events, quiet)
        elif b.y + r > s:
            b.y = s - r
            self._wall_bounce(b, 0.0, -1.0, events, quiet)

    def _wall_bounce(self, b: Body, nx: float, ny: float,
                     events: list[PhysEvent], quiet: bool) -> None:
        """n points from the wall into the board."""
        vn = b.vx * nx + b.vy * ny
        if vn >= 0.0:
            return
        c = self.cfg
        jn = -(1.0 + c.e_border) * vn * b.mass
        b.vx += jn / b.mass * nx
        b.vy += jn / b.mass * ny
        tx, ty = -ny, nx
        vt_contact = b.vx * tx + b.vy * ty - b.omega * b.radius
        jt = -vt_contact * b.mass / 3.0          # 1/m + r^2/I = 3/m for a disc
        lim = c.contact_mu * jn
        jt = clamp(jt, -lim, lim)
        b.vx += jt / b.mass * tx
        b.vy += jt / b.mass * ty
        b.omega += -b.radius * jt / b.inertia
        if not quiet and -vn > 20.0:
            events.append(PhysEvent(self.tick, "border", b.id, None, -vn, b.x, b.y))

    def _pair(self, a: Body, b: Body, events: list[PhysEvent],
              first_contact: list[str | None]) -> None:
        dx, dy = b.x - a.x, b.y - a.y
        rr = a.radius + b.radius
        if dx > rr or dx < -rr or dy > rr or dy < -rr:
            return
        d2 = dx * dx + dy * dy
        if d2 >= rr * rr:
            return
        d = math.sqrt(d2)
        if d < 1e-9:
            nx, ny = 1.0, 0.0  # deterministic fallback for exact overlap
            d = 0.0
        else:
            nx, ny = dx / d, dy / d
        overlap = rr - d
        tm = a.mass + b.mass
        a.x -= nx * overlap * b.mass / tm
        a.y -= ny * overlap * b.mass / tm
        b.x += nx * overlap * a.mass / tm
        b.y += ny * overlap * a.mass / tm

        c = self.cfg
        vn = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny
        if vn >= 0.0:
            return
        inv_m = 1.0 / a.mass + 1.0 / b.mass
        jn = -(1.0 + c.e_coin_coin) * vn / inv_m
        a.vx -= jn / a.mass * nx
        a.vy -= jn / a.mass * ny
        b.vx += jn / b.mass * nx
        b.vy += jn / b.mass * ny

        tx, ty = -ny, nx
        vt = ((b.vx * tx + b.vy * ty - b.omega * b.radius)
              - (a.vx * tx + a.vy * ty + a.omega * a.radius))
        k = 3.0 / a.mass + 3.0 / b.mass   # 1/ma + 1/mb + ra^2/Ia + rb^2/Ib
        jt = clamp(-vt / k, -c.contact_mu * jn, c.contact_mu * jn)
        a.vx -= jt / a.mass * tx
        a.vy -= jt / a.mass * ty
        b.vx += jt / b.mass * tx
        b.vy += jt / b.mass * ty
        a.omega += -jt * a.radius / a.inertia
        b.omega += -jt * b.radius / b.inertia

        if -vn > 15.0:
            px, py = a.x + nx * a.radius, a.y + ny * a.radius
            etype = "striker_hit" if "striker" in (a.kind, b.kind) else "coin_hit"
            events.append(PhysEvent(self.tick, etype, a.id, b.id, -vn, px, py))
        if first_contact[0] is None:
            if a.kind == "striker" and b.kind != "striker":
                first_contact[0] = b.id
            elif b.kind == "striker" and a.kind != "striker":
                first_contact[0] = a.id

    def _pockets(self, b: Body, events: list[PhysEvent], pocketed: list[Body]) -> None:
        for p in self.pockets:
            if math.hypot(b.x - p["x"], b.y - p["y"]) < p["radius"]:
                b.pocketed, b.pocket_id = True, p["id"]
                b.x, b.y = p["x"], p["y"]
                b.vx = b.vy = b.omega = 0.0
                pocketed.append(b)
                events.append(PhysEvent(self.tick, "pocket", b.id, p["id"], 0.0, p["x"], p["y"]))
                return

    # --------------------------------------------------------------- snapshots
    def snapshot(self) -> dict[str, Any]:
        c = self.cfg
        s = self.striker
        return {
            "dimensions": {"width": c.board_size, "height": c.board_size},
            "friction": c.friction,
            "pockets": [dict(p) for p in self.pockets],
            "coins": [
                {"id": b.id, "type": b.kind, "x": round(b.x, 2), "y": round(b.y, 2),
                 "vx": round(b.vx, 2), "vy": round(b.vy, 2), "is_pocketed": b.pocketed}
                for b in self.bodies if b.kind != "striker"
            ],
            "striker": {"x": round(s.x, 2), "y": round(s.y, 2), "vx": round(s.vx, 2),
                        "vy": round(s.vy, 2), "radius": s.radius, "is_in_hand": s.vx == 0 and s.vy == 0},
        }

    def load_coins(self, coins: list[dict[str, Any]]) -> None:
        """Overwrite coin state from a snapshot-style list (used by the CNN detector)."""
        by_id = {b.id: b for b in self.bodies}
        for cd in coins:
            b = by_id.get(cd["id"])
            if b is None:
                continue
            b.x, b.y = float(cd["x"]), float(cd["y"])
            b.vx, b.vy = float(cd.get("vx", 0.0)), float(cd.get("vy", 0.0))
            b.pocketed = bool(cd.get("is_pocketed", False))
