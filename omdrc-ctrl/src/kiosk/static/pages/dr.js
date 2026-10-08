/* Dynamic range: Configure holds the live estimate, measurement and DR log;
 * Albums by DR is a separate page for the stored ranking. */
(() => {
'use strict';
const { h } = K;

// The DR database's own colours: flat red at 7 and below, flat green from 14.
const badgeColor = v => v === null || v === undefined ? '' : v <= 7 ? '#ff0000' : v >= 14 ? '#00ff00'
    : ({ 8: '#ff4800', 9: '#ff9100', 10: '#ffd900', 11: '#d9ff00', 12: '#90ff00', 13: '#48ff00' })[v];
const badge = (v, big, tip) => h('span', {
    class: 'drb' + (v === null || v === undefined ? ' none' : '') + (big ? ' big' : ''),
    title: tip || null, style: v === null || v === undefined ? {} : { background: badgeColor(v) },
}, v === null || v === undefined ? '–' : String(v).padStart(2, '0'));
const albumCover = (image, source, key, cls) => {
    const src = image || (source === 'local' && key.startsWith('local:')
        ? `/dr/library/art?key=${encodeURIComponent(key)}` : '');
    if (!src) return h('span', { class: cls }, '♪');
    return h('img', { class: cls, src, alt: '', loading: 'lazy',
        onerror: e => e.target.replaceWith(h('span', { class: cls }, '♪')) });
};
const detailParent = ({ image, source, key, title, lines, dr, back, backLabel }) =>
    h('button', { class: 'dr-detail-album dr-detail-parent', type: 'button',
        'aria-label': backLabel, onclick: back },
        h('span', { class: 'dr-detail-return', 'aria-hidden': 'true' }, '‹'),
        albumCover(image, source, key, 'dr-detail-cover'),
        h('span', { class: 'dr-detail-title' }, h('strong', {}, title || 'Album'),
            lines.map(line => h('span', { class: 'muted small' }, line))),
        K.drLogBadge(dr, 'big') || h('span', { class: 'drlog none' }, '—'));

const P = { id: 'dr', label: 'DR Configure', title: 'Configure', menuGroup: 'Dynamic range', on: false, sub: null, job: null, jobTimer: null,
    log: null, logTimer: null };
const A = { id: 'dr_albums', label: 'Albums by DR', title: 'Albums by DR', menuGroup: 'Dynamic range',
    rank: { source: '', exact: false, q: '' }, rankSeq: 0 };
const R = { id: 'dr_recent', label: 'Recent DR', title: 'Recent DR', menuGroup: 'Dynamic range',
    hours: 24, limit: 20, seq: 0, groups: [], count: 0 };

P.mount = el => {
    P.el = el;
    // estimate card
    P.toggle = h('button', { class: 'btn big-toggle', type: 'button', role: 'switch', 'aria-checked': 'false', onclick: () => P.setOn(!P.on, true) }, 'Estimate');
    P.value = h('strong', { class: 'dr-value big' }, '—');
    P.status = h('div', { class: 'dr-status' }, 'Off — press Estimate to start collecting');
    P.gaugeHost = h('div', { class: 'drg' });
    P.gauge = K.drGauge(P.gaugeHost);
    P.winLabel = h('output', {}, '');
    P.slider = h('input', { type: 'range', min: 60, max: 5400, step: 60, oninput: e => K.drEstimate.setWindow(e.target.value) });
    P.detect = h('button', { class: 'chip', type: 'button', role: 'switch', onclick: () => K.drEstimate.setDetect(!K.drEstimate.detect) });
    P.barHost = h('div', { class: 'dr-bar tall' });
    P.detail = h('div', { class: 'dr-detail', hidden: true, role: 'status' });
    P.oldest = h('span', {});
    P.bar = new K.DrBar(P.barHost, { onDetail: (text, count, label) => { P.detail.hidden = !label; P.detail.textContent = label || ''; P.detail.title = text || ''; P.oldest.textContent = count ? `−${K.dr.elapsedLabel(count * 3)}` : ''; } });
    K.wireDrViewPopup(P.barHost);
    P.estBody = h('div', { class: 'est-body' },
        h('div', { class: 'dr-head' }, P.value, P.status), P.gaugeHost,
        h('div', { class: 'win-row' }, h('span', { class: 'lbl' }, 'Rolling window'), P.slider, P.winLabel, P.detect),
        P.barHost, h('div', { class: 'dr-times' }, P.oldest, h('span', {}, 'Latest')), P.detail,
        h('p', { class: 'muted small' }, 'Recent excerpt at 44.1 kHz · indicative of what you just heard, not full-track or album DR.'));
    P.estBody.hidden = true;
    const note = (title, ...text) => h('div', { class: 'explain' }, h('strong', {}, title + ' '), ...text);
    const estCard = K.card('Estimate', P.toggle,
        note('What it is.', 'A live figure for what is playing now: the dynamic range of the last few minutes, worked out in 3-second blocks. The bar below is the same estimate you see on Now: each segment is a slice of the window (its height is how loud it was, its number is its DR, red = compressed to green = dynamic). Turning it off also hides the DR value and bar on the Now page, and nothing is computed for them (the DR log below runs on its own).'),
        P.estBody);

    // measurement card
    P.measBtn = h('button', { class: 'btn', type: 'button', onclick: () => P.measStart() }, 'Measure this record');
    P.cancelBtn = h('button', { class: 'btn danger', type: 'button', onclick: () => P.measCancel(), hidden: true }, 'Cancel');
    P.measBody = h('div', { class: 'meas-body' });
    const measCard = K.card('Measure', h('div', { class: 'btn-row' }, P.measBtn, P.cancelBtn,
        K.state.features.drdb ? h('button', { class: 'btn', type: 'button', onclick: () => K.frame('/dr-alternatives', 'DR versions of this record') }, 'Compare ↗') : null),
        note('Meas.', 'Measures the whole record, not a slice: it fetches this record’s tracks again, measures each one’s DR on the copy that actually reaches your DAC, then deletes them. It takes a while and shows a DR per track and for the album.'),
        note('Compare.', 'Looks this record up in the Dynamic Range database and lists its other masters and pressings with their published DR, so you can see whether another edition has more dynamics than the one you are playing.'),
        P.measBody);

    // the DR log: one switch, kept on the server
    P.logBtn = h('button', { class: 'btn big-toggle', type: 'button', role: 'switch', 'aria-checked': 'false', onclick: () => P.logSet(!(P.log && P.log.enabled)) }, 'DR log');
    P.logState = h('div', { class: 'dr-status' }, 'reading…');
    P.logAlbum = h('div', { class: 'drlog-now' });
    P.syncLine = h('div', { class: 'drlog-sync small' });
    const logCard = K.card('DR log', P.logBtn, P.logState, P.logAlbum, P.syncLine,
        note('What it is.', 'Keeps the DR of every track played, on the server, whether or not any page is open, and puts together each album’s figure: exact once every track has been heard whole (over as many sessions as it takes), an estimate (≈) from the tracks heard so far, at least two minutes of them. A seek or a late start makes a track count towards the estimate only. Local albums with a dr14.txt use its value.'),
        note('Cost.', 'While on, MPD’s analyzer FIFO output stays enabled and the server reads it continuously — no FFT, only DR blocks. The Audio chain shows it as “DR log” under MPD’s FIFO. Independent of the Estimate switch.'));

    el.append(h('div', { class: 'two-col' }, h('div', { class: 'col' }, estCard, logCard), h('div', { class: 'col' }, measCard)));
    P.paintWin();
    P.setOn(K.pref('now.dr', true));      // already running for the Now page: show it here too
};

A.mount = el => {
    A.el = el;
    A.rankQ = h('input', { type: 'search', class: 'drrank-q', placeholder: 'Filter: artist, album, label…', oninput: () => { clearTimeout(A.rankTimer); A.rankTimer = setTimeout(() => { A.rank.q = A.rankQ.value.trim(); A.rankLoad(); }, 300); } });
    A.rankChips = h('div', { class: 'btn-row drrank-chips' });
    A.rankList = h('div', { class: 'drrank' });
    A.rankFoot = h('div', { class: 'muted small' });
    A.browse = h('div', { class: 'drrank-browse' }, A.rankQ, A.rankChips, A.rankList, A.rankFoot);
    A.detail = h('div', { class: 'dr-detail-page', hidden: true });
    el.append(K.card('Albums by dynamic range', A.browse, A.detail,
        K.cardOpts({ actions: h('button', { class: 'btn', type: 'button', title: 'Read the local collection’s dr14.txt files again', onclick: () => A.rankImport() }, 'Rescan dr14.txt') })));
    A.paintChips();
};

R.mount = el => {
    R.el = el;
    R.chips = h('div', { class: 'btn-row drrecent-chips' });
    R.list = h('div', { class: 'drrecent' });
    R.foot = h('div', { class: 'muted small' });
    R.more = h('button', { class: 'btn', type: 'button', hidden: true,
        onclick: () => { R.limit = Math.min(R.groups.length, R.limit + 20); R.paint(); } }, 'Show 20 more');
    R.browse = h('div', {}, R.chips, R.list, R.foot, R.more);
    R.detail = h('div', { class: 'dr-detail-page', hidden: true });
    el.append(K.card('Recent DR',
        h('p', { class: 'muted small' }, 'Newest saved track measurements on this box. The DR log retains the best measurement per track, so replaying a track may not add a new row.'),
        R.browse, R.detail));
    R.paintChips();
};

R.paintChips = () => K.clear(R.chips).append(...[[24, '24 hours'], [72, '3 days'], [168, '7 days']].map(([hours, label]) =>
    h('button', { class: 'chip tog' + (R.hours === hours ? ' on' : ''), type: 'button',
        onclick: () => { R.hours = hours; R.limit = 20; R.paintChips(); R.load(); } }, label)));

const recentWhen = t => new Date(t.at * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
R.trackRow = t => {
    const color = K.dr.color(t.dr);
    return h('div', { class: 'drrecent-row' },
        h('span', { class: 'drlog' + (t.complete ? '' : ' est'),
            style: t.complete ? { background: color.bg, color: color.fg } : { borderColor: color.bg } }, `DR${t.dr}`),
        h('div', { class: 'drrecent-body' },
            h('strong', {}, `${t.number ? t.number + '. ' : ''}${t.title || t.track_key || 'Track'}`),
            h('span', { class: 'muted small' }, [t.artist, t.album_title].filter(Boolean).join(' — ')),
            h('span', { class: 'muted small' }, `${recentWhen(t)} · ${t.method === 'measured' ? 'measured' : t.complete ? 'heard whole' : `heard ${Math.round(t.seconds)} s`}`)));
};

R.back = () => {
    R.detail.hidden = true;
    R.browse.hidden = false;
    if (R.body) R.body.scrollTop = R.savedScroll || 0;
};
R.openGroup = group => {
    const t = group.tracks[0], dr = group.dr;
    const tracks = [...group.tracks].sort((a, b) =>
        a.number !== null && b.number !== null ? a.number - b.number : b.at - a.at);
    R.savedScroll = R.body ? R.body.scrollTop : 0;
    K.clear(R.detail).append(
        detailParent({ image: t.image, source: t.source, key: t.album_key,
            title: t.album_title, lines: [t.artist || '', `${tracks.length} saved track${tracks.length === 1 ? '' : 's'} in this period`],
            dr, back: R.back, backLabel: 'Back to recent DR albums' }),
        h('div', { class: 'drrecent' }, tracks.map(R.trackRow)));
    R.browse.hidden = true;
    R.detail.hidden = false;
    if (R.body) R.body.scrollTop = 0;
};
R.row = group => {
    if (group.tracks.length === 1) return R.trackRow(group.tracks[0]);
    const t = group.tracks[0];
    return h('button', { class: 'drrecent-row drrecent-parent', type: 'button', onclick: () => R.openGroup(group) },
        albumCover(t.image, t.source, t.album_key, 'drrecent-cover'),
        K.drLogBadge(group.dr) || h('span', { class: 'drlog none' }, '—'),
        h('span', { class: 'drrecent-body' },
            h('strong', {}, t.album_title || 'Album'),
            h('span', { class: 'muted small' }, t.artist || ''),
            h('span', { class: 'muted small' }, `${group.tracks.length} saved tracks · latest ${recentWhen(t)}`)),
        h('span', { class: 'drrecent-next', 'aria-hidden': 'true' }, '›'));
};
R.paint = () => {
    const shown = R.groups.slice(0, R.limit);
    K.clear(R.list).append(...(shown.length ? shown.map(R.row)
        : [h('p', { class: 'muted' }, 'No saved DR measurements in this period. Try a longer range or play a track with the DR log on.')]));
    R.foot.textContent = `Showing ${shown.length} of ${R.groups.length} albums/songs from ${Math.min(R.count, 500)} saved tracks${R.count > 500 ? ` (latest 500 of ${R.count})` : ''}`;
    R.more.hidden = R.limit >= R.groups.length;
};
R.load = async () => {
    const seq = ++R.seq;
    R.back();
    K.clear(R.list).append(h('p', { class: 'muted' }, 'Loading recent DR…'));
    const d = await K.api(`/dr/library/recent?hours=${R.hours}&limit=500`);
    if (seq !== R.seq) return;
    if (!d.ok) { K.clear(R.list).append(h('p', { class: 'muted' }, d.error || 'unavailable')); R.more.hidden = true; return; }
    const groups = new Map();
    d.tracks.forEach(t => {
        if (!groups.has(t.album_key)) groups.set(t.album_key, { tracks: [], dr: d.summaries[t.album_key] });
        groups.get(t.album_key).tracks.push(t);
    });
    R.groups = [...groups.values()];
    R.count = d.count;
    R.paint();
};

P.paintWin = () => {
    const E = K.drEstimate;
    P.slider.value = E.windowSeconds;
    P.winLabel.textContent = K.dr.windowLabel(E.windowSeconds);
    P.detect.textContent = `Detect song change: ${E.detect ? 'On' : 'Off'}`;
    P.detect.setAttribute('aria-checked', String(E.detect));
};

// The one DR switch: `persist` is true for the user's own tap.  It is remembered, and it
// is what the Now page follows: off hides the DR value and bar there and stops the stream.
P.setOn = (on, persist = false) => {
    P.on = on;
    if (persist) K.setPref('now.dr', on);
    if (persist && !on) { const now = K.pages.find(p => p.id === 'now'); if (now && now.releaseDr) now.releaseDr(); }
    P.toggle.classList.toggle('active', on);
    P.toggle.setAttribute('aria-checked', String(on));
    P.toggle.textContent = on ? 'Estimate — on (tap to turn off)' : 'Estimate — off (tap to turn on)';
    P.estBody.hidden = !on;
    P.sync();
};

// The estimator streams only while this page is showing AND the toggle is on.
P.sync = () => {
    const want = P.on && P.visible && !document.hidden;   // the Now page's keep-alive covers the background
    if (want && !P.sub) P.sub = K.drEstimate.listen(P.paint);
    if (!want && P.sub) { P.sub.close(); P.sub = null; }
};

P.paint = E => {
    P.paintWin();
    const s = E.summary();
    P.value.textContent = s.value === null ? '—' : (s.value > 14 ? 'DR14+' : `DR${s.value}`);
    P.value.style.color = s.value === null ? '' : K.dr.color(s.value).bg;
    P.status.textContent = s.status;
    P.gauge.set(s.value);
    P.bar.render(E.viewBlocks(), E.windowSeconds, E.viewMarks());
};

// ── measurement ──────────────────────────────────────────────────────────────
P.measPaint = job => {
    P.job = job;
    const running = job && (job.status === 'queued' || job.status === 'running');
    P.measBtn.disabled = !!running;
    P.cancelBtn.hidden = !running;
    if (!job) { K.clear(P.measBody); return; }
    const n = job.tracks.length;
    const kids = [h('div', { class: 'meas-title' }, running ? `Measuring DR · ${job.album || 'this record'} · ${n} track${n === 1 ? '' : 's'}` : `DR measured here · ${job.album || 'this record'}`)];
    kids.push(h('div', { class: 'meas-bar' }, h('i', { style: { width: `${Math.round((job.fraction || 0) * 100)}%` } })));
    const where = job.current !== null && job.current !== undefined ? `Track ${job.current + 1} of ${n} · ` : '';
    kids.push(h('div', { class: 'muted small' }, running ? where + (job.message || '') : (job.message || job.status)));
    const measured = job.tracks.filter(t => t.dr !== null);
    if (measured.length) {
        const discs = Object.entries(job.discs || {});
        kids.push(h('div', { class: 'meas-album' }, badge(job.album_dr ?? null, true),
            h('span', {}, `album${running ? ' so far' : ''}`), discs.map(([d, v]) => h('span', { class: 'disc' }, `disc ${d} `, badge(v)))));
        kids.push(h('div', { class: 'meas-tracks' }, job.tracks.map((t, i) => {
            const name = `${t.track || i + 1}. ${t.title}`;
            return h('div', { class: 'meas-track' }, badge(t.dr, false, t.dr !== null ? `DR${t.dr} (${t.dr_exact}), peak ${t.peak_db} dB, RMS ${t.rms_db} dB` : (t.error || t.status)),
                h('span', {}, name));
        })));
    }
    K.clear(P.measBody).append(...kids);
};

P.measPoll = async () => {
    clearTimeout(P.jobTimer);
    if (!P.visible) return;
    const d = await K.api('/dr/measure');
    if (d && d.ok) P.measPaint(d.job);
    const running = d && d.ok && d.job && (d.job.status === 'queued' || d.job.status === 'running');
    if (running && P.visible) P.jobTimer = setTimeout(P.measPoll, 1500);
};

P.measStart = async () => {
    const d = await K.api('/dr/measure', { method: 'POST', timeout: 30000 });
    if (!d.ok) { K.toast(d.error || 'cannot measure DR', 'error'); return; }
    P.measPaint(d.job);
    P.measPoll();
};
P.measCancel = async () => { await K.api('/dr/measure/cancel', { method: 'POST' }); P.measPoll(); };

// ── the DR log ───────────────────────────────────────────────────────────────
P.logPaint = d => {
    P.log = d && d.ok ? d : null;
    const on = !!(P.log && P.log.enabled);
    P.logBtn.classList.toggle('active', on);
    P.logBtn.setAttribute('aria-checked', String(on));
    P.logBtn.textContent = on ? 'DR log — on (tap to turn off)' : 'DR log — off (tap to turn on)';
    if (!P.log) { P.logState.textContent = d ? (d.error || 'unavailable') : 'reading…'; return; }
    const st = P.log.store || {};
    const stored = `${st.albums || 0} album${st.albums === 1 ? '' : 's'} · ${st.tracks || 0} track${st.tracks === 1 ? '' : 's'} stored`;
    P.logState.textContent = !P.log.available ? 'Unavailable: the analyzer is disabled in commands.conf'
        : on && !P.log.listening ? `On, but not listening yet · ${stored}`
        : on ? `On — measuring every track played · ${stored}` : `Off · ${stored}`;
    P.logState.classList.toggle('warn', on && !P.log.listening);
    P.syncPaint(P.log.sync);
};

// Sharing with the other boxes ([dr_sync] in commands.conf, dr_sync.py)
const ago = t => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
P.syncPaint = s => {
    if (!s || !s.configured) {
        K.clear(P.syncLine).append(h('span', { class: 'muted' }, s && s.error ? `Sharing: ${s.error}` : 'Not shared with other boxes ([dr_sync] in commands.conf).'));
        return;
    }
    const others = Object.keys(s.boxes || {});
    const what = `Shared as “${s.box}”${others.length ? ` with ${others.join(', ')}` : ''}`;
    const when = s.running ? 'sharing…' : s.last ? `last ${ago(s.last)}` : 'not yet';
    K.clear(P.syncLine).append(
        h('span', { class: s.ok === false ? 'warn' : 'muted' }, `${what} · ${when}${s.ok === false ? ` · ${s.error}` : ''}`),
        h('button', { type: 'button', class: 'btn', disabled: !!s.running, onclick: async e => {
            e.target.disabled = true; e.target.textContent = 'Sharing…';
            const d = await K.api('/dr/sync', { method: 'POST', timeout: 240000 });
            if (d.ok) { P.syncPaint(d.sync); if (A.el) A.rankLoad(); } else K.toast(d.error || 'cannot share', 'error');
            if (d.ok && d.sync.ok === false) K.toast(d.sync.error, 'error');
        } }, 'Share now'));
};

P.logPoll = async () => {
    clearTimeout(P.logTimer);
    if (!P.visible) return;
    const [d, cur] = await Promise.all([K.api('/dr/log'), K.api('/dr/library/current')]);
    P.logPaint(d);
    P.curPaint(cur);
    if (P.visible) P.logTimer = setTimeout(P.logPoll, 15000);
};

P.logSet = async on => {
    const d = await K.api('/dr/log', { json: { enabled: on } });
    if (!d.ok) { K.toast(d.error || 'cannot switch the DR log', 'error'); return; }
    P.logPaint(d);
    K.toast(on ? 'DR log on' : 'DR log off');
};

// The album playing, as the log knows it so far.
P.curPaint = d => {
    const a = d && d.ok ? d.album : null;
    if (!a) { K.clear(P.logAlbum).append(h('span', { class: 'muted small' }, d && d.ok && d.key ? 'Album playing: nothing stored yet' : '')); return; }
    const s = a.dr;
    K.clear(P.logAlbum).append(h('span', { class: 'muted small' }, 'Album playing: '),
        K.drLogBadge(s) || h('span', { class: 'drlog none' }, '—'),
        h('span', { class: 'small' }, ` ${a.title || ''}`),
        h('span', { class: 'muted small' }, s.dr === null ? ` · ${s.heard} track${s.heard === 1 ? '' : 's'}, under two minutes so far` : ` · ${K.dr.basisText(s)}`));
};

// ── the ranking ──────────────────────────────────────────────────────────────
const SOURCES = [['', 'All'], ['qobuz', 'Qobuz'], ['local', 'Local'], ['stream', 'Other']];
A.paintChips = () => {
    const chip = (label, on, fn) => h('button', { type: 'button', class: 'chip tog' + (on ? ' on' : ''), onclick: fn }, label);
    K.clear(A.rankChips).append(
        ...SOURCES.map(([v, l]) => chip(l, A.rank.source === v, () => { A.rank.source = v; A.paintChips(); A.rankLoad(); })),
        chip('Exact only', A.rank.exact, () => { A.rank.exact = !A.rank.exact; A.paintChips(); A.rankLoad(); }));
};

A.rankLoad = async () => {
    const seq = ++A.rankSeq;
    const q = new URLSearchParams({ source: A.rank.source, exact: A.rank.exact ? '1' : '0', q: A.rank.q, limit: '200' });
    const d = await K.api('/dr/library?' + q);
    if (seq !== A.rankSeq) return;
    if (!d.ok) { K.clear(A.rankList).append(h('p', { class: 'muted' }, d.error || 'unavailable')); return; }
    const rows = d.albums.map((a, i) => A.rankRow(a, i + 1));
    K.clear(A.rankList).append(...(rows.length ? rows : [h('p', { class: 'muted' },
        A.rank.q || A.rank.source || A.rank.exact ? 'No album matches.' : 'Nothing stored yet: turn the DR log on and play something, or scan the local collection.')]));
    const t = d.totals || {}, imp = d.import || {};
    A.rankFoot.textContent = [`${d.count} album${d.count === 1 ? '' : 's'} with a figure`,
        `stored: ${t.qobuz || 0} Qobuz, ${t.local || 0} local, ${t.stream || 0} other`,
        imp.running ? 'reading dr14.txt…' : imp.error ? `dr14.txt: ${imp.error}` : ''].filter(Boolean).join(' · ');
};

A.back = () => {
    A.detailSeq = (A.detailSeq || 0) + 1;
    A.detail.hidden = true;
    A.browse.hidden = false;
    if (A.body) A.body.scrollTop = A.savedScroll || 0;
};
A.trackRow = t => h('div', { class: 'drrecent-row' },
    h('span', { class: 'drlog' + (t.complete ? '' : ' est'),
        style: t.complete ? { background: K.dr.color(t.dr).bg, color: K.dr.color(t.dr).fg }
            : { borderColor: K.dr.color(t.dr).bg } }, `DR${t.dr}`),
    h('span', { class: 'drrecent-body' },
        h('strong', {}, `${t.number ? t.number + '. ' : ''}${t.title || t.track_key}`),
        h('span', { class: 'muted small' }, `${t.method === 'measured' ? 'measured' : t.complete ? 'heard whole' : `heard ${Math.round(t.seconds)} s`}${t.origin ? ` · ${t.origin}` : ''}`)));
A.reportRow = t => {
    const color = K.dr.color(t.dr);
    return h('div', { class: 'drrecent-row' },
        h('span', { class: 'drlog', style: { background: color.bg, color: color.fg } }, `DR${t.dr}`),
        h('span', { class: 'drrecent-body' },
            h('strong', {}, `${t.number}. ${t.title}`),
            h('span', { class: 'muted small' }, 'from dr14.txt')));
};
A.openAlbum = async a => {
    A.savedScroll = A.body ? A.body.scrollTop : 0;
    const report = a.source === 'local' && !a.origin && a.report_dr !== null;
    const seq = A.detailSeq = (A.detailSeq || 0) + 1;
    const rows = report ? [h('p', { class: 'muted' }, 'Reading per-song DR from dr14.txt…')]
        : a.tracks.length ? a.tracks.map(A.trackRow) : [h('p', { class: 'muted' }, 'No saved track measurements.')];
    const list = h('div', { class: 'drrecent' }, rows);
    K.clear(A.detail).append(
        detailParent({ image: a.image, source: a.source, key: a.key, title: a.title,
            lines: [[a.artist, a.year, a.label].filter(Boolean).join(' · '), K.dr.basisText(a.dr)],
            dr: a.dr, back: A.back, backLabel: 'Back to albums by DR' }),
        list);
    A.browse.hidden = true;
    A.detail.hidden = false;
    if (A.body) A.body.scrollTop = 0;
    if (report) {
        const d = await K.api(`/dr/library/album?key=${encodeURIComponent(a.key)}`);
        if (seq !== A.detailSeq || A.detail.hidden) return;
        const reportRows = d.ok && d.album ? d.album.report_track_rows || [] : [];
        const fileCount = d.ok && d.album ? d.album.report_tracks : null;
        const songCount = d.ok && d.album ? d.album.track_count : null;
        const note = reportRows.length && fileCount && songCount && songCount > fileCount
            ? h('p', { class: 'muted small' }, d.album.single_flac_cue
                ? `1 FLAC + 1 CUE: dr14.txt measured the whole FLAC. The cue sheet has ${songCount} indexed songs, but separate song DR values are not in the report.`
                : `dr14.txt measured ${fileCount} audio file${fileCount === 1 ? '' : 's'}, while this album has ${songCount} indexed songs. Separate song DR values are not in the report.`)
            : null;
        K.clear(list).append(...(note ? [note] : []), ...(reportRows.length ? reportRows.map(A.reportRow)
            : [h('p', { class: 'muted' }, d.ok
                ? 'This dr14.txt has an album value but no per-song DR rows.'
                : d.error || 'Could not read the track values from dr14.txt.') ]));
    }
};
A.rankRow = (a, n) => {
    const sub = [a.artist, a.year, a.label].filter(Boolean).join(' · ');
    // where the figure comes from: another box's collection, or tracks heard there
    const elsewhere = (a.dr.origins || []).filter(Boolean);
    const box = a.origin ? `at ${a.origin}` : elsewhere.length ? `heard ${(a.dr.origins || []).map(o => o || 'here').join(' + ')}` : '';
    // a local edition is told apart by its folder: tags often name two the same
    const folder = a.source === 'local' && a.ref ? a.ref.split('/').pop() : '';
    const play = a.source === 'qobuz' ? h('button', { type: 'button', class: 'btn qz-play', title: 'Replace the queue and play', onclick: async e => {
        e.stopPropagation();
        const d = await K.api('/qobuz/play', { json: { album_id: a.ref, mode: 'replace' }, timeout: 90000 });
        K.toast(d.ok ? `Playing ${a.title}` : (d.error || 'cannot play'), d.ok ? 'ok' : 'error');
    } }, K.tIcon ? K.tIcon('play') : '▶') : null;
    return h('div', { class: 'drrank-row' },
        h('div', { class: 'drrank-head' },
            h('span', { class: 'drrank-n muted' }, String(n)),
            h('button', { class: 'drrank-open', type: 'button', onclick: () => A.openAlbum(a) },
                albumCover(a.image, a.source, a.key, 'drrank-cover'),
                K.drLogBadge(a.dr, 'big'),
                h('span', { class: 'drrank-body' }, h('span', { class: 'drrank-title' }, a.title || '—'),
                    h('span', { class: 'muted small' }, sub),
                    folder && folder !== a.title ? h('span', { class: 'muted small drrank-folder', title: a.ref }, `📁 ${folder}`) : null,
                    h('span', { class: 'muted small' }, [{ qobuz: 'Qobuz', local: 'Local', stream: 'Stream' }[a.source] || a.source, box, K.dr.basisText(a.dr)].filter(Boolean).join(' · '))),
                h('span', { class: 'drrecent-next', 'aria-hidden': 'true' }, '›')),
            play));
};

A.rankImport = async () => {
    const d = await K.api('/dr/library/import', { method: 'POST' });
    K.toast(d.ok ? (d.started ? 'Reading dr14.txt files…' : 'Already reading them') : (d.error || 'cannot rescan'), d.ok ? 'ok' : 'error');
    setTimeout(A.rankLoad, 3000);
};

P.show = () => { P.visible = true; P.setOn(K.pref('now.dr', true)); P.measPoll(); P.logPoll(); };
P.hide = () => { P.visible = false; P.sync(); clearTimeout(P.jobTimer); clearTimeout(P.logTimer); };
A.show = () => { A.visible = true; if (A.detail && !A.detail.hidden) A.back(); A.rankLoad(); };
A.hide = () => { A.visible = false; clearTimeout(A.rankTimer); A.rankSeq++; };
R.show = () => { R.visible = true; R.load(); };
R.hide = () => { R.visible = false; R.seq++; };
K.openDrAlbums = () => {
    K.showPage('dr_albums', false);
    if (A.body) A.body.scrollTop = 0;
};

document.addEventListener('visibilitychange', () => P.sync && P.el && P.sync());

K.registerPage(A);
K.registerPage(R);
K.registerPage(P);
})();
