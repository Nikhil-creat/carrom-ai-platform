import asyncio
import json
import time

from ai.agent_workflow import CarromAgent
from multiplayer.anti_cheat import compute_checksum
from multiplayer.lobby import Lobby, update_elo
from multiplayer.manager import Broadcaster, ConnectionManager


class FakeWS:
    def __init__(self):
        self.msgs = []
        self.closed = None

    async def accept(self):
        pass

    async def send_text(self, t):
        self.msgs.append(json.loads(t))

    async def close(self, code=1000):
        self.closed = code

    def events(self, name):
        return [m for m in self.msgs if m.get("event") == name]


def make_manager():
    lobby = Lobby()
    return lobby, ConnectionManager(lobby, Broadcaster(None),
                                    agent_factory=lambda d: CarromAgent(d, seed=3, use_langgraph=False))


def shot_msg(room_id, user_id, x=370.0, angle=1.5708, force=9.0, ts=None, bad_sum=False):
    ts = ts or int(time.time() * 1000)
    payload = {"striker_position": {"x": x, "y": 620.0}, "angle_rad": angle, "impulse_force": force,
               "spin": {"top_spin": 0.0, "side_spin": 0.0}}
    payload["client_checksum"] = "deadbeef" if bad_sum else compute_checksum(room_id, user_id, ts, payload)
    return {"event": "EXECUTE_SHOT", "room_id": room_id, "player_id": user_id, "timestamp": ts, "payload": payload}


async def wait_for(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


def test_elo_math():
    a, b = update_elo(1200, 1200, 1.0)
    assert a == 1216 and b == 1184


def test_human_vs_ai_full_turn_cycle_and_streaming():
    async def run():
        lobby, mgr = make_manager()
        meta = lobby.create_room("h1", vs_ai=True, difficulty="intermediate")
        ws = FakeWS()
        room, conn = await mgr.connect(ws, meta.id, "h1", "Nikhil")
        assert ws.events("WELCOME")[0]["slot"] == "p1"
        assert room.game is not None and ws.events("GAME_START")

        await mgr.handle(room, conn, shot_msg(meta.id, "h1"))
        assert await wait_for(lambda: ws.events("TURN_RESULT"))
        start = ws.events("SHOT_START")[0]
        frames = [f for m in ws.events("FRAMES") for f in m["frames"]]
        assert len(frames) == start["ticks"] and len(frames[0]) == 2 * len(start["body_order"])
        res = ws.events("TURN_RESULT")[0]
        assert res["snapshot"]["room_id"] == meta.id

        # if the turn passed, the AI must reply with a shot carrying its decision schema
        if res["summary"]["next_turn"] == "p2":
            assert await wait_for(lambda: len(ws.events("TURN_RESULT")) >= 2, 10)
            ai_start = ws.events("SHOT_START")[1]
            assert ai_start["slot"] == "p2" and ai_start["ai"]["selected_strategy"]
            assert "thought_chain" in ai_start["ai"] and "rag_retrieval" in ai_start["ai"]
    asyncio.run(run())


def test_anti_cheat_rejections():
    async def run():
        lobby, mgr = make_manager()
        meta = lobby.create_room("h1", vs_ai=True, difficulty="beginner")
        ws = FakeWS()
        room, conn = await mgr.connect(ws, meta.id, "h1", "H")
        await mgr.handle(room, conn, shot_msg(meta.id, "h1", bad_sum=True))
        await mgr.handle(room, conn, shot_msg(meta.id, "h1", force=99.0))
        await mgr.handle(room, conn, shot_msg(meta.id, "h1", x=20.0))
        await mgr.handle(room, conn, shot_msg(meta.id, "h1", ts=int(time.time() * 1000) - 60_000))
        codes = [m["code"] for m in ws.events("ERROR")]
        assert codes[:4] == ["BAD_CHECKSUM", "FORCE_RANGE", "BASELINE_X", "STALE_TIMESTAMP"]
        assert ws.events("KICKED")  # 3 strikes -> kicked
        assert not ws.events("SHOT_START")
    asyncio.run(run())


def test_replay_and_turn_order():
    async def run():
        lobby, mgr = make_manager()
        meta = lobby.create_room("h1", vs_ai=False)
        w1, w2, spec = FakeWS(), FakeWS(), FakeWS()
        room, c1 = await mgr.connect(w1, meta.id, "u1", "A")
        _, c2 = await mgr.connect(w2, meta.id, "u2", "B")
        _, cs = await mgr.connect(spec, meta.id, "u3", "Watcher", role="spectator")
        assert c1.slot == "p1" and c2.slot == "p2" and cs.slot is None
        await mgr.handle(room, c2, shot_msg(meta.id, "u2"))                 # not p2's turn
        assert w2.events("ERROR")[-1]["code"] == "NOT_YOUR_TURN"
        await mgr.handle(room, cs, shot_msg(meta.id, "u3"))
        assert spec.events("ERROR")[-1]["code"] == "SPECTATOR"
        m = shot_msg(meta.id, "u1")
        await mgr.handle(room, c1, m)
        assert await wait_for(lambda: w1.events("TURN_RESULT"))
        assert spec.events("FRAMES")                                          # spectators get the stream
        await mgr.handle(room, c1, m)                                         # replayed payload
        assert w1.events("ERROR")[-1]["code"] in ("REPLAY", "NOT_YOUR_TURN", "BUSY")
    asyncio.run(run())


def test_disconnect_grace_forfeit_updates_elo():
    async def run():
        lobby, mgr = make_manager()
        meta = lobby.create_room("u1", vs_ai=False)
        w1, w2 = FakeWS(), FakeWS()
        room, c1 = await mgr.connect(w1, meta.id, "u1", "A")
        _, c2 = await mgr.connect(w2, meta.id, "u2", "B")
        await mgr.disconnect(room, c2)
        assert w1.events("PLAYER_STATUS")[-1]["connected"] is False
        assert await wait_for(lambda: w1.events("GAME_OVER"), 5)
        over = w1.events("GAME_OVER")[0]
        assert over["winner"] == "p1" and over["reason"] == "forfeit"
        assert lobby.rating("u1") == 1216 and lobby.rating("u2") == 1184
    asyncio.run(run())


def test_reconnect_within_grace_keeps_game():
    async def run():
        lobby, mgr = make_manager()
        meta = lobby.create_room("u1", vs_ai=False)
        w1, w2 = FakeWS(), FakeWS()
        room, c1 = await mgr.connect(w1, meta.id, "u1", "A")
        _, c2 = await mgr.connect(w2, meta.id, "u2", "B")
        await mgr.disconnect(room, c2)
        w2b = FakeWS()
        _, c2b = await mgr.connect(w2b, meta.id, "u2", "B")
        assert c2b.slot == "p2"
        await asyncio.sleep(0.1)
        assert room.game.status == "IN_PROGRESS" and not w1.events("GAME_OVER")
    asyncio.run(run())


def test_matchmaking_and_private_codes():
    lobby = Lobby()
    assert lobby.enqueue("a", "A") is None
    meta = lobby.enqueue("b", "B")
    assert meta is not None and set(u for u, _ in meta.reserved.values()) == {"a", "b"}
    assert lobby.poll("a")["status"] == "matched"
    priv = lobby.create_room("a", private=True)
    assert len(priv.code) == 6 and lobby.find_by_code(priv.code).id == priv.id
