import { API_BASE } from '../config';

/** fetch + JSON with a timeout and human-readable errors (free hosting sleeps, CORS, wrong URL...). */
export async function api<T>(path: string, init: RequestInit = {}, timeoutMs = 25000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(API_BASE + path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
      signal: ctrl.signal,
    });
    const text = await res.text();
    let data: unknown = null;
    try { data = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
    if (!res.ok) {
      const d = (data as { detail?: unknown } | null)?.detail;
      throw new Error(typeof d === 'string' ? d : `Server returned ${res.status}`);
    }
    if (data === null) throw new Error('Unexpected reply from the server. Is VITE_BACKEND_URL pointing at the backend?');
    return data as T;
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error('The server took too long to answer. It may be waking up, so try again in a few seconds.');
    }
    if (e instanceof TypeError) {
      throw new Error("Can't reach the game server. If it was idle it can take 30–60 s to wake up. Wait a moment and try again.");
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
