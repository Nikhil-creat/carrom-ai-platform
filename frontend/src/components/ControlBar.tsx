import { useEffect, type ReactNode } from 'react';
import { useGame } from '../hooks/useWebSocket';
import { useAim, clampX } from '../hooks/useAim';
import { shotFromPull } from '../utils/aim';
import { BASELINE_Y } from '../types';

const EMOJIS = ['👏', '😮', '😂', '🔥', '😅', '🎯'];

export default function ControlBar() {
  const slot = useGame((s) => s.slot);
  const role = useGame((s) => s.role);
  const snapshot = useGame((s) => s.snapshot);
  const busy = useGame((s) => s.animating || !!s.pendingShot);
  const spectators = useGame((s) => s.spectators);
  const fire = useGame((s) => s.fire);
  const sendEmoji = useGame((s) => s.sendEmoji);
  const aim = useAim();

  const playing = snapshot?.status === 'IN_PROGRESS';
  const myTurn = !!slot && playing && snapshot?.game_flow?.current_turn === slot;
  const shot = slot ? shotFromPull(aim.strikerX, BASELINE_Y[slot], aim.pointer) : null;
  const canFire = myTurn && !busy && aim.locked && !!shot;
  const pct = Math.round((shot?.force01 ?? 0) * 100);

  const doFire = () => {
    if (!slot || !shot || !canFire) return;
    fire({ striker_x: aim.strikerX, angle_rad: shot.angle, impulse_force: Math.max(1, shot.force), top_spin: aim.topSpin, side_spin: aim.sideSpin });
    aim.clear();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || !myTurn || busy) return;
      const s = useAim.getState();
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        s.setStrikerX(clampX(s.strikerX + (e.key === 'ArrowLeft' ? -6 : 6)));
        s.setPointer(null); s.setLocked(false);
      } else if ((e.key === ' ' || e.key === 'Enter') && canFire) {
        e.preventDefault();
        doFire();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  let body: ReactNode;
  if (role === 'spectator') {
    body = <p className="py-1 text-center text-sm text-slate-300">👀 Spectating · {spectators} watching</p>;
  } else if (!playing) {
    body = <p className="py-1 text-center text-sm text-slate-400">{snapshot?.status === 'FINISHED' ? 'Game finished' : 'Waiting for the game to start…'}</p>;
  } else if (!myTurn) {
    body = (
      <p className="flex items-center justify-center gap-2 py-1 text-sm text-slate-300">
        Opponent is playing
        <span className="flex gap-1">{[0, 1, 2].map((i) => <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-amber-300" style={{ animationDelay: `${i * 0.15}s` }} />)}</span>
      </p>
    );
  } else if (busy) {
    body = <p className="py-1 text-center text-sm text-amber-200">Shot in progress…</p>;
  } else {
    body = (
      <div className="space-y-3">
        <div>
          <div className="mb-1 flex justify-between text-xs text-slate-300">
            <span>{aim.locked ? 'Aim locked. Press FIRE' : shot ? 'Release to lock aim' : 'Drag the striker back to aim'}</span>
            <span className="tabular-nums">Power {pct}%</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-white/10">
            <div className="h-full rounded-full transition-[width] duration-75" style={{ width: `${pct}%`, background: 'linear-gradient(90deg,#34d399,#fbbf24,#fb7185)' }} />
          </div>
        </div>
        <details className="rounded-xl bg-black/20 px-3 py-2 text-sm">
          <summary className="cursor-pointer select-none text-slate-300">Spin (optional){aim.topSpin || aim.sideSpin ? ' · on' : ''}</summary>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <label className="text-xs text-slate-400">Top / back spin ({aim.topSpin.toFixed(2)})
              <input type="range" min={-1} max={1} step={0.05} value={aim.topSpin} className="w-full accent-amber-400" onChange={(e) => aim.setSpin(Number(e.target.value), aim.sideSpin)} />
            </label>
            <label className="text-xs text-slate-400">Side spin ({aim.sideSpin.toFixed(2)})
              <input type="range" min={-1} max={1} step={0.05} value={aim.sideSpin} className="w-full accent-amber-400" onChange={(e) => aim.setSpin(aim.topSpin, Number(e.target.value))} />
            </label>
          </div>
        </details>
        <div className="flex gap-2">
          <button className="rounded-xl bg-white/10 px-4 py-3 text-sm font-semibold active:scale-95" onClick={() => aim.clear()}>Reset</button>
          <button disabled={!canFire} onClick={doFire}
            className={`flex-1 rounded-xl py-3 text-lg font-extrabold tracking-wide transition active:scale-95 ${canFire ? 'bg-gradient-to-r from-amber-400 to-orange-500 text-slate-900 shadow-lg shadow-orange-500/30' : 'bg-white/10 text-slate-500'}`}>
            FIRE 🎯
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-2xl border border-white/10 bg-white/5 p-3">
      {body}
      <div className="flex justify-center gap-1.5 pt-1">
        {EMOJIS.map((e) => <button key={e} className="rounded-lg bg-white/5 px-2 py-1 text-lg active:scale-90" onClick={() => sendEmoji(e)}>{e}</button>)}
      </div>
    </div>
  );
}
