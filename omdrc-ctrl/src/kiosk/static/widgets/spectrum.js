/* Third-octave spectrum bars (left/right per band) with falling peak caps. */
(() => {
'use strict';
const FALL_DB_S = 30, CAP_HOLD_MS = 900, CAP_FALL_DB_S = 14;

K.Spectrum = class Spectrum {
    constructor(canvas) {
        this.canvas = canvas;
        this.bands = []; this.l = []; this.r = [];
        this.tl = []; this.tr = []; this.capL = []; this.capR = []; this.capAt = [];
        this.raf = null; this.last = 0;
        this.ro = new ResizeObserver(() => this.draw());
        this.ro.observe(canvas);
    }
    get floor() { return Number(K.state.spectrum.floor_db) || -40; }

    update(frame) {
        if (Array.isArray(frame.bands) && frame.bands.length) this.bands = frame.bands;
        const n = this.bands.length;
        const pick = a => Array.isArray(a) && a.length === n ? a : new Array(n).fill(-200);
        this.tl = pick(frame.left); this.tr = pick(frame.right);
        if (!this.raf) { this.last = 0; this.raf = requestAnimationFrame(ts => this.step(ts)); }
    }
    clear() {
        this.tl = this.tr = new Array(this.bands.length).fill(-200);
        if (!this.raf) { this.last = 0; this.raf = requestAnimationFrame(ts => this.step(ts)); }
    }
    destroy() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = null; this.ro.disconnect(); }

    step(ts) {
        const dt = this.last ? Math.min(0.1, (ts - this.last) / 1000) : 0;
        this.last = ts;
        const n = this.bands.length, now = performance.now();
        let settled = true;
        const fl = this.floor - 5;
        for (let i = 0; i < n; i++) {
            const tl = this.tl[i] ?? fl, tr = this.tr[i] ?? fl;
            this.l[i] = Math.max(tl, (this.l[i] ?? fl) - FALL_DB_S * dt);
            this.r[i] = Math.max(tr, (this.r[i] ?? fl) - FALL_DB_S * dt);
            if (Math.abs(this.l[i] - tl) > .1 || Math.abs(this.r[i] - tr) > .1) settled = false;
            for (const [cap, v] of [[this.capL, this.l[i]], [this.capR, this.r[i]]]) {
                if (v >= (cap[i] ?? fl)) { cap[i] = v; this.capAt[i] = now; }
                else if (now - (this.capAt[i] || 0) > CAP_HOLD_MS) { cap[i] = Math.max(v, cap[i] - CAP_FALL_DB_S * dt); }
                if (cap[i] > v + .1) settled = false;
            }
        }
        this.draw();
        this.raf = settled ? null : requestAnimationFrame(t => this.step(t));
    }

    draw() {
        const c = this.canvas, dpr = Math.min(2, window.devicePixelRatio || 1);
        const w = Math.max(1, Math.round(c.clientWidth * dpr)), H = Math.max(1, Math.round(c.clientHeight * dpr));
        if (c.width !== w || c.height !== H) { c.width = w; c.height = H; }
        const ctx = c.getContext('2d');
        ctx.clearRect(0, 0, w, H);
        const n = this.bands.length;
        if (!n) return;
        const labelH = 15 * dpr, top = 4 * dpr, plotH = H - labelH - top;
        const fl = this.floor, y = db => top + plotH * (1 - K.clamp((db - fl) / (0 - fl), 0, 1));
        // faint grid every 10 dB
        ctx.strokeStyle = 'rgba(139,148,158,.14)'; ctx.lineWidth = 1;
        ctx.fillStyle = 'rgba(139,148,158,.6)';
        ctx.font = `${9.5 * dpr}px ui-monospace, monospace`; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
        for (let db = -10; db > fl; db -= 10) {
            ctx.beginPath(); ctx.moveTo(0, Math.round(y(db)) + .5); ctx.lineTo(w, Math.round(y(db)) + .5); ctx.stroke();
            ctx.fillText(String(db), 3 * dpr, y(db) - 1);
        }
        const grad = ctx.createLinearGradient(0, top + plotH, 0, top);
        grad.addColorStop(0, '#1f6feb'); grad.addColorStop(.45, '#3fb950');
        grad.addColorStop(.8, '#d8c23a'); grad.addColorStop(1, '#f85149');
        const slot = w / n, bw = Math.max(2, slot * .38), gap = Math.max(1, slot * .04);
        for (let i = 0; i < n; i++) {
            const x0 = i * slot + (slot - 2 * bw - gap) / 2;
            [[this.l[i], this.capL[i], x0], [this.r[i], this.capR[i], x0 + bw + gap]].forEach(([v, cap, x]) => {
                if (Number.isFinite(v) && v > fl) {
                    ctx.fillStyle = grad;
                    ctx.fillRect(x, y(v), bw, top + plotH - y(v));
                }
                if (Number.isFinite(cap) && cap > fl) {
                    ctx.fillStyle = '#e6edf3';
                    ctx.fillRect(x, y(cap) - 2 * dpr, bw, 2 * dpr);
                }
            });
            if (i % 3 === 0) {
                ctx.fillStyle = '#8b949e';
                ctx.font = `${10 * dpr}px ui-monospace, monospace`;
                ctx.textAlign = 'center'; ctx.textBaseline = 'top';
                ctx.fillText(this.bands[i].label, i * slot + slot / 2 + 3 * dpr, H - labelH + 3 * dpr);
            }
        }
    }
};
})();
