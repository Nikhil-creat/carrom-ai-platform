"""Lobby: room registry, 6-digit private codes, ELO and the matchmaking queue."""
from __future__ import annotations

import random
import string
import time
import uuid
from dataclasses import dataclass, field

K_FACTOR = 32
DEFAULT_RATING = 1200


def expected_score(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def update_elo(ra: int, rb: int, score_a: float, k: int = K_FACTOR) -> tuple[int, int]:
    """score_a: 1 win, 0.5 draw, 0 loss for player A."""
    ea = expected_score(ra, rb)
    na = round(ra + k * (score_a - ea))
    nb = round(rb + k * ((1 - score_a) - (1 - ea)))
    return na, nb


@dataclass
class RoomMeta:
    id: str
    name: str
    private: bool = False
    code: str | None = None
    vs_ai: bool = False
    difficulty: str = "intermediate"
    rated: bool = True
    host_id: str | None = None
    created_at: float = field(default_factory=time.time)
    reserved: dict[str, tuple[str, str]] = field(default_factory=dict)  # slot -> (user_id, name)

    def public_dict(self, players: int = 0, spectators: int = 0) -> dict:
        return {
            "room_id": self.id, "name": self.name, "private": self.private,
            "vs_ai": self.vs_ai, "difficulty": self.difficulty if self.vs_ai else None,
            "rated": self.rated, "players": players, "spectators": spectators,
        }


@dataclass
class _QueueEntry:
    user_id: str
    name: str
    rating: int
    since: float


class Lobby:
    def __init__(self) -> None:
        self.ratings: dict[str, int] = {}
        self.names: dict[str, str] = {}
        self.rooms: dict[str, RoomMeta] = {}
        self.queue: list[_QueueEntry] = []
        self.matched: dict[str, str] = {}  # user_id -> room_id (consumed on first status poll)

    # -------------------------------------------------------------- ratings
    def rating(self, user_id: str) -> int:
        return self.ratings.setdefault(user_id, DEFAULT_RATING)

    def record_result(self, winner_id: str, loser_id: str) -> tuple[int, int]:
        rw, rl = update_elo(self.rating(winner_id), self.rating(loser_id), 1.0)
        self.ratings[winner_id], self.ratings[loser_id] = rw, rl
        return rw, rl

    def leaderboard(self, limit: int = 20) -> list[dict]:
        top = sorted(self.ratings.items(), key=lambda kv: -kv[1])[:limit]
        return [{"user_id": u, "name": self.names.get(u, u), "rating": r} for u, r in top]

    # ---------------------------------------------------------------- rooms
    def _new_code(self) -> str:
        used = {m.code for m in self.rooms.values() if m.code}
        while True:
            code = "".join(random.choices(string.digits, k=6))
            if code not in used:
                return code

    def create_room(self, host_id: str, name: str = "", private: bool = False,
                    vs_ai: bool = False, difficulty: str = "intermediate",
                    rated: bool | None = None) -> RoomMeta:
        rid = "carrom_room_" + uuid.uuid4().hex[:6]
        meta = RoomMeta(
            id=rid, name=name or f"{rid}", private=private,
            code=self._new_code() if private else None, vs_ai=vs_ai,
            difficulty=difficulty, host_id=host_id,
            rated=(not vs_ai) if rated is None else rated,
        )
        self.rooms[rid] = meta
        return meta

    def find_by_code(self, code: str) -> RoomMeta | None:
        for m in self.rooms.values():
            if m.code == code:
                return m
        return None

    def remove_room(self, room_id: str) -> None:
        self.rooms.pop(room_id, None)

    # ---------------------------------------------------------- matchmaking
    @staticmethod
    def _window(waited: float) -> float:
        return min(100.0 + 25.0 * waited, 800.0)

    def enqueue(self, user_id: str, name: str) -> RoomMeta | None:
        self.names[user_id] = name
        self.queue = [q for q in self.queue if q.user_id != user_id]
        me = _QueueEntry(user_id, name, self.rating(user_id), time.time())
        match = self._find_match(me)
        if match:
            self.queue.remove(match)
            meta = self.create_room(user_id, name="Ranked match", private=True, rated=True)
            meta.reserved = {"p1": (match.user_id, match.name), "p2": (user_id, name)}
            self.matched[match.user_id] = meta.id
            self.matched[user_id] = meta.id
            return meta
        self.queue.append(me)
        return None

    def _find_match(self, me: _QueueEntry) -> _QueueEntry | None:
        now = time.time()
        best, best_gap = None, 1e9
        for q in self.queue:
            gap = abs(q.rating - me.rating)
            allowed = max(self._window(now - q.since), self._window(0))
            if gap <= allowed and gap < best_gap:
                best, best_gap = q, gap
        return best

    def poll(self, user_id: str) -> dict:
        if user_id in self.matched:
            rid = self.matched.pop(user_id)
            meta = self.rooms.get(rid)
            return {"status": "matched", "room_id": rid, "code": meta.code if meta else None}
        for q in list(self.queue):
            if q.user_id == user_id:
                # widen the window over time: try to match against other waiters
                self.queue.remove(q)
                match = self._find_match(q)
                if match:
                    self.queue.remove(match)
                    meta = self.create_room(match.user_id, name="Ranked match", private=True, rated=True)
                    meta.reserved = {"p1": (match.user_id, match.name), "p2": (q.user_id, q.name)}
                    self.matched[match.user_id] = meta.id
                    return {"status": "matched", "room_id": meta.id, "code": meta.code}
                self.queue.append(q)
                return {"status": "waiting", "waited": round(time.time() - q.since, 1)}
        return {"status": "idle"}

    def leave_queue(self, user_id: str) -> None:
        self.queue = [q for q in self.queue if q.user_id != user_id]
