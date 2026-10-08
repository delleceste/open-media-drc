/* Third-octave spectrum with separate or paired channel plots and peak caps. */
(() => {
'use strict';
const FALL_DB_S = 30, CAP_HOLD_MS = 900, CAP_FALL_DB_S = 14;

K.Spectrum = class Spectrum {
    constructor(canvas) {
        K.onTheme(() => this.draw());
        this.canvas = canvas;
        this.bands = []; this.l = []; this.r = [];
        this.tl = []; this.tr = []; this.capL = []; this.capR = [];
        this.capAtL = []; this.capAtR = [];
        this.separate = !!K.pref('now.spectrumSeparate', false);
        this.circular = false;
        this.ringCount = K.clamp(Number(K.pref('now.circularBands', 12)) || 12, 4, 24);
        this.peaks = { left: -120, right: -120 };
        this.raf = null; this.last = 0;
        this.ro = new ResizeObserver(() => this.draw());
        this.ro.observe(canvas);
    }
    get floor() { return Number(K.state.spectrum.floor_db) || -40; }
    setSeparate(on) { this.separate = !!on; this.draw(); }
    setCircular(on) { this.circular = !!on; this.draw(); }
    setRingCount(count) { this.ringCount = K.clamp(Math.round(count), 4, 24); this.draw(); }

    update(frame) {
        if (Array.isArray(frame.bands) && frame.bands.length) this.bands = frame.bands;
        const n = this.bands.length;
        const pick = a => Array.isArray(a) && a.length === n ? a : new Array(n).fill(-200);
        this.tl = pick(frame.left); this.tr = pick(frame.right);
        this.peaks = { left: Number(frame.vu?.left_peak ?? -120), right: Number(frame.vu?.right_peak ?? -120) };
        if (!this.raf) { this.last = 0; this.raf = requestAnimationFrame(ts => this.step(ts)); }
    }
    clear() {
        this.tl = this.tr = new Array(this.bands.length).fill(-200);
        this.peaks = { left: -120, right: -120 };
        if (!this.raf) { this.last = 0; this.raf = requestAnimationFrame(ts => this.step(ts)); }
    }
    /** "LAG!" in the corner: frames were dropped by the network. */
    setLag(on) {
        on = !!on;
        if (on === !!this.lag) return;
        this.lag = on;
        this.draw();
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
            for (const [cap, at, v] of [[this.capL, this.capAtL, this.l[i]], [this.capR, this.capAtR, this.r[i]]]) {
                if (v >= (cap[i] ?? fl)) { cap[i] = v; at[i] = now; }
                else if (now - (at[i] || 0) > CAP_HOLD_MS) { cap[i] = Math.max(v, cap[i] - CAP_FALL_DB_S * dt); }
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
        if (this.lag) {
            ctx.font = `800 ${12 * dpr}px system-ui, sans-serif`;
            ctx.textAlign = 'right'; ctx.textBaseline = 'top';
            ctx.fillStyle = K.theme() === 'light' ? '#a54800' : '#ffa726';
            ctx.fillText('LAG!', w - 6 * dpr, 6 * dpr);
        }
        if (this.circular) { this.drawCircular(ctx, w, H, dpr); return; }
        const gap = this.separate ? 8 * dpr : 0;
        const panelW = this.separate ? Math.max(0, (w - gap) / 2) : w;
        const titleH = this.separate ? 14 * dpr : 0;
        const labelH = 15 * dpr, top = 4 * dpr + titleH, plotH = Math.max(1, H - labelH - top);
        const fl = this.floor, y = db => top + plotH * (1 - K.clamp((db - fl) / (0 - fl), 0, 1));
        const colorAt = db => K.clamp((db - fl) / (0 - fl), 0, 1);
        const leftGrad = ctx.createLinearGradient(0, top + plotH, 0, top);
        leftGrad.addColorStop(0, '#1f8f3a');
        leftGrad.addColorStop(colorAt(-18), '#3fb950');
        leftGrad.addColorStop(colorAt(-9), '#d8c23a');
        leftGrad.addColorStop(colorAt(-4), '#e3892b');
        leftGrad.addColorStop(colorAt(-1), '#f85149');
        leftGrad.addColorStop(1, '#ff2d2d');
        const rightGrad = ctx.createLinearGradient(0, top + plotH, 0, top);
        rightGrad.addColorStop(0, '#388f35');
        rightGrad.addColorStop(colorAt(-18), '#55af4d');
        rightGrad.addColorStop(colorAt(-9), '#e1b230');
        rightGrad.addColorStop(colorAt(-4), '#ec772d');
        rightGrad.addColorStop(colorAt(-1), '#f34a43');
        rightGrad.addColorStop(1, '#ff2828');
        const panels = this.separate
            ? [['L', this.l, this.capL, 0, leftGrad, '#ffffff'],
               ['R', this.r, this.capR, panelW + gap, rightGrad, '#ff4545']]
            : [['', this.l, this.capL, 0, leftGrad, '#ffffff']];
        for (const [name, values, caps, offset, panelGrad, peakColor] of panels) {
            if (this.separate) {
                ctx.strokeStyle = 'rgba(139,148,158,.3)';
                ctx.strokeRect(offset + .5, .5, panelW - 1, H - 1);
                ctx.fillStyle = K.css('--text');
                ctx.font = `600 ${11 * dpr}px system-ui, sans-serif`;
                ctx.textAlign = 'left'; ctx.textBaseline = 'top';
                ctx.fillText(name, offset + 4 * dpr, 2 * dpr);
            }
            ctx.strokeStyle = 'rgba(139,148,158,.14)'; ctx.lineWidth = 1;
            ctx.font = `${9.5 * dpr}px ui-monospace, monospace`;
            ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
            for (let db = -10; db > fl; db -= 10) {
                ctx.beginPath(); ctx.moveTo(offset, Math.round(y(db)) + .5);
                ctx.lineTo(offset + panelW, Math.round(y(db)) + .5); ctx.stroke();
                if (!this.separate || name === 'L') {
                    ctx.fillStyle = 'rgba(139,148,158,.6)';
                    ctx.fillText(String(db), offset + 3 * dpr, y(db) - 1);
                }
            }
            const slot = panelW / n;
            const bw = this.separate ? Math.max(1, slot * .72) : Math.max(1, slot * .38);
            const barGap = Math.max(.5, slot * .04);
            const labelEvery = Math.max(3, Math.ceil(44 * dpr / slot));
            for (let i = 0; i < n; i++) {
                const x0 = offset + i * slot + (slot - (this.separate ? bw : 2 * bw + barGap)) / 2;
                const bars = this.separate
                    ? [[values[i], caps[i], x0, panelGrad, peakColor]]
                    : [[this.l[i], this.capL[i], x0, leftGrad, '#ffffff'],
                       [this.r[i], this.capR[i], x0 + bw + barGap, rightGrad, '#ff4545']];
                for (const [v, cap, x, color, capColor] of bars) {
                    if (Number.isFinite(v) && v > fl) {
                        ctx.fillStyle = color;
                        ctx.fillRect(x, y(v), bw, top + plotH - y(v));
                        if (bw >= 2) {
                            ctx.strokeStyle = 'rgba(0,0,0,.35)';
                            ctx.lineWidth = 1;
                            ctx.strokeRect(x, y(v), bw, top + plotH - y(v));
                        }
                    }
                    if (Number.isFinite(cap) && cap > fl) {
                        ctx.fillStyle = capColor;
                        ctx.fillRect(x, y(cap) - 2 * dpr, bw, 2 * dpr);
                    }
                }
                if (i % labelEvery === 0) {
                    ctx.fillStyle = K.css('--muted');
                    ctx.font = `${10 * dpr}px ui-monospace, monospace`;
                    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
                    ctx.fillText(this.bands[i].label, offset + i * slot + slot / 2, H - labelH + 3 * dpr);
                }
            }
        }
    }

    drawCircular(ctx, w, H, dpr) {
        const n = this.bands.length;
        const count = Math.min(this.ringCount, n);
        const radius = Math.max(1, Math.min(w, H) / 2 - 13 * dpr);
        const inner = radius * .27;
        const pitch = (radius - inner) / count;
        const thickness = Math.max(1, pitch * .72);
        const cx = w / 2, cy = H / 2;
        const fl = this.floor;
        const colors = ['#399dc9', '#3aa6ca', '#40b7be', '#50bc9b', '#77bf6b', '#a9c653',
            '#d0c34d', '#e4ad48', '#e69342', '#e3773d', '#e05a45', '#dc4a4a'];
        // Average power within each adjacent group, then convert back to dB.
        // This preserves the energy of narrow peaks better than averaging dB.
        const level = (values, start, end) => {
            let power = 0;
            for (let i = start; i < end; i++) power += Math.pow(10, (values[i] ?? -200) / 10);
            return 10 * Math.log10(Math.max(1e-20, power / (end - start)));
        };
        const arc = (r, start, end, color, alpha) => {
            ctx.beginPath(); ctx.arc(cx, cy, r, start, end, end < start);
            ctx.strokeStyle = color; ctx.globalAlpha = alpha;
            ctx.lineWidth = thickness; ctx.lineCap = 'round'; ctx.stroke();
        };
        for (let j = 0; j < count; j++) {
            const start = Math.floor(j * n / count), end = Math.floor((j + 1) * n / count);
            const r = radius - pitch * (j + .5);
            const color = colors[Math.round(j * (colors.length - 1) / Math.max(1, count - 1))];
            const left = K.clamp(level(this.l, start, end) / -fl + 1, 0, 1);
            const right = K.clamp(level(this.r, start, end) / -fl + 1, 0, 1);
            arc(r, Math.PI / 2, Math.PI * 1.5, color, .16);
            arc(r, Math.PI / 2, -Math.PI / 2, color, .16);
            if (left > .005) arc(r, Math.PI / 2, Math.PI / 2 + Math.PI * left, color, .95);
            if (right > .005) arc(r, Math.PI / 2, Math.PI / 2 - Math.PI * right, color, .95);
        }
        ctx.globalAlpha = 1;
        // Centre: one half disc per channel, filled bottom-up by the peak level.
        const ri = Math.max(1, inner - pitch * .2), gap = Math.max(1, dpr);
        const font = Math.max(9, Math.min(13, ri / dpr * .32)) * dpr;
        const peaks = [];
        for (const [sign, peak, fill] of [[-1, this.peaks.left, 'rgba(255,255,255,.55)'],
            [1, this.peaks.right, 'rgba(248,81,73,.6)']]) {
            ctx.save();
            ctx.beginPath();
            ctx.arc(cx + sign * gap, cy, ri, -Math.PI / 2, Math.PI / 2, sign < 0);
            ctx.closePath(); ctx.clip();
            ctx.fillStyle = 'rgba(255,255,255,.07)';
            ctx.fillRect(cx - ri - gap, cy - ri, 2 * (ri + gap), 2 * ri);
            const frac = Number.isFinite(peak) ? K.clamp(peak / -fl + 1, 0, 1) : 0;
            ctx.fillStyle = fill;
            ctx.fillRect(cx - ri - gap, cy + ri - 2 * ri * frac, 2 * (ri + gap), 2 * ri * frac);
            ctx.restore();
            peaks.push(peak);
        }
        // Channel labels and peak dB outside the rings at 11 and 1 o'clock,
        // the value on the outer side of its label.
        const labelR = radius + 7 * dpr;
        ctx.font = `700 ${Math.max(9 * dpr, font * .85)}px ui-monospace, monospace`;
        ctx.textBaseline = 'middle';
        const space = ctx.measureText(' ').width;
        for (const [side, a, peak, sign] of [['L', -Math.PI * 2 / 3, peaks[0], -1], ['R', -Math.PI / 3, peaks[1], 1]]) {
            const x = cx + Math.cos(a) * labelR, y = cy + Math.sin(a) * labelR;
            ctx.textAlign = 'center'; ctx.fillStyle = K.css('--muted');
            ctx.fillText(side, x, y);
            const text = Number.isFinite(peak) && peak > -100 ? String(Math.round(peak)) : '—';
            ctx.textAlign = sign < 0 ? 'right' : 'left';
            ctx.fillStyle = peak >= -1 ? '#f85149' : K.css('--muted');
            ctx.fillText(text, x + sign * (ctx.measureText(side).width / 2 + space), y);
        }
    }
};
})();
