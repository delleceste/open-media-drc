/* Level meters: analogue needles or LED-style bars, with the same ballistics as
 * the desktop panel (instant attack, knee-gated glide down) plus a peak hold. */
(() => {
'use strict';
const { h, voltagePct, fmtDb } = K;

const FLOOR = -120;          // "no signal"
const SCALE_FLOOR = -60;     // left end of the visible scale
const KNEE = 8, FALL_FAST = 120, FALL_SLOW = 45;   // dB, dB/s, dB/s
const HOLD_MS = 1500, HOLD_FALL = 24;              // peak-hold lamp
const KEYS = ['left_rms', 'right_rms', 'left_peak', 'right_peak'];

const ease = (disp, tgt, dt) => {
    if (tgt >= disp) return tgt;
    const rate = disp - tgt <= KNEE ? FALL_FAST : FALL_SLOW;
    return Math.max(tgt, disp - rate * dt);
};

function fit(canvas) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const hh = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== hh) { canvas.width = w; canvas.height = hh; }
    return { w, h: hh, dpr, ctx: canvas.getContext('2d') };
}

// green → amber → red along the scale, as a fixed gradient across `x0..x1`
function levelGradient(ctx, x0, x1) {
    const g = ctx.createLinearGradient(x0, 0, x1, 0);
    const stop = db => K.clamp(voltagePct(db, SCALE_FLOOR) / 100, 0, 1);
    g.addColorStop(0, '#1f8f3a');
    g.addColorStop(stop(-18), '#3fb950');
    g.addColorStop(stop(-9), '#d8c23a');
    g.addColorStop(stop(-4), '#e3892b');
    g.addColorStop(stop(-1), '#f85149');
    g.addColorStop(1, '#ff2d2d');
    return g;
}

K.VuMeter = class VuMeter {
    /** @param host element to fill; @param mode 'needles' | 'bars' */
    constructor(host, mode = 'needles') {
        this.host = host;
        this.disp = {}; this.tgt = {};
        KEYS.forEach(k => { this.disp[k] = this.tgt[k] = FLOOR; });
        this.hold = { left: { db: FLOOR, at: 0 }, right: { db: FLOOR, at: 0 } };
        this.raf = null; this.last = 0;
        this.glass = 1;          // < 1 only with a cover behind the meters (pages/now.js)
        this.ro = new ResizeObserver(() => this.render());
        this.setMode(mode);
    }

    /** Opacity of the needle faces and bar tracks, 0..1: how much of a cover
     *  behind them shows through.  Scale, needle and readouts stay opaque. */
    setGlass(a) {
        this.glass = K.clamp(a, 0, 1);
        this.render();
    }

    setMode(mode) {
        this.mode = mode === 'bars' ? 'bars' : 'needles';
        K.clear(this.host);
        this.canvases = this.mode === 'bars'
            ? [h('canvas', { class: 'vu-canvas vu-bars' })]
            : [h('canvas', { class: 'vu-canvas vu-needle' }), h('canvas', { class: 'vu-canvas vu-needle' })];
        this.host.classList.toggle('vu-needles', this.mode === 'needles');
        this.host.classList.toggle('vu-barmode', this.mode === 'bars');
        this.canvases.forEach(c => { this.host.append(c); });
        this.ro.disconnect();
        this.ro.observe(this.host);
        this.render();
    }

    /** New analyzer frame's `vu` object. */
    update(vu) {
        const v = vu || {};
        KEYS.forEach(k => { this.tgt[k] = Number.isFinite(Number(v[k])) && v[k] !== null ? Number(v[k]) : FLOOR; });
        if (!this.raf) { this.last = 0; this.raf = requestAnimationFrame(ts => this.step(ts)); }
    }

    /** Jump to a level without animating (idle, disconnect). */
    snap(vu) {
        const v = vu || {};
        KEYS.forEach(k => { this.disp[k] = this.tgt[k] = Number.isFinite(Number(v[k])) && v[k] !== null ? Number(v[k]) : FLOOR; });
        this.hold.left.db = this.hold.right.db = FLOOR;
        if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
        this.render();
    }

    destroy() {
        if (this.raf) cancelAnimationFrame(this.raf);
        this.raf = null;
        this.ro.disconnect();
    }

    step(ts) {
        const dt = this.last ? Math.min(0.1, (ts - this.last) / 1000) : 0;
        this.last = ts;
        let settled = true;
        for (const k of KEYS) {
            this.disp[k] = ease(this.disp[k], this.tgt[k], dt);
            if (Math.abs(this.disp[k] - this.tgt[k]) > 0.1) settled = false;
        }
        // peak hold: latch a new maximum, hold it, then let it fall
        const now = performance.now();
        for (const ch of ['left', 'right']) {
            const hd = this.hold[ch], pk = this.disp[ch + '_peak'];
            if (pk >= hd.db) { hd.db = pk; hd.at = now; }
            else if (now - hd.at > HOLD_MS) hd.db = Math.max(pk, hd.db - HOLD_FALL * dt);
            if (hd.db > pk + 0.1) settled = false;
        }
        this.render();
        this.raf = settled ? null : requestAnimationFrame(t => this.step(t));
    }

    render() {
        if (!this.canvases) return;
        if (this.mode === 'bars') this.drawBars(this.canvases[0]);
        else {
            this.drawNeedle(this.canvases[0], 'L', this.disp.left_rms, this.disp.left_peak);
            this.drawNeedle(this.canvases[1], 'R', this.disp.right_rms, this.disp.right_peak);
        }
    }

    // ── bars ─────────────────────────────────────────────────────────────────
    drawBars(canvas) {
        const { w, h: H, dpr, ctx } = fit(canvas);
        ctx.clearRect(0, 0, w, H);
        const padL = 26 * dpr, padR = 8 * dpr, x0 = padL, x1 = w - padR, span = x1 - x0;
        const scaleH = 22 * dpr;
        const rowH = (H - scaleH - 10 * dpr) / 2, barH = Math.min(rowH * .72, 46 * dpr);
        const x = db => x0 + span * K.clamp(voltagePct(db, SCALE_FLOOR), 0, 100) / 100;
        const grad = levelGradient(ctx, x0, x1);
        const rows = [['L', this.disp.left_peak, this.disp.left_rms, this.hold.left.db],
                      ['R', this.disp.right_peak, this.disp.right_rms, this.hold.right.db]];
        rows.forEach(([label, peak, rms, hold], i) => {
            const cy = 5 * dpr + rowH * i + rowH / 2, y = cy - barH / 2;
            ctx.fillStyle = '#10151c';
            if (this.glass < 1) ctx.globalAlpha = this.glass;
            ctx.beginPath(); ctx.roundRect(x0, y, span, barH, 4 * dpr); ctx.fill();
            ctx.globalAlpha = 1;
            // segmented fill, like an LED ladder
            const seg = 5 * dpr, gap = 1.5 * dpr, fillTo = x(peak);
            ctx.save();
            ctx.beginPath(); ctx.roundRect(x0, y, span, barH, 4 * dpr); ctx.clip();
            ctx.fillStyle = grad;
            for (let sx = x0; sx < fillTo; sx += seg + gap) ctx.fillRect(sx, y + 2 * dpr, Math.min(seg, fillTo - sx), barH - 4 * dpr);
            ctx.restore();
            // RMS witness and peak-hold line
            if (rms > SCALE_FLOOR) {
                ctx.fillStyle = '#e6edf3';
                ctx.fillRect(x(rms) - 1.5 * dpr, y - 3 * dpr, 3 * dpr, barH + 6 * dpr);
            }
            if (hold > SCALE_FLOOR) {
                ctx.fillStyle = hold > -1 ? '#ff5b52' : '#ffffff';
                ctx.fillRect(x(hold) - 1 * dpr, y, 2 * dpr, barH);
            }
            ctx.fillStyle = '#8b949e';
            ctx.font = `600 ${13 * dpr}px system-ui, sans-serif`;
            ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
            ctx.fillText(label, 6 * dpr, cy);
        });
        // scale
        ctx.font = `${10.5 * dpr}px ui-monospace, monospace`;
        ctx.textBaseline = 'top'; ctx.textAlign = 'center';
        const yScale = H - scaleH + 2 * dpr;
        for (const db of [-60, -40, -30, -20, -12, -6, -3, 0]) {
            const px = x(db);
            ctx.strokeStyle = '#3b444f';
            ctx.beginPath(); ctx.moveTo(px, yScale - 2 * dpr); ctx.lineTo(px, yScale + 3 * dpr); ctx.stroke();
            if (db === -40 || db === -30) continue;      // ticks only: too close to label
            ctx.fillStyle = db >= -6 ? '#d29922' : '#8b949e';
            ctx.fillText(db === 0 ? '0 dBFS' : String(db), K.clamp(px, 22 * dpr, w - 24 * dpr), yScale + 5 * dpr);
        }
    }

    // ── needles ──────────────────────────────────────────────────────────────
    drawNeedle(canvas, label, rmsDb, peakDb) {
        const { w, h: H, dpr, ctx } = fit(canvas);
        ctx.clearRect(0, 0, w, H);
        // lit amber face
        const face = ctx.createRadialGradient(w / 2, H * .95, H * .05, w / 2, H * .95, Math.max(w, H) * .9);
        face.addColorStop(0, '#f2d78a'); face.addColorStop(.55, '#c99a3c'); face.addColorStop(1, '#5a4218');
        if (this.glass < 1) ctx.globalAlpha = this.glass;
        ctx.fillStyle = face;
        ctx.beginPath(); ctx.roundRect(0, 0, w, H, 10 * dpr); ctx.fill();
        ctx.fillStyle = 'rgba(0,0,0,.18)';
        ctx.beginPath(); ctx.roundRect(0, 0, w, H, 10 * dpr); ctx.fill();
        ctx.globalAlpha = 1;
        // See-through face: a faint lamp-coloured halo keeps the dark scale legible on the cover
        if (this.glass < 1) { ctx.shadowColor = 'rgba(242,215,138,.85)'; ctx.shadowBlur = 4 * dpr; }

        const A0 = -150, SPAN = 120;
        const ang = db => (A0 + SPAN * voltagePct(db, SCALE_FLOOR) / 100) * Math.PI / 180;
        const cx = w / 2, cy = H * .93, R = Math.min(w * .47, H * .73);
        ctx.lineCap = 'round';
        // scale arc, with the last stretch in red
        ctx.lineWidth = 2.5 * dpr;
        ctx.strokeStyle = '#2a1e08';
        ctx.beginPath(); ctx.arc(cx, cy, R, ang(SCALE_FLOOR), ang(-6)); ctx.stroke();
        ctx.strokeStyle = '#b3261e'; ctx.lineWidth = 5 * dpr;
        ctx.beginPath(); ctx.arc(cx, cy, R, ang(-6), ang(0)); ctx.stroke();
        // ticks and numbers
        ctx.font = `700 ${11 * dpr}px ui-monospace, monospace`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        for (const [db, big] of [[-40, 0], [-30, 0], [-20, 1], [-12, 1], [-9, 0], [-6, 1], [-3, 1], [0, 1]]) {
            const a = ang(db), inner = big ? .84 : .9;
            ctx.strokeStyle = db >= -6 ? '#7a1610' : '#2a1e08';
            ctx.lineWidth = (big ? 2 : 1.2) * dpr;
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(a) * R * inner, cy + Math.sin(a) * R * inner);
            ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
            ctx.stroke();
            if (big) {
                ctx.fillStyle = db >= -6 ? '#7a1610' : '#2a1e08';
                ctx.fillText(String(db), cx + Math.cos(a) * R * .70, cy + Math.sin(a) * R * .70);
            }
        }
        // RMS witness: a short arc segment just inside the scale
        if (rmsDb > SCALE_FLOOR) {
            ctx.strokeStyle = '#0b5fa5'; ctx.lineWidth = 4 * dpr;
            ctx.beginPath(); ctx.arc(cx, cy, R * .93, ang(SCALE_FLOOR), ang(rmsDb)); ctx.stroke();
        }
        // needle (peak), with a soft shadow
        ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0;
        const a = ang(peakDb);
        ctx.save();
        ctx.shadowColor = 'rgba(0,0,0,.45)'; ctx.shadowBlur = 6 * dpr; ctx.shadowOffsetX = 2 * dpr;
        ctx.strokeStyle = '#1a1206'; ctx.lineWidth = 2.4 * dpr;
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(a) * R * .96, cy + Math.sin(a) * R * .96); ctx.stroke();
        ctx.restore();
        ctx.fillStyle = '#1a1206';
        ctx.beginPath(); ctx.arc(cx, cy, 6 * dpr, 0, Math.PI * 2); ctx.fill();
        // label + readout
        if (this.glass < 1) { ctx.shadowColor = 'rgba(242,215,138,.85)'; ctx.shadowBlur = 4 * dpr; }
        ctx.fillStyle = '#2a1e08';
        ctx.font = `800 ${16 * dpr}px system-ui, sans-serif`;
        ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(label, 10 * dpr, 8 * dpr);
        ctx.font = `600 ${10.5 * dpr}px ui-monospace, monospace`;
        ctx.textAlign = 'right';
        ctx.fillText(`PK ${fmtDb(peakDb)}  RMS ${fmtDb(rmsDb)}`, w - 10 * dpr, 10 * dpr);
        ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0;
    }
};
})();
