import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useGame } from '../hooks/useWebSocket';
import { useAim, clampX } from '../hooks/useAim';
import { usePhysics } from '../hooks/usePhysics';
import { drawScene, emitBurst, screenToBoard, stepParticles, type Particle } from '../utils/renderEngine';
import { shotFromPull } from '../utils/aim';
import { sound } from '../utils/soundEngine';
import { BASELINE_Y, STRIKER_R, type PhysEvent } from '../types';

const SIZE = 760; // internal canvas resolution (CSS scales it to the container)
const buzz = (ms: number) => { try { navigator.vibrate?.(ms); } catch { /* unsupported */ } };

/**
 * Interaction (current player, on their own turn):
 *  - touch/click and drag along your baseline  -> move the striker
 *  - press ON the striker and pull back        -> slingshot aim: shoots the opposite way, power = pull length
 *  - release                                   -> aim is locked; press FIRE (or Space/Enter) to shoot
 * Player 2 sees the board rotated 180 degrees so their baseline is at the bottom too.
 */
export default function CarromBoardCanvas() {
  const ref = useRef<HTMLCanvasElement>(null);
  const bodies = useGame((s) => s.bodies);
  const slot = useGame((s) => s.slot);
  const snapshot = useGame((s) => s.snapshot);
  const sendPreview = useGame((s) => s.sendPreview);
  const opp = useGame((s) => s.opponentPreview);
  const busy = useGame((s) => s.animating || !!s.pendingShot);
  const strikerX = useAim((s) => s.strikerX);
  const pointer = useAim((s) => s.pointer);
  const { setStrikerX, setPointer, setLocked, clear } = useAim.getState();
  const derived = usePhysics(bodies, slot);
  const [dragging, setDragging] = useState<'none' | 'slide' | 'aim'>('none');
  const particles = useRef<Particle[]>([]);
  const prevStriker = useRef<{ x: number; y: number } | null>(null);
  const live = useRef({ derived, strikerX, bodies, slot, snapshot, opp });
  live.current = { derived, strikerX, bodies, slot, snapshot, opp };

  const myTurn = !!slot && snapshot?.game_flow?.current_turn === slot && snapshot.status === 'IN_PROGRESS';
  const canShoot = myTurn && !busy;

  // render + playback loop
  useEffect(() => {
    const cvs = ref.current!;
    const ctx = cvs.getContext('2d')!;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    cvs.width = SIZE * dpr; cvs.height = SIZE * dpr;
    let raf = 0, last = performance.now(), shake = 0;
    const loop = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      const events: PhysEvent[] = useGame.getState().tick(now);
      for (const e of events) {
        sound.play(e.type, e.intensity);
        if (e.type === 'pocket') { emitBurst(particles.current, e.x, e.y, '#ffd166', 22, 160); shake = 6; buzz(40); }
        else if (e.type === 'border') emitBurst(particles.current, e.x, e.y, '#c9a26b', 4, 70);
        else emitBurst(particles.current, e.x, e.y, '#ffffff', 5 + Math.min(8, e.intensity / 120), 110);
      }
      const L = live.current;
      const strikerNow = L.bodies.find((b) => b.id === 'striker');
      if (strikerNow && useGame.getState().animating) {            // dust trail behind a moving striker
        const p = prevStriker.current;
        if (p && Math.hypot(strikerNow.x - p.x, strikerNow.y - p.y) > 2) emitBurst(particles.current, strikerNow.x, strikerNow.y, 'rgba(255,236,170,0.8)', 1, 14);
        prevStriker.current = { x: strikerNow.x, y: strikerNow.y };
      } else prevStriker.current = null;
      shake *= 0.85; stepParticles(particles.current, dt);
      const cur = L.snapshot?.game_flow?.current_turn ?? null;
      const animating = useGame.getState().animating;
      const mine = !!L.slot && cur === L.slot && !animating && L.snapshot?.status === 'IN_PROGRESS';
      const oppPreview = !mine && L.opp && cur && cur !== L.slot ? { x: L.opp.x, y: BASELINE_Y[cur] } : null;
      drawScene(ctx, SIZE * dpr, {
        bodies: L.bodies, slot: L.slot,
        aim: mine && L.derived ? { path: L.derived.path, force01: L.derived.force01 } : null,
        previewStriker: mine ? { x: L.strikerX, y: BASELINE_Y[L.slot!] } : oppPreview ?? (strikerNow ? { x: strikerNow.x, y: strikerNow.y } : null),
        particles: particles.current,
        highlightBaseline: cur && L.snapshot?.status === 'IN_PROGRESS' && !animating ? cur : null,
        pulse: now / 1000, shake, glow: mine,
      });
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => { clear(); }, [snapshot?.game_flow?.current_turn, slot, clear]); // fresh aim every turn

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { clear(); setDragging('none'); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clear]);

  const toBoard = (e: ReactPointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return screenToBoard(((e.clientX - r.left) / r.width) * SIZE, ((e.clientY - r.top) / r.height) * SIZE, SIZE, slot);
  };

  const onDown = (e: ReactPointerEvent) => {
    if (!canShoot || !slot) return;
    sound.unlock();
    ref.current!.setPointerCapture(e.pointerId);
    const p = toBoard(e), sy = BASELINE_Y[slot];
    if (Math.hypot(p.x - strikerX, p.y - sy) <= STRIKER_R + 22) { setDragging('aim'); setLocked(false); setPointer(p); }
    else if (Math.abs(p.y - sy) < 55) { setDragging('slide'); setPointer(null); setLocked(false); const x = clampX(p.x); setStrikerX(x); sendPreview(x, null); }
  };
  const onMove = (e: ReactPointerEvent) => {
    if (dragging === 'none' || !slot) return;
    const p = toBoard(e);
    if (dragging === 'slide') { const x = clampX(p.x); setStrikerX(x); sendPreview(x, null); }
    else setPointer(p);
  };
  const onUp = () => {
    if (dragging === 'aim' && slot) {
      if (shotFromPull(strikerX, BASELINE_Y[slot], pointer)) setLocked(true);   // aim kept; FIRE confirms
      else setPointer(null);
    }
    setDragging('none');
  };

  return (
    <canvas
      ref={ref}
      className={`block aspect-square w-full touch-none select-none rounded-2xl shadow-2xl shadow-black/60 ring-1 ring-white/10 ${canShoot ? 'cursor-crosshair' : 'cursor-default'}`}
      onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
    />
  );
}
