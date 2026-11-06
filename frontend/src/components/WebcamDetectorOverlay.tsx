import { useEffect, useRef, useState } from 'react';
import { BOARD } from '../types';
import { API_BASE } from '../config';

interface Det { type: string; x: number; y: number; radius: number; confidence: number }
interface Resp { board_found: boolean; detections: Det[]; coins: { id: string; type: string; x: number; y: number; is_pocketed: boolean }[] }

const COLOR: Record<string, string> = { white: '#ffffff', black: '#111111', queen: '#e11d48', striker: '#facc15' };

/** Streams webcam frames to POST /api/vision/detect and draws the detected board state over the video. */
export default function WebcamDetectorOverlay() {
  const video = useRef<HTMLVideoElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const [on, setOn] = useState(false);
  const [info, setInfo] = useState('Camera off');

  useEffect(() => {
    if (!on) return;
    let stream: MediaStream | undefined, stop = false, timer = 0;
    const grab = document.createElement('canvas');
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 960, height: 720 } });
        video.current!.srcObject = stream;
        await video.current!.play();
      } catch { setInfo('Camera permission denied or unavailable'); setOn(false); return; }
      const run = async () => {
        if (stop) return;
        const v = video.current!;
        if (v.videoWidth) {
          grab.width = v.videoWidth; grab.height = v.videoHeight;
          grab.getContext('2d')!.drawImage(v, 0, 0);
          const blob: Blob | null = await new Promise((r) => grab.toBlob(r, 'image/jpeg', 0.8));
          if (blob) {
            try {
              const res = await fetch(`${API_BASE}/api/vision/detect`, { method: 'POST', body: blob, headers: { 'Content-Type': 'image/jpeg' } });
              if (res.ok) paint(await res.json());
              else setInfo(`Detector error ${res.status}`);
            } catch { setInfo('Detector unreachable'); }
          }
        }
        timer = window.setTimeout(run, 400);
      };
      run();
    })();
    return () => { stop = true; window.clearTimeout(timer); stream?.getTracks().forEach((t) => t.stop()); };
  }, [on]);

  const paint = (r: Resp) => {
    const c = overlay.current!, ctx = c.getContext('2d')!;
    c.width = BOARD; c.height = BOARD; ctx.clearRect(0, 0, BOARD, BOARD);
    for (const d of r.detections) {
      ctx.strokeStyle = COLOR[d.type] ?? '#0ff'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(d.x, d.y, d.radius, 0, 7); ctx.stroke();
      ctx.fillStyle = '#0f172a'; ctx.font = '14px sans-serif'; ctx.fillText(d.type[0].toUpperCase(), d.x - 4, d.y + 5);
    }
    setInfo(r.board_found ? `Board locked · ${r.detections.length} objects` : 'Board not found — showing raw frame');
  };

  return (
    <div className="space-y-2 rounded-lg bg-slate-800/60 p-3">
      <div className="flex items-center justify-between text-sm">
        <span className="font-semibold">Camera board scan</span>
        <button className="rounded bg-sky-600 px-3 py-1 text-xs hover:bg-sky-500" onClick={() => setOn((v) => !v)}>{on ? 'Stop' : 'Start'}</button>
      </div>
      <div className="relative overflow-hidden rounded bg-black" style={{ aspectRatio: '4 / 3' }}>
        <video ref={video} className="h-full w-full object-cover" muted playsInline />
        <canvas ref={overlay} className="pointer-events-none absolute inset-0 h-full w-full opacity-90" />
      </div>
      <div className="text-xs text-slate-400">{info}</div>
      <p className="text-[11px] text-slate-500">The overlay is drawn in warped top-down board coordinates; it appears aligned only when the whole board fills the camera frame.</p>
    </div>
  );
}
