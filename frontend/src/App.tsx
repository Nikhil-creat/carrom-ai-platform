import { useCallback, useEffect, useMemo, useState } from 'react';
import AIThoughtLogs from './components/AIThoughtLogs';
import CarromBoardCanvas from './components/CarromBoardCanvas';
import MultiplayerLobby, { type JoinRequest } from './components/MultiplayerLobby';
import WebcamDetectorOverlay from './components/WebcamDetectorOverlay';
import { useGame } from './hooks/useWebSocket';
import { sound } from './utils/soundEngine';

const EMOJIS = ['👏', '😮', '😂', '🔥', '😅', '🎯'];

function useUserId(): string {
  return useMemo(() => {
    try {
      let id = localStorage.getItem('carrom_user');
      if (!id) { id = 'usr_' + Math.random().toString(36).slice(2, 10); localStorage.setItem('carrom_user', id); }
      return id;
    } catch { return 'usr_' + Math.random().toString(36).slice(2, 10); }
  }, []);
}

function Timer() {
  const deadline = useGame((s) => s.turnDeadline);
  const [, force] = useState(0);
  useEffect(() => { const t = setInterval(() => force((n) => n + 1), 250); return () => clearInterval(t); }, []);
  const left = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
  return <span className={left <= 8 ? 'font-bold text-rose-400' : ''}>{deadline ? `${left}s` : '—'}</span>;
}

function Game({ onLeave }: { onLeave: () => void }) {
  const { snapshot, slot, role, status, log, emojis, error, gameOver, latencyMs, spectators, sendChat, sendEmoji } = useGame();
  const [text, setText] = useState('');
  const [muted, setMuted] = useState(false);
  const [tab, setTab] = useState<'ai' | 'chat' | 'cam'>('chat');
  const turn = snapshot?.game_flow?.current_turn;
  const players = snapshot?.players ?? {};
  const row = (s: 'p1' | 'p2') => {
    const p = players[s];
    return (
      <div className={`flex items-center justify-between rounded-lg px-3 py-2 ${turn === s ? 'bg-amber-500/20 ring-1 ring-amber-400' : 'bg-slate-800/60'}`}>
        <div>
          <div className="font-semibold">{p?.name ?? 'Waiting…'} {s === slot && <span className="text-xs text-emerald-300">(you)</span>} {p?.is_ai && <span className="text-xs text-sky-300">AI</span>}</div>
          <div className="text-xs text-slate-400">{p?.assigned_color ?? (s === 'p1' ? 'white' : 'black')} · fouls {p?.fouls ?? 0}{p?.connected === false ? ' · disconnected' : ''}</div>
        </div>
        <div className="text-2xl font-bold">{p?.score ?? 0}</div>
      </div>
    );
  };

  return (
    <div className="mx-auto grid max-w-[1200px] gap-4 p-4 lg:grid-cols-[1fr_360px]">
      <div className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-2">{row(slot === 'p2' ? 'p2' : 'p1')}{row(slot === 'p2' ? 'p1' : 'p2')}</div>
        <div className="relative">
          <CarromBoardCanvas />
          <div className="pointer-events-none absolute inset-x-0 top-2 flex justify-center gap-2">
            {emojis.map((e) => <span key={e.id} className="animate-bounce rounded-full bg-black/50 px-3 py-1 text-2xl" title={e.from}>{e.emoji}</span>)}
          </div>
          {snapshot?.status === 'WAITING' && <div className="absolute inset-0 grid place-items-center rounded-xl bg-black/60 text-lg">Waiting for an opponent… share the room link or code.</div>}
          {gameOver && (
            <div className="absolute inset-0 grid place-items-center rounded-xl bg-black/70 text-center">
              <div>
                <div className="text-3xl font-bold">{gameOver.winner ? (gameOver.winner === slot ? 'You win!' : role === 'spectator' ? `${players[gameOver.winner]?.name} wins` : 'You lose') : 'Game over'}</div>
                <div className="mt-1 text-slate-300">{gameOver.reason}</div>
                <button className="mt-4 rounded bg-amber-500 px-4 py-2 font-semibold text-slate-900" onClick={onLeave}>Back to lobby</button>
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-300">
          <span>{role === 'spectator' ? 'Spectating' : turn === slot ? 'Your turn' : "Opponent's turn"} · <Timer /></span>
          <span>{status} · {latencyMs} ms · {spectators} watching</span>
          <span className="flex gap-1">
            {EMOJIS.map((e) => <button key={e} className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => sendEmoji(e)}>{e}</button>)}
            <button className="rounded bg-slate-800 px-2 py-1" onClick={() => { sound.unlock(); sound.setMuted(!muted); setMuted(!muted); }}>{muted ? '🔇' : '🔊'}</button>
            <button className="rounded bg-rose-700 px-2 py-1" onClick={onLeave}>Leave</button>
          </span>
        </div>
        {error && <div className="rounded bg-rose-900/70 p-2 text-sm text-rose-100">{error}</div>}
      </div>

      <aside className="space-y-3">
        <div className="flex gap-1 text-sm">
          {(['chat', 'ai', 'cam'] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={`flex-1 rounded px-3 py-1 ${tab === t ? 'bg-amber-500 text-slate-900' : 'bg-slate-800'}`}>{t === 'ai' ? 'AI thoughts' : t === 'cam' ? 'Camera' : 'Chat & log'}</button>)}
        </div>
        {tab === 'ai' && <AIThoughtLogs />}
        {tab === 'cam' && <WebcamDetectorOverlay />}
        {tab === 'chat' && (
          <div className="flex h-[480px] flex-col rounded-lg bg-slate-800/60 p-3">
            <div className="flex-1 space-y-1 overflow-y-auto text-sm">
              {log.map((l) => <div key={l.id} className={l.kind === 'chat' ? '' : 'text-slate-400'}>{l.from && <b className="text-amber-300">{l.from}: </b>}{l.text}</div>)}
            </div>
            <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); sendChat(text); setText(''); }}>
              <input className="flex-1 rounded bg-slate-900 px-3 py-2 text-sm" placeholder="Message…" value={text} maxLength={300} onChange={(e) => setText(e.target.value)} />
              <button className="rounded bg-sky-600 px-3 text-sm">Send</button>
            </form>
          </div>
        )}
      </aside>
    </div>
  );
}

export default function App() {
  const userId = useUserId();
  const [name, setName] = useState(() => { try { return localStorage.getItem('carrom_name') || 'Player'; } catch { return 'Player'; } });
  const roomId = useGame((s) => s.roomId);
  const connect = useGame((s) => s.connect);
  const disconnect = useGame((s) => s.disconnect);

  useEffect(() => { try { localStorage.setItem('carrom_name', name); } catch { /* storage unavailable */ } }, [name]);
  const onJoin = useCallback((j: JoinRequest) => { sound.unlock(); connect({ roomId: j.roomId, userId, name, role: j.role, code: j.code }); }, [connect, userId, name]);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      {roomId ? <Game onLeave={disconnect} /> : <MultiplayerLobby userId={userId} name={name} setName={setName} onJoin={onJoin} />}
    </main>
  );
}
