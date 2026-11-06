import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import GameScreen from './components/GameScreen';
import MultiplayerLobby, { type JoinRequest } from './components/MultiplayerLobby';
import { useGame } from './hooks/useWebSocket';
import { api } from './utils/api';
import { sound } from './utils/soundEngine';
import type { Difficulty } from './types';

function useUserId(): string {
  return useMemo(() => {
    try {
      let id = localStorage.getItem('carrom_user');
      if (!id) { id = 'usr_' + Math.random().toString(36).slice(2, 10); localStorage.setItem('carrom_user', id); }
      return id;
    } catch { return 'usr_' + Math.random().toString(36).slice(2, 10); }
  }, []);
}

export default function App() {
  const userId = useUserId();
  const [name, setName] = useState(() => { try { return localStorage.getItem('carrom_name') || 'Player'; } catch { return 'Player'; } });
  const roomId = useGame((s) => s.roomId);
  const connect = useGame((s) => s.connect);
  const disconnect = useGame((s) => s.disconnect);
  const lastAi = useRef<Difficulty | null>(null);

  useEffect(() => { try { localStorage.setItem('carrom_name', name); } catch { /* storage unavailable */ } }, [name]);

  useEffect(() => { // browsers only allow audio after a gesture
    const unlock = () => sound.unlock();
    window.addEventListener('pointerdown', unlock, { once: true });
    return () => window.removeEventListener('pointerdown', unlock);
  }, []);

  useEffect(() => { // invite links: /?room=ID&code=123456 (add &watch=1 to spectate)
    const q = new URLSearchParams(location.search);
    const room = q.get('room');
    if (!room) return;
    connect({ roomId: room, userId, name, role: q.get('watch') ? 'spectator' : 'player', code: q.get('code') ?? undefined });
    history.replaceState(null, '', location.pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onJoin = useCallback((j: JoinRequest) => {
    lastAi.current = j.ai ?? null;
    connect({ roomId: j.roomId, userId, name, role: j.role, code: j.code });
  }, [connect, userId, name]);

  const rematch = useCallback(async () => {
    const diff = lastAi.current;
    if (!diff) return;
    disconnect();
    try {
      const r = await api<{ room_id: string }>('/api/rooms', { method: 'POST', body: JSON.stringify({ host_id: userId, name: `${name} vs AI`, vs_ai: true, difficulty: diff }) });
      connect({ roomId: r.room_id, userId, name, role: 'player' });
    } catch { /* the lobby is showing again and displays the server status */ }
  }, [connect, disconnect, userId, name]);

  return (
    <main className="min-h-screen">
      {roomId
        ? <GameScreen onLeave={disconnect} onRematch={lastAi.current ? rematch : undefined} />
        : <MultiplayerLobby userId={userId} name={name} setName={setName} onJoin={onJoin} />}
    </main>
  );
}
