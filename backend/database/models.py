"""SQLAlchemy models: users, ELO ratings, matches, tournaments, strategy vectors.

Schema is created with `init_db()` (also enables the pgvector extension).
The live game server keeps ratings in memory (multiplayer/lobby.py) and
persists results through `record_match()` via the on_game_over hook.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover
    Vector = None  # type: ignore[assignment]

EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "256"))


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    rating: Mapped[int] = mapped_column(Integer, default=1200, index=True)
    games: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(String(64), index=True)
    p1_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    p2_id: Mapped[str] = mapped_column(String(64))  # may be the AI id, so not a FK
    winner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    p1_score: Mapped[int] = mapped_column(Integer, default=0)
    p2_score: Mapped[int] = mapped_column(Integer, default=0)
    rated: Mapped[bool] = mapped_column(default=True)
    reason: Mapped[str] = mapped_column(String(16), default="completed")
    finished_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    p1: Mapped[User] = relationship(foreign_keys=[p1_id])


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))
    bracket: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeDoc(Base):
    """RAG chunks (rules + tactics) with their embedding."""
    __tablename__ = "knowledge_docs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    if Vector is not None:
        embedding = mapped_column(Vector(EMBED_DIM))


class StrategyVector(Base):
    """Historical shot situations indexed for similarity search."""
    __tablename__ = "strategy_vectors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(32), index=True)
    outcome_value: Mapped[float] = mapped_column(Float, default=0.0)
    shot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    if Vector is not None:
        embedding = mapped_column(Vector(EMBED_DIM))


def make_engine(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", "")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True)


def init_db(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def record_match(engine, room_id: str, p1: tuple[str, str], p2: tuple[str, str], winner_id: str | None,
                 scores: tuple[int, int], rated: bool, reason: str, new_ratings: dict[str, int] | None) -> None:
    """Persist a finished game and update games/wins/rating for every human player (ids starting 'ai_' are skipped)."""
    with Session(engine) as s:
        for uid, name in (p1, p2):
            if uid.startswith("ai_"):
                continue
            u = s.get(User, uid) or User(id=uid, name=name, games=0, wins=0)
            u.name = name
            u.games = (u.games or 0) + 1
            if winner_id == uid:
                u.wins = (u.wins or 0) + 1
            if new_ratings and uid in new_ratings:
                u.rating = new_ratings[uid]
            s.add(u)
        s.flush()
        s.add(Match(room_id=room_id, p1_id=p1[0], p2_id=p2[0], winner_id=winner_id,
                    p1_score=scores[0], p2_score=scores[1], rated=rated, reason=reason))
        s.commit()
