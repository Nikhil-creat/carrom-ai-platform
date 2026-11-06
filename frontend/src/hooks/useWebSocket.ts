// Zustand store + WebSocket client + frame playback.
// Server -> client: the authoritative simulation arrives as chunked FRAMES (60 FPS); we buffer ~100 ms,
// then play them back. While waiting for the first chunk after firing, the striker is *predicted*
// locally (straight line, decaying) and then blended into the authoritative frames (reconciliation).
import { create } from 'zustand';
import { sound } from '../utils/soundEngine';
import { computeChecksum, q } from '../utils/checksum';
import {
  BASELINE_MAX_X, BASELINE_MIN_X, BASELINE_Y, MAX_IMPULSE, POCKETS, type AiDecision, type CoinType, type PhysEvent,
  type ServerMessage, type ShotPayload, type Slot, type Snapshot, type TurnSummary,
} from '../types';
import { angleToDir } from '../utils/aim';
import { WS_BASE } from '../config';

export interface Body { id: string; type: CoinType; x: number; y: number; pocketed: boolean }
export interface LogLine { id: number; kind: 'chat' | 'system' | 'turn'; from?: string; text: string }

interface Playback {
  order: string[]; frames: (number[] | undefined)[]; events: Map<number, PhysEvent[]>;
  next: number; total: number; received: number; startedAt: number | null; seq: number;
  offset: Record<string, { dx: number; dy: number }>;
}

interface Store {
  status: 'idle' | 'connecting' | 'open' | 'closed';
  userId: string; name: string; roomId: string | null; slot: Slot | null; role: 'player' | 'spectator';
  snapshot: Snapshot | null;
  bodies: Body[];
  animating: boolean;
  previewStriker: { x: number; y: number } | null;
  opponentPreview: { x: number; angle: number | null } | null;
  turnDeadline: number;       // performance.now() ms
  turnSeconds: number;
  thoughts: { seq: number; slot: Slot; decision: AiDecision }[];
  log: LogLine[];
  emojis: { id: number; emoji: string; from: string }[];
  lastTurn: TurnSummary | null;
  gameOver: { winner: Slot | null; reason: string; ratings?: Record<string, number> } | null;
  error: string | null;
  latencyMs: number;
  spectators: number;
  pendingShot: { x: number; angle: number; force: number; firedAt: number } | null;
  connect: (o: { roomId: string; userId: string; name: string; role?: 'player' | 'spectator'; code?: string }) => void;
  disconnect: () => void;
  fire: (shot: Omit<ShotPayload, 'top_spin' | 'side_spin'> & { top_spin?: number; side_spin?: number }) => void;
  sendPreview: (x: number, angle: number | null) => void;
  sendChat: (text: string) => void;
  sendEmoji: (emoji: string) => void;
  setPreviewStriker: (p: { x: number; y: number } | null) => void;
  tick: (nowMs: number) => PhysEvent[];
}

let ws: WebSocket | null = null;
let logId = 1, emojiId = 1, pingTimer: number | undefined, reconnectTimer: number | undefined, lastOpts: Parameters<Store['connect']>[0] | null = null;
let attempts = 0;
let pb: Playback = emptyPlayback();
const BUFFER_FRAMES = 6;

function emptyPlayback(): Playback {
  return { order: [], frames: [], events: new Map(), next: 0, total: 0, received: 0, startedAt: null, seq: -1, offset: {} };
}

function bodiesFromSnapshot(s: Snapshot): Body[] {
  if (!s.board) return [];
  const out: Body[] = s.board.coins.map((c) => ({ id: c.id, type: c.type, x: c.x, y: c.y, pocketed: c.is_pocketed }));
  out.push({ id: 'striker', type: 'striker', x: s.board.striker.x, y: s.board.striker.y, pocketed: false });
  return out;
}

export const useGame = create<Store>((set, get) => ({
  status: 'idle', userId: '', name: '', roomId: null, slot: null, role: 'player', snapshot: null, bodies: [],
  animating: false, previewStriker: null, opponentPreview: null, turnDeadline: 0, turnSeconds: 30,
  thoughts: [], log: [], emojis: [], lastTurn: null, gameOver: null, error: null, latencyMs: 0, spectators: 0, pendingShot: null,

  connect: (o) => {
    lastOpts = o;
    window.clearTimeout(reconnectTimer);
    ws?.close();
    set({ status: 'connecting', userId: o.userId, name: o.name, roomId: o.roomId, error: null, gameOver: null });
    const qs = new URLSearchParams({ user_id: o.userId, name: o.name, role: o.role ?? 'player' });
    if (o.code) qs.set('code', o.code);
    const sock = new WebSocket(`${WS_BASE}/ws/${o.roomId}?${qs}`);
    ws = sock;
    sock.onopen = () => {
      attempts = 0;
      set({ status: 'open' });
      window.clearInterval(pingTimer);
      pingTimer = window.setInterval(() => sock.readyState === 1 && sock.send(JSON.stringify({ event: 'PING', t: performance.now() })), 5000);
    };
    sock.onmessage = (e) => handle(JSON.parse(e.data) as ServerMessage, set, get);
    sock.onclose = (ev) => {
      window.clearInterval(pingTimer);
      set({ status: 'closed' });
      // auto-reconnect inside the server's 30 s grace window (not after kick / bad room / game over)
      if (ev.code < 4000 && lastOpts && !get().gameOver && attempts < 8) {
        attempts++;
        reconnectTimer = window.setTimeout(() => get().connect(lastOpts!), Math.min(4000, 500 * attempts));
      }
    };
  },

  disconnect: () => {
    lastOpts = null; window.clearTimeout(reconnectTimer); window.clearInterval(pingTimer);
    ws?.close(); ws = null; pb = emptyPlayback();
    set({ status: 'idle', roomId: null, slot: null, snapshot: null, bodies: [], thoughts: [], log: [], gameOver: null, animating: false });
  },

  fire: (s) => {
    const st = get();
    if (!ws || ws.readyState !== 1 || !st.slot || !st.roomId) return;
    const ts = Date.now();
    const fields = { x: q(s.striker_x, 2), angle: q(s.angle_rad, 6), force: q(Math.min(Math.max(s.impulse_force, 0), MAX_IMPULSE), 3), top: q(s.top_spin ?? 0, 3), side: q(s.side_spin ?? 0, 3) };
    const payload = {
      striker_position: { x: fields.x, y: BASELINE_Y[st.slot] }, angle_rad: fields.angle, impulse_force: fields.force,
      spin: { top_spin: fields.top, side_spin: fields.side },
      client_checksum: computeChecksum(st.roomId, st.userId, ts, fields),
    };
    ws.send(JSON.stringify({ event: 'EXECUTE_SHOT', room_id: st.roomId, player_id: st.userId, timestamp: ts, payload }));
    sound.play('striker_hit', 200 + fields.force * 30);
    set({ pendingShot: { x: fields.x, angle: fields.angle, force: fields.force, firedAt: performance.now() }, previewStriker: null });
  },

  sendPreview: (x, angle) => { if (ws?.readyState === 1) ws.send(JSON.stringify({ event: 'PLACE_STRIKER', x, angle_rad: angle })); },
  sendChat: (text) => { if (ws?.readyState === 1 && text.trim()) ws.send(JSON.stringify({ event: 'CHAT', text })); },
  sendEmoji: (emoji) => { if (ws?.readyState === 1) ws.send(JSON.stringify({ event: 'EMOJI', emoji })); },
  setPreviewStriker: (p) => set({ previewStriker: p }),

  /** Advance playback to `nowMs`. Returns physics events that are due (for sound/particles). */
  tick: (nowMs) => {
    const st = get();
    const due: PhysEvent[] = [];
    if (pb.total === 0 && !st.pendingShot) return due;

    if (pb.startedAt === null) {
      // not started: predict striker until the first chunk is buffered
      if (st.pendingShot && pb.received < BUFFER_FRAMES) {
        const p = st.pendingShot, t = (nowMs - p.firedAt) / 1000;
        const speed = (p.force / MAX_IMPULSE) * 2600, d = angleToDir(p.angle), travel = Math.min(speed * t * (1 - 0.25 * t), 120);
        const bodies = st.bodies.map((b) => (b.id === 'striker' ? { ...b, x: p.x + d.x * travel, y: BASELINE_Y[st.slot ?? 'p1'] + d.y * travel } : b));
        set({ bodies, animating: true });
        return due;
      }
      if (pb.received >= BUFFER_FRAMES || (pb.total > 0 && pb.received >= pb.total)) pb.startedAt = nowMs;
      else return due;
    }

    const target = Math.min(pb.total - 1, Math.floor(((nowMs - (pb.startedAt as number)) / 1000) * 60));
    if (target < pb.next) return due;
    // stall (not enough buffered) -> hold position rather than skip
    let last = pb.next - 1;
    for (let f = pb.next; f <= target; f++) {
      if (!pb.frames[f]) break;
      last = f;
      const evs = pb.events.get(f);
      if (evs) due.push(...evs);
    }
    if (last < pb.next) return due;
    pb.next = last + 1;
    const fr = pb.frames[last] as number[];
    const decay = Math.exp(-(nowMs - (pb.startedAt as number)) / 120);
    const bodies = st.bodies.map((b) => {
      const i = pb.order.indexOf(b.id);
      if (i < 0) return b;
      const x = fr[2 * i], y = fr[2 * i + 1];
      const o = pb.offset[b.id];
      const pocketed = b.pocketed || POCKETS.some((pk) => Math.abs(pk.x - x) < 0.5 && Math.abs(pk.y - y) < 0.5);
      return { ...b, x: x + (o ? o.dx * decay : 0), y: y + (o ? o.dy * decay : 0), pocketed };
    });
    set({ bodies, animating: true });
    return due;
  },
}));

type S = (p: Partial<Store>) => void;
type G = () => Store;

function addLog(get: G, set: S, kind: LogLine['kind'], text: string, from?: string): void {
  set({ log: [...get().log.slice(-99), { id: logId++, kind, from, text }] });
}

function handle(m: ServerMessage, set: S, get: G): void {
  switch (m.event) {
    case 'WELCOME':
      set({ slot: m.slot, role: m.role, snapshot: m.snapshot, bodies: bodiesFromSnapshot(m.snapshot), turnSeconds: m.turn_seconds });
      addLog(get, set, 'system', m.slot ? `You are ${m.slot === 'p1' ? 'White (bottom)' : 'Black (top)'}` : 'Spectating');
      break;
    case 'GAME_START': case 'STATE':
      if (!get().animating) set({ snapshot: m.snapshot, bodies: bodiesFromSnapshot(m.snapshot) }); else set({ snapshot: m.snapshot });
      break;
    case 'TURN_START':
      set({ snapshot: m.snapshot, turnDeadline: performance.now() + (m.turn_seconds ?? 30) * 1000, turnSeconds: m.turn_seconds ?? 30 });
      if (m.slot === get().slot) sound.play('turn');
      if (!get().animating) set({ bodies: bodiesFromSnapshot(m.snapshot) });
      break;
    case 'TURN_TIMEOUT':
      addLog(get, set, 'system', `${m.slot} ran out of time`);
      set({ snapshot: m.snapshot, bodies: bodiesFromSnapshot(m.snapshot) });
      break;
    case 'PLAYER_STATUS':
      if (m.spectators !== undefined) set({ spectators: m.spectators });
      if (m.slot && m.connected === false) addLog(get, set, 'system', `${m.slot} disconnected — ${m.grace_sec ?? 30}s to return`);
      break;
    case 'SHOT_START': {
      const prev = get().bodies;
      pb = emptyPlayback();
      pb.order = m.body_order; pb.total = m.ticks; pb.seq = m.seq; pb.frames = new Array(m.ticks);
      // reconciliation: remember where our prediction put the striker vs. where the server will say it is
      const ps = get().pendingShot;
      if (ps) { const b = prev.find((x) => x.id === 'striker'); if (b) pb.offset.striker = { dx: b.x - ps.x, dy: b.y - BASELINE_Y[m.slot] }; }
      if (m.ai) set({ thoughts: [...get().thoughts.slice(-19), { seq: m.seq, slot: m.slot, decision: m.ai }] });
      if (m.slot !== get().slot) sound.play('striker_hit', 500);
      set({ animating: true, pendingShot: null });
      break;
    }
    case 'FRAMES': {
      if (m.seq !== pb.seq) break;
      m.frames.forEach((f, i) => { pb.frames[m.start + i] = f; });
      pb.received += m.frames.length;
      for (const e of m.events) { const arr = pb.events.get(e.frame) ?? []; arr.push(e); pb.events.set(e.frame, arr); }
      break;
    }
    case 'TURN_RESULT': {
      const finish = () => {
        pb = emptyPlayback();
        set({ snapshot: m.snapshot, bodies: bodiesFromSnapshot(m.snapshot), animating: false, lastTurn: m.summary, pendingShot: null });
      };
      // let playback finish the last frames, then snap to the authoritative snapshot
      const wait = pb.total > 0 ? Math.max(0, ((pb.total - pb.next) / 60) * 1000 + 60) : 0;
      window.setTimeout(finish, Math.min(wait, 1500));
      if (m.summary.foul) { sound.play('foul'); addLog(get, set, 'turn', `Foul: ${m.summary.foul_reason}`); }
      if (m.summary.queen_event) addLog(get, set, 'turn', `Queen ${m.summary.queen_event}`);
      if (m.summary.pocketed.length) addLog(get, set, 'turn', `${m.summary.slot} pocketed ${m.summary.pocketed.join(', ')}`);
      break;
    }
    case 'GAME_OVER':
      set({ snapshot: m.snapshot, gameOver: { winner: m.winner, reason: m.reason, ratings: m.ratings } });
      if (m.winner && m.winner === get().slot) sound.play('win');
      addLog(get, set, 'system', `Game over — ${m.winner ?? 'no winner'} (${m.reason})`);
      break;
    case 'STRIKER_PREVIEW': if (m.slot !== get().slot) set({ opponentPreview: { x: m.x, angle: m.angle_rad } }); break;
    case 'CHAT': addLog(get, set, 'chat', m.text, m.from); sound.play('chat'); break;
    case 'EMOJI': {
      const id = emojiId++;
      set({ emojis: [...get().emojis.slice(-6), { id, emoji: m.emoji, from: m.from }] });
      window.setTimeout(() => set({ emojis: get().emojis.filter((x) => x.id !== id) }), 2600);
      break;
    }
    case 'PONG': set({ latencyMs: Math.round(performance.now() - m.t) }); break;
    case 'KICKED': set({ error: `Removed from the room: ${m.reason}` }); break;
    case 'ERROR': {
      const snap = get().snapshot;   // a rejected shot: drop the local prediction and show the real board
      if (get().pendingShot && snap) set({ bodies: bodiesFromSnapshot(snap), animating: false });
      set({ error: m.message, pendingShot: null });
      window.setTimeout(() => get().error === m.message && set({ error: null }), 3500);
      break;
    }
  }
}

export const clampX = (x: number): number => Math.min(BASELINE_MAX_X, Math.max(BASELINE_MIN_X, x));
