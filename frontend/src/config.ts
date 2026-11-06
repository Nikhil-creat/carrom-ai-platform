// Backend location. Empty (default) = same origin, which is what Vite's dev proxy and the Docker/nginx setup use.
// On Vercel set VITE_BACKEND_URL=https://your-backend.example.com (no trailing slash).
const raw = (import.meta.env.VITE_BACKEND_URL as string | undefined)?.replace(/\/+$/, '') ?? '';

export const API_BASE = raw;
export const WS_BASE = raw
  ? raw.replace(/^http/, 'ws')
  : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
