import { useGame } from '../hooks/useWebSocket';

const TONE = {
  info: 'border-sky-400/40 bg-slate-900/90 text-sky-100',
  good: 'border-emerald-400/50 bg-emerald-950/90 text-emerald-100',
  warn: 'border-amber-400/50 bg-amber-950/90 text-amber-100',
} as const;

export default function Toasts() {
  const toasts = useGame((s) => s.toasts);
  return (
    <div className="pointer-events-none fixed inset-x-0 top-3 z-50 flex flex-col items-center gap-2 px-3" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`max-w-sm rounded-full border px-4 py-2 text-sm font-semibold shadow-xl backdrop-blur ${TONE[t.tone]}`}>{t.text}</div>
      ))}
    </div>
  );
}
