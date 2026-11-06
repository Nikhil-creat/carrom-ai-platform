import { useEffect, useRef, useState } from 'react';
import AIThoughtLogs from './AIThoughtLogs';
import CarromBoardCanvas from './CarromBoardCanvas';
import ControlBar from './ControlBar';
import ScoreCard from './ScoreCard';
import Toasts from './Toasts';
import WebcamDetectorOverlay from './WebcamDetectorOverlay';
import { useGame } from '../hooks/useWebSocket';
import { sound } from '../utils/soundEngine';
import type { Slot } from '../types';

const REASONS: Record<string, string> = {
  completed: 'All coins cleared',
  forfeit: 'A player left the game',
  stalemate: 'No progress for too long, decided on score',
};

function InviteBox() {
  const roomId = useGame((s) => s.roomId);
  const code = useGame((s) => s.roomCode);
  const toast = useGame((s) => s.pushToast);
  const link = `${location.origin}/?room=${roomId}${code ? `&code=${code}` : ''}`;
  const copy = async (text: string, what: string) => {
    try { await navigator.clipboard.writeText(text); toast(`${what} copied`, 'good'); }
    catch { toast('Copy failed, long-press the text instead', 'warn'); }
  };
  return (
    <div className="space-y-2 text-center">
      <p className="text-lg font-semibold">Waiting for an opponent…</p>
      {code && <button onClick={() => copy(code, 'Code')} className="mx-auto block rounded-xl bg-white/10 px-5 py-2 text-3xl font-extrabold tracking-[0.3em]">{code}</button>}
      <button onClick={() => copy(link, 'Invite link')} className="rounded-xl bg-sky-500 px-4 py-2 text-sm font-bold text-slate-900 active:scale-95">Copy invite link</button>
      <p className="text-xs text-slate-400">Send the link to a friend. They tap it and join this room.</p>
    </div>
  );
}

export default function GameScreen({ onLeave, onRematch }: { onLeave: () => void; onRematch?: () => void }) {
  const slot = useGame((s) => s.slot);
  const role = useGame((s) => s.role);
  const snapshot = useGame((s) => s.snapshot);
  const status = useGame((s) => s.status);
  const latency = useGame((s) => s.latencyMs);
  const error = useGame((s) => s.error);
  const gameOver = useGame((s) => s.gameOver);
  const userId = useGame((s) => s.userId);
  const log = useGame((s) => s.log);
  const emojis = useGame((s) => s.emojis);
  const sendChat = useGame((s) => s.sendChat);
  const [tab, setTab] = useState<'chat' | 'ai' | 'cam'>('chat');
  const [text, setText] = useState('');
  const [muted, setMuted] = useState(sound.muted);
  const chatEnd = useRef<HTMLDivElement>(null);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'nearest' }); }, [log.length, tab]);

  const me: Slot = slot ?? 'p1';
  const them: Slot = me === 'p1' ? 'p2' : 'p1';
  const players = snapshot?.players ?? {};
  const winnerName = gameOver?.winner ? players[gameOver.winner]?.name : null;
  const youWon = !!gameOver?.winner && gameOver.winner === slot;
  const newRating = gameOver?.ratings?.[userId];

  return (
    <div className="mx-auto w-full max-w-[1180px] px-3 pb-8 pt-3 lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start lg:gap-4">
      <Toasts />
      <div className="mx-auto flex w-full max-w-[640px] flex-col gap-3 lg:max-w-none">
        <header className="flex items-center justify-between">
          <button onClick={onLeave} className="rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold active:scale-95">‹ Leave</button>
          <div className="text-center leading-tight">
            <div className="font-extrabold tracking-tight">Carrom<span className="text-amber-400"> AI</span></div>
            <div className="text-[11px] text-slate-400"><span className={status === 'open' ? 'text-emerald-400' : 'text-amber-400'}>●</span> {status === 'open' ? `${latency} ms` : status}</div>
          </div>
          <button aria-label="Toggle sound" onClick={() => { sound.unlock(); sound.setMuted(!muted); setMuted(!muted); }} className="rounded-lg bg-white/10 px-3 py-1.5 text-sm active:scale-95">{muted ? '🔇' : '🔊'}</button>
        </header>

        {status === 'closed' && !gameOver && <div className="rounded-xl bg-amber-500/20 px-3 py-2 text-center text-sm text-amber-100">Connection lost. Reconnecting…</div>}
        {error && <div className="rounded-xl bg-rose-900/60 px-3 py-2 text-center text-sm text-rose-100">{error}</div>}

        <ScoreCard slot={them} isYou={false} />
        <div className="relative">
          <CarromBoardCanvas />
          <div className="pointer-events-none absolute inset-x-0 top-2 flex justify-center gap-2">
            {emojis.map((e) => <span key={e.id} title={e.from} className="animate-bounce rounded-full bg-black/60 px-3 py-1 text-2xl">{e.emoji}</span>)}
          </div>
          {snapshot?.status === 'WAITING' && <div className="absolute inset-0 grid place-items-center rounded-2xl bg-slate-950/80 p-4 backdrop-blur-sm"><InviteBox /></div>}
          {gameOver && (
            <div className="absolute inset-0 grid place-items-center rounded-2xl bg-slate-950/85 p-4 text-center backdrop-blur-sm">
              <div className="space-y-2">
                <div className="text-5xl">{!gameOver.winner ? '🤝' : youWon ? '🏆' : role === 'spectator' ? '🎉' : '😔'}</div>
                <div className="text-3xl font-extrabold">{!gameOver.winner ? 'Draw' : role === 'spectator' ? `${winnerName} wins` : youWon ? 'You win!' : 'You lose'}</div>
                <div className="text-sm text-slate-300">{REASONS[gameOver.reason] ?? gameOver.reason}</div>
                {newRating !== undefined && <div className="text-sm text-amber-300">New rating: {newRating}</div>}
                <div className="flex justify-center gap-2 pt-3">
                  {onRematch && <button onClick={onRematch} className="rounded-xl bg-gradient-to-r from-amber-400 to-orange-500 px-5 py-2.5 font-bold text-slate-900 active:scale-95">Rematch</button>}
                  <button onClick={onLeave} className="rounded-xl bg-white/10 px-5 py-2.5 font-semibold active:scale-95">Lobby</button>
                </div>
              </div>
            </div>
          )}
        </div>
        <ScoreCard slot={me} isYou={role === 'player'} />
        <ControlBar />
      </div>

      <aside className="mx-auto mt-3 w-full max-w-[640px] space-y-3 lg:mt-0 lg:max-w-none">
        <div className="grid grid-cols-3 gap-1 rounded-xl bg-white/5 p-1 text-sm">
          {(['chat', 'ai', 'cam'] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-lg px-3 py-1.5 font-semibold ${tab === t ? 'bg-amber-400 text-slate-900' : 'text-slate-300'}`}>{t === 'ai' ? 'AI thoughts' : t === 'cam' ? 'Camera' : 'Chat'}</button>
          ))}
        </div>
        {tab === 'ai' && <AIThoughtLogs />}
        {tab === 'cam' && <WebcamDetectorOverlay />}
        {tab === 'chat' && (
          <div className="flex h-72 flex-col rounded-2xl border border-white/10 bg-white/5 p-3 lg:h-[440px]">
            <div className="flex-1 space-y-1 overflow-y-auto text-sm">
              {log.length === 0 && <p className="text-slate-500">Match events and chat show up here.</p>}
              {log.map((l) => <div key={l.id} className={l.kind === 'chat' ? '' : 'text-slate-400'}>{l.from && <b className="text-amber-300">{l.from}: </b>}{l.text}</div>)}
              <div ref={chatEnd} />
            </div>
            <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); sendChat(text); setText(''); }}>
              <input className="min-w-0 flex-1 rounded-xl bg-slate-900 px-3 py-2 text-sm outline-none ring-1 ring-white/10 focus:ring-amber-400" placeholder="Message…" value={text} maxLength={300} onChange={(e) => setText(e.target.value)} />
              <button className="rounded-xl bg-sky-500 px-4 text-sm font-bold text-slate-900 active:scale-95">Send</button>
            </form>
          </div>
        )}
      </aside>
    </div>
  );
}
