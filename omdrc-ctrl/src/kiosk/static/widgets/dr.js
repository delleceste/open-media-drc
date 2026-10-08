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
    clockLabel(seconds) {
        const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = Math.floor(seconds % 60);
        return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
            : `${m}:${String(s).padStart(2, '0')}`;
    },
};

// ── shared estimator ─────────────────────────────────────────────────────────
// Runs only while something is listening, so a view that is off screen costs the
// host nothing.  listen() returns a handle; close() when the view hides.
K.drEstimate = (() => {
    const E = {
        blocks: [], frame: null, trackAge: 0, total: 0, tracks: [], subs: new Set(), stream: null,
        viewStart: null, viewEnd: null,
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

    // Only the bar uses this viewport. The estimate still uses selected(), and
    // the server's track measurements and database never see these settings.
    E.availableStart = () => E.total - E.blocks.length;
    E.viewRange = () => {
        const oldest = E.availableStart();
        const end = K.clamp(E.viewEnd ?? E.total, oldest, E.total);
        const start = K.clamp(E.viewStart === 'full' ? oldest : E.viewStart ?? E.total - E.selected().length, oldest, end);
        return { start, end };
    };
    E.viewBlocks = () => {
        const { start, end } = E.viewRange();
        return E.blocks.slice(start - E.availableStart(), end - E.availableStart());
    };
    E.viewMarks = () => ({ origin: E.viewRange().start, tracks: E.tracks });
    E.setView = (start, end) => {
        E.viewStart = K.clamp(Math.round(start), E.availableStart(), E.total);
        E.viewEnd = end >= E.total ? null : K.clamp(Math.round(end), E.viewStart, E.total);
        E.emit();
    };
    E.resetView = () => E.setView(E.total, E.total);
    // Keep the left edge attached to the oldest retained block, including
    // blocks that arrive after the popup is closed.
    E.fullView = () => { E.viewStart = 'full'; E.viewEnd = null; E.emit(); };
    E.extendView = edge => {
        const { start, end } = E.viewRange();
        if (edge === 'start') E.setView(start - 20, end);
        if (edge === 'end') E.setView(start, end + 20);
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
        if (Number.isInteger(f.dr && f.dr.total_blocks)) {
            const next = f.dr.total_blocks;
            if (next < E.total) { E.viewStart = null; E.viewEnd = null; }
            E.total = next;
        }
        if (f.dr && Array.isArray(f.dr.tracks)) E.tracks = f.dr.tracks;
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

// A time-axis viewport for the bar. Only the centre grip pans; the edge grips
// resize. The right edge follows live audio when at Now.
K.drViewPopup = (() => {
    let closeCurrent = null;
    return () => {
        if (closeCurrent) closeCurrent();
        const E = K.drEstimate;
        const original = { start: E.viewStart, end: E.viewEnd };
        const axis = h('div', { class: 'dr-view-axis', role: 'group', 'aria-label': 'DR history time axis' });
        const selection = h('div', { class: 'dr-view-selection' },
            h('span', { class: 'dr-view-handle', dataset: { edge: 'start' }, title: 'Drag left edge to change start' }, '⋮'),
            h('span', { class: 'dr-view-move', title: 'Drag here to move the whole window' },
                h('span', {}, '↑'), h('span', {}, '← · →'), h('span', {}, '↓')),
            h('span', { class: 'dr-view-handle', dataset: { edge: 'end' }, title: 'Drag right edge to change end' }, '⋮'));
        axis.append(selection);
        const oldest = h('span', {}), middle = h('span', {}), range = h('span', { class: 'dr-view-range' });
        const step = dir => {
            const { start, end } = E.viewRange();
            const width = end - start;
            const nextStart = K.clamp(start + dir * 20, E.availableStart(), E.total - width);
            E.setView(nextStart, nextStart + width);
        };
        const extendLeft = h('button', { class: 'btn', type: 'button', onclick: () => E.extendView('start') }, '← Extend left');
        const extendRight = h('button', { class: 'btn', type: 'button', onclick: () => E.extendView('end') }, 'Extend right →');
        const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) cancel(); } },
            h('div', { class: 'sheet dr-view-sheet' },
                h('h2', {}, 'DR history window'),
                h('p', { class: 'muted small' }, 'Drag the centre arrows to move the whole window, or an edge grip to change only that edge. Reset starts a new visible bar at Now; Full window follows all retained blocks as time passes. Done keeps the live view. Saved DR records and the live estimate are unchanged.'),
                h('div', { class: 'dr-view-controls' },
                    h('button', { class: 'btn', type: 'button', title: 'One minute earlier', onclick: () => step(-1) }, '−'),
                    axis,
                    h('button', { class: 'btn', type: 'button', title: 'One minute later', onclick: () => step(1) }, '+')),
                h('div', { class: 'dr-view-labels' }, oldest, middle, h('span', {}, 'Now')),
                h('div', { class: 'dr-view-extend' }, extendLeft, extendRight),
                range,
                h('button', { class: 'btn dr-view-albums', type: 'button', onclick: () => { close(); K.openDrAlbums(); } }, 'Albums by DR →'),
                h('div', { class: 'sheet-actions' },
                    h('button', { class: 'btn', type: 'button', onclick: () => cancel() }, 'Cancel'),
                    h('button', { class: 'btn', type: 'button', title: 'Start the visible bar again at Now', onclick: () => E.resetView() }, 'Reset'),
                    h('button', { class: 'btn', type: 'button', title: 'Show all DR blocks still held in memory', onclick: () => E.fullView() }, 'Full window'),
                    h('button', { class: 'btn primary', type: 'button', onclick: () => close() }, 'Done'))));
        const paint = () => {
            const first = E.availableStart(), total = E.total;
            const { start, end } = E.viewRange();
            const span = Math.max(1, total - first);
            const axisWidth = axis.clientWidth || 280;
            const width = Math.max(36, (end - start) / span * axisWidth);
            selection.style.left = `${Math.min((start - first) / span * axisWidth, axisWidth - width)}px`;
            selection.style.width = `${width}px`;
            selection.classList.toggle('compact', width < 80 || end === start);
            extendLeft.disabled = start <= first;
            extendRight.disabled = end >= total;
            oldest.textContent = `−${dr.elapsedLabel((total - first) * BLOCK_S)}`;
            middle.textContent = `−${dr.elapsedLabel(Math.round((total - first) * BLOCK_S / 2))}`;
            range.textContent = `Showing ${dr.elapsedLabel((end - start) * BLOCK_S)} · ${dr.elapsedLabel((total - end) * BLOCK_S)} behind live`;
        };
        const sub = E.listen(paint);
        const close = () => { resize.disconnect(); sub.close(); scrim.remove(); if (closeCurrent === close) closeCurrent = null; };
        const cancel = () => { E.viewStart = original.start; E.viewEnd = original.end; E.emit(); close(); };
        closeCurrent = close;
        document.getElementById('overlay-root').append(scrim);
        paint();
        const resize = new ResizeObserver(paint);
        resize.observe(axis);
        let drag = null;
        axis.addEventListener('pointerdown', e => {
            const edge = e.target.closest('.dr-view-handle')?.dataset.edge;
            const move = e.target.closest('.dr-view-move');
            if (!edge && !move) return;
            const { start, end } = E.viewRange();
            drag = { id: e.pointerId, x: e.clientX, start, end, edge: edge || 'move' };
            axis.setPointerCapture(e.pointerId);
            e.preventDefault();
            e.stopPropagation();
        });
        axis.addEventListener('pointermove', e => {
            if (!drag || drag.id !== e.pointerId) return;
            const width = axis.getBoundingClientRect().width || 1;
            const delta = Math.round((e.clientX - drag.x) / width * E.blocks.length);
            const first = E.availableStart();
            if (drag.edge === 'start') E.setView(K.clamp(drag.start + delta, first, drag.end), drag.end);
            else if (drag.edge === 'end') E.setView(drag.start, K.clamp(drag.end + delta, drag.start, E.total));
            else if (drag.edge === 'move') {
                const span = drag.end - drag.start;
                const start = K.clamp(drag.start + delta, first, E.total - span);
                E.setView(start, start + span);
            }
        });
        const endDrag = e => { if (drag && drag.id === e.pointerId) drag = null; };
        axis.addEventListener('pointerup', endDrag);
        axis.addEventListener('pointercancel', endDrag);
    };
})();

K.wireDrViewPopup = host => {
    let timer = null, start = null, held = false;
    const cancel = () => { clearTimeout(timer); timer = null; };
    host.addEventListener('pointerdown', e => {
        if (e.button || e.target.closest('.splitter')) return;
        held = false;
        start = { x: e.clientX, y: e.clientY };
        timer = setTimeout(() => { held = true; timer = null; K.drViewPopup(); }, 600);
    });
    host.addEventListener('pointermove', e => {
        if (timer && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) cancel();
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(type => host.addEventListener(type, cancel));
    host.addEventListener('click', e => { if (held) { held = false; e.preventDefault(); e.stopPropagation(); } }, true);
    host.addEventListener('contextmenu', e => { e.preventDefault(); if (timer || e.pointerType === 'mouse') { cancel(); held = true; K.drViewPopup(); } });
};

// ── segmented history bar ────────────────────────────────────────────────────
// The bar always spans the full width and holds the whole window; the audio so
// far is divided among as many segments as fit.  A stop, pause or silence is one
// narrow gap segment of its own.  Numbers sit at the segment base; a segment's
// fill is as tall as its RMS level.  Tapping a segment reports its details.
// The view stays continuous across tracks: no segment straddles a track start
// (a solid divider) or a seek (a dashed one), and a tapped segment also names
// its track and the DR of that track alone.
K.DrBar = class DrBar {
    constructor(host, { minCell = 22, onDetail = null, interactive = true } = {}) {
        this.host = host; this.minCell = minCell; this.onDetail = onDetail;
        this.sel = null; this.blocks = []; this.windowSeconds = 60; this.marks = null;
        host.classList.toggle('static', !interactive);
        if (interactive) host.addEventListener('click', ev => {
            const cell = ev.target.closest('[data-r]');
            if (!cell) return;
            const r = Number(cell.dataset.r);
            this.sel = this.sel === r ? null : r;
            this.render(this.blocks, this.windowSeconds, this.marks);
        });
        this.ro = new ResizeObserver(() => this.render(this.blocks, this.windowSeconds, this.marks));
        this.ro.observe(host);
    }
    destroy() { this.ro.disconnect(); }

    render(blocks, windowSeconds, marks = null) {
        this.blocks = blocks; this.windowSeconds = windowSeconds; this.marks = marks;
        const count = blocks.length;
        // cuts, relative to this selection: track starts and seeks inside it
        const origin = marks ? marks.origin : 0, tracks = (marks && marks.tracks) || [];
        const cuts = new Map();
        for (const t of tracks) {
            for (const s of t.seeks || []) if (s - origin > 0 && s - origin < count) cuts.set(s - origin, 'seek');
            if (t.start - origin > 0 && t.start - origin < count) cuts.set(t.start - origin, 'track');
        }
        const trackAt = i => tracks.find(t => t.start <= origin + i && (t.end === null || origin + i < t.end));
        const runs = [];
        for (let i = 0; i < count;) {
            const silent = dr.silent(blocks[i]);
            let j = i + 1;
            while (j < count && dr.silent(blocks[j]) === silent && !cuts.has(j)) j++;
            runs.push({ start: i, end: j, silent, cut: cuts.get(i) || '' });
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
                parts.push({ start: run.start + Math.floor(k * len / n), end: run.start + Math.floor((k + 1) * len / n), silent: false, cut: k ? '' : run.cut });
        }
        if (!parts.length) parts.push({ start: 0, end: 0, silent: false });

        let detail = null, detailLabel = null;
        const cells = parts.map((part, i) => {
            const sample = blocks.slice(part.start, part.end);
            const value = sample.length && !part.silent ? dr.fromBlocks(sample) : null;
            const period = `${Math.round((count - part.end) * BLOCK_S)}–${Math.round((count - part.start) * BLOCK_S)} s before the latest interval`;
            const level = value === null ? -Infinity : dr.levelDb(sample);
            const t = sample.length ? trackAt(part.start) : null;
            const whole = !t ? '' : t.end === null ? 'so far' : t.complete ? 'heard whole' : t.kept ? 'heard in part' : 'too short to keep';
            const about = t ? ` · ${t.title || 'track'}${t.dr !== null && t.dr !== undefined ? `: track DR${Number(t.dr).toFixed(1)} (${whole})` : ''}` : '';
            const title = !sample.length ? 'Waiting for audio'
                : part.silent ? `${period}: stopped, paused or silent`
                : `${period}: DR${value.toFixed(1)}, level ${level.toFixed(1)} dB${about}`;
            const fill = value === null ? 0
                : Math.max(8, Math.min(100, (1 - Math.max(level, LEVEL_FLOOR_DB) / LEVEL_FLOOR_DB) * 100));
            const col = value === null ? null : dr.color(value);
            const fromRight = parts.length - 1 - i;
            const selected = fromRight === this.sel;
            if (selected) {
                detail = title;
                const name = t && t.title ? t.title : part.silent ? 'Silence' : 'Unknown song';
                const chars = [...name];
                const short = chars.length > 30 ? chars.slice(0, 29).join('') + '…' : name;
                detailLabel = `${short} · −${dr.clockLabel((count - part.start) * BLOCK_S)} → −${dr.clockLabel((count - part.end) * BLOCK_S)}`;
            }
            return h('span', {
                dataset: { r: fromRight }, title,
                class: (part.silent ? 'gap ' : '') + (part.cut ? `cut-${part.cut} ` : '') + (selected ? 'selected' : ''),
                style: col ? { flex: `${part.end - part.start} 1 0`, color: fill >= 58 ? col.fg : 'var(--text)' } : {},
            }, col ? h('i', { style: { height: fill.toFixed(1) + '%', background: col.bg } }) : null,
               h('b', {}, value === null ? '' : String(K.clamp(Math.round(value), 0, 99))));
        });
        if (detail === null) this.sel = null;
        K.clear(this.host).append(...cells);
        if (this.onDetail) this.onDetail(detail, count, detailLabel);
    }
};

// ── an album's stored figure (the DR log, dr_store.py) ───────────────────────
// Exact: a solid badge.  Estimate: "≈" and a dashed outline, from part of the
// record heard.  The tooltip says how it was reached.
dr.basisText = s => s.basis === 'report' ? 'from dr14.txt'
    : s.kind === 'exact' ? (s.basis === 'measured' ? 'every track measured' : 'every track heard whole')
    : `estimate · ${s.heard}${s.track_count ? ` of ${s.track_count}` : ''} track${(s.track_count || s.heard) === 1 ? '' : 's'} heard`;
K.drLogBadge = (s, extra = '') => {
    if (!s || s.dr === null || s.dr === undefined) return null;
    const col = dr.color(s.dr);
    const est = s.kind !== 'exact';
    return h('span', {
        class: 'drlog' + (est ? ' est' : '') + (extra ? ' ' + extra : ''),
        title: `DR${s.dr} — ${dr.basisText(s)}`,
        style: est ? { borderColor: col.bg } : { background: col.bg, color: col.fg },
    }, `${est ? '≈' : ''}DR${s.dr}`);
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
