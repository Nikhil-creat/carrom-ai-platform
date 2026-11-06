// Backend location. Empty (default) = same origin, which is what Vite's dev proxy and the Docker/nginx setup use.
// On Vercel set VITE_BACKEND_URL=https://your-backend.example.com (no trailing slash) and redeploy.
const raw = (import.meta.env.VITE_BACKEND_URL as string | undefined)?.trim().replace(/\/+$/, '') ?? '';
const isLocal = ['localhost', '127.0.0.1'].includes(location.hostname);

export const API_BASE = raw;
export const WS_BASE = raw
  ? raw.replace(/^http/, 'ws')
  : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
/** false on a deployed static host with no backend URL: every request would hit the SPA and fail confusingly. */
export const BACKEND_CONFIGURED = raw !== '' || isLocal;
export const BACKEND_LABEL = raw ? raw.replace(/^https?:\/\//, '') : location.host;
