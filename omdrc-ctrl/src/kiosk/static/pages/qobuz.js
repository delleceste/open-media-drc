/* Qobuz (optional: [qobuz_search] enabled): album search with label
 * and release-date filters, results newest first, and a player strip.  ▶ and +
 * queue an album on upmpdcli (/qobuz/play), so the box streams it itself; the
 * phone only drives.  The whole page is greyed while upmpdcli is not the running
 * renderer: the other renderer could not play what is found here.
 *
 * The search field completes from a classical word list (composers, works,
 * forms, instruments, performers: /qobuz/words) and from what was played
 * after a search (the search text, the album's artist and composer).  The
 * player strip opens into a full-screen player with the cover and the queue. */
(() => {
'use strict';
const { h } = K;

const P = {
    id: 'qobuz', label: 'Qobuz', title: 'Qobuz search',
    optional: () => !!K.state.features.qobuz_search,
    status: null,              // /qobuz/status
    favourites: [],            // configured label groups' names
    selected: new Set(K.pref('qobuz.labels', [])),
    last: null,                // the last search's answer, and the request it answered
    request: null,
    open: new Set(),           // album ids whose track list is unfolded
    albums: new Map(),         // album id -> /qobuz/album answer (or a pending promise)
    searching: 0,              // a sequence number: a slow answer never paints over a newer one
};

// Date filter: 'any', 'last' (N rolling years) or 'span' (calendar years).
const pref = (k, d) => K.pref('qobuz.' + k, d);
const setPref = (k, v) => K.setPref('qobuz.' + k, v);
const thisYear = new Date().getFullYear();

P.mount = el => {
    P.el = el;
    P.banner = h('div', {});
    P.input = h('input', {
        type: 'search', class: 'qz-input', placeholder: 'Composer, work, performer…', enterkeyhint: 'search',
        autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
        onkeydown: e => P.suggestKey(e),
        oninput: () => P.suggestSoon(),
        onfocus: () => P.suggestSoon(),
        onblur: () => setTimeout(() => P.showSuggestions([]), 150),
    });
    P.suggestBox = h('div', { class: 'qz-suggest', role: 'listbox' });
    P.input.value = pref('q', '');
    P.labelsBox = h('div', { class: 'qz-chips' });
    P.seenBox = h('div', {});
    P.dateBox = h('div', {});
    P.sortBox = h('div', {});
    P.form = h('div', { class: 'qz-form' },
        K.card(null,
            h('div', { class: 'qz-searchwrap' },
                h('div', { class: 'qz-searchrow' }, P.input,
                    h('button', { type: 'button', class: 'btn primary', onclick: () => { P.input.blur(); P.search(); } }, 'Search')),
                P.suggestBox),
            h('div', { class: 'lbl' }, 'Labels'), P.labelsBox, P.seenBox,
            h('div', { class: 'lbl' }, 'Released'), P.dateBox,
            h('div', { class: 'lbl' }, 'Order'), P.sortBox));
    P.results = h('div', { class: 'qz-results' });
    P.player = h('div', { class: 'qz-player' });
    P.main = h('div', { class: 'qz-main' }, P.form, P.results);
    el.append(h('div', { class: 'qz' }, P.banner, P.main, P.player));
    P.buildPlayer();
    P.paintDate(); P.paintSort(); P.paintLabels();
    P.poll = new K.Poller(P.refreshStatus, 10000);
    P.playerPoll = new K.Poller(P.refreshPlayer, 2000);
    P.recent();
    P.loadWords();
};

P.show = () => {
    P.poll.start();
    P.playerPoll.start();
    P.clock = setInterval(P.paintTime, 500);
};
P.hide = () => {
    P.poll.stop();
    P.playerPoll.stop();
    clearInterval(P.clock);
};
P.prefetch = () => P.refreshStatus();     // which also loads the labels

// ── availability ─────────────────────────────────────────────────────────────
P.refreshStatus = async () => {
    const s = await K.api('/qobuz/status', { timeout: 8000 });
    P.status = s;
    if (!P.favouritesLoaded && s.ok && s.enabled) P.loadLabels();
    P.paintBanner();
};

P.usable = () => !!(P.status && P.status.ok && P.status.enabled && P.status.renderer);

P.paintBanner = () => {
    const s = P.status;
    let box = null;
    if (!s) box = null;
    else if (!s.ok) box = h('div', { class: 'errbox' }, s.error || 'The Qobuz search cannot be reached.');
    else if (!s.renderer) box = h('div', { class: 'errbox warn qz-banner' },
        h('div', {}, h('strong', {}, 'upmpdcli is not running.'),
            h('div', { class: 'small' }, 'Albums found here play through upmpdcli on the box. Switch the renderer to upmpdcli to use this page.')),
        h('button', { type: 'button', class: 'btn', onclick: () => K.goto('source') }, 'Source page'));
    else if (!s.token) box = h('div', { class: 'errbox warn qz-banner' },
        h('div', {}, h('strong', {}, 'Not signed in to Qobuz.'),
            h('div', { class: 'small' }, 'upmpdcli’s Qobuz plugin has no sign-in yet. Sign in from the full panel.')),
        h('button', { type: 'button', class: 'btn', onclick: () => K.frame('/', 'Full panel') }, 'Full panel'));
    K.clear(P.banner);
    if (box) P.banner.append(box);
    P.main.classList.toggle('qz-disabled', !P.usable());
    P.player.classList.toggle('qz-disabled', !P.usable());
    if (!P.usable()) P.closeFull();
};

// ── labels ───────────────────────────────────────────────────────────────────
P.loadLabels = async () => {
    const d = await K.api('/qobuz/labels');
    if (!d.ok) return;
    P.favouritesLoaded = true;
    P.favourites = d.labels.map(l => l.name);
    P.paintLabels();
};

// A ticked name that is not a favourite (picked from a result's labels) is still
// sent: the server takes an unknown name as a label keyword of its own.
P.paintLabels = () => {
    const names = [...P.favourites, ...[...P.selected].filter(n => !P.favourites.some(f => f.toLowerCase() === n.toLowerCase()))];
    const chip = name => h('button', {
        type: 'button', class: 'chip tog qz-chip' + (P.selected.has(name) ? ' on' : ''),
        onclick: () => P.toggleLabel(name),
    }, name);
    K.clear(P.labelsBox).append(...names.map(chip));
    if (!names.length && P.favouritesLoaded) P.labelsBox.append(h('span', { class: 'muted small' }, 'No favourite labels configured ([qobuz_search] labels).'));
    P.paintSeen();
};

// The labels met in the last results, to tick one that is not a favourite.
P.paintSeen = () => {
    K.clear(P.seenBox);
    const seen = (P.last && P.last.labels_seen || [])
        .filter(l => !l.groups.length && !P.selected.has(l.name)).slice(0, 24);
    if (!seen.length) return;
    P.seenBox.append(h('details', { class: 'qz-seen' },
        h('summary', { class: 'small muted' }, `Other labels in these results (${seen.length})`),
        h('div', { class: 'qz-chips' }, seen.map(l => h('button', {
            type: 'button', class: 'chip qz-chip', onclick: () => P.toggleLabel(l.name),
        }, `${l.name} · ${l.count}`)))));
};

P.toggleLabel = name => {
    if (P.selected.has(name)) P.selected.delete(name);
    else P.selected.add(name);
    setPref('labels', [...P.selected]);
    P.paintLabels();
    P.searchSoon();
};

// ── release date and order ───────────────────────────────────────────────────
P.paintDate = () => {
    const mode = pref('date', 'any');
    const kids = [K.segmented([
        { value: 'any', label: 'Any time' }, { value: 'last', label: 'Last years' }, { value: 'span', label: 'From – to' },
    ], mode, v => { setPref('date', v); P.paintDate(); P.searchSoon(); }, 'small')];
    if (mode === 'last') {
        const n = pref('lastN', 2);
        const out = h('output', {}, n === 1 ? 'the last year' : `the last ${n} years`);
        kids.push(h('div', { class: 'win-row' },
            h('input', {
                type: 'range', min: 1, max: 30, step: 1, value: n,
                oninput: e => { const v = +e.target.value; out.textContent = v === 1 ? 'the last year' : `the last ${v} years`; },
                onchange: e => { setPref('lastN', +e.target.value); P.searchSoon(); },
            }), out));
    } else if (mode === 'span') {
        const year = (key, dflt) => h('input', {
            type: 'number', class: 'qz-year', min: 1900, max: thisYear, step: 1, inputmode: 'numeric',
            value: pref(key, dflt), placeholder: key === 'to' ? 'today' : '',
            onchange: e => { const v = parseInt(e.target.value, 10); setPref(key, Number.isFinite(v) ? v : ''); P.searchSoon(); },
        });
        kids.push(h('div', { class: 'win-row' }, year('from', thisYear - 5), h('span', { class: 'muted' }, 'to'), year('to', '')));
    }
    K.clear(P.dateBox).append(...kids);
};

P.paintSort = () => {
    K.clear(P.sortBox).append(K.segmented([
        { value: 'date', label: 'Newest first' }, { value: 'relevance', label: 'Best match' },
    ], pref('sort', 'date'), v => { setPref('sort', v); P.paintSort(); P.searchSoon(); }, 'small'));
};

// ── search ───────────────────────────────────────────────────────────────────
P.params = scan => {
    const q = new URLSearchParams();
    const text = P.input.value.trim();
    if (text) q.set('q', text);
    for (const name of P.selected) q.append('label', name);
    const mode = pref('date', 'any');
    if (mode === 'last') q.set('last', pref('lastN', 2));
    if (mode === 'span') {
        if (pref('from', thisYear - 5)) q.set('from', pref('from', thisYear - 5));
        if (pref('to', '')) q.set('to', pref('to', ''));
    }
    q.set('sort', pref('sort', 'date'));
    if (scan) q.set('scan', scan);
    return q;
};

// A filter changed: search again, but only once a search has been made (the
// first one is always asked for), and not on every step of a quick change.
let soon = null;
P.searchSoon = () => {
    clearTimeout(soon);
    if (P.last || P.request) soon = setTimeout(() => P.search(), 450);
};

P.search = async (scan = 0) => {
    clearTimeout(soon);
    const params = P.params(scan);
    if (!params.has('q') && !params.has('label')) {
        K.toast('Type something or tick a label', 'error');
        return;
    }
    setPref('q', P.input.value.trim());
    const seq = ++P.searching;
    P.request = params.toString();
    P.paintWorking(scan ? 'Reading further…' : 'Searching…');
    const d = await K.api('/qobuz/search?' + params, { timeout: 120000 });
    if (seq !== P.searching) return;
    P.request = null;
    if (!d.ok) {
        if (d.renderer === false) P.refreshStatus();
        P.paintError(d.error || 'search failed');
        return;
    }
    P.last = d;
    P.paintSeen();
    P.paintResults();
};

P.paintWorking = text => {
    // A "Load more" keeps the list on screen and only turns its button into a spinner.
    const more = P.results.querySelector('.qz-more');
    if (more) { K.clear(more).append(h('div', { class: 'spinner small' }), h('span', {}, text)); return; }
    K.clear(P.results).append(h('div', { class: 'qz-working' }, h('div', { class: 'spinner' }), h('div', { class: 'muted' }, text)));
};

P.paintError = error => {
    K.clear(P.results).append(h('div', { class: 'errbox' }, error));
};

P.paintResults = () => {
    const d = P.last;
    const failed = d.queries.filter(q => q.error);
    const kids = [h('div', { class: 'qz-summary small muted' },
        `${d.count} album${d.count === 1 ? '' : 's'}`,
        d.window.from || d.window.to ? ` · ${d.window.from ? d.window.from.slice(0, 4) : '…'}–${d.window.to ? d.window.to.slice(0, 4) : 'today'}` : '',
        ` · ${d.considered} looked at`,
        d.sort === 'date' ? ' · newest first' : ' · best match first')];
    failed.forEach(q => kids.push(h('div', { class: 'errbox warn small' }, `“${q.query}”: stopped after ${q.fetched} albums: ${q.error}`)));
    if (!d.results.length) kids.push(h('p', { class: 'muted' }, d.more
        ? 'Nothing matches yet among the albums read so far.'
        : 'Nothing matches.'));
    kids.push(h('div', { class: 'qz-list' }, d.results.map(P.row)));
    if (d.more) kids.push(h('button', {
        type: 'button', class: 'btn qz-more', onclick: () => P.search(d.next_scan),
    }, `Load more (read ${d.next_scan} per query)`));
    K.clear(P.results).append(...kids);
};

// Before any search: the albums played from here, newest first.
P.recent = async () => {
    const d = await K.api('/qobuz/played?limit=30');
    if (P.last || P.request || !d.ok || !d.albums.length) return;
    K.clear(P.results).append(h('div', { class: 'lbl' }, 'Played recently'),
        h('div', { class: 'qz-list' }, d.albums.map(P.row)));
};

// ── completion ───────────────────────────────────────────────────────────────
// The whole list comes once (a few tens of KB) and is matched here as the user
// types: the last few words typed are compared with the start of each entry and
// of each of its words, ignoring case and accents.  Learned entries (played
// after a search) come first, most played first.
const FOLD1 = { 'ø': 'o', 'ł': 'l', 'đ': 'd', 'ı': 'i', 'ð': 'd', 'þ': 't' };
// Strips accents but keeps the length, so a match can be shown in the original.
const fold = t => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[øłđıðþ]/g, c => FOLD1[c]);
const entry = (text, learned = 0) => {
    const nfc = text.normalize('NFC');
    const f = fold(nfc);
    const starts = [0];
    for (let i = 1; i < f.length; i++) if (/[\s\-'’.(/]/.test(f[i - 1]) && !/[\s\-'’.(/]/.test(f[i])) starts.push(i);
    return { text: nfc, f, starts, learned };
};
P.words = [];
P.learned = [];

P.loadWords = async () => {
    const d = await K.api('/qobuz/words', { timeout: 30000 });
    if (!d.ok) return;
    P.words = d.words.map(w => entry(w));
    P.learned = d.learned.map(w => entry(w.text, w.count));
};

P.learn = texts => {
    for (const t of texts.map(x => String(x || '').trim()).filter(x => x.length > 1).reverse()) {
        const e = entry(t);
        const old = P.learned.find(x => x.f === e.f);
        e.learned = (old ? old.learned : 0) + 1;
        P.learned = [e, ...P.learned.filter(x => x !== old)];
    }
};

// [{entry, from, to, at}]: `entry` would replace input[from:to]; the match is
// at entry.f[at .. at + to - from].
P.suggestions = (value, caret) => {
    const before = value.slice(0, caret);
    if (!before.trim()) return P.learned.slice(0, 8).map(e => ({ e, from: 0, to: caret, at: -1 }));
    if (/\s$/.test(before)) return [];
    const tokens = [...before.matchAll(/\S+/g)];
    const best = new Map();
    for (let k = Math.min(4, tokens.length); k >= 1; k--) {
        const from = tokens[tokens.length - k].index;
        const frag = fold(before.slice(from)).replace(/\s+/g, ' ');
        if (frag.length < 2) continue;
        const consider = (e, source) => {
            if (best.has(e.f) || e.f === frag) return;
            const at = e.starts.find(i => e.f.startsWith(frag, i));
            if (at === undefined) return;
            best.set(e.f, { e, from, to: caret, at, k, source });
        };
        P.learned.forEach(e => consider(e, 0));
        P.words.forEach(e => consider(e, 1));
    }
    return [...best.values()].sort((a, b) =>
        a.source - b.source || (b.e.learned - a.e.learned) || (a.at > 0) - (b.at > 0)
        || b.k - a.k || a.e.text.length - b.e.text.length).slice(0, 8);
};

let suggestTimer = null;
P.suggestSoon = () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => {
        if (document.activeElement !== P.input) return;
        P.showSuggestions(P.suggestions(P.input.value, P.input.selectionStart ?? P.input.value.length));
    }, 60);
};

P.showSuggestions = list => {
    P.shown = list;
    P.active = -1;
    K.clear(P.suggestBox);
    P.suggestBox.classList.toggle('on', list.length > 0);
    list.forEach((s, n) => {
        const t = s.e.text, len = s.to - s.from;
        const label = s.at < 0 ? [t]
            : [t.slice(0, s.at), h('b', {}, t.slice(s.at, s.at + len)), t.slice(s.at + len)];
        P.suggestBox.append(h('button', {
            type: 'button', class: 'qz-sug' + (s.e.learned ? ' learned' : ''), role: 'option',
            // pointerdown, not click: the field must keep the focus (and the keyboard)
            onpointerdown: e => { e.preventDefault(); P.pick(n); },
        }, h('span', { class: 'qz-sug-text' }, label), s.e.learned ? h('span', { class: 'qz-sug-mark', title: 'played before' }, '↺') : null));
    });
};

P.pick = n => {
    const s = P.shown && P.shown[n];
    if (!s) return;
    const v = P.input.value;
    const head = v.slice(0, s.from) + s.e.text + ' ';
    P.input.value = head + v.slice(s.to).replace(/^\s+/, '');
    P.input.setSelectionRange(head.length, head.length);
    P.input.focus();
    P.showSuggestions([]);
};

P.suggestKey = e => {
    const n = (P.shown || []).length;
    if (n && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        const down = e.key === 'ArrowDown';
        P.active = P.active < 0 ? (down ? 0 : n - 1) : (P.active + (down ? 1 : n - 1)) % n;
        [...P.suggestBox.children].forEach((b, i) => b.classList.toggle('on', i === P.active));
    } else if (e.key === 'Tab' && n) {
        e.preventDefault();
        P.pick(Math.max(0, P.active));
    } else if (e.key === 'Escape') {
        P.showSuggestions([]);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        if (n && P.active >= 0) { P.pick(P.active); return; }
        P.showSuggestions([]);
        P.input.blur();
        P.search();
    }
};

// ── one result ───────────────────────────────────────────────────────────────
const quality = c => c.bits && c.bits > 16 ? `${c.bits}/${+(+c.rate).toFixed(1)}` : '';
const who = p => p.roles && p.roles.length ? `${p.name} (${p.roles[0]})` : p.name;

P.row = c => {
    const tracks = h('div', { class: 'qz-tracks' });
    const body = h('div', { class: 'qz-body tap', onclick: () => P.toggleTracks(c, row, tracks) },
        h('div', { class: 'qz-title' }, c.title, c.version ? h('span', { class: 'muted' }, ` (${c.version})`) : null),
        h('div', { class: 'qz-artist' }, c.artist),
        c.performers && c.performers.length
            ? h('div', { class: 'qz-perf small muted' }, c.performers.slice(0, 4).map(who).join(', ')) : null,
        h('div', { class: 'qz-meta small' },
            h('span', {}, c.label || '—'), h('span', { class: 'muted' }, c.year || ''),
            quality(c) ? h('span', { class: 'chip ok' }, quality(c)) : null,
            c.played ? h('span', { class: 'chip dim' }, `played ${c.played}×`) : null));
    const row = h('div', { class: 'qz-row' },
        c.image ? h('img', { class: 'qz-cover', src: c.image, alt: '', loading: 'lazy' }) : h('div', { class: 'qz-cover' }),
        body,
        h('div', { class: 'qz-act' },
            h('button', { type: 'button', class: 'btn primary qz-play', title: 'Replace the queue and play', onclick: () => P.play(c, 'replace') }, '▶'),
            h('button', { type: 'button', class: 'btn qz-add', title: 'Add to the queue', onclick: () => P.play(c, 'append') }, '+')),
        tracks);
    if (P.open.has(c.id)) P.toggleTracks(c, row, tracks, true);
    return row;
};

P.album = id => {
    if (!P.albums.has(id)) {
        const pending = K.api('/qobuz/album/' + encodeURIComponent(id), { timeout: 30000 })
            .then(d => { if (d.ok) P.albums.set(id, d); else P.albums.delete(id); return d; });
        P.albums.set(id, pending);
    }
    return Promise.resolve(P.albums.get(id));
};

// Tap on an album: its tracks, each one playable from there.
P.toggleTracks = async (c, row, box, keep = false) => {
    if (!keep && P.open.has(c.id)) { P.open.delete(c.id); row.classList.remove('open'); K.clear(box); return; }
    P.open.add(c.id);
    row.classList.add('open');
    K.clear(box).append(h('div', { class: 'spinner small' }));
    const d = await P.album(c.id);
    if (!P.open.has(c.id)) return;
    if (!d.ok) { K.clear(box).append(h('div', { class: 'errbox' }, d.error || 'could not read the album')); return; }
    const a = d.album;
    const multiDisc = new Set(a.track_list.map(t => t.disc)).size > 1;
    let work = '';
    const lines = [];
    for (const t of a.track_list) {
        if (t.work && t.work !== work) { work = t.work; lines.push(h('div', { class: 'qz-work small muted' }, t.work)); }
        lines.push(h('div', { class: 'qz-track' + (t.streamable ? '' : ' off') },
            h('span', { class: 'qz-num muted' }, multiDisc ? `${t.disc}.${t.number}` : t.number),
            h('span', { class: 'qz-ttitle' }, t.title, t.version ? h('span', { class: 'muted' }, ` (${t.version})`) : null),
            h('span', { class: 'qz-dur muted' }, K.fmtClock(t.duration)),
            h('button', { type: 'button', class: 'btn qz-tplay', disabled: !t.streamable, title: 'Play the album from here',
                onclick: () => P.play(c, 'replace', t) }, '▶')));
    }
    K.clear(box).append(...lines);
};

// ── play ─────────────────────────────────────────────────────────────────────
P.play = async (c, mode, track = null) => {
    if (!P.usable()) { K.toast('Switch the renderer to upmpdcli first', 'error'); return; }
    const busy = K.busy(mode === 'append' ? `Adding “${c.title}”…` : `Queueing “${c.title}”…`);
    const body = { album_id: c.id, mode };
    if (track) body.start = track.id;
    // Played from a search's results: the server learns the search text and the
    // album's artist and composer as completions.  Not from "Played recently".
    const fromSearch = !!(P.last && P.last.results.includes(c));
    if (fromSearch) body.query = P.last.query || '';
    const d = await K.api('/qobuz/play', { json: body, timeout: 90000 });
    busy.done();
    if (!d.ok) {
        K.toast(d.error || 'could not queue the album', 'error');
        if (d.renderer === false) P.refreshStatus();
        return;
    }
    const n = `${d.queued} track${d.queued === 1 ? '' : 's'}`;
    K.toast(mode === 'append' ? `Added ${n} to the queue` : `Playing — ${n} queued`);
    if (d.remembered) c.played = (c.played || 0) + 1;
    if (fromSearch) P.learn([P.last.query, c.artist, c.composer]);
    P.playerPoll.now();
};

// ── player ───────────────────────────────────────────────────────────────────
// MPD's own state (/k/api/player), which is also upmpdcli's: the playlist
// upmpdcli shows is MPD's queue.  Between polls the time runs on locally.  The
// strip and the full-screen player are two views of that one state; the cover
// comes from Qobuz for a track the plugin streams (/qobuz/track/<id>).
P.views = [];
P.tracks = new Map();         // Qobuz track id -> /qobuz/track's track, null, or a pending promise
const PLUGIN_TRACK = /\/qobuz\/track\/version\/\d+\/trackId\/(\d+)/;

P.makeView = full => {
    const btn = (label, title, fn, cls = '') => h('button', { type: 'button', class: 'btn qz-pbtn ' + cls, title, onclick: fn }, label);
    const v = { full };
    v.cover = h('div', { class: 'qz-pcover' });
    v.title = h('div', { class: 'qz-ptitle' }, '—');
    v.sub = h('div', { class: 'qz-psub muted' }, '');
    v.toggle = btn('▶', 'Play / pause', () => P.transport(P.base && P.base.playing ? 'pause' : 'play'), 'primary qz-toggle');
    v.elapsed = h('span', { class: 'qz-ptime' }, '–:––');
    v.total = h('span', { class: 'qz-ptime' }, '–:––');
    v.seek = h('input', {
        type: 'range', class: 'qz-seek', min: 0, max: 1, step: 1, value: 0,
        oninput: () => { P.seeking = true; v.elapsed.textContent = K.fmtClock(+v.seek.value); },
        onchange: () => P.seekTo(+v.seek.value),
    });
    v.buttons = h('div', { class: 'qz-pbtns' },
        btn('⏮', 'Previous track', () => P.transport('prev')),
        v.toggle,
        btn('⏹', 'Stop', () => P.transport('stop')),
        btn('⏭', 'Next track', () => P.transport('next')));
    v.seekRow = h('div', { class: 'qz-pseek' }, v.elapsed, v.seek, v.total);
    return v;
};

P.buildPlayer = () => {
    const v = P.makeView(false);
    P.views.push(v);
    K.clear(P.player).append(
        h('div', { class: 'qz-pinfo tap', title: 'Open the player', onclick: () => P.openFull() },
            v.cover, h('div', { class: 'qz-ptext' }, v.title, v.sub)),
        v.buttons, v.seekRow,
        h('button', { type: 'button', class: 'btn qz-pbtn qz-expand', title: 'Open the player', onclick: () => P.openFull() }, '⌃'));
};

// Full screen, like Qobuz's own player: the cover as large as the screen
// allows, the track, the transport, and the queue (a tap plays from there).
P.openFull = () => {
    if (P.fullEl || !P.usable()) return;
    const v = P.makeView(true);
    v.work = h('div', { class: 'qz-fwork muted' });
    v.detail = h('div', { class: 'qz-fdetail muted' });
    P.queueHead = h('div', { class: 'lbl' }, 'Queue');
    P.queueBox = h('div', { class: 'qz-queue' });
    P.fullEl = h('div', { class: 'scrim qz-full' },
        h('div', { class: 'qz-full-top' },
            h('button', { type: 'button', class: 'btn qz-pbtn', title: 'Back to the search', onclick: () => P.closeFull() }, '⌄'),
            h('span', { class: 'qz-full-label' }, 'Now playing')),
        h('div', { class: 'qz-full-body' },
            v.cover,
            h('div', { class: 'qz-full-side' },
                h('div', { class: 'qz-full-info' }, v.title, v.work, v.sub, v.detail),
                v.seekRow, v.buttons, P.queueHead, P.queueBox)));
    document.getElementById('overlay-root').append(P.fullEl);
    P.fullView = v;
    P.views.push(v);
    P.queueVersion = null;
    P.markedId = null;
    document.addEventListener('keydown', P.fullKey);
    P.paintViews();
    P.playerPoll.now();
};

P.closeFull = () => {
    if (!P.fullEl) return;
    P.fullEl.remove();
    P.fullEl = null;
    P.views = P.views.filter(v => v !== P.fullView);
    P.fullView = null;
    document.removeEventListener('keydown', P.fullKey);
};
P.fullKey = e => { if (e.key === 'Escape') P.closeFull(); };

P.transport = async (action, extra = {}) => {
    const d = await K.api('/k/api/transport', { json: { action, ...extra } });
    if (!d.ok) K.toast(d.error || `${action} failed`, 'error');
    P.playerPoll.now();
};

P.seekTo = async seconds => {
    await P.transport('seek', { seconds });
    P.seeking = false;
};

// The Qobuz track playing, once known: its album's cover, title, label, year.
P.trackInfo = () => {
    const t = P.tracks.get(P.trackId);
    return t && typeof t.then !== 'function' ? t : null;
};

P.refreshPlayer = async () => {
    const d = await K.api('/k/api/player', { timeout: 6000 });
    P.now = d.ok ? d : null;
    P.playerError = d.ok ? '' : (d.error || 'MPD unavailable');
    P.base = d.ok ? { elapsed: d.elapsed, duration: d.duration, at: performance.now(), playing: d.state === 'play' } : null;
    const m = d.ok ? d.file.match(PLUGIN_TRACK) : null;
    P.trackId = m ? m[1] : null;
    if (P.trackId && !P.tracks.has(P.trackId)) {
        const id = P.trackId;
        // Looked up once per track; a failure is remembered too, not retried every poll.
        P.tracks.set(id, K.api('/qobuz/track/' + id, { timeout: 15000 })
            .then(r => { P.tracks.set(id, r.ok ? r.track : null); P.paintViews(); }));
    }
    P.paintViews();
    if (P.fullEl && d.ok && d.version !== P.queueVersion) P.loadQueue();
    else if (P.fullEl) P.markQueue();
};

const setCover = (box, src) => {
    if (box.dataset.src === src) return;
    box.dataset.src = src;
    K.clear(box).append(src ? h('img', { src, alt: '' }) : h('span', { class: 'qz-nocover' }, '♪'));
};

P.paintViews = () => {
    const d = P.now, t = P.trackInfo(), album = t ? t.album : null;
    const playing = !!(P.base && P.base.playing);
    const title = !d ? '—' : d.state === 'stop' && !d.title ? 'Stopped' : (d.title || (t && t.title) || '—');
    const pos = d && d.pos && d.length ? `${d.pos} / ${d.length}` : '';
    for (const v of P.views) {
        v.title.textContent = title;
        v.toggle.textContent = playing ? '⏸' : '▶';
        setCover(v.cover, album ? (v.full ? album.image_large || album.image : album.image || album.image_large) : '');
        if (v.full) {
            v.work.textContent = t && t.work ? t.work : '';
            v.sub.textContent = d ? d.artist || (t && t.performer) || '' : P.playerError;
            v.detail.textContent = [
                (d && d.album) || (album && album.title), (album && album.label) || (d && d.label),
                (album && album.year) || (d && d.date || '').slice(0, 4), pos ? `track ${pos}` : '',
            ].filter(Boolean).join(' · ');
        } else {
            v.sub.textContent = d ? [d.artist, d.album, pos].filter(Boolean).join(' · ') : P.playerError;
        }
    }
    P.paintTime();
};

P.paintTime = () => {
    const b = P.base;
    const known = b && Number.isFinite(b.duration) && b.duration > 0 && Number.isFinite(b.elapsed);
    const e = known ? K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration) : 0;
    for (const v of P.views) {
        v.seek.disabled = !known;
        v.total.textContent = b && Number.isFinite(b.duration) ? K.fmtClock(b.duration) : '–:––';
        if (P.seeking) continue;          // a finger is on a slider
        if (!known) { v.elapsed.textContent = '–:––'; v.seek.value = 0; continue; }
        v.seek.max = Math.floor(b.duration);
        v.seek.value = Math.floor(e);
        v.elapsed.textContent = K.fmtClock(e);
    }
};

// The queue is re-read only when MPD says it changed (status "playlist").
P.loadQueue = async () => {
    const q = await K.api('/k/api/queue', { timeout: 8000 });
    if (!P.fullEl) return;
    if (!q.ok) { K.clear(P.queueBox).append(h('div', { class: 'muted small' }, q.error || 'queue unavailable')); return; }
    P.queueVersion = q.version;
    P.queueHead.textContent = q.length ? `Queue · ${q.length}` : 'Queue is empty';
    K.clear(P.queueBox).append(...q.songs.map(s => h('button', {
        type: 'button', class: 'qz-qrow', dataset: { id: s.id }, title: 'Play from here',
        onclick: () => P.transport('jump', { pos: s.pos }),
    },
        h('span', { class: 'qz-num muted' }, s.pos),
        h('span', { class: 'qz-qtext' }, h('span', { class: 'qz-qtitle' }, s.title),
            s.artist ? h('span', { class: 'qz-qartist muted' }, s.artist) : null),
        h('span', { class: 'qz-dur muted' }, K.fmtClock(s.duration)))));
    if (q.length > q.songs.length) P.queueBox.append(h('div', { class: 'muted small' }, `… and ${q.length - q.songs.length} more`));
    P.markedId = null;
    P.markQueue();
};

P.markQueue = () => {
    const id = P.now ? P.now.songid : '';
    let current = null;
    for (const row of P.queueBox.children) {
        const on = !!id && row.dataset.id === id;
        row.classList.toggle('on', on);
        if (on) current = row;
    }
    if (current && id !== P.markedId) current.scrollIntoView({ block: 'nearest' });
    P.markedId = id;
};

K.registerPage(P);
})();
