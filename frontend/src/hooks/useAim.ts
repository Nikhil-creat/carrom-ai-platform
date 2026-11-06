// Aim state shared by the canvas (drag input) and the control bar (power meter, spin, FIRE).
import { create } from 'zustand';
import { BASELINE_MAX_X, BASELINE_MIN_X } from '../types';

interface AimStore {
  strikerX: number;
  pointer: { x: number; y: number } | null;   // where the slingshot drag currently is, in board units
  locked: boolean;                             // aim confirmed by releasing the drag; FIRE sends it
  topSpin: number;
  sideSpin: number;
  setStrikerX: (x: number) => void;
  setPointer: (p: { x: number; y: number } | null) => void;
  setLocked: (l: boolean) => void;
  setSpin: (top: number, side: number) => void;
  clear: () => void;
}

export const clampX = (x: number): number => Math.min(BASELINE_MAX_X, Math.max(BASELINE_MIN_X, x));

export const useAim = create<AimStore>((set) => ({
  strikerX: 370, pointer: null, locked: false, topSpin: 0, sideSpin: 0,
  setStrikerX: (x) => set({ strikerX: clampX(x) }),
  setPointer: (p) => set({ pointer: p }),
  setLocked: (l) => set({ locked: l }),
  setSpin: (top, side) => set({ topSpin: top, sideSpin: side }),
  clear: () => set({ pointer: null, locked: false, topSpin: 0, sideSpin: 0 }),
}));
