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
    const S = { view: null, analysis: null, stamp: '', timer: null, tick: null, polledAt: 0, lastTap: null, cursor: null,
        browse: null, browseView: null };   // browse: {id, origin} of an analysed track shown without playback

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
    const listBtn = h('button', { type: 'button', class: 'bd-btn', onclick: () => toggleList() }, 'Analysed tracks');
    const backBtn = h('button', { type: 'button', class: 'bd-btn bd-start', onclick: backToLive, hidden: true }, 'Back to now playing');
    const listSearch = h('input', { type: 'search', class: 'bd-note', placeholder: 'Filter: title, artist, album…' });
    let listTimer = null;
    listSearch.addEventListener('input', () => { clearTimeout(listTimer); listTimer = setTimeout(loadList, 250); });
    const listRows = h('div', { class: 'bd-list-rows' });
    const listBox = h('div', { class: 'bd-list', hidden: true }, listSearch, listRows);
    const browseText = h('span', {});
    const keptAgainBtn = h('button', { type: 'button', class: 'bd-btn bd-mini', onclick: reanalyseKept, hidden: true }, 'Analyse again');
    const browseLine = h('div', { class: 'bd-browse', hidden: true }, browseText, ' ', keptAgainBtn);
    const trackLine = h('div', { class: 'bd-track' });
    const score = h('div', { class: 'bd-score' });
    const timeline = h('div', { class: 'bd-tl', onclick: pick });
    // The table shows the whole track, or the passages at the cursor: set by
    // the slider under the graph, a tap on the graph, or "Now".  While the track
    // shown plays or is paused, the slider and the cursor follow the player and
    // moving either seeks it; stopped (or another track shown) the slider is
    // white and only moves the cursor.
    const slider = h('input', { type: 'range', class: 'bd-slider offline', min: 0, max: 1, step: 0.1, value: 0,
        'aria-label': 'Position the table describes',
        oninput: e => { S.sliding = true; setCursor(Number(e.target.value)); },
        onchange: e => { S.sliding = false; if (live()) seek(Number(e.target.value)); } });
    slider.addEventListener('pointerdown', () => { S.sliding = true; });
    ['pointerup', 'pointercancel'].forEach(t => slider.addEventListener(t, () => { S.sliding = false; }));
    const cursorRow = h('div', { class: 'bd-row bd-cursor-row', hidden: true },
        h('span', { class: 'bd-lbl' }, 'Position'), slider);
    const tableHead = h('div', { class: 'bd-table-head' });
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
        h('div', { class: 'bd-head' }, statusLine, h('span', { class: 'bd-spacer' }), againBtn, listBtn, backBtn),
        listBox, browseLine, trackLine, score, timeline, cursorRow, tableHead, table,
        h('div', { class: 'bd-fb' },
            h('div', { class: 'bd-fb-q' }, 'How deep do the basses sound right now?'),
            fbButtons, noteBox, msg, tapList),
        h('details', { class: 'bd-explain' }, h('summary', {}, 'How this works'),
            h('p', {}, 'Nothing happens until you start a tracking session, for the recordings you mean to evaluate. During a session every track played is analysed in the background and kept on the music drive (a streamed track is fetched again, a local one copied), so the drive can travel with you. The analysis finds the passages where the double basses play alone, with other sections, under a tutti, or pizzicato. Solo and with-sections passages can receive an experimental depth score from 0 to 10: green 7 and above, yellow 4–7, red below 4. The score has not been validated as perceived distance. Grey passages have no score: they may be too short, lack a matching reference, or belong to tutti or pizzicato contexts. Balance is where the basses sit, right or left in dB, measured on their harmonics (which can include cellos doubling them an octave up) and, as Bal. fund, around their fundamental, where other low instruments may also contribute. Move the Position slider, or tap the graph, to read the passages at one moment. Only solo and with-sections passages count towards the track score; tutti and pizzicato are shown but not yet calibrated. Bowed bass candidates are searched up to 66 Hz, including the cello’s lowest C2; plucked lines sit higher, so pizzicato is searched up to G2 (98 Hz), and there it may include cellos playing the same notes.'),
            h('p', {}, 'Tap Forward, In between or Back whenever the basses catch your ear. The box records the moment you heard (the DRC delay is taken off) and the passage under it, plus your note. Sessions, taps and analyses travel with the DR log’s sync, and can be compared with the experimental measurements. Listening judgments collected while scores are visible are not blind validation.'),
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
        againBtn.hidden = !v.track || !v.enabled || !!S.browse;
        backBtn.hidden = !S.browse;
        const live = v.track;
        const t = S.browse ? S.browseView.track : live;
        browseLine.hidden = !S.browse;
        if (S.browse) {
            const when = t.at ? new Date(t.at * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
            const cur = S.browseView.analysis.current;
            browseText.textContent = `Not playing: an analysis from ${t.origin ? `box ${t.origin}` : 'this box'}${when ? `, ${when}` : ''}${cur ? '' : ', by an older detector'}.`;
            // this box's own analyses can be redone from the copy kept on the drive
            keptAgainBtn.hidden = !t.kept || S.reanalysing;
        }
        clear(trackLine);
        if (t) trackLine.append(h('strong', {}, t.title || 'Untitled'), ' — ', t.artist || '', t.album ? h('span', { class: 'bd-muted' }, ` · ${t.album}`) : null);
        // feedback is for what is playing: off while looking at another track
        const tappable = live && v.enabled && (!S.browse || S.browse.id === live.id);
        fbButtons.querySelectorAll('button').forEach(b => { b.disabled = !tappable; });
        if (t && t.id !== S.trackId) { S.trackId = t.id; S.lastTap = null; noteBox.hidden = true; }
    }

    function paintAnalysis() {
        const res = S.analysis;
        clear(score); clear(timeline); clear(table); clear(tableHead);
        cursorRow.hidden = !res;
        if (!res) { S.cur = null; score.append(h('span', { class: 'bd-muted' }, S.view && S.view.track ? 'No analysis of this track yet.' : '')); return; }
        const d = res.depth;
        if (d) {
            score.append(h('span', { class: `bd-badge ${d.band}` }, d.score.toFixed(1)),
                h('span', { class: 'bd-score-text' }, h('strong', {}, `Experimental · ${d.band} — ${d.meaning}`),
                    h('span', { class: 'bd-muted' }, ` ×coh ${num(d.coh_ratio)}${Number.isFinite(d.fund_db) ? `, solo fund ${num(d.fund_db, 1, true)} dB` : ''}${Number.isFinite(d.fund_seconds) ? ` (${num(d.fund_seconds, 1)} s solo)` : ''}, ${Math.round(d.seconds)} s of bass${d.low_material ? ' — little material, provisional' : ''}`)));
        } else {
            score.append(h('span', { class: 'bd-muted' }, 'No score: insufficient bass material or no matching reference.'));
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
        S.cur = h('div', { class: 'bd-cursor', hidden: true });
        timeline.append(h('div', { class: 'bd-row' }, h('span', { class: 'bd-lbl' }, 'Your taps'), S.marks), ...rows,
            h('div', { class: 'bd-row bd-axis' }, h('span', { class: 'bd-lbl' }), h('div', { class: 'bd-lane' },
                h('span', { style: { left: '0' } }, '0:00'), h('span', { style: { right: '0' } }, clock(dur)))),
            h('div', { class: 'bd-headlane' }, S.head, S.cur));
        slider.max = String(dur);
        if (S.cursor !== null && S.cursor > dur) S.cursor = null;
        paintMarks();
        paintPlayhead();
        paintTable();
    }

    // Where the basses sit: right minus left, in dB.
    const bal = x => !Number.isFinite(x) ? '–' : Math.abs(x) < 1 ? 'centre' : `${x > 0 ? 'R' : 'L'} ${Math.abs(x).toFixed(1)}`;

    function setCursor(at) {
        S.cursor = at === null || !Number.isFinite(at) ? null : Math.max(0, Math.min(S.dur || 0, at));
        paintTable();
        paintPlayhead();
    }

    function paintTable() {
        const res = S.analysis;
        clear(table); clear(tableHead);
        if (!res) return;
        const nowBtn = h('button', { type: 'button', class: 'bd-btn bd-mini', onclick: () => setCursor(position()) }, 'Now');
        const wholeBtn = h('button', { type: 'button', class: 'bd-btn bd-mini', onclick: () => setCursor(null) }, 'Whole track');
        // `narrow`: the column a phone drops
        const head = (narrow, ...cols) => h('div', { class: 'bd-tr bd-th' }, cols.map((c, i) => h('span', { class: i === narrow ? 'bd-ncol' : null }, c)));
        if (S.cursor === null) {
            tableHead.append(h('strong', {}, 'Whole track'), h('span', { class: 'bd-muted' }, ' · move the slider or tap the graph for one moment'), h('span', { class: 'bd-spacer' }), nowBtn);
            const c = res.contexts || {};
            table.append(head(6, 'Context', 'Seconds', '×coh', 'Fund dB', 'Balance', 'Bal. fund', ''),
                ...CONTEXTS.map(([ctx, label]) => {
                    const x = c[ctx] || {};
                    return h('div', { class: 'bd-tr' }, h('span', {}, label), h('span', {}, num(x.seconds, 1)),
                        h('span', {}, num(x.coh_ratio)), h('span', {}, num(x.fund_db, 1, true)),
                        h('span', {}, bal(x.balance_db)), h('span', {}, bal(x.balance_fund_db)),
                        h('span', { class: 'bd-muted bd-ncol' }, SCORED.has(ctx) ? 'in the score' : (ctx === 'pizzicato' ? `${x.notes || 0} notes${Number.isFinite(x.clarity_db) ? `, clarity ${num(x.clarity_db, 1, true)} dB` : ''}; not in the score, may include cellos above C2` : 'not in the score yet')));
                }));
            return;
        }
        const at = S.cursor;
        slider.value = String(at);
        tableHead.append(h('strong', {}, `At ${clock(at)}`), h('span', { class: 'bd-spacer' }), nowBtn, wholeBtn);
        table.append(head(1, 'Context', 'Passage', 'Score', '×coh', 'Fund dB', 'Balance', 'Bal. fund'),
            ...CONTEXTS.map(([ctx, label]) => {
                // the passage under the cursor, else one within a second of it
                const near = (res.blocks || []).filter(b => b.context === ctx && b.start - 1 <= at && at <= b.end + 1)
                    .sort((a, b) => (a.start <= at && at <= a.end ? 0 : 1) - (b.start <= at && at <= b.end ? 0 : 1) || b.seconds - a.seconds);
                const b = near[0];
                if (!b) return h('div', { class: 'bd-tr bd-none' }, h('span', {}, label), h('span', { class: 'bd-ncol' }),
                    h('span', { class: 'bd-muted' }, 'no passage'), h('span', {}), h('span', {}), h('span', {}), h('span', {}));
                return h('div', { class: 'bd-tr' }, h('span', {}, label), h('span', { class: 'bd-ncol' }, `${clock(b.start)}–${clock(b.end)}`),
                    h('span', {}, b.score !== undefined ? [h('span', { class: `bd-dot ${b.band}` }), ` ${b.score.toFixed(1)}`] : h('span', { class: 'bd-muted' }, 'unscored')),
                    h('span', {}, num(b.coh_ratio)), h('span', {}, num(b.fund_db, 1, true)),
                    h('span', {}, bal(b.balance_db)), h('span', {}, bal(b.balance_fund_db)));
            }));
    }

    const shownTaps = () => (S.browse ? S.browseView.taps : S.view && S.view.taps) || [];

    function paintMarks() {
        if (!S.marks) return;
        clear(S.marks);
        const taps = shownTaps();
        for (const t of taps) {
            S.marks.append(h('span', { class: `bd-mark ${t.verdict}`, style: { left: `${100 * t.pos / S.dur}%` },
                title: `${VERDICT_LABEL[t.verdict]} at ${clock(t.pos)}${t.note ? ': ' + t.note : ''}` }));
        }
    }

    function position() {
        const t = S.view && S.view.track;
        if (S.browse && (!t || t.id !== S.browse.id)) return null;   // not the track shown
        if (!t || !Number.isFinite(t.pos)) return null;
        const extra = t.state === 'play' ? (Date.now() - S.polledAt) / 1000 : 0;
        return t.pos + extra;
    }

    // the player has the track shown, playing or paused: the slider is its own
    const live = () => position() !== null;

    async function seek(at) {
        const t = S.view.track;
        t.pos = at; S.polledAt = Date.now();          // optimistic; the next poll confirms
        if (S.cursor !== null) setCursor(at); else paintPlayhead();
        const d = await api('/k/api/transport', { action: 'seek', seconds: at });
        if (!d.ok) msg.textContent = d.error || 'Seek failed';
        poll();
    }

    function paintPlayhead() {
        if (!S.head || !S.dur) return;
        // the lanes start after the label column, whatever its font makes it
        const lane = timeline.querySelector('.bd-lane'), track = S.head.parentNode;
        if (lane) { track.style.left = `${lane.offsetLeft}px`; track.style.width = `${lane.offsetWidth}px`; }
        const p = position();
        S.head.hidden = p === null;
        if (p !== null) S.head.style.left = `${Math.min(100, 100 * p / S.dur)}%`;
        slider.classList.toggle('offline', p === null);
        if (p !== null && !S.sliding) {
            const at = Math.min(S.dur, p);
            slider.value = String(at);
            // a cursor set follows the player too; the table is redrawn once a second
            if (S.cursor !== null) {
                const sec = Math.floor(S.cursor) !== Math.floor(at);
                S.cursor = at;
                if (sec) paintTable();
            }
        }
        if (S.cur) {
            S.cur.hidden = S.cursor === null;
            if (S.cursor !== null) S.cur.style.left = `${Math.min(100, 100 * S.cursor / S.dur)}%`;
        }
        // the slider spans the lanes too
        if (lane) { slider.style.marginLeft = '0'; slider.style.width = `${lane.offsetWidth}px`; }
    }

    function pick(e) {
        const lane = timeline.querySelector('.bd-lane');
        if (!S.analysis || !lane) return;
        const r = lane.getBoundingClientRect();
        const at = (e.clientX - r.left) / r.width * S.dur;
        if (at >= 0 && at <= S.dur) { if (live()) seek(at); else setCursor(at); }
    }

    function paintTaps() {
        clear(tapList);
        const taps = shownTaps();
        const own = !S.browse || !S.browse.origin;          // another box's taps are read-only
        if (!taps.length) return;
        tapList.append(h('div', { class: 'bd-muted' }, `Your taps on this track (${taps.length}):`),
            ...taps.map(t => h('div', { class: 'bd-tap' },
                h('span', { class: `bd-dot ${t.verdict}` }), h('span', { class: 'bd-tap-at' }, clock(t.pos)),
                h('span', {}, VERDICT_LABEL[t.verdict]), t.context ? h('span', { class: 'bd-muted' }, `(${t.context})`) : null,
                h('span', { class: 'bd-tap-note' }, t.note || ''),
                own ? h('button', { type: 'button', class: 'bd-x', title: 'Delete this tap', onclick: () => delTap(t.id) }, '×') : null)));
    }

    async function poll() {
        const d = await api(`/bass?have=${encodeURIComponent(S.stamp)}`);
        if (!d.ok) { statusLine.textContent = d.error || 'unavailable'; return; }
        S.view = d;
        S.polledAt = Date.now();
        if (S.browse) {                       // the shown analysis stays put; only the live parts move
            paintHead(); paintMarks(); paintTaps(); paintPlayhead();
            return;
        }
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

    async function toggleList(open) {
        open = open === undefined ? listBox.hidden : open;
        listBox.hidden = !open;
        listBtn.classList.toggle('on', open);
        if (open) loadList();
    }

    async function loadList() {
        const d = await api(`/bass/tracks?q=${encodeURIComponent(listSearch.value.trim())}`);
        clear(listRows);
        if (!d.ok) { listRows.append(h('div', { class: 'bd-muted' }, d.error || 'unavailable')); return; }
        if (!d.tracks.length) { listRows.append(h('div', { class: 'bd-muted' }, 'No analysed tracks yet: they come from tracking sessions.')); return; }
        listRows.append(...d.tracks.map(r => {
            const dd = r.depth;
            const when = r.at ? new Date(r.at * 1000).toLocaleDateString() : '';
            return h('button', { type: 'button', class: 'bd-list-row', onclick: () => openTrack(r) },
                h('span', { class: `bd-badge small ${dd ? dd.band : 'none'}` }, dd ? dd.score.toFixed(1) : '–'),
                h('span', { class: 'bd-list-text' }, h('strong', {}, r.title || 'Untitled'),
                    h('span', { class: 'bd-muted' }, ` — ${r.artist || ''}${r.album ? ` · ${r.album}` : ''}`),
                    h('span', { class: 'bd-muted bd-list-meta' }, [when, r.origin ? `box ${r.origin}` : 'this box',
                        r.taps ? `${r.taps} tap${r.taps === 1 ? '' : 's'}` : '', r.origin && r.timeline === false ? 'no timeline' : ''].filter(Boolean).join(' · '))));
        }));
    }

    async function openTrack(r) {
        const d = await api(`/bass/track?id=${encodeURIComponent(r.track_id)}&origin=${encodeURIComponent(r.origin || '')}`);
        if (!d.ok) { msg.textContent = d.error || 'failed'; return; }
        S.browse = { id: r.track_id, origin: r.origin || '' };
        S.browseView = d;
        S.analysis = d.analysis.result;
        S.cursor = null;
        toggleList(false);
        paintAnalysis(); paintHead(); paintMarks(); paintTaps(); paintPlayhead();
    }

    async function reanalyseKept() {
        const id = S.browse && S.browse.id;
        if (!id) return;
        const before = S.browseView.track.at;
        const d = await api('/bass/track/reanalyse', { id });
        if (!d.ok) { msg.textContent = d.error || 'failed'; return; }
        S.reanalysing = true;
        msg.textContent = 'Analysing again from the kept copy…';
        paintHead();
        for (let i = 0; i < 60 && S.browse && S.browse.id === id; i++) {
            await new Promise(r => setTimeout(r, 2000));
            const v = await api(`/bass/track?id=${encodeURIComponent(id)}`);
            if (v.ok && v.track.at !== before) {
                S.browseView = v; S.analysis = v.analysis.result;
                msg.textContent = 'Analysed again.';
                break;
            }
        }
        S.reanalysing = false;
        if (S.browse && S.browse.id === id) { paintAnalysis(); paintHead(); paintMarks(); paintTaps(); }
    }

    function backToLive() {
        S.browse = S.browseView = null;
        S.stamp = '';
        S.painted = false;
        S.cursor = null;
        poll();
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
