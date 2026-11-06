"""Authoritative WebSocket game server.

Flow of a shot
--------------
1. Client sends EXECUTE_SHOT (the `shot_action.json` payload).
2. AntiCheat validates ranges / baseline / timestamp / replay / checksum.
3. The server runs the deterministic physics to completion (in a worker
   thread, a few ms) and gets the full frame list + events + rule outcome.
4. Frames are streamed to every socket (players + spectators) at 60 FPS in
   chunks of `frames_per_chunk` (default 6 = 100 ms), so the client buffers
   ~100 ms and plays smoothly. The client may predict the striker locally
   for instant feedback; the first FRAMES chunk reconciles it.
5. TURN_RESULT carries the rule outcome and the final authoritative snapshot.

Clients never send positions of coins, only a shot request.

Scaling note: Redis pub/sub fans messages out across API instances, but a
room's game object lives in the instance that created it, so route a room to
one instance (nginx `hash $uri consistent` on /ws/<room>, see nginx.conf).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from config import settings
from multiplayer.anti_cheat import AntiCheat, CheatViolation
from multiplayer.lobby import Lobby, RoomMeta
from physics.engine import PhysicsConfig, Shot
from physics.rules import CarromGame, RuleError, TurnOutcome

log = logging.getLogger("carrom.manager")

AI_USER_ID = "ai_agent_v1"
AI_NAME = "CarromMaster-AI"


# --------------------------------------------------------------------------- bus
class Broadcaster:
    """Room fan-out. Uses Redis pub/sub when REDIS_URL is set, else in-process."""

    def __init__(self, redis_url: str | None) -> None:
        self.redis_url = redis_url
        self.instance_id = uuid.uuid4().hex
        self._redis: Any = None
        self._task: asyncio.Task | None = None
        self._local: dict[str, set[WebSocket]] = {}

    async def start(self) -> None:
        if not self.redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore

            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            await self._redis.ping()
            self._task = asyncio.create_task(self._listen())
            log.info("redis pub/sub enabled")
        except Exception as exc:  # pragma: no cover - depends on infra
            log.warning("redis unavailable (%s); falling back to in-process fan-out", exc)
            self._redis = None

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._redis:
            await self._redis.aclose()

    def join(self, room_id: str, ws: WebSocket) -> None:
        self._local.setdefault(room_id, set()).add(ws)

    def leave(self, room_id: str, ws: WebSocket) -> None:
        self._local.get(room_id, set()).discard(ws)

    async def publish(self, room_id: str, message: dict[str, Any]) -> None:
        if self._redis is not None:
            await self._redis.publish(f"room:{room_id}", json.dumps(message, separators=(",", ":")))
        else:
            await self._deliver(room_id, json.dumps(message, separators=(",", ":")))

    async def _deliver(self, room_id: str, payload: str) -> None:
        dead = []
        for ws in list(self._local.get(room_id, ())):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._local.get(room_id, set()).discard(ws)

    async def _listen(self) -> None:  # pragma: no cover - depends on infra
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("room:*")
        async for msg in pubsub.listen():
            if msg.get("type") != "pmessage":
                continue
            await self._deliver(msg["channel"].split(":", 1)[1], msg["data"])


# -------------------------------------------------------------------------- room
@dataclass
class SeatInfo:
    user_id: str
    name: str
    is_ai: bool = False
    connected: bool = True


@dataclass
class Conn:
    ws: WebSocket
    user_id: str
    name: str
    slot: str | None  # "p1" | "p2" | None (spectator)


@dataclass
class Room:
    meta: RoomMeta
    cheat: AntiCheat
    game: CarromGame | None = None
    seats: dict[str, SeatInfo] = field(default_factory=dict)
    conns: dict[int, Conn] = field(default_factory=dict)
    busy: bool = False
    seq: int = 0
    turn_deadline: float = 0.0
    timer_task: asyncio.Task | None = None
    grace_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    ai_task: asyncio.Task | None = None
    agent: Any = None
    result_recorded: bool = False

    def counts(self) -> tuple[int, int]:
        players = sum(1 for c in self.conns.values() if c.slot)
        return players, len(self.conns) - players


GameOverHook = Callable[[Room, str | None], Awaitable[None]]


class ConnectionManager:
    def __init__(self, lobby: Lobby, bus: Broadcaster | None = None,
                 agent_factory: Callable[[str], Any] | None = None,
                 on_game_over: GameOverHook | None = None) -> None:
        self.lobby = lobby
        self.bus = bus or Broadcaster(settings.redis_url)
        self.rooms: dict[str, Room] = {}
        self.agent_factory = agent_factory
        self.on_game_over = on_game_over

    # --------------------------------------------------------------- plumbing
    def get_room(self, room_id: str) -> Room | None:
        if room_id in self.rooms:
            return self.rooms[room_id]
        meta = self.lobby.rooms.get(room_id)
        if meta is None:
            return None
        room = Room(meta=meta, cheat=AntiCheat(PhysicsConfig()))
        if meta.vs_ai:
            room.seats["p2"] = SeatInfo(AI_USER_ID, AI_NAME, is_ai=True)
            if self.agent_factory:
                room.agent = self.agent_factory(meta.difficulty)
        self.rooms[room_id] = room
        return room

    async def _send(self, ws: WebSocket, message: dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(message, separators=(",", ":")))
        except Exception:
            pass

    async def _bcast(self, room: Room, message: dict[str, Any]) -> None:
        await self.bus.publish(room.meta.id, message)

    def _snapshot(self, room: Room) -> dict[str, Any]:
        if room.game is None:
            return {"room_id": room.meta.id, "status": "WAITING", "players": {
                s: {"id": st.user_id, "name": st.name, "connected": st.connected}
                for s, st in room.seats.items()}}
        remaining = max(0.0, room.turn_deadline - time.monotonic()) if room.turn_deadline else 0.0
        snap = room.game.snapshot(turn_time_remaining=remaining)
        for s, st in room.seats.items():
            snap["players"][s]["connected"] = st.connected
            snap["players"][s]["is_ai"] = st.is_ai
            snap["players"][s]["rating"] = self.lobby.rating(st.user_id)
        return snap

    # ---------------------------------------------------------------- connect
    async def connect(self, ws: WebSocket, room_id: str, user_id: str, name: str,
                      role: str = "player", code: str | None = None) -> tuple[Room, Conn] | None:
        await ws.accept()
        room = self.get_room(room_id)
        if room is None:
            await self._send(ws, {"event": "ERROR", "code": "NO_SUCH_ROOM", "message": "room not found"})
            await ws.close(code=4404)
            return None
        if room.meta.private and room.meta.code != code and user_id not in {u for u, _ in room.meta.reserved.values()}:
            await self._send(ws, {"event": "ERROR", "code": "BAD_CODE", "message": "invalid access code"})
            await ws.close(code=4403)
            return None

        self.lobby.names[user_id] = name
        slot = self._assign_slot(room, user_id, name) if role == "player" else None
        conn = Conn(ws, user_id, name, slot)
        room.conns[id(ws)] = conn
        self.bus.join(room.meta.id, ws)

        if slot:
            room.seats[slot].connected = True
            task = room.grace_tasks.pop(slot, None)
            if task:
                task.cancel()
            await self._maybe_start(room)

        await self._send(ws, {"event": "WELCOME", "room_id": room.meta.id, "slot": slot,
                              "role": "player" if slot else "spectator",
                              "user_id": user_id, "snapshot": self._snapshot(room),
                              "turn_seconds": settings.turn_seconds})
        await self._bcast(room, {"event": "PLAYER_STATUS", "slot": slot, "user_id": user_id,
                                 "name": name, "connected": True,
                                 "spectators": room.counts()[1]})
        return room, conn

    def _assign_slot(self, room: Room, user_id: str, name: str) -> str | None:
        for slot, st in room.seats.items():  # reconnect
            if st.user_id == user_id and not st.is_ai:
                return slot
        for slot, (uid, _) in room.meta.reserved.items():  # matchmaking reservation
            if uid == user_id and slot not in room.seats:
                room.seats[slot] = SeatInfo(user_id, name)
                return slot
        reserved_slots = set(room.meta.reserved)
        for slot in ("p1", "p2"):
            if slot not in room.seats and slot not in reserved_slots:
                room.seats[slot] = SeatInfo(user_id, name)
                return slot
        return None  # becomes a spectator

    async def _maybe_start(self, room: Room) -> None:
        if room.game is not None or len(room.seats) < 2:
            return
        p1, p2 = room.seats["p1"], room.seats["p2"]
        room.game = CarromGame(room.meta.id, (p1.user_id, p1.name), (p2.user_id, p2.name))
        await self._bcast(room, {"event": "GAME_START", "snapshot": self._snapshot(room)})
        self._start_turn(room)

    # ------------------------------------------------------------ disconnects
    async def disconnect(self, room: Room, conn: Conn) -> None:
        room.conns.pop(id(conn.ws), None)
        self.bus.leave(room.meta.id, conn.ws)
        if conn.slot and conn.slot in room.seats:
            still = any(c.slot == conn.slot for c in room.conns.values())
            if not still:
                room.seats[conn.slot].connected = False
                await self._bcast(room, {"event": "PLAYER_STATUS", "slot": conn.slot,
                                         "user_id": conn.user_id, "connected": False,
                                         "grace_sec": settings.disconnect_grace_seconds})
                if room.game and room.game.status == "IN_PROGRESS":
                    room.grace_tasks[conn.slot] = asyncio.create_task(self._grace(room, conn.slot))
        if not room.conns and (room.game is None or room.game.status != "IN_PROGRESS"):
            self._cleanup(room)

    async def _grace(self, room: Room, slot: str) -> None:
        try:
            await asyncio.sleep(settings.disconnect_grace_seconds / max(settings.stream_speed, 1.0))
        except asyncio.CancelledError:
            return
        if room.game and room.game.status == "IN_PROGRESS" and not room.seats[slot].connected:
            room.game.forfeit(slot)
            await self._finish(room, reason="forfeit")

    def _cleanup(self, room: Room) -> None:
        for t in [room.timer_task, room.ai_task, *room.grace_tasks.values()]:
            if t:
                t.cancel()
        self.rooms.pop(room.meta.id, None)
        self.lobby.remove_room(room.meta.id)

    # -------------------------------------------------------------- messaging
    async def handle(self, room: Room, conn: Conn, msg: dict[str, Any]) -> None:
        ev = msg.get("event")
        if ev == "EXECUTE_SHOT":
            await self._on_shot(room, conn, msg)
        elif ev == "PLACE_STRIKER":  # live preview for spectators / opponent
            if conn.slot and room.game and room.game.current_turn == conn.slot and not room.busy:
                x = msg.get("x")
                if isinstance(x, (int, float)):
                    await self._bcast(room, {"event": "STRIKER_PREVIEW", "slot": conn.slot,
                                             "x": float(x), "angle_rad": msg.get("angle_rad")})
        elif ev == "CHAT":
            text = str(msg.get("text", ""))[:300].strip()
            if text:
                await self._bcast(room, {"event": "CHAT", "from": conn.name, "slot": conn.slot, "text": text})
        elif ev == "EMOJI":
            emoji = str(msg.get("emoji", ""))[:8]
            if emoji:
                await self._bcast(room, {"event": "EMOJI", "from": conn.name, "slot": conn.slot, "emoji": emoji})
        elif ev == "PING":
            await self._send(conn.ws, {"event": "PONG", "t": msg.get("t"), "server_time": int(time.time() * 1000)})
        elif ev == "SYNC":
            await self._send(conn.ws, {"event": "STATE", "snapshot": self._snapshot(room)})
        else:
            await self._send(conn.ws, {"event": "ERROR", "code": "UNKNOWN_EVENT", "message": str(ev)})

    async def _on_shot(self, room: Room, conn: Conn, msg: dict[str, Any]) -> None:
        if room.game is None or room.game.status != "IN_PROGRESS":
            return await self._err(conn, "NO_GAME", "game not in progress")
        if conn.slot is None:
            return await self._err(conn, "SPECTATOR", "spectators cannot shoot")
        if room.busy:
            return await self._err(conn, "BUSY", "a shot is already being resolved")
        if room.game.current_turn != conn.slot:
            return await self._err(conn, "NOT_YOUR_TURN", "wait for your turn")
        try:
            shot = room.cheat.validate_shot(room.meta.id, conn.user_id, conn.slot,
                                            room.game.baseline_y(conn.slot), msg)
        except CheatViolation as v:
            await self._err(conn, v.code, v.message)
            if room.cheat.should_kick(conn.user_id):
                await self._send(conn.ws, {"event": "KICKED", "reason": "repeated invalid actions"})
                await conn.ws.close(code=4408)
            return
        await self.execute_shot(room, conn.slot, shot)

    async def _err(self, conn: Conn, code: str, message: str) -> None:
        await self._send(conn.ws, {"event": "ERROR", "code": code, "message": message})

    # ------------------------------------------------------------- shot engine
    async def execute_shot(self, room: Room, slot: str, shot: Shot,
                           ai_decision: dict[str, Any] | None = None) -> None:
        assert room.game is not None
        room.busy = True
        self._cancel_timer(room)
        try:
            try:
                outcome: TurnOutcome = await asyncio.to_thread(room.game.play_shot, slot, shot)
            except RuleError as exc:
                room.busy = False
                self._start_turn(room)
                for c in room.conns.values():
                    if c.slot == slot:
                        await self._err(c, "ILLEGAL", str(exc))
                return
            room.seq += 1
            res = outcome.result
            await self._bcast(room, {
                "event": "SHOT_START", "seq": room.seq, "slot": slot, "ticks": res.ticks,
                "body_order": res.body_order,
                "shot": {"striker_x": shot.striker_x, "striker_y": shot.striker_y,
                         "angle_rad": shot.angle_rad, "impulse_force": shot.impulse_force,
                         "top_spin": shot.top_spin, "side_spin": shot.side_spin},
                "ai": ai_decision,
            })
            await self._stream_frames(room, outcome)
            snap = self._snapshot(room)
            await self._bcast(room, {"event": "TURN_RESULT", "seq": room.seq,
                                     "summary": outcome.summary(), "snapshot": snap})
        finally:
            room.busy = False
        if room.game.status == "FINISHED":
            await self._finish(room, reason=room.game.end_reason or "completed")
        else:
            self._start_turn(room)

    async def _stream_frames(self, room: Room, outcome: TurnOutcome) -> None:
        """Stream recorded frames at 60 FPS in chunks, paced against a fixed clock."""
        res = outcome.result
        n = settings.frames_per_chunk
        dt = (1.0 / 60.0) * n / max(settings.stream_speed, 1e-6)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for ci, start in enumerate(range(0, len(res.frames), n)):
            evs = [e.to_dict() | {"frame": e.tick - res.start_tick}
                   for e in res.events if start <= e.tick - res.start_tick < start + n]
            await self._bcast(room, {"event": "FRAMES", "seq": room.seq, "start": start,
                                     "frames": res.frames[start:start + n], "events": evs})
            delay = t0 + (ci + 1) * dt - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)

    # ------------------------------------------------------------- turn timing
    def _cancel_timer(self, room: Room) -> None:
        if room.timer_task:
            room.timer_task.cancel()
            room.timer_task = None
        room.turn_deadline = 0.0

    def _start_turn(self, room: Room) -> None:
        if room.game is None or room.game.status != "IN_PROGRESS":
            return
        self._cancel_timer(room)
        slot = room.game.current_turn
        seat = room.seats[slot]
        room.turn_deadline = time.monotonic() + settings.turn_seconds
        if seat.is_ai:
            room.ai_task = asyncio.create_task(self._ai_turn(room, slot))
        else:
            room.timer_task = asyncio.create_task(self._turn_timer(room, slot))
        asyncio.create_task(self._bcast(room, {"event": "TURN_START", "slot": slot,
                                               "turn_seconds": settings.turn_seconds,
                                               "snapshot": self._snapshot(room)}))

    async def _turn_timer(self, room: Room, slot: str) -> None:
        try:
            await asyncio.sleep(settings.turn_seconds / max(settings.stream_speed, 1.0))
        except asyncio.CancelledError:
            return
        if room.busy or room.game is None or room.game.current_turn != slot:
            return
        room.game.pass_turn("timeout")
        await self._bcast(room, {"event": "TURN_TIMEOUT", "slot": slot, "snapshot": self._snapshot(room)})
        self._start_turn(room)

    async def _ai_turn(self, room: Room, slot: str) -> None:
        try:
            await asyncio.sleep(settings.ai_think_seconds / max(settings.stream_speed, 1.0)
                                + random.uniform(0.0, 0.4) / max(settings.stream_speed, 1.0))
            if room.game is None or room.agent is None:
                return
            decision = await asyncio.to_thread(room.agent.decide, room.game, slot)
            if room.game.current_turn != slot or room.busy:
                return
            await self.execute_shot(room, slot, decision.shot, ai_decision=decision.to_dict())
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("AI turn failed; passing turn")
            if room.game and room.game.current_turn == slot:
                room.game.pass_turn("ai_error")
                self._start_turn(room)

    # ---------------------------------------------------------------- game end
    async def _finish(self, room: Room, reason: str) -> None:
        self._cancel_timer(room)
        if room.game is None:
            return
        winner_slot = room.game.winner
        payload: dict[str, Any] = {"event": "GAME_OVER", "reason": reason, "winner": winner_slot,
                                   "snapshot": self._snapshot(room)}
        if room.meta.rated and winner_slot and not room.result_recorded:
            loser_slot = "p2" if winner_slot == "p1" else "p1"
            w, l = room.seats[winner_slot], room.seats[loser_slot]
            if not w.is_ai and not l.is_ai:
                payload["ratings"] = dict(zip((w.user_id, l.user_id),
                                              self.lobby.record_result(w.user_id, l.user_id)))
        room.result_recorded = True
        await self._bcast(room, payload)
        if self.on_game_over:
            try:
                await self.on_game_over(room, winner_slot)
            except Exception:
                log.exception("on_game_over hook failed")

