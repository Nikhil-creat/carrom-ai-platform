import { useEffect, useState } from 'react';
import { BACKEND_LABEL } from '../config';
import { useServerStatus, type ServerState } from '../hooks/useServerStatus';
import { api } from '../utils/api';
import type { Difficulty } from '../types';

interface RoomRow { room_id: string; name: string; players: number; spectators: number; vs_ai: boolean; difficulty: string | null; rated: boolean }
export interface JoinRequest { roomId: string; role: 'player' | 'spectator'; code?: string; ai?: Difficulty }

const LEVELS: { id: Difficulty; title: string; blurb: string; icon: string }[] = [
  { id: 'beginner', title: 'Beginner', blurb: 'Relaxed. Misses a lot.', icon: '🌱' },
  { id: 'intermediate', title: 'Solid', blurb: 'Accurate, uses banks.', icon: '🎯' },
  { id: 'grandmaster', title: 'Grandmaster', blurb: 'No mistakes. Trick shots.', icon: '👑' },
];

const card = 'rounded-2xl border border-white/10 bg-white/5 p-4 backdrop-blur';
const btn = 'rounded-xl px-4 py-3 text-sm font-bold transition active:scale-95 disabled:opacity-40 disabled:active:scale-100';

function StatusPill({ state, waited }: { state: ServerState; waited: number }) {
  const map: Record<ServerState, { dot: string; text: string; box: string }> = {
    online: { dot: 'bg-emerald-400', text: `Server online (${BACKEND_LABEL})`, box: 'border-emerald-400/30 bg-emerald-500/10 text-emerald-100' },
    checking: { dot: 'animate-pulse bg-slate-400', text: 'Checking server…', box: 'border-white/10 bg-white/5 text-slate-300' },
    waking: { dot: 'animate-pulse bg-amber-400', text: `Waking the server… ${waited}s. Free hosting sleeps when idle; buttons unlock automatically.`, box: 'border-amber-400/30 bg-amber-500/10 text-amber-100' },
    offline: { dot: 'bg-rose-400', text: 'Server unreachable. Check the backend on Render, then reload.', box: 'border-rose-400/30 bg-rose-500/10 text-rose-100' },
    unconfigured: { dot: 'bg-rose-400', text: 'Backend URL not set. Add VITE_BACKEND_URL in Vercel and redeploy.', box: 'border-rose-400/30 bg-rose-500/10 text-rose-100' },
  };
  const m = map[state];
  return <div className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-xs ${m.box}`}><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${m.dot}`} />{m.text}</div>;
}

export default function MultiplayerLobby({ userId, name, setName, onJoin }: { userId: string; name: string; setName: (n: string) => void; onJoin: (j: JoinRequest) => void }) {
  const { state, waited } = useServerStatus();
  const online = state === 'online';
  const [rooms, setRooms] = useState<RoomRow[]>([]);
  const [board, setBoard] = useState<{ name: string; rating: number }[]>([]);
  const [code, setCode] = useState('');
  const [diff, setDiff] = useState<Difficulty>('intermediate');
  const [priv, setPriv] = useState(false);
  const [queueing, setQueueing] = useState(false);
  const [working, setWorking] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!online) return;
    const refresh = () => {
      api<RoomRow[]>('/api/rooms').then(setRooms).catch(() => undefined);
      api<{ name: string; rating: number }[]>('/api/leaderboard').then(setBoard).catch(() => undefined);
    };
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [online]);

  useEffect(() => { // poll the matchmaking queue
    if (!queueing) return;
    const t = setInterval(async () => {
      try {
        const r = await api<{ status: string; room_id?: string; code?: string }>(`/api/queue/${userId}`);
        if (r.status === 'matched' && r.room_id) { setQueueing(false); onJoin({ roomId: r.room_id, role: 'player', code: r.code ?? undefined }); }
      } catch (e) { setErr((e as Error).message); }
    }, 1500);
    return () => clearInterval(t);
  }, [queueing, userId, onJoin]);

  const run = async (fn: () => Promise<void>) => {
    if (working) return;
    setErr(''); setWorking(true);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setWorking(false); }
  };

  const vsAi = () => run(async () => {
    const r = await api<{ room_id: string }>('/api/rooms', { method: 'POST', body: JSON.stringify({ host_id: userId, name: `${name} vs AI`, vs_ai: true, difficulty: diff }) });
    onJoin({ roomId: r.room_id, role: 'player', ai: diff });
  });
  const createRoom = () => run(async () => {
    const r = await api<{ room_id: string; code: string | null }>('/api/rooms', { method: 'POST', body: JSON.stringify({ host_id: userId, name: `${name}'s room`, private: priv }) });
    onJoin({ roomId: r.room_id, role: 'player', code: r.code ?? undefined });
  });
  const joinCode = () => run(async () => {
    const r = await api<{ room_id: string }>('/api/rooms/by-code', { method: 'POST', body: JSON.stringify({ code }) });
    onJoin({ roomId: r.room_id, role: 'player', code });
  });
  const ranked = () => run(async () => {
    const r = await api<{ status: string; room_id?: string; code?: string }>('/api/queue', { method: 'POST', body: JSON.stringify({ user_id: userId, name }) });
    if (r.status === 'matched' && r.room_id) onJoin({ roomId: r.room_id, role: 'player', code: r.code ?? undefined }); else setQueueing(true);
  });
  const cancelQueue = () => run(async () => { await api(`/api/queue/${userId}`, { method: 'DELETE' }); setQueueing(false); });

  return (
    <div className="mx-auto w-full max-w-5xl px-4 pb-10 pt-6">
      <header className="mb-5 flex items-center gap-3">
        <svg viewBox="0 0 64 64" className="h-11 w-11 shrink-0" aria-hidden="true">
          <rect width="64" height="64" rx="14" fill="#9b6b3a" />
          <rect x="6" y="6" width="52" height="52" rx="8" fill="#f1d9a6" />
          <circle cx="32" cy="32" r="12" fill="none" stroke="#b3402f" strokeWidth="2" />
          <circle cx="32" cy="32" r="4" fill="#c0392b" />
          <circle cx="24" cy="30" r="3.5" fill="#f4efe4" stroke="#8a7f66" /><circle cx="40" cy="34" r="3.5" fill="#25221f" />
          {([[11, 11], [53, 11], [11, 53], [53, 53]] as const).map(([x, y]) => <circle key={`${x}${y}`} cx={x} cy={y} r="4" fill="#1b1411" />)}
        </svg>
        <div>
          <h1 className="text-3xl font-extrabold leading-none tracking-tight">Carrom<span className="text-amber-400"> AI</span></h1>
          <p className="mt-1 text-sm text-slate-400">Real-time multiplayer · AI opponents</p>
        </div>
      </header>

      <div className="mb-4"><StatusPill state={state} waited={waited} /></div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <section className={card}>
            <label className="block text-sm text-slate-300">Your name
              <input className="mt-1 w-full rounded-xl bg-slate-900 px-3 py-2.5 text-base outline-none ring-1 ring-white/10 focus:ring-amber-400" value={name} maxLength={24} onChange={(e) => setName(e.target.value)} />
            </label>
          </section>

          <section className={card}>
            <h2 className="mb-3 text-lg font-bold">Play the AI</h2>
            <div className="grid grid-cols-3 gap-2">
              {LEVELS.map((l) => (
                <button key={l.id} onClick={() => setDiff(l.id)} className={`rounded-xl border p-2.5 text-left transition active:scale-95 ${diff === l.id ? 'border-amber-400 bg-amber-400/15' : 'border-white/10 bg-white/5'}`}>
                  <div className="text-xl">{l.icon}</div>
                  <div className="text-sm font-bold">{l.title}</div>
                  <div className="text-[11px] leading-tight text-slate-400">{l.blurb}</div>
                </button>
              ))}
            </div>
            <button disabled={!online || working} onClick={vsAi} className={`${btn} mt-3 w-full bg-gradient-to-r from-emerald-400 to-teal-500 py-3.5 text-base text-slate-900`}>{working ? 'Starting…' : 'Play vs AI'}</button>
          </section>

          <section className={card}>
            <h2 className="mb-3 text-lg font-bold">Play a friend</h2>
            <div className="grid grid-cols-2 gap-2">
              {queueing
                ? <button disabled={working} onClick={cancelQueue} className={`${btn} col-span-2 bg-rose-500`}>Searching for a match… tap to cancel</button>
                : <button disabled={!online || working} onClick={ranked} className={`${btn} col-span-2 bg-sky-500 text-slate-900`}>⚡ Ranked quick match</button>}
              <button disabled={!online || working} onClick={createRoom} className={`${btn} bg-white/10`}>Create room</button>
              <label className="flex items-center justify-center gap-2 rounded-xl bg-white/5 px-3 text-sm"><input type="checkbox" className="accent-amber-400" checked={priv} onChange={(e) => setPriv(e.target.checked)} />Private</label>
            </div>
            <div className="mt-3 flex gap-2">
              <input className="min-w-0 flex-1 rounded-xl bg-slate-900 px-3 py-3 text-center text-lg tracking-[0.35em] outline-none ring-1 ring-white/10 focus:ring-amber-400" placeholder="6-digit code" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
              <button disabled={!online || working || code.length !== 6} onClick={joinCode} className={`${btn} bg-white/10`}>Join</button>
            </div>
          </section>

          <section className={card}>
            <h2 className="mb-2 text-lg font-bold">Public rooms</h2>
            {rooms.length === 0 && <p className="text-sm text-slate-400">{online ? 'No public rooms yet. Create one!' : 'Rooms appear once the server is online.'}</p>}
            <ul className="space-y-2">
              {rooms.map((r) => (
                <li key={r.room_id} className="flex items-center justify-between gap-2 rounded-xl bg-slate-900/60 px-3 py-2 text-sm">
                  <span className="min-w-0 truncate">{r.name}<span className="block text-xs text-slate-400">{r.players}/2 players · {r.spectators} watching{r.vs_ai ? ` · AI ${r.difficulty}` : ''}</span></span>
                  <span className="flex shrink-0 gap-2">
                    {r.players < 2 && !r.vs_ai && <button className="rounded-lg bg-emerald-500 px-3 py-1.5 font-bold text-slate-900" onClick={() => onJoin({ roomId: r.room_id, role: 'player' })}>Join</button>}
                    <button className="rounded-lg bg-white/10 px-3 py-1.5" onClick={() => onJoin({ roomId: r.room_id, role: 'spectator' })}>Watch</button>
                  </span>
                </li>
              ))}
            </ul>
          </section>

          {err && <p className="rounded-xl bg-rose-900/60 p-3 text-sm text-rose-100">{err}</p>}
        </div>

        <aside className="space-y-4">
          <section className={card}>
            <h2 className="mb-2 text-lg font-bold">Leaderboard</h2>
            {board.length === 0 && <p className="text-sm text-slate-400">Win a ranked game to appear here.</p>}
            <ol className="space-y-1.5 text-sm">{board.map((b, i) => <li key={i} className="flex justify-between"><span>{['🥇', '🥈', '🥉'][i] ?? `${i + 1}.`} {b.name}</span><span className="font-bold text-amber-300">{b.rating}</span></li>)}</ol>
          </section>
          <section className={card}>
            <details>
              <summary className="cursor-pointer select-none text-lg font-bold">How to play</summary>
              <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-slate-300">
                <li>Pocket all your coins (White or Black). Pocketing your own coin scores 1 and you shoot again.</li>
                <li>The red queen is worth 3 but must be covered by pocketing one of your coins right after.</li>
                <li>Pocketing the striker is a foul: you lose a coin and the turn.</li>
                <li><b>Aim:</b> drag along your baseline to place the striker, pull the striker back to aim, release, then press FIRE.</li>
                <li>Dotted white line is your path, yellow line shows where the hit coin will go.</li>
              </ul>
            </details>
          </section>
        </aside>
      </div>
    </div>
  );
}
