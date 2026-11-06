import { useEffect, useState } from 'react';
import { API_BASE } from '../config';

interface RoomRow { room_id: string; name: string; players: number; spectators: number; vs_ai: boolean; difficulty: string | null; rated: boolean }
export interface JoinRequest { roomId: string; role: 'player' | 'spectator'; code?: string }

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const r = await fetch(API_BASE + path, { headers: { 'Content-Type': 'application/json' }, ...init });
  if (!r.ok) throw new Error((await r.json().catch(() => ({ detail: r.statusText }))).detail ?? r.statusText);
  return r.json() as Promise<T>;
};

export default function MultiplayerLobby({ userId, name, setName, onJoin }: { userId: string; name: string; setName: (n: string) => void; onJoin: (j: JoinRequest) => void }) {
  const [rooms, setRooms] = useState<RoomRow[]>([]);
  const [board, setBoard] = useState<{ name: string; rating: number }[]>([]);
  const [code, setCode] = useState('');
  const [diff, setDiff] = useState<'beginner' | 'intermediate' | 'grandmaster'>('intermediate');
  const [queueing, setQueueing] = useState(false);
  const [err, setErr] = useState('');

  const refresh = () => {
    api<RoomRow[]>('/api/rooms').then(setRooms).catch(() => undefined);
    api<{ name: string; rating: number }[]>('/api/leaderboard').then(setBoard).catch(() => undefined);
  };
  useEffect(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t); }, []);

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

  const wrap = (fn: () => Promise<void>) => async () => { setErr(''); try { await fn(); } catch (e) { setErr((e as Error).message); } };

  const vsAi = wrap(async () => {
    const r = await api<{ room_id: string; code: string | null }>('/api/rooms', { method: 'POST', body: JSON.stringify({ host_id: userId, name: `${name} vs AI`, vs_ai: true, difficulty: diff }) });
    onJoin({ roomId: r.room_id, role: 'player' });
  });
  const createPrivate = wrap(async () => {
    const r = await api<{ room_id: string; code: string }>('/api/rooms', { method: 'POST', body: JSON.stringify({ host_id: userId, name: `${name}'s room`, private: true }) });
    onJoin({ roomId: r.room_id, role: 'player', code: r.code });
  });
  const createPublic = wrap(async () => {
    const r = await api<{ room_id: string }>('/api/rooms', { method: 'POST', body: JSON.stringify({ host_id: userId, name: `${name}'s room` }) });
    onJoin({ roomId: r.room_id, role: 'player' });
  });
  const joinCode = wrap(async () => {
    const r = await api<{ room_id: string }>('/api/rooms/by-code', { method: 'POST', body: JSON.stringify({ code }) });
    onJoin({ roomId: r.room_id, role: 'player', code });
  });
  const ranked = wrap(async () => {
    const r = await api<{ status: string; room_id?: string; code?: string }>('/api/queue', { method: 'POST', body: JSON.stringify({ user_id: userId, name }) });
    if (r.status === 'matched' && r.room_id) onJoin({ roomId: r.room_id, role: 'player', code: r.code ?? undefined }); else setQueueing(true);
  });
  const cancelQueue = wrap(async () => { await api(`/api/queue/${userId}`, { method: 'DELETE' }); setQueueing(false); });

  const btn = 'rounded-lg px-4 py-2 text-sm font-semibold transition disabled:opacity-40';
  return (
    <div className="mx-auto grid max-w-5xl gap-6 p-6 md:grid-cols-3">
      <section className="space-y-4 md:col-span-2">
        <h1 className="text-3xl font-bold">Carrom<span className="text-amber-400"> AI</span></h1>
        <label className="block text-sm text-slate-300">Display name
          <input className="mt-1 w-full rounded bg-slate-800 px-3 py-2" value={name} maxLength={24} onChange={(e) => setName(e.target.value)} />
        </label>

        <div className="rounded-xl bg-slate-800/60 p-4">
          <h2 className="mb-2 font-semibold">Play the AI</h2>
          <div className="flex flex-wrap items-center gap-2">
            {(['beginner', 'intermediate', 'grandmaster'] as const).map((d) => (
              <button key={d} onClick={() => setDiff(d)} className={`${btn} ${diff === d ? 'bg-amber-500 text-slate-900' : 'bg-slate-700'}`}>{d}</button>
            ))}
            <button onClick={vsAi} className={`${btn} bg-emerald-500 text-slate-900`}>Start</button>
          </div>
        </div>

        <div className="rounded-xl bg-slate-800/60 p-4">
          <h2 className="mb-2 font-semibold">Play a person</h2>
          <div className="flex flex-wrap gap-2">
            {queueing
              ? <button onClick={cancelQueue} className={`${btn} bg-rose-500`}>Searching… cancel</button>
              : <button onClick={ranked} className={`${btn} bg-sky-500 text-slate-900`}>Ranked matchmaking</button>}
            <button onClick={createPublic} className={`${btn} bg-slate-700`}>Create public room</button>
            <button onClick={createPrivate} className={`${btn} bg-slate-700`}>Create private room</button>
          </div>
          <div className="mt-3 flex gap-2">
            <input className="w-40 rounded bg-slate-900 px-3 py-2 tracking-widest" placeholder="6-digit code" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
            <button onClick={joinCode} disabled={code.length !== 6} className={`${btn} bg-slate-700`}>Join with code</button>
          </div>
        </div>

        <div className="rounded-xl bg-slate-800/60 p-4">
          <h2 className="mb-2 font-semibold">Public rooms</h2>
          {rooms.length === 0 && <p className="text-sm text-slate-400">No public rooms yet.</p>}
          <ul className="space-y-2">
            {rooms.map((r) => (
              <li key={r.room_id} className="flex items-center justify-between rounded bg-slate-900/60 px-3 py-2 text-sm">
                <span>{r.name} <span className="text-slate-400">· {r.players}/2 players · {r.spectators} watching{r.vs_ai ? ` · AI ${r.difficulty}` : ''}</span></span>
                <span className="flex gap-2">
                  {r.players < 2 && !r.vs_ai && <button className="rounded bg-emerald-600 px-2 py-1" onClick={() => onJoin({ roomId: r.room_id, role: 'player' })}>Join</button>}
                  <button className="rounded bg-slate-600 px-2 py-1" onClick={() => onJoin({ roomId: r.room_id, role: 'spectator' })}>Watch</button>
                </span>
              </li>
            ))}
          </ul>
        </div>
        {err && <p className="rounded bg-rose-900/60 p-2 text-sm text-rose-200">{err}</p>}
      </section>
      <aside className="rounded-xl bg-slate-800/60 p-4">
        <h2 className="mb-2 font-semibold">Leaderboard</h2>
        {board.length === 0 && <p className="text-sm text-slate-400">Play a ranked game to appear here.</p>}
        <ol className="space-y-1 text-sm">{board.map((b, i) => <li key={i} className="flex justify-between"><span>{i + 1}. {b.name}</span><span className="text-amber-300">{b.rating}</span></li>)}</ol>
      </aside>
    </div>
  );
}
