import { useEffect, useState } from 'react';
import { useGame } from '../hooks/useWebSocket';
import type { Slot } from '../types';

function useNow(ms: number): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => { const t = setInterval(() => setNow(performance.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

/** Seconds left in the current turn, ticking 4x/second. */
export function useTurnLeft(): { left: number; frac: number } {
  const deadline = useGame((s) => s.turnDeadline);
  const total = useGame((s) => s.turnSeconds);
  const now = useNow(250);
  if (!deadline) return { left: 0, frac: 1 };
  const left = Math.max(0, (deadline - now) / 1000);
  return { left, frac: Math.min(1, left / Math.max(1, total)) };
}

export default function ScoreCard({ slot, isYou }: { slot: Slot; isYou: boolean }) {
  const snapshot = useGame((s) => s.snapshot);
  const { left, frac } = useTurnLeft();
  const p = snapshot?.players[slot];
  const color = p?.assigned_color ?? (slot === 'p1' ? 'white' : 'black');
  const mine = (snapshot?.board?.coins ?? []).filter((c) => c.type === color);
  const potted = mine.filter((c) => c.is_pocketed).length;
  const flow = snapshot?.game_flow;
  const queenMine = !!flow?.queen_covered && flow.queen_pocketed_by === slot;
  const active = flow?.current_turn === slot && snapshot?.status === 'IN_PROGRESS';
  const name = p?.name ?? 'Waiting…';

  return (
    <div className={`relative overflow-hidden rounded-2xl border px-3 py-2.5 transition ${active ? 'border-amber-400/70 bg-amber-400/10 shadow-lg shadow-amber-500/10' : 'border-white/10 bg-white/5'}`}>
      <div className="flex items-center gap-3">
        <div className={`grid h-10 w-10 shrink-0 place-items-center rounded-full text-sm font-bold ring-2 ${color === 'white' ? 'bg-gradient-to-br from-white to-stone-300 text-stone-800 ring-stone-300' : 'bg-gradient-to-br from-stone-700 to-stone-950 text-stone-100 ring-stone-600'}`}>
          {name.slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 truncate font-semibold">
            <span className="truncate">{name}</span>
            {isYou && <span className="rounded bg-emerald-500/20 px-1.5 text-[10px] font-bold uppercase text-emerald-300">You</span>}
            {p?.is_ai && <span className="rounded bg-sky-500/20 px-1.5 text-[10px] font-bold uppercase text-sky-300">AI</span>}
            {p?.connected === false && <span className="rounded bg-rose-500/20 px-1.5 text-[10px] font-bold uppercase text-rose-300">Offline</span>}
          </div>
          <div className="mt-1 flex items-center gap-1" aria-label={`${potted} of ${mine.length || 9} coins pocketed`}>
            {Array.from({ length: mine.length || 9 }, (_, i) => (
              <span key={i} className={`h-2.5 w-2.5 rounded-full border ${i < potted ? (color === 'white' ? 'border-stone-300 bg-stone-100' : 'border-stone-500 bg-stone-900') : 'border-white/20 bg-transparent'}`} />
            ))}
            {queenMine && <span className="ml-1 h-2.5 w-2.5 rounded-full border border-rose-300 bg-rose-600" title="Queen" />}
            {!!p?.fouls && <span className="ml-2 text-[10px] text-amber-300">{p.fouls} foul{p.fouls > 1 ? 's' : ''}</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="text-3xl font-extrabold leading-none tabular-nums">{p?.score ?? 0}</div>
          {active && <div className={`mt-1 text-xs tabular-nums ${left <= 8 ? 'font-bold text-rose-300' : 'text-amber-200'}`}>{Math.ceil(left)}s</div>}
        </div>
      </div>
      {active && (
        <div className="absolute inset-x-0 bottom-0 h-1 bg-white/10">
          <div className={`h-full transition-[width] duration-300 ${left <= 8 ? 'bg-rose-400' : 'bg-amber-400'}`} style={{ width: `${frac * 100}%` }} />
        </div>
      )}
    </div>
  );
}
