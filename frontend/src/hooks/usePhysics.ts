// Local trajectory preview. Purely a visual aid: the server's physics decide what actually happens
// (client-side prediction with server reconciliation lives in useWebSocket).
import { useMemo } from 'react';
import { predictPath, shotFromPull, type AimPath } from '../utils/aim';
import { BASELINE_Y, COIN_R, STRIKER_R, type Slot } from '../types';
import { useAim } from './useAim';
import type { Body } from './useWebSocket';

export const MAX_PULL = 170; // board units of slingshot drag for 100 % power

export interface Derived { angle: number; force01: number; force: number; path: AimPath }

export function usePhysics(bodies: Body[], slot: Slot | null): Derived | null {
  const strikerX = useAim((s) => s.strikerX);
  const pointer = useAim((s) => s.pointer);
  return useMemo(() => {
    if (!slot) return null;
    const sy = BASELINE_Y[slot];
    const shot = shotFromPull(strikerX, sy, pointer, MAX_PULL);
    if (!shot) return null;
    const obstacles = bodies.filter((b) => !b.pocketed && b.type !== 'striker').map((b) => ({ id: b.id, x: b.x, y: b.y, r: COIN_R }));
    return { ...shot, path: predictPath({ x: strikerX, y: sy }, shot.angle, STRIKER_R, obstacles) };
  }, [strikerX, pointer, bodies, slot]);
}
