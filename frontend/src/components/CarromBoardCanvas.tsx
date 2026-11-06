import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useGame, clampX } from '../hooks/useWebSocket';
import { usePhysics } from '../hooks/usePhysics';
import { drawScene, emitBurst, screenToBoard, stepParticles, type Particle } from '../utils/renderEngine';
import { sound } from '../utils/soundEngine';
import { BASELINE_Y, STRIKER_R, type PhysEvent } from '../types';

const SIZE = 760; // CSS px of the square canvas (scaled by devicePixelRatio)

/**
 * Interaction (current player only, on their own turn):
 *  - click / drag along your baseline  -> move the striker
 *  - press ON the striker, drag back   -> slingshot: direction = opposite of the drag, power = drag length
 *  - release to fire, Esc to cancel
 * Player 2 sees the board rotated 180 degrees so their baseline is at the bottom as well.
 */
export default function CarromBoardCanvas() {
  const ref = useRef<HTMLCanvasElement>(null);
  const bodies = useGame((s) => s.bodies);
  const slot = useGame((s) => s.slot);
  const snapshot = useGame((s) => s.snapshot);
  const fire = useGame((s) => s.fire);
  const sendPreview = useGame((s) => s.sendPreview);
  const opp = useGame((s) => s.opponentPreview);
  const { aim, setAim, derived, reset } = usePhysics(bodies, slot);
  const [dragging, setDragging] = useState<'none' | 'slide' | 'aim'>('none');
  const particles = useRef<Particle[]>([]);
  const live = useRef({ derived, aim, bodies, slot, snapshot, opp });
  live.current = { derived, aim, bodies, slot, snapshot, opp };

  const myTurn = !!slot && snapshot?.game_flow?.current_turn === slot && snapshot.status === 'IN_PROGRESS';
  const busy = useGame((s) => s.animating || !!s.pendingShot);
  const canShoot = myTurn && !busy;

  // render + playback loop
  useEffect(() => {
    const cvs = ref.current!;
    const ctx = cvs.getContext('2d')!;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    cvs.width = SIZE * dpr; cvs.height = SIZE * dpr;
    ctx.scale(dpr, dpr);
    let raf = 0, last = performance.now(), shake = 0;
    const loop = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      const events: PhysEvent[] = useGame.getState().tick(now);
      for (const e of events) {
        sound.play(e.type, e.intensity);
        if (e.type === 'pocket') { emitBurst(particles.current, e.x, e.y, '#ffd166', 22, 160); shake = 6; }
        else if (e.type === 'border') emitBurst(particles.current, e.x, e.y, '#c9a26b', 4, 70);
        else emitBurst(particles.current, e.x, e.y, '#ffffff', 5 + Math.min(8, e.intensity / 120), 110);
      }
      shake *= 0.85; stepParticles(particles.current, dt);
      const L = live.current;
      const cur = L.snapshot?.game_flow?.current_turn ?? null;
      const mine = !!L.slot && cur === L.slot && !useGame.getState().animating;
      const oppPreview = !mine && L.opp && cur && cur !== L.slot
        ? { x: L.opp.x, y: BASELINE_Y[cur] } : null;
      const strikerNow = L.bodies.find((b) => b.id === 'striker');
      drawScene(ctx, SIZE, {
        bodies: L.bodies, slot: L.slot,
        aim: mine && L.derived ? { path: L.derived.path, force01: L.derived.force01 } : null,
        previewStriker: mine ? { x: L.aim.strikerX, y: BASELINE_Y[L.slot!] } : oppPreview ?? (strikerNow ? { x: strikerNow.x, y: strikerNow.y } : null),
        particles: particles.current, highlightBaseline: cur && L.snapshot?.status === 'IN_PROGRESS' && !useGame.getState().animating ? cur : null,
        pulse: now / 1000, shake,
      });
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => { // keep the striker on the baseline between turns
    if (slot) setAim((a) => ({ ...a, strikerX: clampX(a.strikerX), pointer: null }));
  }, [snapshot?.game_flow?.current_turn, slot, setAim]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { reset(); setDragging('none'); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [reset]);

  const toBoard = (e: ReactPointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return screenToBoard(((e.clientX - r.left) / r.width) * SIZE, ((e.clientY - r.top) / r.height) * SIZE, SIZE, slot);
  };

  const onDown = (e: ReactPointerEvent) => {
    if (!canShoot || !slot) return;
    sound.unlock();
    ref.current!.setPointerCapture(e.pointerId);
    const p = toBoard(e), sy = BASELINE_Y[slot];
    if (Math.hypot(p.x - aim.strikerX, p.y - sy) <= STRIKER_R + 14) { setDragging('aim'); setAim((a) => ({ ...a, pointer: p })); }
    else if (Math.abs(p.y - sy) < 45) { setDragging('slide'); const x = clampX(p.x); setAim({ strikerX: x, pointer: null }); sendPreview(x, null); }
  };
  const onMove = (e: ReactPointerEvent) => {
    if (dragging === 'none' || !slot) return;
    const p = toBoard(e);
    if (dragging === 'slide') { const x = clampX(p.x); setAim({ strikerX: x, pointer: null }); sendPreview(x, null); }
    else setAim((a) => ({ ...a, pointer: p }));
  };
  const onUp = () => {
    if (dragging === 'aim' && derived && slot) {
      fire({ striker_x: aim.strikerX, angle_rad: derived.angle, impulse_force: Math.max(1, derived.force) });
      reset();
    }
    setDragging('none');
  };

  return (
    <div className="relative mx-auto w-full max-w-[760px]" style={{ aspectRatio: '1 / 1' }}>
      <canvas
        ref={ref}
        className={`h-full w-full touch-none rounded-xl shadow-2xl ${canShoot ? 'cursor-crosshair' : 'cursor-default'}`}
        style={{ width: '100%', height: '100%' }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
      />
      {myTurn && !busy && (
        <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-black/60 px-4 py-1 text-xs text-white">
          Your turn — drag along the baseline to position, pull the striker back to aim, release to shoot
        </div>
      )}
    </div>
  );
}
