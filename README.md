# Carrom AI Platform

Real-time multiplayer Carrom with an authoritative physics server, an agentic AI opponent
(Analyst → Tactician → Executor, with RAG and an optional RL residual policy), a computer-vision
board scanner, and a React canvas client.

```
Browser (React/Canvas) ──WS/REST──▶ nginx ──▶ FastAPI (physics + rules + AI) ──▶ Redis (pub/sub)
                                                      └──────────────────────▶ Postgres + pgvector
```

## Run it

```bash
cp .env.example .env
docker compose up --build                        # app on http://localhost (and https, self-signed)
docker compose --profile observability up        # + Prometheus, Grafana on :3000
```

Without Docker (backend needs Python 3.11+, frontend needs Node 20+):

```bash
cd backend  && pip install -r requirements.txt && uvicorn main:app --port 8000
cd frontend && npm install && npm run dev        # http://localhost:5173, proxies /api and /ws to :8000
cd backend  && pytest                            # 22 tests
```

Redis and Postgres are optional: with no `REDIS_URL` / `DATABASE_URL` the server uses in-process fan-out
and in-memory ratings + RAG store.

## How it fits together

| Area | Files | Notes |
|---|---|---|
| Physics | `backend/physics/engine.py` | Fixed 60 Hz tick, adaptive sub-steps (no tunnelling), impulse collisions with restitution (coin 0.85, border 0.78), Coulomb contact friction → spin, dynamic friction, pockets. Deterministic: same shot ⇒ same `final_hash`. |
| Rules | `backend/physics/rules.py` | Turns, striker baseline validation, fouls + penalty, queen cover, last-coin-before-queen, stalemate guard, win condition. |
| Server | `backend/multiplayer/manager.py` | Server runs the shot, streams 60 FPS frames in 100 ms chunks to players **and spectators**, 30 s turn timer, 30 s reconnect grace then forfeit, chat/emoji, ELO on rated games. |
| Anti-cheat | `backend/multiplayer/anti_cheat.py` | Clients only send a shot *request*; ranges, baseline, clock skew, replay, rate limit, checksum, 3 strikes ⇒ kick. |
| Lobby | `backend/multiplayer/lobby.py` | ELO, widening-window matchmaking queue, public rooms, 6-digit private codes. |
| AI | `backend/ai/agent_workflow.py` | Ghost-ball + cushion/double-bank unfolding candidates → RAG-based strategy priors → verification by simulating on a clone of the real engine. LangGraph graph when installed, identical built-in runner otherwise. |
| RAG | `backend/ai/rag_pipeline.py`, `database/vector_store.py` | Hashing embedder (offline) or sentence-transformers; in-memory or pgvector store. |
| Vision | `backend/ai/cnn_detector.py` | Lighting normalisation → board quad → perspective warp → per-colour segmentation + distance-transform peaks → optional CNN patch verifier. `POST /api/vision/detect`. |
| RL | `backend/ai/rl_policy.py` | PPO (one-step episodes against the real physics) trained as a bounded *residual* on top of the geometric shot. Identity until you train weights. |
| Client | `frontend/src` | Canvas renderer, slingshot aiming with ghost-ball preview, local striker prediction reconciled with server frames, Web Audio synth, AI thought log, webcam overlay. |

### Difficulty levels
`beginner`: direct shots only, no look-ahead, σ = 6° / 20 % force noise.
`intermediate`: direct + single bank, verified by 10 simulations/candidate with angle+force tweaks, small noise.
`grandmaster`: up to double banks, safeties, opponent-exposure look-ahead, zero noise.

### Wire protocol
`EXECUTE_SHOT`, the agent decision object and the board snapshot follow the schemas in your spec
(angle convention: 0 = +x, π/2 = toward the top of the screen). `client_checksum` is the first 8 hex chars of
`sha256("room|player|timestamp|x|angle|force|top|side")` with fixed decimals (see `anti_cheat.compute_checksum`
and `frontend/src/utils/checksum.ts`; verified identical).

## Honest status: what has and hasn't been verified

Verified by running it (in the build sandbox):
- Physics engine, rules, AI agents, lobby/ELO, WebSocket manager flow (via an in-memory fake socket): 22 passing tests, including determinism, restitution/momentum, pocketing, no-tunnelling, forfeit/reconnect, anti-cheat rejections, AI reply with decision schema.
- Four complete AI-vs-AI games run to completion with a coin-conservation invariant (grandmaster beats intermediate and beginner).
- CV pipeline on synthetic renders, including a JPEG with perspective, uneven lighting and noise.
- JS and Python checksums agree; aim geometry checked.

**Not** run — treat as unverified until you do:
- `docker compose up` (no Docker daemon was available); compose file only passes `docker compose config`; `nginx.conf` was not syntax-checked.
- `backend/main.py` against real FastAPI/uvicorn (FastAPI could not be installed; the file compiles and the vision function it calls was tested directly).
- Redis pub/sub, PostgreSQL/pgvector store and match persistence (code paths written, not exercised).
- Everything that needs PyTorch: `CoinPatchCNN`, `train_cnn`, `PolicyNet`, `train_ppo`. The agents and detector run without it.
- LangGraph graph path (the built-in runner that mirrors it is what was tested).
- The frontend was type-checked against stub typings and bundle-parsed with esbuild, but never installed (`npm install` was blocked), built with Vite, or opened in a browser. Expect small fixes on first run.

Known limitations
- Rules are a sensible simplification, **not** the official ICF rulebook; seed RAG "rules" are paraphrases written for this project. Ingest the real rulebook and reconcile `rules.py`.
- Physics constants are plausible, not calibrated against a real board.
- A room's game state lives in one process, so run **one backend worker** (or route a room consistently to one instance).
- The checksum is tamper-evidence, not a secret MAC; the real protection is that the server owns the physics.
- The 3D (Three.js) view mentioned in the spec is not implemented; the client is 2D canvas.
- Camera overlay is only aligned when the whole board fills the camera frame.
