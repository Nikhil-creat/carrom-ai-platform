// Pure geometry for the aim preview: straight line, up to two cushion bounces, first coin hit
// ("ghost" striker) and the struck coin's departure line. Matches the server's angle convention:
// angle 0 = +x, pi/2 = towards the TOP of the board (y decreasing).
const BOARD = 740;

export interface Pt { x: number; y: number }
export interface Obstacle { id: string; x: number; y: number; r: number }
export interface AimPath {
  points: Pt[];
  ghost: Pt | null;
  hitId: string | null;
  coinLine: Pt[] | null;
}

export const angleToDir = (a: number): Pt => ({ x: Math.cos(a), y: -Math.sin(a) });
export const dirToAngle = (d: Pt): number => Math.atan2(-d.y, d.x);

export function predictPath(start: Pt, angle: number, strikerR: number, obstacles: Obstacle[], maxBounces = 2): AimPath {
  let pos = { ...start };
  let d = angleToDir(angle);
  const points: Pt[] = [pos];
  const lo = strikerR, hi = BOARD - strikerR;
  for (let bounce = 0; bounce <= maxBounces; bounce++) {
    // nearest wall
    let tWall = Infinity, wallAxis: 'x' | 'y' = 'x';
    if (d.x > 1e-9) { const t = (hi - pos.x) / d.x; if (t < tWall) { tWall = t; wallAxis = 'x'; } }
    if (d.x < -1e-9) { const t = (lo - pos.x) / d.x; if (t < tWall) { tWall = t; wallAxis = 'x'; } }
    if (d.y > 1e-9) { const t = (hi - pos.y) / d.y; if (t < tWall) { tWall = t; wallAxis = 'y'; } }
    if (d.y < -1e-9) { const t = (lo - pos.y) / d.y; if (t < tWall) { tWall = t; wallAxis = 'y'; } }
    // nearest coin (ray vs circle of radius strikerR + coin.r)
    let tCoin = Infinity; let hit: Obstacle | null = null;
    for (const o of obstacles) {
      const fx = pos.x - o.x, fy = pos.y - o.y, R = strikerR + o.r;
      const b = fx * d.x + fy * d.y, c = fx * fx + fy * fy - R * R;
      const disc = b * b - c;
      if (disc < 0 || c < 0) continue; // no hit, or already overlapping
      const t = -b - Math.sqrt(disc);
      if (t > 1e-6 && t < tCoin) { tCoin = t; hit = o; }
    }
    if (hit && tCoin < tWall) {
      const ghost = { x: pos.x + d.x * tCoin, y: pos.y + d.y * tCoin };
      points.push(ghost);
      const n = { x: hit.x - ghost.x, y: hit.y - ghost.y };
      const len = Math.hypot(n.x, n.y) || 1;
      const u = { x: n.x / len, y: n.y / len };
      return { points, ghost, hitId: hit.id, coinLine: [{ x: hit.x, y: hit.y }, { x: hit.x + u.x * 220, y: hit.y + u.y * 220 }] };
    }
    if (!isFinite(tWall)) break;
    pos = { x: pos.x + d.x * tWall, y: pos.y + d.y * tWall };
    points.push(pos);
    d = wallAxis === 'x' ? { x: -d.x, y: d.y } : { x: d.x, y: -d.y };
  }
  return { points, ghost: null, hitId: null, coinLine: null };
}
