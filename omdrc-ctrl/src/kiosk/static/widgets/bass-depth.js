/* Bass depth card, shared by the web UI (templates/index.html) and the kiosk
 * (pages/bass.js).  In a tracking session the listener starts, the track
 * playing is analysed and kept on the music drive; the card shows the
 * analysis as a timeline of bass passages per context, and three buttons
 * say how deep the basses sound right now.  Server: /bass*, bass_live.py. */
(() => {
'use strict';

const CONTEXTS = [['solo', 'Solo'], ['with-sections', 'With sections'], ['tutti', 'Tutti'], ['pizzicato', 'Pizzicato']];
const SCORED = new Set(['solo', 'with-sections']);
const VERDICTS = [['forward', 'Forward'], ['between', 'In between'], ['back', 'Back']];
const VERDICT_LABEL = Object.fromEntries(VERDICTS);
const POLL_MS = 2000;

const h = (tag, attrs, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (v === true) el.setAttribute(k, '');
        else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
        if (kid === null || kid === undefined || kid === false) continue;
        el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
};
const clear = el => { el.replaceChildren(); return el; };
const clock = s => {
    if (!Number.isFinite(s) || s < 0) return '–:––';
    s = Math.floor(s);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const num = (v, d = 2, sign = false) => Number.isFinite(v) ? (sign && v >= 0 ? '+' : '') + v.toFixed(d) : '–';
const api = async (url, json) => {
    try {
        const init = { cache: 'no-store' };
        if (json !== undefined) Object.assign(init, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(json) });
        const r = await fetch(url, init);
        try { return await r.json(); } catch { return { ok: false, error: `HTTP ${r.status}` }; }
    } catch { return { ok: false, error: 'network error' }; }
};

const STATUS = {
    off: 'No tracking session',
    idle: 'Waiting for a track',
    fetching: 'Fetching the track again…',
    analysing: 'Analysing…',
    done: 'Analysed',
    skipped: 'Not analysed',
    error: 'Analysis failed',
};

function mount(root) {
    const S = { view: null, analysis: null, stamp: '', timer: null, tick: null, polledAt: 0, lastTap: null, picked: null };

    const statusLine = h('div', { class: 'bd-status' });
    const sessionName = h('input', { type: 'text', class: 'bd-note', maxlength: 200, placeholder: 'What will you evaluate? e.g. Altinoglu, Shostakovich 8' });
    sessionName.addEventListener('keydown', e => { if (e.key === 'Enter') startSession(); });
    const startBox = h('div', { class: 'bd-session' }, sessionName,
        h('button', { type: 'button', class: 'bd-btn bd-start', onclick: startSession }, 'Start tracking session'));
    const sessionLine = h('span', { class: 'bd-session-line' });
    const runBox = h('div', { class: 'bd-session', hidden: true }, sessionLine,
        h('button', { type: 'button', class: 'bd-btn', onclick: stopSession }, 'Stop session'));
    const saveLine = h('div', { class: 'bd-muted bd-save' });
    const againBtn = h('button', { type: 'button', class: 'bd-btn', onclick: reanalyse, hidden: true }, 'Analyse again');
    const trackLine = h('div', { class: 'bd-track' });
    const score = h('div', { class: 'bd-score' });
    const timeline = h('div', { class: 'bd-tl', onclick: pick });
    const pickLine = h('div', { class: 'bd-pick', hidden: true });
    const table = h('div', { class: 'bd-table' });
    const fbButtons = h('div', { class: 'bd-verdicts' }, VERDICTS.map(([v, label]) =>
        h('button', { type: 'button', class: `bd-btn bd-verdict ${v}`, onclick: () => tap(v) }, label)));
    const noteInput = h('input', { type: 'text', class: 'bd-note', maxlength: 2000, placeholder: 'Optional note: what you hear, which instrument…' });
    const noteBox = h('div', { class: 'bd-notebox', hidden: true },
        h('div', { class: 'bd-tapped' }), noteInput,
        h('button', { type: 'button', class: 'bd-btn', onclick: saveNote }, 'Save note'));
    noteInput.addEventListener('keydown', e => { if (e.key === 'Enter') saveNote(); });
    const tapList = h('div', { class: 'bd-taps' });
    const msg = h('div', { class: 'bd-msg', role: 'status' });

    clear(root).append(h('div', { class: 'bass-depth' },
        startBox, runBox, saveLine,
        h('div', { class: 'bd-head' }, statusLine, h('span', { class: 'bd-spacer' }), againBtn),
        trackLine, score, timeline, pickLine, table,
        h('div', { class: 'bd-fb' },
            h('div', { class: 'bd-fb-q' }, 'How deep do the basses sound right now?'),
            fbButtons, noteBox, msg, tapList),
        h('details', { class: 'bd-explain' }, h('summary', {}, 'How this works'),
            h('p', {}, 'Nothing happens until you start a tracking session, for the recordings you mean to evaluate. During a session every track played is analysed in the background and kept on the music drive (a streamed track is fetched again, a local one copied), so the drive can travel with you. The analysis finds the passages where the double basses play alone, with other sections, under a tutti, or pizzicato. Each passage gets a depth score from 0 (forward, spot-mic’d) to 10 (back, in the hall): green 7 and above, yellow 4–7, red below 4. Grey passages are too short to score (a single plucked note is scored on its own). Only solo and with-sections passages count towards the track score; tutti and pizzicato are shown but not yet calibrated. Bowed basses are searched below C2 (65 Hz), where no cello reaches; plucked lines sit higher, so pizzicato is searched up to G2 (98 Hz), and there it may include cellos playing the same notes.'),
            h('p', {}, 'Tap Forward, In between or Back whenever the basses catch your ear. The box records the moment you heard (the DRC delay is taken off) and the passage under it, plus your note. Sessions, taps and analyses travel with the DR log’s sync, and are used to recalibrate the score.'),
            h('p', {}, 'Background: doc/BASS-DEPTH-DETECTOR.md in the open-media-drc repository.'))));

    function paintHead() {
        const v = S.view;
        if (!v) return;
        v.enabled = !!v.session;
        startBox.hidden = v.enabled;
        runBox.hidden = !v.enabled;
        if (v.session) {
            const since = new Date(v.session.started * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            sessionLine.textContent = `Session${v.session.name ? ` “${v.session.name}”` : ''} since ${since} · ${v.session.tracks} track${v.session.tracks === 1 ? '' : 's'} · ${v.session.taps} tap${v.session.taps === 1 ? '' : 's'}`;
        }
        const sv = v.save || {};
        saveLine.textContent = sv.ok ? `Tracks are kept in ${sv.dir}` : `Tracks are analysed but not kept${sv.dir ? ` in ${sv.dir}` : ''}: ${sv.why}`;
        saveLine.classList.toggle('bd-warn', !sv.ok);
        const st = v.state || {};
        const own = v.track && st.track_id === v.track.id;
        let text = STATUS[v.enabled ? (own || st.status === 'idle' ? st.status : 'idle') : 'off'] || st.status || '';
        if (own && st.message && st.status !== 'done') text += ` — ${st.message}`;
        else if (own && st.status === 'done' && st.message) text += ` (${st.message})`;
        if (!v.playing) text = v.enabled ? 'Nothing is playing' : STATUS.off;
        const a = v.analysis;
        if (a && !own && a.status === 'done') text = a.current ? 'Analysed' : 'Analysed by an older detector: playing it again re-analyses it';
        if (a && !own && a.status !== 'done') text = `${STATUS[a.status] || a.status}${a.error ? ' — ' + a.error : ''}`;
        statusLine.textContent = text;
        againBtn.hidden = !v.track || !v.enabled;
        const t = v.track;
        clear(trackLine);
        if (t) trackLine.append(h('strong', {}, t.title || 'Untitled'), ' — ', t.artist || '', t.album ? h('span', { class: 'bd-muted' }, ` · ${t.album}`) : null);
        fbButtons.querySelectorAll('button').forEach(b => { b.disabled = !t || !v.enabled; });
        if (t && t.id !== S.trackId) { S.trackId = t.id; S.lastTap = null; noteBox.hidden = true; }
    }

    function paintAnalysis() {
        const res = S.analysis;
        clear(score); clear(timeline); clear(table);
        pickLine.hidden = true;
        if (!res) { score.append(h('span', { class: 'bd-muted' }, S.view && S.view.track ? 'No analysis of this track yet.' : '')); return; }
        const d = res.depth;
        if (d) {
            score.append(h('span', { class: `bd-badge ${d.band}` }, d.score.toFixed(1)),
                h('span', { class: 'bd-score-text' }, h('strong', {}, `${d.band} — ${d.meaning}`),
                    h('span', { class: 'bd-muted' }, ` ×coh ${num(d.coh_ratio)}${Number.isFinite(d.fund_db) ? `, solo fund ${num(d.fund_db, 1, true)} dB` : ''}, ${Math.round(d.seconds)} s of bass${d.low_material ? ' — little material, provisional' : ''}`)));
        } else {
            score.append(h('span', { class: 'bd-muted' }, 'No score: too little bass in this track.'));
        }
        const dur = res.duration_s || (S.view.track && S.view.track.duration) || 1;
        S.dur = dur;
        const rows = CONTEXTS.map(([ctx, label]) => {
            const lane = h('div', { class: 'bd-lane' });
            for (const b of (res.blocks || []).filter(b => b.context === ctx)) {
                lane.append(h('span', {
                    class: `bd-blk ${b.band || 'none'}`,
                    style: { left: `${100 * b.start / dur}%`, width: `${Math.max(0.35, 100 * (b.end - b.start) / dur)}%` },
                    title: `${label} ${clock(b.start)}–${clock(b.end)}${b.score !== undefined ? ` · ${b.score.toFixed(1)}` : ''}`,
                }));
            }
            return h('div', { class: 'bd-row' }, h('span', { class: 'bd-lbl' }, label), lane);
        });
        S.marks = h('div', { class: 'bd-lane bd-marks' });
        S.head = h('div', { class: 'bd-playhead' });
        timeline.append(h('div', { class: 'bd-row' }, h('span', { class: 'bd-lbl' }, 'Your taps'), S.marks), ...rows,
            h('div', { class: 'bd-row bd-axis' }, h('span', { class: 'bd-lbl' }), h('div', { class: 'bd-lane' },
                h('span', { style: { left: '0' } }, '0:00'), h('span', { style: { right: '0' } }, clock(dur)))),
            h('div', { class: 'bd-headlane' }, S.head));
        paintMarks();
        paintPlayhead();
        const c = res.contexts || {};
        table.append(h('div', { class: 'bd-tr bd-th' }, h('span', {}, 'Context'), h('span', {}, 'Seconds'), h('span', {}, '×coh'), h('span', {}, 'Fund dB'), h('span', {}, '')),
            ...CONTEXTS.map(([ctx, label]) => {
                const x = c[ctx] || {};
                return h('div', { class: 'bd-tr' }, h('span', {}, label), h('span', {}, num(x.seconds, 1)),
                    h('span', {}, num(x.coh_ratio)), h('span', {}, num(x.fund_db, 1, true)),
                    h('span', { class: 'bd-muted' }, SCORED.has(ctx) ? 'in the score' : (ctx === 'pizzicato' ? `${x.notes || 0} notes${Number.isFinite(x.clarity_db) ? `, clarity ${num(x.clarity_db, 1, true)} dB` : ''}; not in the score, may include cellos above C2` : 'not in the score yet')));
            }));
    }

    function paintMarks() {
        if (!S.marks) return;
        clear(S.marks);
        const taps = (S.view && S.view.taps) || [];
        for (const t of taps) {
            S.marks.append(h('span', { class: `bd-mark ${t.verdict}`, style: { left: `${100 * t.pos / S.dur}%` },
                title: `${VERDICT_LABEL[t.verdict]} at ${clock(t.pos)}${t.note ? ': ' + t.note : ''}` }));
        }
    }

    function position() {
        const t = S.view && S.view.track;
        if (!t || !Number.isFinite(t.pos)) return null;
        const extra = t.state === 'play' ? (Date.now() - S.polledAt) / 1000 : 0;
        return t.pos + extra;
    }

    function paintPlayhead() {
        if (!S.head || !S.dur) return;
        // the lanes start after the label column, whatever its font makes it
        const lane = timeline.querySelector('.bd-lane'), track = S.head.parentNode;
        if (lane) { track.style.left = `${lane.offsetLeft}px`; track.style.width = `${lane.offsetWidth}px`; }
        const p = position();
        S.head.hidden = p === null;
        if (p !== null) S.head.style.left = `${Math.min(100, 100 * p / S.dur)}%`;
    }

    function pick(e) {
        const res = S.analysis;
        const lane = timeline.querySelector('.bd-lane');
        if (!res || !lane) return;
        const r = lane.getBoundingClientRect();
        const at = (e.clientX - r.left) / r.width * S.dur;
        if (at < 0 || at > S.dur) return;
        const hits = (res.blocks || []).filter(b => b.start - 1 <= at && at <= b.end + 1);
        pickLine.hidden = false;
        clear(pickLine).append(h('strong', {}, clock(at)), ' ');
        if (!hits.length) { pickLine.append('No bass passage here.'); return; }
        pickLine.append(...hits.map(b => h('span', { class: 'bd-pickitem' },
            `${CONTEXTS.find(c => c[0] === b.context)[1]} ${clock(b.start)}–${clock(b.end)}: `,
            b.score !== undefined ? h('span', { class: `bd-dot ${b.band}` }) : null,
            b.score !== undefined ? ` ${b.score.toFixed(1)} (×coh ${num(b.coh_ratio)}, fund ${num(b.fund_db, 1, true)} dB)` : 'too short to score')));
    }

    function paintTaps() {
        clear(tapList);
        const taps = (S.view && S.view.taps) || [];
        if (!taps.length) return;
        tapList.append(h('div', { class: 'bd-muted' }, `Your taps on this track (${taps.length}):`),
            ...taps.map(t => h('div', { class: 'bd-tap' },
                h('span', { class: `bd-dot ${t.verdict}` }), h('span', { class: 'bd-tap-at' }, clock(t.pos)),
                h('span', {}, VERDICT_LABEL[t.verdict]), t.context ? h('span', { class: 'bd-muted' }, `(${t.context})`) : null,
                h('span', { class: 'bd-tap-note' }, t.note || ''),
                h('button', { type: 'button', class: 'bd-x', title: 'Delete this tap', onclick: () => delTap(t.id) }, '×'))));
    }

    async function poll() {
        const d = await api(`/bass?have=${encodeURIComponent(S.stamp)}`);
        if (!d.ok) { statusLine.textContent = d.error || 'unavailable'; return; }
        S.view = d;
        S.polledAt = Date.now();
        const a = d.analysis;
        const stamp = a && a.status === 'done' ? a.stamp : '';
        if (stamp !== S.stamp || (a && a.result) || !S.painted) {
            S.painted = true;
            if (!stamp) S.analysis = null;
            else if (a.result) S.analysis = a.result;
            S.stamp = stamp;
            paintAnalysis();
        }
        paintHead();
        paintMarks();
        paintTaps();
        paintPlayhead();
    }

    async function startSession() {
        const d = await api('/bass/session', { action: 'start', name: sessionName.value.trim() });
        msg.textContent = d.ok ? '' : (d.error || 'failed');
        if (d.ok) sessionName.value = '';
        poll();
    }

    async function stopSession() {
        const d = await api('/bass/session', { action: 'stop' });
        msg.textContent = d.ok ? 'Session stopped. Its taps and analyses are kept and shared.' : (d.error || 'failed');
        poll();
    }

    async function reanalyse() {
        const d = await api('/bass/reanalyse', {});
        msg.textContent = d.ok ? 'The track will be analysed again in a few seconds.' : (d.error || 'failed');
        setTimeout(poll, 4500);
    }

    async function tap(verdict) {
        const d = await api('/bass/tap', { verdict, note: '' });
        if (!d.ok) { msg.textContent = d.error || 'failed'; return; }
        msg.textContent = '';
        S.lastTap = d.tap;
        noteBox.hidden = false;
        noteBox.querySelector('.bd-tapped').textContent = `${VERDICT_LABEL[verdict]} at ${clock(d.tap.pos)}${d.tap.context ? ` (${d.tap.context})` : ''}. Add a note?`;
        noteInput.value = '';
        poll();
    }

    async function saveNote() {
        if (!S.lastTap) return;
        const d = await api(`/bass/tap/${S.lastTap.id}`, { note: noteInput.value.trim() });
        msg.textContent = d.ok ? 'Note saved.' : (d.error || 'failed');
        if (d.ok) noteBox.hidden = true;
        poll();
    }

    async function delTap(id) {
        const d = await api(`/bass/tap/${id}/delete`, {});
        if (!d.ok) msg.textContent = d.error || 'failed';
        if (S.lastTap && S.lastTap.id === id) { S.lastTap = null; noteBox.hidden = true; }
        poll();
    }

    return {
        start() {
            if (S.timer) return;
            poll();
            S.timer = setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
            S.tick = setInterval(paintPlayhead, 250);
        },
        stop() { clearInterval(S.timer); clearInterval(S.tick); S.timer = S.tick = null; },
    };
}

window.BassDepth = { mount };
})();
