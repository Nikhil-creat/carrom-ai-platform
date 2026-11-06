// Web Audio synthesis: no audio files. Call unlock() from a user gesture (browser autoplay policy).
type Kind = 'striker_hit' | 'coin_hit' | 'border' | 'pocket' | 'turn' | 'win' | 'foul' | 'chat';

class SoundEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  muted = false;

  unlock(): void {
    if (!this.ctx) {
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!AC) return;
      this.ctx = new AC();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.6;
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume();
  }

  setMuted(m: boolean): void { this.muted = m; if (this.master) this.master.gain.value = m ? 0 : 0.6; }

  private tone(freq: number, dur: number, type: OscillatorType, gain: number, when = 0, slideTo?: number): void {
    if (!this.ctx || !this.master) return;
    const t0 = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + 0.004);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(this.master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  private noise(dur: number, gain: number, cutoff: number): void {
    if (!this.ctx || !this.master) return;
    const n = Math.floor(this.ctx.sampleRate * dur);
    const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    const f = this.ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.frequency.value = cutoff;
    const g = this.ctx.createGain();
    g.gain.value = gain;
    src.connect(f).connect(g).connect(this.master);
    src.start();
  }

  /** `intensity` is the impact speed (units/s) from the server's physics events. */
  play(kind: Kind, intensity = 300): void {
    if (this.muted || !this.ctx) return;
    const v = Math.min(1, intensity / 1200);
    switch (kind) {
      case 'striker_hit': this.tone(900 - 200 * v, 0.09, 'triangle', 0.25 + 0.5 * v, 0, 500); this.noise(0.03, 0.2 * v, 6000); break;
      case 'coin_hit': this.tone(1500 - 300 * v, 0.06, 'triangle', 0.15 + 0.35 * v, 0, 900); this.noise(0.02, 0.12 * v, 7000); break;
      case 'border': this.tone(180, 0.1, 'sine', 0.2 + 0.4 * v, 0, 90); this.noise(0.05, 0.15 * v, 1200); break;
      case 'pocket': this.tone(520, 0.18, 'sine', 0.35, 0, 140); this.noise(0.12, 0.2, 900); break;
      case 'turn': this.tone(660, 0.12, 'sine', 0.2); this.tone(880, 0.14, 'sine', 0.2, 0.1); break;
      case 'win': [523, 659, 784, 1046].forEach((f, i) => this.tone(f, 0.25, 'triangle', 0.3, i * 0.12)); break;
      case 'foul': this.tone(200, 0.3, 'sawtooth', 0.2, 0, 90); break;
      case 'chat': this.tone(1200, 0.05, 'sine', 0.1); break;
    }
  }
}

export const sound = new SoundEngine();
