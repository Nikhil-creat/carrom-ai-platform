// All canvas drawing. The React component only owns the <canvas>, input and the rAF loop.
import { BOARD, COIN_R, POCKETS, STRIKER_R, type CoinType, type Slot } from '../types';
import type { AimPath } from './aim';

export interface DrawBody { id: string; type: CoinType; x: number; y: number; pocketed: boolean }
export interface Particle { x: number; y: number; vx: number; vy: number; life: number; max: number; color: string; size: number }
export interface DrawState {
  bodies: DrawBody[];
  slot: Slot | null;
  aim: { path: AimPath; force01: number } | null;
  previewStriker: { x: number; y: number } | null;
  particles: Particle[];
  highlightBaseline: Slot | null;
  pulse: number;
  shake: number;
  glow: boolean;          // it is your turn: halo on the striker
}

const COLORS: Record<CoinType, { fill: string; ring: string }> = {
  white: { fill: '#f4efe4', ring: '#b9b09a' },
  black: { fill: '#25221f', ring: '#0b0a09' },
  queen: { fill: '#c0392b', ring: '#7b1d14' },
  striker: { fill: '#e8d9a8', ring: '#a08940' },
};

export function emitBurst(list: Particle[], x: number, y: number, color: string, n: number, speed: number): void {
  for (let i = 0; i < n; i++) {
    const a = Math.random() * Math.PI * 2, s = speed * (0.3 + Math.random());
    list.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: 0, max: 0.35 + Math.random() * 0.35, color, size: 1.5 + Math.random() * 2.5 });
  }
  if (list.length > 400) list.splice(0, list.length - 400);
}

export function stepParticles(list: Particle[], dt: number): void {
  for (let i = list.length - 1; i >= 0; i--) {
    const p = list[i];
    p.life += dt;
    if (p.life >= p.max) { list.splice(i, 1); continue; }
    p.x += p.vx * dt; p.y += p.vy * dt; p.vx *= 0.94; p.vy *= 0.94;
  }
}

function drawBoardSurface(ctx: CanvasRenderingContext2D, pulseSlot: Slot | null, pulse: number): void {
  const wood = ctx.createLinearGradient(-30, -30, BOARD + 30, BOARD + 30);
  wood.addColorStop(0, '#6a4222'); wood.addColorStop(0.5, '#4d2f17'); wood.addColorStop(1, '#6a4222');
  ctx.fillStyle = wood; ctx.fillRect(-30, -30, BOARD + 60, BOARD + 60);                // wooden frame
  ctx.strokeStyle = 'rgba(0,0,0,0.12)'; ctx.lineWidth = 1;                              // wood grain
  for (let i = -30; i < BOARD + 30; i += 9) { ctx.beginPath(); ctx.moveTo(i, -30); ctx.bezierCurveTo(i + 6, 200, i - 6, 520, i + 2, BOARD + 30); ctx.stroke(); }
  ctx.fillStyle = '#9b6b3a'; ctx.fillRect(-14, -14, BOARD + 28, BOARD + 28);
  ctx.fillStyle = '#7c5228'; ctx.fillRect(-14, -14, BOARD + 28, 4); ctx.fillRect(-14, -14, 4, BOARD + 28);
  const g = ctx.createLinearGradient(0, 0, BOARD, BOARD);
  g.addColorStop(0, '#f1d9a6'); g.addColorStop(1, '#e6c88c');
  ctx.fillStyle = g; ctx.fillRect(0, 0, BOARD, BOARD);                                  // playing surface
  const sh = ctx.createLinearGradient(0, 0, 0, 40);                                      // cushion shadow
  sh.addColorStop(0, 'rgba(0,0,0,0.22)'); sh.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = sh; ctx.fillRect(0, 0, BOARD, 40);
  const sl = ctx.createLinearGradient(0, 0, 40, 0);
  sl.addColorStop(0, 'rgba(0,0,0,0.18)'); sl.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = sl; ctx.fillRect(0, 0, 40, BOARD);
  ctx.strokeStyle = '#b3402f'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(BOARD / 2, BOARD / 2, 85, 0, 7); ctx.stroke();               // centre rings
  ctx.beginPath(); ctx.arc(BOARD / 2, BOARD / 2, 18, 0, 7); ctx.stroke();
  ctx.strokeStyle = '#2b2b2b'; ctx.lineWidth = 1.5;
  for (const y of [120, 620]) {                                                           // baselines + end circles
    ctx.beginPath(); ctx.moveTo(150, y - 17); ctx.lineTo(590, y - 17); ctx.moveTo(150, y + 17); ctx.lineTo(590, y + 17); ctx.stroke();
    for (const x of [150, 590]) { ctx.beginPath(); ctx.arc(x, y, 17, 0, 7); ctx.fillStyle = '#b3402f'; ctx.fill(); ctx.stroke(); }
  }
  if (pulseSlot) {                                                                          // whose baseline is live
    const y = pulseSlot === 'p1' ? 620 : 120;
    ctx.fillStyle = `rgba(255,255,255,${0.1 + 0.08 * Math.sin(pulse * 4)})`;
    ctx.fillRect(150, y - 17, 440, 34);
  }
  ctx.strokeStyle = '#7a5a2a'; ctx.lineWidth = 1;                                          // corner arrows
  for (const [x, y, dx, dy] of [[70, 70, 1, 1], [670, 70, -1, 1], [70, 670, 1, -1], [670, 670, -1, -1]] as const) {
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + dx * 90, y + dy * 90); ctx.stroke();
  }
  for (const p of POCKETS) {                                                                // pockets
    const r = ctx.createRadialGradient(p.x, p.y, 2, p.x, p.y, p.radius);
    r.addColorStop(0, '#000'); r.addColorStop(1, '#1b1411');
    ctx.fillStyle = r; ctx.beginPath(); ctx.arc(p.x, p.y, p.radius, 0, 7); ctx.fill();
    ctx.strokeStyle = '#5b3a1e'; ctx.lineWidth = 3; ctx.stroke();                          // pocket rim
  }
}

function drawDisc(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, type: CoinType, alpha = 1): void {
  const c = COLORS[type];
  ctx.globalAlpha = alpha;
  ctx.fillStyle = 'rgba(0,0,0,0.28)'; ctx.beginPath(); ctx.arc(x + 2.5, y + 3, r, 0, 7); ctx.fill();
  const g = ctx.createRadialGradient(x - r * 0.35, y - r * 0.35, r * 0.1, x, y, r);
  g.addColorStop(0, '#fff'); g.addColorStop(0.15, c.fill); g.addColorStop(1, c.fill);
  ctx.fillStyle = type === 'black' ? '#25221f' : g;
  ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  ctx.strokeStyle = c.ring; ctx.lineWidth = 2; ctx.stroke();
  ctx.strokeStyle = c.ring; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, r * 0.62, 0, 7); ctx.stroke();
  ctx.globalAlpha = 1;
}

export function drawScene(ctx: CanvasRenderingContext2D, size: number, s: DrawState): void {
  const scale = size / (BOARD + 60);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, size, size);
  ctx.setTransform(scale, 0, 0, scale, 30 * scale + (s.shake ? (Math.random() - 0.5) * s.shake : 0), 30 * scale);
  if (s.slot === 'p2') {                                                                     // view from your own baseline
    ctx.translate(BOARD, BOARD); ctx.rotate(Math.PI);
  }
  drawBoardSurface(ctx, s.highlightBaseline, s.pulse);

  const live = s.bodies.filter((b) => !b.pocketed && b.type !== 'striker');
  for (const b of live) drawDisc(ctx, b.x, b.y, COIN_R, b.type);
  const striker = s.bodies.find((b) => b.type === 'striker' && !b.pocketed);
  const sp = s.previewStriker ?? (striker ? { x: striker.x, y: striker.y } : null);

  if (s.aim && sp) {                                                                         // trajectory preview
    const { path, force01 } = s.aim;
    ctx.save();
    ctx.setLineDash([10, 8]); ctx.lineWidth = 2.5;
    ctx.strokeStyle = `rgba(255,255,255,${0.55 + 0.35 * force01})`;
    ctx.beginPath(); path.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.stroke();
    if (path.ghost) { ctx.setLineDash([]); drawDisc(ctx, path.ghost.x, path.ghost.y, STRIKER_R, 'striker', 0.35); }
    if (path.coinLine) {
      ctx.setLineDash([4, 6]); ctx.strokeStyle = 'rgba(255,214,102,0.9)';
      ctx.beginPath(); ctx.moveTo(path.coinLine[0].x, path.coinLine[0].y); ctx.lineTo(path.coinLine[1].x, path.coinLine[1].y); ctx.stroke();
    }
    ctx.restore();
  }
  if (sp && s.glow) {
    const pr = STRIKER_R + 10 + 4 * Math.sin(s.pulse * 5);
    const halo = ctx.createRadialGradient(sp.x, sp.y, STRIKER_R * 0.6, sp.x, sp.y, pr + 8);
    halo.addColorStop(0, 'rgba(255,214,102,0.55)'); halo.addColorStop(1, 'rgba(255,214,102,0)');
    ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(sp.x, sp.y, pr + 8, 0, 7); ctx.fill();
  }
  if (sp) drawDisc(ctx, sp.x, sp.y, STRIKER_R, 'striker');

  if (s.aim && sp) {                                                                         // power meter around striker
    ctx.strokeStyle = `hsl(${120 - 120 * s.aim.force01},85%,55%)`; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(sp.x, sp.y, STRIKER_R + 8, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * s.aim.force01); ctx.stroke();
  }
  for (const p of s.particles) {                                                             // impact particles
    ctx.globalAlpha = 1 - p.life / p.max; ctx.fillStyle = p.color;
    ctx.beginPath(); ctx.arc(p.x, p.y, p.size, 0, 7); ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/** screen px (canvas coords) -> board units, honouring the p2 180deg flip. */
export function screenToBoard(px: number, py: number, size: number, slot: Slot | null): { x: number; y: number } {
  const scale = size / (BOARD + 60);
  let x = px / scale - 30, y = py / scale - 30;
  if (slot === 'p2') { x = BOARD - x; y = BOARD - y; }
  return { x, y };
}
