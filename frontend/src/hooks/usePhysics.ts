// Aim state + local trajectory preview. The preview is *only* a visual aid: the server's physics decide
// what actually happens (client-side prediction with server reconciliation lives in useWebSocket).
import { useCallback, useMemo, useState } from 'react';
import { predictPath, dirToAngle, type AimPath } from '../utils/aim';
import { BASELINE_Y, COIN_R, MAX_IMPULSE, STRIKER_R, type Slot } from '../types';
import type { Body } from './useWebSocket';

export const MAX_PULL = 170; // board units of slingshot drag for 100 % power
const MIN_PULL = 12;

export interface AimState { strikerX: number; pointer: { x: number; y: number } | null }

export function usePhysics(bodies: Body[], slot: Slot | null) {
  const [aim, setAim] = useState<AimState>({ strikerX: 370, pointer: null });

  const derived = useMemo(() => {
    if (!slot || !aim.pointer) return null;
    const sy = BASELINE_Y[slot];
    const vx = aim.strikerX - aim.pointer.x, vy = sy - aim.pointer.y; // pull back = opposite of shot
    const len = Math.hypot(vx, vy);
    if (len < MIN_PULL) return null;
    const angle = dirToAngle({ x: vx / len, y: vy / len });
    const force01 = Math.min(1, len / MAX_PULL);
    const obstacles = bodies.filter((b) => !b.pocketed && b.type !== 'striker').map((b) => ({ id: b.id, x: b.x, y: b.y, r: COIN_R }));
    const path: AimPath = predictPath({ x: aim.strikerX, y: sy }, angle, STRIKER_R, obstacles);
    return { angle, force01, force: force01 * MAX_IMPULSE, path };
  }, [aim, bodies, slot]);

  const reset = useCallback(() => setAim((a) => ({ ...a, pointer: null })), []);
  return { aim, setAim, derived, reset };
}
