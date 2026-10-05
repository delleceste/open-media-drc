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
        this.clips = { left: 0, right: 0 };
        this.suspects = { left: false, right: false };
        this.blinkOn = false; this.blinkTimer = null;
        this.clipTargets = {};
        this.raf = null; this.last = 0;
        this.glass = 1;          // < 1 only with a cover behind the meters (pages/now.js)
        this.ro = new ResizeObserver(() => this.render());
        K.onTheme(() => this.render());     // a still meter is not redrawn by itself
        this.setMode(mode);
    }

    /** Opacity of the needle faces and bar tracks, 0..1: how much of a cover
     *  behind them shows through.  Scale, needle and readouts stay opaque. */
    setGlass(a) {
        this.glass = K.clamp(a, 0, 1);
        this.render();
    }

    /** Brightness (linear, 0..1) of what shows behind each needle face, or null:
     *  over a light cover the gauge switches to dark ink. */
    setBackdrop(lums) {
        this.backdrop = lums;
        this.render();
    }

    setClips(clips) {
        this.clips = { left: clips.left || 0, right: clips.right || 0 };
        this.syncBlink();
        this.render();
    }

    /** A meter-timing calibration is listening: the readouts and the dB scale say so. */
    setCalibrating(on) {
        on = !!on;
        if (on === !!this.calibrating) return;
        this.calibrating = on;
        this.render();
    }

    /** Missing calibration replaces PK/RMS with the page's red warning + chip. */
    setTimingMissing(on) {
        if (!!on === !!this.timingMissing) return;
        this.timingMissing = !!on;
        this.render();
    }

    /** "LAG!" where CLIP would be: level frames were dropped by the network. */
    setLag(on) {
        on = !!on;
        if (on === !!this.lag) return;
        this.lag = on;
        this.render();
    }

    setSuspects(suspects) {
        this.suspects = { left: !!suspects.left, right: !!suspects.right };
        this.syncBlink();
        this.render();
    }

    syncBlink() {
        const pending = ['left', 'right'].some(ch => this.suspects[ch] && !this.clips[ch]);
        if (pending && !this.blinkTimer) {
            this.blinkOn = true;
            this.blinkTimer = setInterval(() => { this.blinkOn = !this.blinkOn; this.render(); }, 450);
        } else if (!pending && this.blinkTimer) {
            clearInterval(this.blinkTimer);
            this.blinkTimer = null;
            this.blinkOn = false;
        }
    }

    clipAt(canvas, e) {
        return Object.entries(this.clipTargets).find(([ch, r]) => this.clips[ch] && r.canvas === canvas &&
            e.offsetX >= r.x0 && e.offsetX <= r.x1 && e.offsetY >= r.y0 && e.offsetY <= r.y1)?.[0] || null;
    }

    setMode(mode) {
        this.mode = mode === 'bars' ? 'bars' : 'needles';
        K.clear(this.host);
        this.canvases = this.mode === 'bars'
            ? [h('canvas', { class: 'vu-canvas vu-bars' })]
            : [h('canvas', { class: 'vu-canvas vu-needle' }), h('canvas', { class: 'vu-canvas vu-needle' })];
        this.host.classList.toggle('vu-needles', this.mode === 'needles');
        this.host.classList.toggle('vu-barmode', this.mode === 'bars');
        this.clipTargets = {};
        this.canvases.forEach(c => {
            this.host.append(c);
            let pressed = null, consumed = false;
            c.addEventListener('pointerdown', e => {
                consumed = false;
                pressed = this.clipAt(c, e);
                if (pressed) e.stopPropagation();
            });
            c.addEventListener('pointerup', e => {
                if (!pressed) return;
                e.stopPropagation();
                consumed = true;
                const ch = this.clipAt(c, e);
                if (ch === pressed && this.onClipReset) this.onClipReset(ch);
                pressed = null;
            });
            c.addEventListener('pointercancel', () => { pressed = null; });
            c.addEventListener('click', e => { if (consumed) { e.stopPropagation(); consumed = false; } });
        });
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
        if (this.blinkTimer) clearInterval(this.blinkTimer);
        this.blinkTimer = null;
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
        const padL = 26 * dpr, padR = 32 * dpr, x0 = padL, x1 = w - padR, span = x1 - x0;
        const scaleH = 22 * dpr;
        const rowH = (H - scaleH - 10 * dpr) / 2, barH = Math.min(rowH * .72, 46 * dpr);
        const x = db => x0 + span * K.clamp(voltagePct(db, SCALE_FLOOR), 0, 100) / 100;
        const grad = levelGradient(ctx, x0, x1);
        const rows = [['L', this.disp.left_peak, this.disp.left_rms, this.hold.left.db],
                      ['R', this.disp.right_peak, this.disp.right_rms, this.hold.right.db]];
        rows.forEach(([label, peak, rms, hold], i) => {
            const cy = 5 * dpr + rowH * i + rowH / 2, y = cy - barH / 2;
            ctx.fillStyle = K.css('--meter-face');
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
                ctx.fillStyle = K.css('--meter-mark');
                ctx.fillRect(x(rms) - 1.5 * dpr, y - 3 * dpr, 3 * dpr, barH + 6 * dpr);
            }
            if (hold > SCALE_FLOOR) {
                ctx.fillStyle = hold > -1 ? '#ff5b52' : K.css('--meter-hold');
                ctx.fillRect(x(hold) - 1 * dpr, y, 2 * dpr, barH);
            }
            ctx.fillStyle = K.css('--muted');
            ctx.font = `600 ${13 * dpr}px system-ui, sans-serif`;
            ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
            ctx.fillText(label, 6 * dpr, cy);
            const ch = i ? 'right' : 'left';
            const state = this.clips[ch] ? 'clip' : this.suspects[ch] && this.blinkOn ? 'warn' : this.lag ? 'lag' : '';
            if (state) {
                ctx.save();
                ctx.translate(w - 13 * dpr, cy);
                ctx.rotate(-Math.PI / 2);
                ctx.fillStyle = state === 'clip' ? K.css('--red') : K.theme() === 'light' ? '#a54800' : '#ffa726';
                ctx.font = `800 ${11 * dpr}px system-ui, sans-serif`;
                ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                const text = state === 'clip' ? `CLIP ${this.clips[ch]}` : state === 'lag' ? 'LAG!' : 'CLIP?';
                ctx.fillText(text, 0, 0, rowH - 8 * dpr);
                ctx.restore();
                if (state === 'clip') this.clipTargets[ch] = { canvas, x0: w / dpr - 32, x1: w / dpr,
                    y0: (cy - rowH / 2) / dpr, y1: (cy + rowH / 2) / dpr };
            }
            if (state !== 'clip') delete this.clipTargets[ch];
        });
        // scale
        ctx.font = `${10.5 * dpr}px ui-monospace, monospace`;
        ctx.textBaseline = 'top'; ctx.textAlign = 'center';
        const yScale = H - scaleH + 2 * dpr;
        if (this.calibrating) {
            ctx.fillStyle = K.theme() === 'light' ? '#0969da' : '#58a6ff';
            ctx.font = `700 ${11.5 * dpr}px system-ui, sans-serif`;
            ctx.fillText('CALIBRATING…', w / 2, yScale + 4 * dpr);
            return;
        }
        for (const db of [-60, -40, -30, -20, -12, -6, -3, 0]) {
            const px = x(db);
            ctx.strokeStyle = K.css('--meter-tick');
            ctx.beginPath(); ctx.moveTo(px, yScale - 2 * dpr); ctx.lineTo(px, yScale + 3 * dpr); ctx.stroke();
            if (db === -40 || db === -30) continue;      // ticks only: too close to label
            ctx.fillStyle = db >= -6 ? K.css('--yellow') : K.css('--muted');
            ctx.fillText(db === 0 ? '0 dBFS' : String(db), K.clamp(px, 22 * dpr, w - 24 * dpr), yScale + 5 * dpr);
        }
    }

    // ── needles ──────────────────────────────────────────────────────────────
    // Dark gauge in the app's own style: black face, white scale and needle, red for the
    // last 6 dB.  Inside the scale a glowing arc follows the peak (green -> amber -> red
    // along the scale) over a softer band for the RMS; a small dot holds the recent peak.
    drawNeedle(canvas, label, rmsDb, peakDb) {
        const { w, h: H, dpr, ctx } = fit(canvas);
        ctx.clearRect(0, 0, w, H);
        const see = this.glass < 1;                       // a cover shows through the face
        // Ink: light on the dark face, dark on the light theme's face; over a cover, dark
        // when a light cover dominates what is behind.  Contrast of white vs black
        // against that brightness decides (WCAG luminance).
        const bg = see && this.backdrop ? (this.backdrop[label === 'L' ? 0 : 1] || 0) : 0;
        const dark = see ? (bg + .05) / .05 > 1.05 / (bg + .05) : K.theme() === 'light';
        const I = dark ? {
            ink: '#0d1117', minor: '#3d444d', text: '#1f2328', dim: '#3d444d', red: '#cf222e', redText: '#a40e26',
            track: 'rgba(0,0,0,.14)', halo: 'rgba(255,255,255,.8)', glow: 'rgba(255,255,255,.55)',
            cap: '#f0f6fc', ring: '#0d1117', hold: '#0d1117',
            lvl: ['#116329', '#1a7f37', '#9a6700', '#bc4c00', '#cf222e', '#a40e26'],
        } : {
            ink: '#f0f6fc', minor: '#8b949e', text: '#c9d1d9', dim: '#8b949e', red: '#f85149', redText: '#ff7b72',
            track: 'rgba(255,255,255,.06)', halo: 'rgba(0,0,0,.85)', glow: 'rgba(240,246,252,.35)',
            cap: '#0d1117', ring: '#f0f6fc', hold: '#f0f6fc',
            lvl: ['#1f8f3a', '#3fb950', '#d8c23a', '#f0883e', '#f85149', '#ff3b30'],
        };
        const A0 = -150, SPAN = 120;
        const ang = db => (A0 + SPAN * voltagePct(db, SCALE_FLOOR) / 100) * Math.PI / 180;
        // the pivot's cap (10 px) stays above the bottom edge, the long ticks (1.1 R) below the top
        const cx = w / 2, cy = Math.min(H * .93, H - 10 * dpr);
        const R = Math.max(4 * dpr, Math.min(w * .47, H * .73, (cy - 3 * dpr) / 1.1));
        const at = (a, r) => [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
        const aFloor = ang(SCALE_FLOOR), aRed = ang(-6), aTop = ang(0);

        // face: the app's black, a faint lift under the scale, a hairline border
        ctx.globalAlpha = see ? this.glass : 1;
        const face = ctx.createRadialGradient(cx, cy, R * .1, cx, cy, R * 1.25);
        face.addColorStop(0, K.css('--gauge-face-a')); face.addColorStop(1, K.css('--gauge-face-b'));
        ctx.fillStyle = face;
        ctx.beginPath(); ctx.roundRect(0, 0, w, H, 10 * dpr); ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = K.css('--border'); ctx.lineWidth = 1 * dpr;
        ctx.beginPath(); ctx.roundRect(.5 * dpr, .5 * dpr, w - dpr, H - dpr, 10 * dpr); ctx.stroke();
        // over a cover everything drawn below gets a dark halo, so it stays legible
        const halo = on => { ctx.shadowColor = on && see ? I.halo : 'transparent'; ctx.shadowBlur = on && see ? 5 * dpr : 0; };
        ctx.lineCap = 'round';

        // level colours along the scale (a conic gradient: colour follows the angle)
        // (started a little before the floor, so a round line end there stays green)
        const g0 = aFloor - .3;
        const conic = ctx.createConicGradient(g0, cx, cy);
        const stop = db => K.clamp((ang(db) - g0) / (2 * Math.PI), 0, 1);
        conic.addColorStop(0, I.lvl[0]); conic.addColorStop(stop(-18), I.lvl[1]);
        conic.addColorStop(stop(-9), I.lvl[2]); conic.addColorStop(stop(-4), I.lvl[3]);
        conic.addColorStop(stop(-1), I.lvl[4]); conic.addColorStop(stop(0), I.lvl[5]);
        conic.addColorStop(Math.min(1, stop(0) + .001), I.lvl[5]);

        // track for the level arcs
        const rLvl = R * .87, wLvl = Math.max(5, R * .075);
        ctx.strokeStyle = I.track; ctx.lineWidth = wLvl;
        ctx.beginPath(); ctx.arc(cx, cy, rLvl, aFloor, aTop); ctx.stroke();
        // RMS: a soft band
        if (rmsDb > SCALE_FLOOR) {
            ctx.globalAlpha = dark ? .5 : .38; ctx.strokeStyle = conic; ctx.lineWidth = wLvl;
            ctx.beginPath(); ctx.arc(cx, cy, rLvl, aFloor, ang(rmsDb)); ctx.stroke();
            ctx.globalAlpha = 1;
        }
        // peak: the bright, glowing arc
        if (peakDb > SCALE_FLOOR) {
            ctx.save();
            ctx.shadowColor = peakDb > -6 ? 'rgba(248,81,73,.8)' : 'rgba(63,185,80,.55)';
            ctx.shadowBlur = 10 * dpr;
            ctx.strokeStyle = conic; ctx.lineWidth = wLvl * .45;
            ctx.beginPath(); ctx.arc(cx, cy, rLvl, aFloor, ang(peakDb)); ctx.stroke();
            ctx.restore();
        }
        // peak hold: a small dot on the scale
        const hold = (label === 'L' ? this.hold.left : this.hold.right).db;
        if (hold > SCALE_FLOOR) {
            const [hx, hy] = at(ang(hold), rLvl);
            ctx.fillStyle = hold > -6 ? I.red : I.hold;
            ctx.beginPath(); ctx.arc(hx, hy, wLvl * .32, 0, Math.PI * 2); ctx.fill();
        }

        // the scale: a white arc to -6 dB, then red; ticks outside, numbers inside
        halo(true);
        ctx.strokeStyle = I.ink; ctx.lineWidth = 2 * dpr;
        ctx.beginPath(); ctx.arc(cx, cy, R, aFloor, aRed); ctx.stroke();
        ctx.strokeStyle = I.red; ctx.lineWidth = 3.5 * dpr;
        ctx.beginPath(); ctx.arc(cx, cy, R, aRed, aTop); ctx.stroke();
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        for (const [db, big] of [[-40, 0], [-30, 0], [-20, 1], [-15, 0], [-12, 1], [-9, 0], [-6, 1], [-4, 0], [-3, 1], [-2, 0], [-1, 0], [0, 1]]) {
            const a = ang(db);
            const [x0, y0] = at(a, R * (big ? 1.1 : 1.06)), [x1, y1] = at(a, R * 1.005);
            ctx.strokeStyle = db >= -6 ? I.red : big ? I.ink : I.minor;
            ctx.lineWidth = (big ? 2 : 1.2) * dpr;
            ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
            if (big) {
                const [tx, ty] = at(a, R * .70);
                ctx.fillStyle = db >= -6 ? I.redText : I.text;
                ctx.font = `${db === 0 ? 700 : 500} ${11.5 * dpr}px system-ui, sans-serif`;
                ctx.fillText(String(db), tx, ty);
            }
        }

        // needle: white, tapered, with a soft glow; pivot with a dark cap and white ring
        const a = ang(peakDb), len = R * .98, base = 3.2 * dpr;
        const nx = Math.cos(a), ny = Math.sin(a), px = -ny, py = nx;
        ctx.save();
        ctx.shadowColor = I.glow; ctx.shadowBlur = 8 * dpr;
        ctx.fillStyle = I.ink;
        ctx.beginPath();
        ctx.moveTo(cx + px * base, cy + py * base);
        ctx.lineTo(cx + nx * len, cy + ny * len);
        ctx.lineTo(cx - px * base, cy - py * base);
        ctx.closePath(); ctx.fill();
        ctx.restore();
        ctx.fillStyle = I.cap;
        ctx.beginPath(); ctx.arc(cx, cy, 7.5 * dpr, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = I.ring; ctx.lineWidth = 2 * dpr;
        ctx.beginPath(); ctx.arc(cx, cy, 7.5 * dpr, 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = peakDb > -6 ? I.red : (dark ? '#0969da' : '#58a6ff');
        ctx.beginPath(); ctx.arc(cx, cy, 2.6 * dpr, 0, Math.PI * 2); ctx.fill();

        // channel and readout
        halo(true);
        ctx.fillStyle = I.ink;
        ctx.font = `800 ${16 * dpr}px system-ui, sans-serif`;
        ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(label, 11 * dpr, 9 * dpr);
        ctx.font = `500 ${10.5 * dpr}px ui-monospace, monospace`;
        ctx.textAlign = 'right';
        ctx.fillStyle = I.dim;
        if (this.calibrating) {
            ctx.font = `700 ${10.5 * dpr}px system-ui, sans-serif`;
            ctx.fillStyle = '#58a6ff';
        }
        ctx.fillText(this.calibrating ? 'CALIBRATING…' : this.timingMissing ? '' : `PK ${fmtDb(peakDb)}  RMS ${fmtDb(rmsDb)}`, w - 11 * dpr, 11 * dpr);
        halo(false);
        const ch = label === 'L' ? 'left' : 'right';
        const state = this.clips[ch] ? 'clip' : this.suspects[ch] && this.blinkOn ? 'warn' : this.lag ? 'lag' : '';
        if (state) {
            const text = state === 'clip' ? `CLIP ${this.clips[ch]}` : state === 'lag' ? 'LAG!' : 'CLIP?';
            // Sit beside the full-scale needle, with the word parallel to it.
            const [zeroX, zeroY] = at(aTop, R * .93);
            const tx = Math.min(w - 19 * dpr, zeroX + 9 * dpr);
            const ty = zeroY + 16 * dpr;
            ctx.save();
            ctx.translate(tx, ty);
            ctx.rotate(aTop);
            ctx.font = `800 ${12 * dpr}px system-ui, sans-serif`;
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            const maxWidth = R * .65;
            const width = Math.min(ctx.measureText(text).width, maxWidth);
            ctx.lineWidth = 3 * dpr;
            ctx.strokeStyle = dark ? '#fff' : '#000';
            ctx.strokeText(text, 0, 0, maxWidth);
            ctx.fillStyle = state === 'clip' ? I.red : dark ? '#a54800' : '#ffa726';
            ctx.fillText(text, 0, 0, maxWidth);
            ctx.restore();
            const hitX = Math.abs(Math.cos(aTop)) * width / 2 + 5 * dpr;
            const hitY = Math.abs(Math.sin(aTop)) * width / 2 + 8 * dpr;
            if (state === 'clip') this.clipTargets[ch] = { canvas,
                x0: (tx - hitX) / dpr - 8, x1: (tx + hitX) / dpr + 8,
                y0: (ty - hitY) / dpr - 8, y1: (ty + hitY) / dpr + 8 };
        }
        if (state !== 'clip') delete this.clipTargets[ch];
    }
};
})();
