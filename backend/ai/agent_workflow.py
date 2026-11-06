"""Multi-agent strategy engine: Analyst -> Tactician -> Executor.

State machine (LangGraph when installed, identical built-in runner otherwise):

    analyst -> tactician -> executor --(poor best shot, first time)--> analyst (widened search)
                                     \\--(otherwise)--> END

* Analyst    : pure geometry. Enumerates candidate shots with the ghost-ball
               method for direct pots, cushion banks and double banks (mirror
               "unfolding" of the pocket), checks line clearance and estimates
               the force needed. Also produces a board analysis.
* Tactician  : turns the analysis into a retrieval query, pulls tactics and
               rules from the RAG store and re-ranks candidates with the
               retrieved strategy priors.
* Executor   : verifies the top candidates by *simulating them on a clone of
               the authoritative physics + rules* (with small angle/force
               variations), optionally refines with the RL residual policy,
               then applies the difficulty's execution noise.

Difficulty:
    beginner      no look-ahead, direct shots only, large Gaussian noise
    intermediate  direct + single bank, verified by simulation, small noise
    grandmaster   direct + bank + double bank + safeties, wide verification,
                  opponent-exposure look-ahead, zero noise
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, TypedDict

from ai.rag_pipeline import Hit, RagPipeline
from ai.rl_policy import RLShotPolicy
from physics.engine import Shot
from physics.rules import CarromGame, TurnOutcome, other
from physics.vector import Vec2, clamp, dir_to_angle, point_segment_distance


# ------------------------------------------------------------------ difficulty
@dataclass(frozen=True)
class Profile:
    name: str
    angle_noise_deg: float
    force_noise: float
    max_bounces: int
    top_k: int
    variations: int
    safety: bool
    lookahead: bool


PROFILES = {
    "beginner": Profile("beginner", 6.0, 0.20, 0, 1, 0, False, False),
    "intermediate": Profile("intermediate", 0.9, 0.04, 1, 10, 6, False, False),
    "grandmaster": Profile("grandmaster", 0.0, 0.0, 2, 16, 10, True, True),
}

STRATEGIES = ("DIRECT_SHOT", "INDIRECT_BANK_SHOT", "DOUBLE_BANK_SHOT", "BREAK_SHOT",
              "SAFETY_PLACEMENT", "BLOCK_CLEAR", "QUEEN_COVER")


@dataclass
class Candidate:
    strategy: str
    coin_id: str | None
    pocket_id: str | None
    striker_x: float
    angle_rad: float
    force: float
    cut_deg: float = 0.0
    path_len: float = 0.0
    bounces: int = 0
    geom_score: float = 0.0
    priority: float = 0.0


@dataclass
class AgentDecision:
    shot: Shot
    strategy: str
    agent_id: str
    thought_chain: list[str]
    rag: dict[str, Any]
    expected_value: float
    simulations: int
    difficulty: str

    def to_dict(self) -> dict[str, Any]:
        """Matches the 'Agentic AI Decision & RAG Schema' of the spec (+ extras)."""
        return {
            "agent_id": self.agent_id,
            "selected_strategy": self.strategy,
            "rag_retrieval": self.rag,
            "shot_parameters": {
                "striker_x": round(self.shot.striker_x, 1),
                "aim_angle_deg": round(math.degrees(self.shot.angle_rad), 1),
                "force_percentage": round(self.shot.impulse_force / 20.0 * 100.0, 1),
            },
            "thought_chain": self.thought_chain,
            "difficulty": self.difficulty,
            "expected_value": round(self.expected_value, 2),
            "simulations": self.simulations,
        }


class AgentState(TypedDict, total=False):
    game: CarromGame
    slot: str
    widen: bool
    iteration: int
    analysis: dict[str, Any]
    candidates: list[Candidate]
    hits: list[Hit]
    rule_hit: Hit | None
    bias: dict[str, float]
    chosen: Candidate | None
    shot: Shot
    value: float
    sims: int
    thoughts: list[str]
    needs_replan: bool


# --------------------------------------------------------------------- geometry
def _walls(cfg) -> dict[str, tuple[str, float]]:
    rc, s = cfg.coin_radius, cfg.board_size
    return {"left": ("x", rc), "right": ("x", s - rc), "top": ("y", rc), "bottom": ("y", s - rc)}


def _mirror(p: Vec2, wall: str, W: dict[str, tuple[str, float]]) -> Vec2:
    axis, c = W[wall]
    return Vec2(2 * c - p.x, p.y) if axis == "x" else Vec2(p.x, 2 * c - p.y)


def _unfold(target: Vec2, walls: list[str], W) -> Vec2:
    """Reflect `target` across the walls in reverse hit order -> straight-line aim point."""
    for w in reversed(walls):
        target = _mirror(target, w, W)
    return target


def _trace(c: Vec2, u: Vec2, walls: list[str], W, pocket: Vec2, pocket_r: float) -> list[Vec2] | None:
    """Follow the coin's centre from `c` along `u`; return polyline ending in the pocket or None."""
    pts, pos, d, seq = [c], c, u, []
    for _ in range(len(walls)):
        best: tuple[float, str] | None = None
        for name, (axis, coord) in W.items():
            comp, p0 = (d.x, pos.x) if axis == "x" else (d.y, pos.y)
            if abs(comp) < 1e-9:
                continue
            t = (coord - p0) / comp
            if t > 1e-6 and (best is None or t < best[0]):
                best = (t, name)
        if best is None:
            return None
        t, name = best
        pos = pos + d * t
        pts.append(pos)
        seq.append(name)
        d = Vec2(-d.x, d.y) if W[name][0] == "x" else Vec2(d.x, -d.y)
    if seq != walls:
        return None
    t_star = (pocket - pos).dot(d)
    if t_star <= 0:
        return None
    end = pos + d * t_star
    if (end - pocket).length() > pocket_r * 0.8:
        return None
    lo, hi = W["left"][1], W["right"][1]
    if not (lo - 1 <= end.x <= hi + 1 and lo - 1 <= end.y <= hi + 1):
        return None
    pts.append(end)
    return pts


def _path_clear(pts: list[Vec2], obstacles: list[tuple[Vec2, float]], moving_r: float,
                slack: float = 1.0) -> str | None:
    """Return None if clear, else the id-less blocker marker index as string."""
    for a, b in zip(pts, pts[1:]):
        for i, (op, orad) in enumerate(obstacles):
            if point_segment_distance(op, a, b) < moving_r + orad - slack:
                return str(i)
    return None


def _speed_for_distance(dist_: float, v_end: float, decel: float) -> float:
    v = math.sqrt(v_end * v_end + 2.0 * decel * dist_)
    return v * (1.0 + 0.00035 * dist_)  # viscous damping fudge; simulation verifies


# ------------------------------------------------------------- candidate search
def generate_candidates(game: CarromGame, slot: str, max_bounces: int,
                        direct_only_for_opponent: bool = False) -> tuple[list[Candidate], dict[str, Any]]:
    phys, cfg = game.physics, game.cfg
    W = _walls(cfg)
    rc, rs = cfg.coin_radius, cfg.striker_radius
    color = game.players[slot].color
    yb = game.baseline_y(slot)
    decel = cfg.friction * cfg.friction_scale
    ms, mc = cfg.striker_mass, cfg.coin_mass
    transfer = (1 + cfg.e_coin_coin) * ms / (ms + mc)

    alive = [b for b in phys.bodies if not b.pocketed and b.kind != "striker"]
    targets = [b for b in alive if b.kind == color]
    pending = game.queen_state == "pending" and game.queen_owner == slot
    queen = next((b for b in alive if b.kind == "queen"), None)
    if queen is not None and not pending:
        targets.append(queen)

    routes: list[list[str]] = [[]]
    names = list(W)
    if max_bounces >= 1:
        routes += [[w] for w in names]
    if max_bounces >= 2:
        routes += [[a, b] for a in names for b in names if a != b]

    cands: list[Candidate] = []
    blockers: list[str] = []
    direct_total = direct_blocked = 0
    for coin in targets:
        c = Vec2(coin.x, coin.y)
        others = [(Vec2(o.x, o.y), o.radius) for o in alive if o is not coin]
        other_ids = [o.id for o in alive if o is not coin]
        strike_obs = others
        for pk in phys.pockets:
            pv = Vec2(pk["x"], pk["y"])
            for walls in routes:
                target = _unfold(pv, walls, W)
                vec = target - c
                if vec.length() < 1.0:
                    continue
                u = vec.normalized()
                pts = _trace(c, u, walls, W, pv, pk["radius"])
                if pts is None:
                    continue
                direct = not walls
                if direct:
                    direct_total += 1
                bad = _path_clear(pts, others, rc)
                if bad is not None:
                    if direct:
                        direct_blocked += 1
                        blockers.append(f"{coin.id}->{pk['id']} blocked by {other_ids[int(bad)]}")
                    continue
                g = c - u * (rc + rs)
                if not (rs <= g.x <= cfg.board_size - rs and rs <= g.y <= cfg.board_size - rs):
                    continue
                # striker x positions: the one that makes a straight (zero-cut) line, plus neighbours
                xs: list[float] = []
                if abs(u.y) > 1e-6:
                    t = (g.y - yb) / u.y
                    if t > 0:
                        xs.append(g.x - u.x * t)
                if not xs:
                    xs = [cfg.board_size / 2]
                base = clamp(xs[0], cfg.baseline_min_x, cfg.baseline_max_x)
                for x in sorted({round(base, 1), round(clamp(base - 45, cfg.baseline_min_x, cfg.baseline_max_x), 1),
                                 round(clamp(base + 45, cfg.baseline_min_x, cfg.baseline_max_x), 1)}):
                    s_pos = Vec2(x, yb)
                    if any((s_pos - Vec2(o.x, o.y)).length() < rs + o.radius + 0.5 for o in alive):
                        continue
                    sg = g - s_pos
                    d_sg = sg.length()
                    if d_sg < 1.0:
                        continue
                    ds = sg.normalized()
                    cut = math.degrees(math.acos(clamp(ds.dot(u), -1.0, 1.0)))
                    if cut > 72.0:
                        continue
                    if _path_clear([s_pos, g], [(p, r) for p, r in strike_obs if p != c], rs, slack=0.5) is not None:
                        continue
                    # required speeds, working backwards from the pocket
                    legs = [(a - b).length() for a, b in zip(pts[1:], pts[:-1])]
                    v_req = 40.0
                    for leg in reversed(legs):
                        v_req = _speed_for_distance(leg, v_req, decel)
                        if walls:
                            v_req /= 0.80
                    v_req = v_req / 0.80 if walls else v_req
                    v_hit = v_req / (transfer * max(math.cos(math.radians(cut)), 0.25))
                    v0 = _speed_for_distance(d_sg, v_hit, decel)
                    force = v0 / cfg.max_shot_speed * cfg.max_impulse
                    if force > cfg.max_impulse:
                        continue
                    force = max(force, 1.5)
                    total = sum(legs)
                    strat = ("DIRECT_SHOT", "INDIRECT_BANK_SHOT", "DOUBLE_BANK_SHOT")[min(len(walls), 2)]
                    if coin.kind == "queen":
                        strat = "QUEEN_COVER" if direct else strat
                    score = 5.0 - cut / 15.0 - total / 250.0 - 1.6 * len(walls) - force / 25.0
                    cands.append(Candidate(strat, coin.id, pk["id"], x, dir_to_angle(ds), force,
                                           cut, total, len(walls), score, score))
    cands.sort(key=lambda k: -k.geom_score)

    near_wall = sum(1 for b in targets if min(b.x, b.y, cfg.board_size - b.x, cfg.board_size - b.y) < 70)
    analysis = {
        "own_left": sum(1 for b in alive if b.kind == color),
        "opp_left": sum(1 for b in alive if b.kind not in (color, "queen")),
        "score_diff": game.players[slot].score - game.players[other(slot)].score,
        "opening": game.strike_count == 0,
        "direct_open": any(k.bounces == 0 for k in cands),
        "blocked_ratio": (direct_blocked / direct_total) if direct_total else 0.0,
        "near_wall_coins": near_wall,
        "has_scoring_candidate": bool(cands),
        "queen_on_board": queen is not None,
        "queen_exposed": any(k.coin_id == "q1" for k in cands),
        "queen_pending": pending,
        "blockers": blockers[:3],
    }
    if direct_only_for_opponent:
        return [k for k in cands if k.bounces == 0], analysis
    return cands, analysis


def fallback_candidates(game: CarromGame, slot: str, opening: bool) -> list[Candidate]:
    cfg = game.cfg
    base = math.pi / 2 if slot == "p1" else -math.pi / 2
    mid = cfg.board_size / 2
    out: list[Candidate] = []
    if opening:
        out.append(Candidate("BREAK_SHOT", None, None, mid, base, 13.0, geom_score=4.0, priority=4.0))
        out.append(Candidate("BREAK_SHOT", None, None, mid - 40, base - 0.04, 12.0, geom_score=3.6, priority=3.6))
    for x in (mid - 120, mid - 50, mid, mid + 50, mid + 120):
        for f in (4.5, 8.0):
            out.append(Candidate("SAFETY_PLACEMENT", None, None, x, base + (mid - x) * 0.0006 * (1 if slot == "p1" else -1),
                                 f, geom_score=-1.0, priority=-1.0))
    for x in (mid - 90, mid + 90):
        out.append(Candidate("BLOCK_CLEAR", None, None, x, base + (mid - x) * 0.0016 * (1 if slot == "p1" else -1),
                             10.0, geom_score=-0.5, priority=-0.5))
    return out


# ----------------------------------------------------------------- evaluation
def evaluate(out: TurnOutcome, slot: str) -> float:
    v = float(out.score_delta) - 1.0 * out.opp_pocketed
    if out.foul:
        v -= 4.0
    if out.next_turn == slot and not out.game_over:
        v += 1.2
    if out.queen_event == "pending":
        v += 1.0
    elif out.queen_event == "returned":
        v -= 1.0
    if out.game_over:
        v += 100.0 if out.winner == slot else -100.0
    return v


# -------------------------------------------------------------------- the agent
_RAG: RagPipeline | None = None


def shared_rag() -> RagPipeline:
    global _RAG
    if _RAG is None:
        _RAG = RagPipeline()
    return _RAG


class CarromAgent:
    def __init__(self, difficulty: str = "intermediate", rag: RagPipeline | None = None,
                 policy: RLShotPolicy | None = None, seed: int | None = None,
                 use_langgraph: bool = True) -> None:
        if difficulty not in PROFILES:
            raise ValueError(f"unknown difficulty {difficulty!r}")
        self.profile = PROFILES[difficulty]
        self.rag = rag or shared_rag()
        self.policy = policy or RLShotPolicy()
        self.rng = random.Random(seed)
        self.agent_id = f"carrom_agent_{difficulty}"
        self.graph = self._build_graph() if use_langgraph else None

    # -- nodes -----------------------------------------------------------------
    def analyst_node(self, st: AgentState) -> AgentState:
        game, slot = st["game"], st["slot"]
        bounces = max(self.profile.max_bounces, 2 if st.get("widen") else 0)
        cands, analysis = generate_candidates(game, slot, bounces)
        thoughts = list(st.get("thoughts", []))
        n_dir = sum(1 for c in cands if c.bounces == 0)
        thoughts.append(f"Analyst: {analysis['own_left']} own coins left; {len(cands)} geometric candidates "
                        f"({n_dir} direct, {len(cands) - n_dir} cushion shots).")
        for b in analysis["blockers"][:2]:
            thoughts.append(f"Detected blocked line: {b}.")
        if st.get("widen"):
            thoughts.append("Replanning with wider search (double banks enabled).")
        return {"analysis": analysis, "candidates": cands, "thoughts": thoughts}

    def tactician_node(self, st: AgentState) -> AgentState:
        analysis = st["analysis"]
        query = self.rag.describe_situation(analysis)
        hits = self.rag.retrieve(query, k=3, kind="tactic")
        rule_hits = self.rag.retrieve(query + (" queen cover" if analysis["queen_pending"] else " striker"), k=1, kind="rule")
        bias: dict[str, float] = {}
        for h in hits:
            strat = h.doc.meta.get("strategy")
            if strat:
                bias[strat] = bias.get(strat, 0.0) + h.score * float(h.doc.meta.get("bias", 0.0))
        cands: list[Candidate] = list(st["candidates"])
        if analysis["opening"] or not cands or not any(k.geom_score > 0 for k in cands) or self.profile.safety:
            cands += fallback_candidates(st["game"], st["slot"], analysis["opening"])
        for k in cands:
            k.priority = k.geom_score + bias.get(k.strategy, 0.0)
        if analysis["queen_pending"]:
            for k in cands:  # must cover with an own coin right now
                if k.coin_id and k.coin_id != "q1":
                    k.priority += 2.0
        cands.sort(key=lambda k: -k.priority)
        thoughts = list(st["thoughts"])
        if hits:
            thoughts.append(f"Tactician: queried RAG -> '{hits[0].doc.title}' (sim {hits[0].score:.2f}); "
                            f"strategy priors {{{', '.join(f'{s}: {b:+.2f}' for s, b in bias.items())}}}.")
        return {"hits": hits, "rule_hit": rule_hits[0] if rule_hits else None, "bias": bias,
                "candidates": cands, "thoughts": thoughts}

    def executor_node(self, st: AgentState) -> AgentState:
        game, slot, p = st["game"], st["slot"], self.profile
        cands: list[Candidate] = st["candidates"]
        thoughts = list(st["thoughts"])
        if not cands:
            cands = fallback_candidates(game, slot, False)
        sims = 0
        best: tuple[float, float, Candidate, Shot] | None = None

        def make_shot(k: Candidate, da: float = 0.0, fm: float = 1.0) -> Shot:
            return Shot(k.striker_x, game.baseline_y(slot), k.angle_rad + da,
                        clamp(k.force * fm, 1.0, game.cfg.max_impulse))

        if p.top_k == 1 and p.variations == 0:      # beginner: trust the geometry, no look-ahead
            k = cands[0]
            best = (k.priority, k.priority, k, make_shot(k))
            thoughts.append(f"Executor: no look-ahead at {p.name} level; playing '{k.strategy}'.")
        else:
            # interleave force and angle tweaks so even a short list probes both axes
            variants = [(0.0, 1.0), (0.0, 1.12), (math.radians(0.8), 1.0), (-math.radians(0.8), 1.0), (0.0, 0.90),
                        (math.radians(1.6), 1.0), (-math.radians(1.6), 1.0), (0.0, 1.25),
                        (math.radians(0.8), 1.12), (-math.radians(0.8), 1.12)][: max(1, p.variations)]
            for k in cands[: p.top_k]:
                for da, fm in variants:
                    shot = make_shot(k, da, fm)
                    g = game.clone()
                    try:
                        out = g.play_shot(slot, shot)
                    except Exception:
                        continue
                    sims += 1
                    val = evaluate(out, slot)
                    if p.lookahead and out.next_turn != slot and not out.game_over:
                        opp_c, _ = generate_candidates(g, out.next_turn, 0, direct_only_for_opponent=True)
                        val -= 0.35 * min(len(opp_c), 8) / 2.0   # fewer clean shots left for the opponent
                    key = (val, k.priority)
                    if best is None or key > (best[0], best[1]):
                        best = (val, k.priority, k, shot)
                if best is not None and best[0] >= 100:
                    break
            if best is None:
                k = cands[0]
                best = (0.0, k.priority, k, make_shot(k))
            if self.policy.trained and best is not None:
                refined = self.policy.refine(best[3], game, slot)
                out = game.clone().play_shot(slot, refined)
                sims += 1
                if evaluate(out, slot) > best[0]:
                    best = (evaluate(out, slot), best[1], best[2], refined)
                    thoughts.append("RL policy refined the shot parameters.")
            thoughts.append(f"Executor: simulated {sims} physics variations; selected '{best[2].strategy}'"
                            f"{' on ' + best[2].coin_id if best[2].coin_id else ''} (expected value {best[0]:+.2f}).")

        value, _, chosen, shot = best
        if p.angle_noise_deg > 0 or p.force_noise > 0:
            shot = Shot(shot.striker_x, shot.striker_y,
                        shot.angle_rad + math.radians(self.rng.gauss(0.0, p.angle_noise_deg)),
                        clamp(shot.impulse_force * (1.0 + self.rng.gauss(0.0, p.force_noise)), 1.0, game.cfg.max_impulse),
                        shot.top_spin, shot.side_spin)
            thoughts.append(f"Execution noise applied (σ={p.angle_noise_deg}°, {p.force_noise:.0%} force).")
        needs_replan = bool(value <= -1.0 and not st.get("widen") and p.max_bounces < 2 and p.top_k > 1)
        return {"chosen": chosen, "shot": shot, "value": value, "sims": sims,
                "thoughts": thoughts, "needs_replan": needs_replan}

    # -- graph -----------------------------------------------------------------
    def _route_after_executor(self, st: AgentState) -> str:
        return "analyst" if st.get("needs_replan") and st.get("iteration", 0) < 1 else "end"

    def _bump(self, st: AgentState) -> AgentState:
        return {"iteration": st.get("iteration", 0) + 1, "widen": True}

    def _build_graph(self):
        try:
            from langgraph.graph import END, StateGraph
        except Exception:
            return None
        g = StateGraph(AgentState)
        g.add_node("analyst", self.analyst_node)
        g.add_node("tactician", self.tactician_node)
        g.add_node("executor", self.executor_node)
        g.add_node("replan", self._bump)
        g.set_entry_point("analyst")
        g.add_edge("analyst", "tactician")
        g.add_edge("tactician", "executor")
        g.add_conditional_edges("executor",
                                lambda s: "replan" if s.get("needs_replan") and s.get("iteration", 0) < 1 else "end",
                                {"replan": "replan", "end": END})
        g.add_edge("replan", "analyst")
        return g.compile()

    def _run_builtin(self, st: AgentState) -> AgentState:
        while True:
            st = {**st, **self.analyst_node(st)}
            st = {**st, **self.tactician_node(st)}
            st = {**st, **self.executor_node(st)}
            if st.get("needs_replan") and st.get("iteration", 0) < 1:
                st = {**st, **self._bump(st)}
                continue
            return st

    # -- public ----------------------------------------------------------------
    def decide(self, game: CarromGame, slot: str) -> AgentDecision:
        work = game.clone(record_frames=False)
        st: AgentState = {"game": work, "slot": slot, "iteration": 0, "widen": False, "thoughts": []}
        final = self.graph.invoke(st) if self.graph is not None else self._run_builtin(st)
        chosen: Candidate = final["chosen"]
        hits: list[Hit] = final.get("hits", [])
        rule_hit: Hit | None = final.get("rule_hit")
        rag = {
            "rule_reference": rule_hit.doc.title if rule_hit else None,
            "matching_historical_vector_id": hits[0].doc.id if hits else None,
            "similarity_score": round(hits[0].score, 3) if hits else 0.0,
        }
        return AgentDecision(final["shot"], chosen.strategy, self.agent_id, final["thoughts"], rag,
                             final.get("value", 0.0), final.get("sims", 0), self.profile.name)
