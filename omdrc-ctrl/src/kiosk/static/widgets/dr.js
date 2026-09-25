/* Dynamic range: the standard algorithm on the analyzer's 3-second blocks, the
 * shared estimator (one stream, however many views), the segmented history bar
 * and the gauge.  Same maths and colours as the desktop panel. */
(() => {
'use strict';
const { h } = K;
const BLOCK_S = 3;
const LEVEL_FLOOR_DB = -40;

// ── maths ────────────────────────────────────────────────────────────────────
// block = [[rmsL², rmsR²], [peakL, peakR]]
const dr = K.dr = {
    fromBlocks(blocks) {
        if (!blocks.length) return null;
        const channels = blocks[0][0].length;
        let sum = 0;
        for (let ch = 0; ch < channels; ch++) {
            const rms2 = blocks.map(b => b[0][ch]).sort((a, b) => b - a);
            const peaks = blocks.map(b => b[1][ch]).sort((a, b) => b - a);
            const top = Math.max(1, Math.floor(blocks.length * .2));
            const rms = Math.sqrt(rms2.slice(0, top).reduce((a, b) => a + b, 0) / top);
            const peak = peaks[Math.min(1, peaks.length - 1)];
            sum += rms > 0 && peak > 0 ? 20 * Math.log10(peak / rms) : 0;
        }
        return sum / channels;
    },
    // below -80 dBFS peak for a whole block: stopped, paused or silent
    silent: block => block[1].every(p => p <= 0.0001),
    active(blocks) {
        const last = blocks.findLastIndex(dr.silent);
        return blocks.slice(last + 1);
    },
    levelDb(blocks) {
        let sum = 0, n = 0;
        for (const b of blocks) for (const v of b[0]) { sum += v; n++; }
        return n && sum > 0 ? 10 * Math.log10(sum / n) : -Infinity;
    },
    // red → orange → yellow → progressively stronger greens
    color(value) {
        const stops = [[0, 0, 72, 56], [5, 7, 79, 57], [8, 27, 88, 60],
                       [10, 49, 88, 65], [12, 89, 65, 58], [14, 135, 61, 54]];
        const v = K.clamp(value, 0, 14);
        let up = stops.findIndex(s => s[0] >= v);
        if (up <= 0) up = 1;
        const a = stops[up - 1], b = stops[up], f = (v - a[0]) / (b[0] - a[0]);
        const mix = i => a[i] + (b[i] - a[i]) * f;
        return { bg: `hsl(${mix(1)} ${mix(2)}% ${mix(3)}%)`, fg: `hsl(${mix(1)} ${mix(2)}% 7%)` };
    },
    windowLabel(seconds) {
        const m = seconds / 60;
        return `${m} ${m === 1 ? 'minute' : 'minutes'}`;
    },
    elapsedLabel(seconds) {
        const hh = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
        if (hh) return `${hh} h ${String(m).padStart(2, '0')} min`;
        return m ? (s ? `${m} min ${s} s` : `${m} min`) : `${s} s`;
    },
};

// ── shared estimator ─────────────────────────────────────────────────────────
// Runs only while something is listening, so a view that is off screen costs the
// host nothing.  listen() returns a handle; close() when the view hides.
K.drEstimate = (() => {
    const E = {
        blocks: [], frame: null, trackAge: 0, subs: new Set(), stream: null,
        windowSeconds: K.pref('dr.window', 60),
        detect: K.pref('dr.detect', true),
    };
    if (!(Number.isInteger(E.windowSeconds) && E.windowSeconds >= 60 && E.windowSeconds <= 5400 && E.windowSeconds % 60 === 0))
        E.windowSeconds = 60;

    E.setWindow = s => { E.windowSeconds = K.clamp(Math.round(Number(s) / 60) * 60 || 60, 60, 5400); K.setPref('dr.window', E.windowSeconds); E.emit(); };
    E.setDetect = on => { E.detect = !!on; K.setPref('dr.detect', E.detect); E.emit(); };
    E.emit = () => E.subs.forEach(f => f(E));

    E.selected = () => {
        const want = E.windowSeconds / BLOCK_S;
        const count = E.detect ? Math.min(want, E.trackAge) : want;
        return count > 0 ? E.blocks.slice(-count) : [];
    };

    // What to say and show for the current selection.
    E.summary = () => {
        const f = E.frame, d = (f && f.dr) || {};
        const active = dr.active(E.selected());
        const exact = active.length >= 2 ? dr.fromBlocks(active) : null;
        const out = { value: null, exact: null, status: 'Collecting audio…', short: 'Collecting…', sampled: active.length * BLOCK_S };
        if (!f) return out;
        if (!f.ok) { out.status = f.error || 'Waiting for MPD audio'; out.short = 'No audio'; return out; }
        if (f.source !== 'mpd' || d.state === 'unavailable') { out.status = 'Available while MPD plays a renderer stream'; out.short = 'Needs MPD stream'; return out; }
        if (d.state !== 'ready' || exact === null) {
            out.status = d.playback_state === 'pause' ? 'Paused — waiting for audio'
                : d.playback_state === 'stop' ? 'Stopped — waiting for audio'
                : 'Collecting audio — first estimate after 6 seconds';
            out.short = d.playback_state === 'pause' ? 'Paused' : d.playback_state === 'stop' ? 'Stopped' : 'Collecting…';
            return out;
        }
        out.exact = exact;
        out.value = Math.max(0, Math.round(exact));
        out.status = `${active.length * BLOCK_S}s sampled · rolling ${dr.windowLabel(E.windowSeconds)} window`;
        return out;
    };

    const onFrame = f => {
        E.frame = f;
        if (Array.isArray(f.dr_blocks)) E.blocks = f.dr_blocks;
        if (Number.isInteger(f.dr && f.dr.track_age_blocks)) E.trackAge = f.dr.track_age_blocks;
        E.emit();
    };

    E.listen = fn => {
        E.subs.add(fn);
        if (!E.stream) E.stream = K.streams.open('dr', onFrame);
        fn(E);
        return { close() {
            E.subs.delete(fn);
            if (!E.subs.size && E.stream) { E.stream.close(); E.stream = null; }
        } };
    };
    return E;
})();

// ── segmented history bar ────────────────────────────────────────────────────
// The bar always spans the full width and holds the whole window; the audio so
// far is divided among as many segments as fit.  A stop, pause or silence is one
// narrow gap segment of its own.  Numbers sit at the segment base; a segment's
// fill is as tall as its RMS level.  Tapping a segment reports its details.
K.DrBar = class DrBar {
    constructor(host, { minCell = 22, onDetail = null, interactive = true } = {}) {
        this.host = host; this.minCell = minCell; this.onDetail = onDetail;
        this.sel = null; this.blocks = []; this.windowSeconds = 60;
        host.classList.toggle('static', !interactive);
        if (interactive) host.addEventListener('click', ev => {
            const cell = ev.target.closest('[data-r]');
            if (!cell) return;
            const r = Number(cell.dataset.r);
            this.sel = this.sel === r ? null : r;
            this.render(this.blocks, this.windowSeconds);
        });
        this.ro = new ResizeObserver(() => this.render(this.blocks, this.windowSeconds));
        this.ro.observe(host);
    }
    destroy() { this.ro.disconnect(); }

    render(blocks, windowSeconds) {
        this.blocks = blocks; this.windowSeconds = windowSeconds;
        const count = blocks.length;
        const runs = [];
        for (let i = 0; i < count;) {
            const silent = dr.silent(blocks[i]);
            let j = i;
            while (j < count && dr.silent(blocks[j]) === silent) j++;
            runs.push({ start: i, end: j, silent });
            i = j;
        }
        const gapRuns = runs.filter(r => r.silent).length;
        const audioBlocks = runs.reduce((n, r) => n + (r.silent ? 0 : r.end - r.start), 0);
        const width = this.host.clientWidth || 300;
        const budget = Math.max(runs.length - gapRuns, Math.floor((width - gapRuns * 12) / this.minCell));
        const parts = [];
        for (const run of runs) {
            if (run.silent) { parts.push(run); continue; }
            const len = run.end - run.start;
            const n = Math.max(1, Math.min(len, Math.round(budget * len / audioBlocks)));
            for (let k = 0; k < n; k++)
                parts.push({ start: run.start + Math.floor(k * len / n), end: run.start + Math.floor((k + 1) * len / n), silent: false });
        }
        if (!parts.length) parts.push({ start: 0, end: 0, silent: false });

        let detail = null;
        const cells = parts.map((part, i) => {
            const sample = blocks.slice(part.start, part.end);
            const value = sample.length && !part.silent ? dr.fromBlocks(sample) : null;
            const period = `${Math.round((count - part.end) * BLOCK_S)}–${Math.round((count - part.start) * BLOCK_S)} s before the latest interval`;
            const level = value === null ? -Infinity : dr.levelDb(sample);
            const title = !sample.length ? 'Waiting for audio'
                : part.silent ? `${period}: stopped, paused or silent`
                : `${period}: DR${value.toFixed(1)}, level ${level.toFixed(1)} dB`;
            const fill = value === null ? 0
                : Math.max(8, Math.min(100, (1 - Math.max(level, LEVEL_FLOOR_DB) / LEVEL_FLOOR_DB) * 100));
            const col = value === null ? null : dr.color(value);
            const fromRight = parts.length - 1 - i;
            const selected = fromRight === this.sel;
            if (selected) detail = title;
            return h('span', {
                dataset: { r: fromRight }, title,
                class: (part.silent ? 'gap ' : '') + (selected ? 'selected' : ''),
                style: col ? { flex: `${part.end - part.start} 1 0`, color: fill >= 58 ? col.fg : 'var(--text)' } : {},
            }, col ? h('i', { style: { height: fill.toFixed(1) + '%', background: col.bg } }) : null,
               h('b', {}, value === null ? '' : String(K.clamp(Math.round(value), 0, 99))));
        });
        if (detail === null) this.sel = null;
        K.clear(this.host).append(...cells);
        if (this.onDetail) this.onDetail(detail, count);
    }
};

// ── gauge: a coloured track with a marker at DR 0..14+ ───────────────────────
K.drGauge = host => {
    const marker = h('div', { class: 'drg-marker', hidden: true });
    const stops = [0, 5, 8, 10, 12, 14].map(v => `${dr.color(v).bg} ${v / 14 * 100}%`).join(', ');
    K.clear(host).append(
        h('div', { class: 'drg-track', style: { background: `linear-gradient(90deg, ${stops})` } }, marker),
        h('div', { class: 'drg-scale' },
            h('span', { style: { left: '0%' } }, '0'), h('span', { style: { left: '50%' } }, '7'),
            h('span', { style: { left: '86%' } }, '12'), h('span', { style: { left: '100%' } }, '14+')));
    return { set(value) {
        marker.hidden = value === null;
        if (value !== null) marker.style.left = K.clamp(value / 14 * 100, 0, 100) + '%';
    } };
};
})();
