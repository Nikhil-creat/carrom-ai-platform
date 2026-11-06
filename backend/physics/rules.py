"""ICF-style rules enforcement layered on top of CarromPhysics.

Implemented rules (simplified but self-consistent; the official rulebook
should be ingested into the RAG store and any deviation reconciled here):

* p1 plays white from the bottom baseline, p2 plays black from the top.
* Pocketing your own coin scores 1 and keeps your turn.
* Pocketing an opponent's coin credits it to the opponent and (unless you also
  pocketed one of your own) ends your turn.
* Pocketing the striker is a foul: coins pocketed on that shot go back to the
  centre, one previously pocketed coin of the offender is returned as a
  penalty, and the turn passes.
* Queen: worth 3, must be "covered" by pocketing one of your own coins on the
  same shot or within `queen_cover_strikes` following strikes of the same
  visit. If not covered the queen returns to the centre and the turn ends.
* If a player pockets their last coin while the queen is still unresolved,
  that coin is returned to the centre. A player who clears all their coins
  (with the queen settled) wins.
* Stalemate guard (not an ICF rule): after `stall_limit` consecutive strikes in
  which no coin is pocketed, the game ends and the higher score wins (equal
  scores: draw). It keeps a pair of weak players from playing forever.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .engine import Body, CarromPhysics, PhysicsConfig, Shot, ShotResult


class RuleError(ValueError):
    """Raised for an illegal action (wrong turn, bad striker placement...)."""


@dataclass
class PlayerState:
    id: str
    name: str
    color: str  # "white" | "black"
    score: int = 0
    fouls: int = 0
    pocketed: list[str] = field(default_factory=list)  # coin ids credited to this player


@dataclass
class TurnOutcome:
    slot: str
    pocketed_ids: list[str]
    own_pocketed: int
    opp_pocketed: int
    queen_event: str | None      # "pending" | "covered" | "returned" | None
    foul: bool
    foul_reason: str | None
    score_delta: int
    returned_ids: list[str]
    next_turn: str
    game_over: bool
    winner: str | None
    result: ShotResult

    def summary(self) -> dict[str, Any]:
        return {
            "slot": self.slot, "pocketed": self.pocketed_ids,
            "own_pocketed": self.own_pocketed, "opp_pocketed": self.opp_pocketed,
            "queen_event": self.queen_event, "foul": self.foul,
            "foul_reason": self.foul_reason, "score_delta": self.score_delta,
            "returned": self.returned_ids, "next_turn": self.next_turn,
            "game_over": self.game_over, "winner": self.winner,
            "ticks": self.result.ticks, "final_hash": self.result.final_hash,
        }


def other(slot: str) -> str:
    return "p2" if slot == "p1" else "p1"


class CarromGame:
    def __init__(self, room_id: str, p1: tuple[str, str], p2: tuple[str, str],
                 config: PhysicsConfig | None = None, queen_cover_strikes: int = 1,
                 stall_limit: int = 60) -> None:
        self.room_id = room_id
        self.physics = CarromPhysics(config)
        self.cfg = self.physics.cfg
        self.players = {
            "p1": PlayerState(p1[0], p1[1], "white"),
            "p2": PlayerState(p2[0], p2[1], "black"),
        }
        self.current_turn = "p1"
        self.status = "IN_PROGRESS"
        self.winner: str | None = None
        self.queen_cover_strikes = queen_cover_strikes
        self.queen_state = "board"       # "board" | "pending" | "covered"
        self.queen_owner: str | None = None
        self._strikes_left = 0
        self.strike_count = 0
        self.stall_limit = stall_limit
        self.stalled = 0
        self.end_reason: str | None = None
        self.record_frames = True   # AI look-ahead clones switch this off
        self._prepare_striker()

    def clone(self, record_frames: bool = False) -> "CarromGame":
        g = CarromGame.__new__(CarromGame)
        g.room_id, g.cfg = self.room_id, self.cfg
        g.physics = self.physics.clone()
        g.players = {k: PlayerState(p.id, p.name, p.color, p.score, p.fouls, list(p.pocketed))
                     for k, p in self.players.items()}
        g.current_turn, g.status, g.winner = self.current_turn, self.status, self.winner
        g.queen_cover_strikes, g.queen_state, g.queen_owner = self.queen_cover_strikes, self.queen_state, self.queen_owner
        g._strikes_left, g.strike_count, g.record_frames = self._strikes_left, self.strike_count, record_frames
        g.stall_limit, g.stalled, g.end_reason = self.stall_limit, self.stalled, self.end_reason
        return g

    # ----------------------------------------------------------------- helpers
    def baseline_y(self, slot: str) -> float:
        return self.cfg.baseline_p1_y if slot == "p1" else self.cfg.baseline_p2_y

    def _prepare_striker(self) -> None:
        self.physics.place_striker(self.cfg.board_size / 2.0, self.baseline_y(self.current_turn))

    def validate_striker_placement(self, slot: str, x: float, y: float | None = None) -> None:
        c = self.cfg
        y = self.baseline_y(slot) if y is None else y
        if abs(y - self.baseline_y(slot)) > 1e-6:
            raise RuleError("striker must sit on your baseline")
        if not (c.baseline_min_x <= x <= c.baseline_max_x):
            raise RuleError("striker outside baseline limits")
        for b in self.physics.active():
            if b.kind == "striker":
                continue
            if math.hypot(b.x - x, b.y - y) < c.striker_radius + b.radius:
                raise RuleError("striker placement overlaps a coin")

    def _free_spot_near_centre(self, radius: float) -> tuple[float, float]:
        mid = self.cfg.board_size / 2.0
        others = [b for b in self.physics.active() if b.kind != "striker"]
        for ring in range(0, 12):
            n = max(1, ring * 6)
            for k in range(n):
                a = 2 * math.pi * k / n
                x, y = mid + ring * (radius * 2 + 1) * math.cos(a), mid + ring * (radius * 2 + 1) * math.sin(a)
                if all(math.hypot(o.x - x, o.y - y) >= radius + o.radius + 0.5 for o in others):
                    return x, y
        return mid, mid

    def _return_coin(self, b: Body) -> None:
        x, y = self._free_spot_near_centre(b.radius)
        b.x, b.y, b.vx, b.vy, b.omega = x, y, 0.0, 0.0, 0.0
        b.pocketed, b.pocket_id = False, None

    def _credit(self, b: Body, slot: str) -> int:
        pts = 3 if b.kind == "queen" else 1
        p = self.players[slot]
        p.score += pts
        p.pocketed.append(b.id)
        return pts

    def _uncredit(self, coin_id: str) -> None:
        for slot, p in self.players.items():
            if coin_id in p.pocketed:
                p.pocketed.remove(coin_id)
                p.score -= 3 if coin_id == "q1" else 1

    # -------------------------------------------------------------------- play
    def play_shot(self, slot: str, shot: Shot) -> TurnOutcome:
        if self.status != "IN_PROGRESS":
            raise RuleError("game is over")
        if slot != self.current_turn:
            raise RuleError("not your turn")
        shot.striker_y = self.baseline_y(slot)
        self.validate_striker_placement(slot, shot.striker_x, shot.striker_y)

        res = self.physics.simulate_shot(shot, record_frames=self.record_frames)
        me, opp = self.players[slot], self.players[other(slot)]
        pocketed = list(res.pocketed)
        striker_fell = any(b.kind == "striker" for b in pocketed)
        coins = [b for b in pocketed if b.kind != "striker"]
        own = [b for b in coins if b.kind == me.color]
        theirs = [b for b in coins if b.kind == opp.color]
        queen = [b for b in coins if b.kind == "queen"]
        self.strike_count += 1

        delta = 0
        queen_event: str | None = None
        foul_reason: str | None = None
        returned: list[str] = []
        keep_turn = False

        if striker_fell:
            foul_reason = "striker pocketed"
            me.fouls += 1
            for b in coins:
                self._return_coin(b)
                returned.append(b.id)
            if me.pocketed:  # penalty: give back one earlier coin
                penalty_id = me.pocketed[-1]
                pb = self.physics.body(penalty_id)
                if pb.kind != "queen":
                    self._uncredit(penalty_id)
                    self._return_coin(pb)
                    returned.append(pb.id)
                    delta -= 1
            if self.queen_state == "pending" and self.queen_owner == slot:
                self._return_queen()
                returned.append("q1")
                queen_event = "returned"
        else:
            for b in theirs:
                self._credit(b, other(slot))
            for b in own:
                delta += self._credit(b, slot)

            if self.queen_state == "pending" and self.queen_owner == slot:
                if own:
                    self.queen_state = "covered"
                    delta += self._credit(self.physics.body("q1"), slot)
                    queen_event = "covered"
                    keep_turn = True
                else:
                    self._strikes_left -= 1
                    if queen:  # cannot happen (queen already pocketed) but be safe
                        pass
                    if self._strikes_left <= 0:
                        self._return_queen()
                        returned.append("q1")
                        queen_event = "returned"
                    else:
                        keep_turn = True
            elif queen and self.queen_state == "board":
                if own:
                    self.queen_state = "covered"
                    self.queen_owner = slot
                    delta += self._credit(queen[0], slot)
                    queen_event = "covered"
                    keep_turn = True
                else:
                    self.queen_state = "pending"
                    self.queen_owner = slot
                    self._strikes_left = self.queen_cover_strikes
                    queen_event = "pending"
                    keep_turn = True

            if own and queen_event != "returned":
                keep_turn = True

            # last-coin-before-queen rule
            remaining_own = [b for b in self.physics.bodies
                             if b.kind == me.color and not b.pocketed]
            if not remaining_own and self.queen_state != "covered":
                last = own[-1] if own else None
                if last is not None:
                    self._uncredit(last.id)
                    self._return_coin(last)
                    returned.append(last.id)
                    delta -= 1
                    keep_turn = False

        self.stalled = 0 if coins else self.stalled + 1
        game_over, winner = self._check_game_over(slot)
        if game_over:
            self.end_reason = "completed"
        elif self.stalled >= self.stall_limit:
            game_over, self.end_reason = True, "stalemate"
            a, b = self.players["p1"].score, self.players["p2"].score
            winner = "p1" if a > b else "p2" if b > a else None
        if game_over:
            self.status, self.winner = "FINISHED", winner
        elif not keep_turn or striker_fell:
            self.current_turn = other(slot)
            self._strikes_left = 0 if self.queen_state != "pending" else self._strikes_left
        self._prepare_striker()

        return TurnOutcome(
            slot=slot, pocketed_ids=[b.id for b in pocketed], own_pocketed=len(own),
            opp_pocketed=len(theirs), queen_event=queen_event, foul=striker_fell,
            foul_reason=foul_reason, score_delta=delta, returned_ids=returned,
            next_turn=self.current_turn, game_over=game_over, winner=winner, result=res,
        )

    def _return_queen(self) -> None:
        self._return_coin(self.physics.body("q1"))
        self.queen_state, self.queen_owner, self._strikes_left = "board", None, 0

    def _check_game_over(self, slot: str) -> tuple[bool, str | None]:
        for s, p in self.players.items():
            left = [b for b in self.physics.bodies if b.kind == p.color and not b.pocketed]
            if not left and self.queen_state == "covered":
                return True, s
        return False, None

    def pass_turn(self, reason: str = "timeout") -> None:
        """Used by the turn timer: forfeit the visit (counts as a foul)."""
        if self.queen_state == "pending" and self.queen_owner == self.current_turn:
            self._return_queen()
        self.players[self.current_turn].fouls += 1
        self.current_turn = other(self.current_turn)
        self._prepare_striker()

    def forfeit(self, slot: str) -> None:
        self.status, self.winner, self.end_reason = "FINISHED", other(slot), "forfeit"

    # ---------------------------------------------------------------- snapshot
    def snapshot(self, turn_time_remaining: float = 0.0, tick: int | None = None) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "tick": self.physics.tick if tick is None else tick,
            "status": self.status,
            "board": self.physics.snapshot(),
            "players": {
                s: {"id": p.id, "name": p.name, "assigned_color": p.color,
                    "score": p.score, "fouls": p.fouls}
                for s, p in self.players.items()
            },
            "game_flow": {
                "current_turn": self.current_turn,
                "queen_covered": self.queen_state == "covered",
                "queen_pocketed_by": self.queen_owner if self.queen_state != "board" else None,
                "turn_time_remaining_sec": round(turn_time_remaining, 1),
            },
            "winner": self.winner,
        }
