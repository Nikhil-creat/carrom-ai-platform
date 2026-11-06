"""FastAPI gateway: REST (lobby, matchmaking, leaderboard) + WebSocket game hub."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from config import settings
from multiplayer.lobby import Lobby
from multiplayer.manager import Broadcaster, ConnectionManager

try:  # optional observability
    import structlog

    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    log = structlog.get_logger("carrom")
except Exception:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("carrom")

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

    WS_CONNECTIONS = Gauge("carrom_ws_connections", "Open WebSocket connections")
    SHOTS = Counter("carrom_shots_total", "Shots resolved")
    ROOMS = Gauge("carrom_rooms", "Active rooms")
except Exception:  # pragma: no cover
    generate_latest = None
    WS_CONNECTIONS = SHOTS = ROOMS = None


def _agent_factory(difficulty: str):
    from ai.agent_workflow import CarromAgent

    return CarromAgent(difficulty=difficulty)


_engine = None


async def _persist_result(room, winner_slot) -> None:
    """on_game_over hook: write the match to PostgreSQL (no-op when DATABASE_URL is unset)."""
    if _engine is None or room.game is None:
        return
    from database.models import record_match

    g = room.game
    p1, p2 = room.seats["p1"], room.seats["p2"]
    winner_id = room.seats[winner_slot].user_id if winner_slot else None
    ratings = {u: lobby.rating(u) for u in (p1.user_id, p2.user_id) if not u.startswith("ai_")}
    await asyncio.to_thread(
        record_match, _engine, room.meta.id, (p1.user_id, p1.name), (p2.user_id, p2.name), winner_id,
        (g.players["p1"].score, g.players["p2"].score), room.meta.rated and not room.meta.vs_ai,
        "completed" if g.winner else "forfeit", ratings,
    )


lobby = Lobby()
bus = Broadcaster(settings.redis_url)
manager = ConnectionManager(lobby, bus, agent_factory=_agent_factory, on_game_over=_persist_result)


def _init_database() -> None:
    """Create tables + pgvector extension and move the RAG store into Postgres. Falls back gracefully."""
    global _engine
    if not settings.database_url:
        return
    try:
        from ai import agent_workflow
        from ai.rag_pipeline import RagPipeline
        from database.models import init_db, make_engine
        from database.vector_store import PgVectorStore

        _engine = make_engine(settings.database_url)
        init_db(_engine)
        agent_workflow._RAG = RagPipeline(store=PgVectorStore(settings.database_url))
        log.info("postgres + pgvector ready")
    except Exception as exc:  # keep serving with in-memory stores
        _engine = None
        log.warning("database unavailable (%s); using in-memory stores", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_init_database)
    await bus.start()
    yield
    await bus.stop()


app = FastAPI(title="Carrom AI Platform", version="1.0.0", lifespan=lifespan)
origins = [o.strip() for o in settings.allowed_origins.split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_origin_regex=settings.allowed_origin_regex,
                   allow_methods=["*"], allow_headers=["*"])


class CreateRoom(BaseModel):
    host_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=40)
    private: bool = False
    vs_ai: bool = False
    difficulty: str = Field(default="intermediate", pattern="^(beginner|intermediate|grandmaster)$")


class JoinCode(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class QueueJoin(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="Player", max_length=32)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "carrom-ai-platform", "status": "ok", "health": "/health", "docs": "/docs"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "rooms": len(manager.rooms), "redis": bus._redis is not None}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    if generate_latest is None:
        raise HTTPException(503, "prometheus_client not installed")
    if ROOMS:
        ROOMS.set(len(manager.rooms))
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/rooms")
async def create_room(body: CreateRoom) -> dict[str, Any]:
    meta = lobby.create_room(body.host_id, body.name, body.private, body.vs_ai, body.difficulty)
    return {**meta.public_dict(), "code": meta.code}


@app.get("/api/rooms")
async def list_rooms() -> list[dict[str, Any]]:
    out = []
    for meta in lobby.rooms.values():
        if meta.private:
            continue
        room = manager.rooms.get(meta.id)
        p, s = room.counts() if room else (0, 0)
        out.append(meta.public_dict(p, s))
    return out


@app.post("/api/rooms/by-code")
async def room_by_code(body: JoinCode) -> dict[str, Any]:
    meta = lobby.find_by_code(body.code)
    if meta is None:
        raise HTTPException(404, "no room with that code")
    return {"room_id": meta.id, "name": meta.name}


@app.post("/api/queue")
async def queue_join(body: QueueJoin) -> dict[str, Any]:
    meta = lobby.enqueue(body.user_id, body.name)
    if meta:
        return {"status": "matched", "room_id": meta.id, "code": meta.code}
    return {"status": "waiting"}


@app.get("/api/queue/{user_id}")
async def queue_status(user_id: str) -> dict[str, Any]:
    return lobby.poll(user_id)


@app.delete("/api/queue/{user_id}")
async def queue_leave(user_id: str) -> dict[str, str]:
    lobby.leave_queue(user_id)
    return {"status": "left"}


_detector = None


def run_detection(data: bytes) -> dict[str, Any]:
    """Decode an image and run the CV pipeline (CPU-bound; called in a worker thread)."""
    global _detector
    import cv2
    import numpy as np

    from ai.cnn_detector import BoardDetector, to_physics_coins

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a decodable image")
    if _detector is None:
        _detector = BoardDetector()
    res = _detector.detect(img)
    return {"board_found": res.board_found, "detections": [d.to_dict() for d in res.detections],
            "coins": to_physics_coins(res)}


@app.post("/api/vision/detect")
async def vision_detect(request: Request) -> dict[str, Any]:
    """Raw image bytes in (jpeg/png), detected board state out. Max 8 MB."""
    data = await request.body()
    if not data or len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "image missing or larger than 8 MB")
    try:
        return await asyncio.to_thread(run_detection, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/leaderboard")
async def leaderboard() -> list[dict[str, Any]]:
    return lobby.leaderboard()


@app.websocket("/ws/{room_id}")
async def ws_endpoint(ws: WebSocket, room_id: str,
                      user_id: str = Query(..., min_length=1, max_length=64),
                      name: str = Query("Player", max_length=32),
                      role: str = Query("player", pattern="^(player|spectator)$"),
                      code: str | None = Query(None)) -> None:
    joined = await manager.connect(ws, room_id, user_id, name, role, code)
    if joined is None:
        return
    room, conn = joined
    if WS_CONNECTIONS:
        WS_CONNECTIONS.inc()
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 4096:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                if msg.get("event") == "EXECUTE_SHOT" and SHOTS:
                    SHOTS.inc()
                await manager.handle(room, conn, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if WS_CONNECTIONS:
            WS_CONNECTIONS.dec()
        await manager.disconnect(room, conn)
