/* Cover art: where its visual weight sits, how to place it behind the meters so
 * that weight is in view, and the two narrow vertical views the Cover page can
 * show beside it (level bars, DR).  Covers come from this panel (/qconnect/art),
 * same origin, so their pixels can be read. */
(() => {
'use strict';
const { h } = K;

// ── weight ───────────────────────────────────────────────────────────────────
// The cover is shrunk to 48x48.  The background is the median colour of its
// border; a pixel weighs by how far it is from that colour plus how much it
// differs from its neighbours (edges, detail), squared so the subject dominates
// flat areas.  The result is the weighted centroid, 0..1 from the top left, and
// the spread around it (small = concentrated).  A plain or unreadable cover is
// the centre.
const N = 48;
const cache = new Map();
K.cover = {
    analyze(url) {
        if (!url) return Promise.resolve(null);
        if (cache.has(url)) return cache.get(url);
        const job = new Promise(resolve => {
            const img = new Image();
            img.onload = () => {
                try { resolve(weigh(img)); } catch { resolve({ cx: .5, cy: .5, spread: 1 }); }
            };
            img.onerror = () => resolve(null);
            img.src = url;
        });
        cache.set(url, job);
        while (cache.size > 8) cache.delete(cache.keys().next().value);
        return job;
    },

    /** Square cover of weight `f` in a W x H box: covers the box (no bare band),
     *  anchored at the top left, then slid so the weight is as near the box's
     *  centre as the cover allows.  → { size, x, y } in px. */
    place(W, H, f) {
        const size = Math.max(W, H);
        const fx = f ? f.cx : 0, fy = f ? f.cy : 0;       // unknown: stay anchored top left
        return {
            size,
            x: K.clamp(W / 2 - fx * size, W - size, 0),
            y: K.clamp(H / 2 - fy * size, H - size, 0),
        };
    },
};

function weigh(img) {
    const c = document.createElement('canvas');
    c.width = c.height = N;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, N, N);
    const px = ctx.getImageData(0, 0, N, N).data;
    const at = (x, y) => (y * N + x) * 4;
    const border = [[], [], []];
    for (let i = 0; i < N; i++) for (const [x, y] of [[i, 0], [i, N - 1], [0, i], [N - 1, i]]) {
        const p = at(x, y);
        for (let ch = 0; ch < 3; ch++) border[ch].push(px[p + ch]);
    }
    const bg = border.map(v => v.sort((a, b) => a - b)[v.length >> 1]);
    const dist = (p, q) => Math.hypot(px[p] - px[q], px[p + 1] - px[q + 1], px[p + 2] - px[q + 2]) / 441.7;
    let sw = 0, sx = 0, sy = 0;
    const w = new Float32Array(N * N);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        const p = at(x, y);
        const d = Math.hypot(px[p] - bg[0], px[p + 1] - bg[1], px[p + 2] - bg[2]) / 441.7;
        const g = Math.max(x + 1 < N ? dist(p, at(x + 1, y)) : 0, y + 1 < N ? dist(p, at(x, y + 1)) : 0);
        const v = (0.7 * d + 0.3 * Math.min(1, g * 3)) ** 2;
        w[y * N + x] = v;
        sw += v; sx += v * (x + .5) / N; sy += v * (y + .5) / N;
    }
    if (sw < 1e-3) return { cx: .5, cy: .5, spread: 1, lum: null };
    const cx = sx / sw, cy = sy / sw;
    let sv = 0;
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        sv += w[y * N + x] * (((x + .5) / N - cx) ** 2 + ((y + .5) / N - cy) ** 2);
    }
    // perceived brightness per cell, 0..1, for the meters' contrast (pages/now.js)
    const lum = new Float32Array(N * N);
    for (let k = 0; k < N * N; k++) {
        const lin = c => { c /= 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
        lum[k] = .2126 * lin(px[k * 4]) + .7152 * lin(px[k * 4 + 1]) + .0722 * lin(px[k * 4 + 2]);
    }
    return { cx, cy, spread: Math.sqrt(sv / sw), lum, n: N };
}

// ── vertical level bars ──────────────────────────────────────────────────────
// The same ballistics as the meters (instant attack, glide down, peak hold), as
// two upright LED ladders.
const FLOOR = -60, FALL = 45, HOLD_MS = 1500, HOLD_FALL = 24;
K.VLevels = class VLevels {
    constructor(host) {
        this.canvas = h('canvas', { class: 'vlv-canvas' });
        K.clear(host).append(this.canvas);
        this.tgt = { l: FLOOR, r: FLOOR, lr: FLOOR, rr: FLOOR };
        this.disp = { ...this.tgt };
        this.hold = { l: { db: FLOOR, at: 0 }, r: { db: FLOOR, at: 0 } };
        this.raf = null; this.last = 0;
        this.ro = new ResizeObserver(() => this.draw());
        this.ro.observe(this.canvas);
    }
    update(vu) {
        const v = vu || {}, n = x => Number.isFinite(Number(x)) && x !== null ? Math.max(FLOOR, Number(x)) : FLOOR;
        this.tgt = { l: n(v.left_peak), r: n(v.right_peak), lr: n(v.left_rms), rr: n(v.right_rms) };
        if (!this.raf) { this.last = 0; this.raf = requestAnimationFrame(t => this.step(t)); }
    }
    clear() { this.update({}); }
    step(ts) {
        const dt = this.last ? Math.min(.1, (ts - this.last) / 1000) : 0;
        this.last = ts;
        let settled = true;
        for (const k of Object.keys(this.tgt)) {
            const t = this.tgt[k], d = this.disp[k];
            this.disp[k] = t >= d ? t : Math.max(t, d - FALL * dt);
            if (Math.abs(this.disp[k] - t) > .1) settled = false;
        }
        const now = performance.now();
        for (const ch of ['l', 'r']) {
            const hd = this.hold[ch], pk = this.disp[ch];
            if (pk >= hd.db) { hd.db = pk; hd.at = now; }
            else if (now - hd.at > HOLD_MS) hd.db = Math.max(pk, hd.db - HOLD_FALL * dt);
            if (hd.db > pk + .1) settled = false;
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
        const top = 6 * dpr, labelH = 18 * dpr, bottom = H - labelH, span = bottom - top;
        const y = db => bottom - span * K.clamp(K.voltagePct(db, FLOOR), 0, 100) / 100;
        const g = ctx.createLinearGradient(0, bottom, 0, top);
        const stop = db => K.clamp(K.voltagePct(db, FLOOR) / 100, 0, 1);
        g.addColorStop(0, '#1f8f3a'); g.addColorStop(stop(-18), '#3fb950'); g.addColorStop(stop(-9), '#d8c23a');
        g.addColorStop(stop(-4), '#e3892b'); g.addColorStop(stop(-1), '#f85149'); g.addColorStop(1, '#ff2d2d');
        const scaleW = 22 * dpr, gap = 6 * dpr, bw = Math.max(6 * dpr, (w - scaleW - 3 * gap) / 2);
        ctx.font = `${9.5 * dpr}px ui-monospace, monospace`; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        for (const db of [0, -6, -12, -20, -40]) {
            ctx.fillStyle = db >= -6 ? '#d29922' : '#8b949e';
            ctx.fillText(String(db), scaleW - 3 * dpr, y(db));
        }
        [['L', this.disp.l, this.disp.lr, this.hold.l.db], ['R', this.disp.r, this.disp.rr, this.hold.r.db]].forEach(([label, pk, rms, hold], i) => {
            const x = scaleW + gap + i * (bw + gap);
            ctx.fillStyle = '#10151c';
            ctx.beginPath(); ctx.roundRect(x, top, bw, span, 3 * dpr); ctx.fill();
            const seg = 4 * dpr, sgap = 1.5 * dpr, to = y(pk);
            ctx.fillStyle = g;
            for (let sy = bottom - seg; sy + seg > to; sy -= seg + sgap) ctx.fillRect(x + 2 * dpr, Math.max(sy, to), bw - 4 * dpr, Math.min(seg, sy + seg - to));
            if (rms > FLOOR) { ctx.fillStyle = '#e6edf3'; ctx.fillRect(x - 2 * dpr, y(rms) - 1.5 * dpr, bw + 4 * dpr, 3 * dpr); }
            if (hold > FLOOR) { ctx.fillStyle = hold > -1 ? '#ff5b52' : '#ffffff'; ctx.fillRect(x, y(hold) - 1 * dpr, bw, 2 * dpr); }
            ctx.fillStyle = '#8b949e'; ctx.font = `600 ${12 * dpr}px system-ui, sans-serif`;
            ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
            ctx.fillText(label, x + bw / 2, H - 2 * dpr);
        });
    }
    destroy() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = null; this.ro.disconnect(); }
};

// ── vertical DR ──────────────────────────────────────────────────────────────
// The value on top, and the DR gauge's colour scale standing up with a marker.
K.VDr = host => {
    const value = h('strong', { class: 'vdr-value' }, '—');
    const status = h('span', { class: 'vdr-status' });
    const marker = h('div', { class: 'vdr-marker', hidden: true });
    const stops = [0, 5, 8, 10, 12, 14].map(v => `${K.dr.color(v).bg} ${v / 14 * 100}%`).join(', ');
    const scale = h('div', { class: 'vdr-scale' }, ...[[14, '14+'], [12, '12'], [7, '7'], [0, '0']].map(([v, t]) =>
        h('span', { style: { bottom: `${v / 14 * 100}%` } }, t)));
    K.clear(host).append(h('span', { class: 'lbl' }, 'DR'), value, status,
        h('div', { class: 'vdr-body' }, h('div', { class: 'vdr-track', style: { background: `linear-gradient(0deg, ${stops})` } }, marker), scale));
    return { paint(E) {
        const s = E.summary();
        value.textContent = s.value === null ? '—' : (s.value > 14 ? '14+' : String(s.value));
        value.style.color = s.value === null ? '' : K.dr.color(s.value).bg;
        status.textContent = s.value === null ? s.short : `${s.sampled}s`;
        marker.hidden = s.value === null;
        if (s.value !== null) marker.style.bottom = K.clamp(s.value / 14 * 100, 0, 100) + '%';
    } };
};
})();
