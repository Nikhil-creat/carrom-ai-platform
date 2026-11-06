"""Runtime configuration, read from environment variables (see docker-compose.yml)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    redis_url: str | None = os.getenv("REDIS_URL") or None
    database_url: str | None = os.getenv("DATABASE_URL") or None
    turn_seconds: float = _f("TURN_SECONDS", 30.0)
    disconnect_grace_seconds: float = _f("DISCONNECT_GRACE_SECONDS", 30.0)
    # 1.0 = real time. Tests set a large value so streamed shots finish instantly.
    stream_speed: float = _f("STREAM_SPEED", 1.0)
    frames_per_chunk: int = int(os.getenv("FRAMES_PER_CHUNK", "6"))   # 6 @ 60 FPS = 100 ms
    ai_think_seconds: float = _f("AI_THINK_SECONDS", 1.0)
    max_shots_per_minute: int = int(os.getenv("MAX_SHOTS_PER_MINUTE", "20"))
    max_clock_skew_ms: int = int(os.getenv("MAX_CLOCK_SKEW_MS", "10000"))
    cheat_strikes_before_kick: int = int(os.getenv("CHEAT_STRIKES", "3"))
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "*")
    # Vercel gives every deployment its own URL (production alias, preview, team-scoped), so also accept these.
    allowed_origin_regex: str = os.getenv(
        "ALLOWED_ORIGIN_REGEX",
        r"https://([a-z0-9-]+\.)*vercel\.app|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    )
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "256"))


settings = Settings()
