import { useEffect, useState } from 'react';
import { BACKEND_CONFIGURED } from '../config';
import { api } from '../utils/api';

export type ServerState = 'unconfigured' | 'checking' | 'waking' | 'online' | 'offline';

/** Pings /health. Doing this as soon as the lobby opens also wakes a sleeping free-tier backend. */
export function useServerStatus(): { state: ServerState; waited: number } {
  const [state, setState] = useState<ServerState>(BACKEND_CONFIGURED ? 'checking' : 'unconfigured');
  const [waited, setWaited] = useState(0);

  useEffect(() => {
    if (!BACKEND_CONFIGURED) return;
    let stop = false, timer = 0, failStart = 0;
    const loop = async () => {
      try {
        await api('/health', {}, 8000);
        if (stop) return;
        failStart = 0; setWaited(0); setState('online');
        timer = window.setTimeout(loop, 20000);
      } catch {
        if (stop) return;
        if (!failStart) failStart = Date.now();
        const w = Math.round((Date.now() - failStart) / 1000);
        setWaited(w); setState(w > 120 ? 'offline' : 'waking');
        timer = window.setTimeout(loop, w > 120 ? 15000 : 3000);
      }
    };
    void loop();
    return () => { stop = true; window.clearTimeout(timer); };
  }, []);

  return { state, waited };
}
