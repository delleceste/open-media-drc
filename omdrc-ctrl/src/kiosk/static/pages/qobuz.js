/* Qobuz (optional: [qobuz_search] enabled): album search with label
 * and release-date filters, results newest first, and a player strip.  ▶ and +
 * queue an album on upmpdcli (/qobuz/play), so the box streams it itself; the
 * phone only drives.  The whole page is greyed while upmpdcli is not the running
 * renderer: the other renderer could not play what is found here.
 *
 * The search field completes from a classical word list (composers, works,
 * forms, instruments, performers: /qobuz/words) and from what was played
 * after a search (the search text, the album's artist and composer).  The
 * player strip opens into a full-screen player with the cover and the queue.
 *
 * − on a result lowers it: the album (and, from the bar that follows, its label
 * or artist) moves to the end of every result list, folded away; the Lowered
 * list, by the filters, restores entries or clears them (/qobuz/lowered). */
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
        oninput: () => { P.suggestSoon(); P.paintStale(); },
        onfocus: () => P.suggestSoon(),
        onblur: () => setTimeout(() => P.showSuggestions([]), 150),
    });
    P.suggestBox = h('div', { class: 'qz-suggest', role: 'listbox' });
    P.input.value = pref('q', '');
    P.labelsBox = h('div', { class: 'qz-chips' });
    P.seenBox = h('div', {});
    P.dateBox = h('div', {});
    P.sortBox = h('div', {});
    // Full width; the filters sit side by side where there is room, and fold
    // into one summary line once results arrive (a tap opens them again).
    P.fsum = h('button', { type: 'button', class: 'qz-fsum', onclick: () => P.openFilters(!P.filtersOpen) });
    P.filters = h('div', { class: 'qz-filters' },
        h('div', { class: 'qz-fgroup' }, h('div', { class: 'lbl' }, 'Labels'), P.labelsBox, P.seenBox),
        h('div', { class: 'qz-fgroup' }, h('div', { class: 'lbl' }, 'Released'), P.dateBox,
            h('div', { class: 'lbl' }, 'Order'), P.sortBox,
            P.lowLink = h('button', { type: 'button', class: 'btn link qz-lowlink', onclick: () => P.openLowered() }, 'Lowered list ›')));
    P.form = h('div', { class: 'qz-form' },
        K.card(null,
            h('div', { class: 'qz-searchwrap' },
                // a real form: the keyboard's Search key submits it (Android's IME action
                // does not always come through as an Enter keydown)
                h('form', { class: 'qz-searchrow', action: '', onsubmit: e => { e.preventDefault(); P.go(); } }, P.input,
                    h('button', { type: 'submit', class: 'btn primary qz-go' }, 'Search'),
                    // down on Now, once there are results: over to them, in Search's place
                    // until the text or a filter changes (kiosk.css, paintStale)
                    h('button', { type: 'button', class: 'btn qz-toresults', title: 'Open the results', 'aria-label': 'Open the results', onclick: () => P.openResults() }, '›')),
                P.suggestBox),
            P.fsum, P.filters));
    P.results = h('div', { class: 'qz-results' });
    P.preview = h('div', { class: 'qz-preview', hidden: true });   // on Now only, above the box
    P.player = h('div', { class: 'qz-player' });
    // the box's place on this page (it may be on Now instead: see "where the search box is"),
    // and the head shown in its place over results searched from Now
    P.formHome = h('div', {}, P.form);
    P.resHead = h('div', { class: 'qz-reshead', hidden: true },
        h('button', { type: 'button', class: 'btn qz-back', title: 'Back to Now playing', 'aria-label': 'Back to Now playing', onclick: () => K.showPage('now') }, '‹'),
        h('span', { class: 'qz-reshead-title' }, 'Search results'));
    P.main = h('div', { class: 'qz-main' }, P.resHead, P.formHome, P.results);
    el.append(h('div', { class: 'qz' }, P.banner, P.main, P.player));
    P.input.addEventListener('focus', () => P.revealInput());
    P.buildPlayer();
    P.paintDate(); P.paintSort(); P.paintLabels();
    P.openFilters(true);
    P.poll = new K.Poller(P.refreshStatus, 10000);
    P.playerPoll = new K.Poller(P.refreshPlayer, 2000);
    P.recent();
    P.loadWords();
    K.api('/qobuz/lowered').then(d => { if (d.ok) { P.lowCountCache = d.entries.length; P.paintLowLink(); } });
};

P.show = () => {
    P.visible = true;
    // searched from Now: the results under their head, the box stays down there;
    // otherwise the box is up here
    P.fromNowShown = P.fromNow;
    P.fromNow = false;
    P.resHead.hidden = !P.fromNowShown;
    if (!P.fromNowShown) P.undock();
    P.poll.start();
    P.playerPoll.start();
    P.clock = setInterval(P.paintTime, 500);
};
P.hide = () => {
    P.visible = false;
    P.poll.stop();
    P.playerPoll.stop();
    clearInterval(P.clock);
    // results searched from Now are only for the moment: left (‹, a play, a swipe),
    // they go, from here and from the preview on Now
    if (P.fromNowShown) { P.fromNowShown = false; P.forget(); }
};

// Back to before any search: no results, no preview, "Played recently" again.  The
// text and the filters stay.  A search still running is dropped.
P.forget = () => {
    ++P.searching;
    if (P.streamEnd) P.streamEnd({ ok: false, error: 'dropped' });
    if (P.moreObserver) P.moreObserver.disconnect();
    P.last = null; P.request = null; P.searchedKey = null;
    P.paintPreview();
    P.paintStale();
    P.paintSeen();
    K.clear(P.results);
    P.recent();
};
P.prefetch = () => P.refreshStatus();     // which also loads the labels

// ── where the search box is ──────────────────────────────────────────────────
// One search box (field, completions, filters), in one place at a time: at the
// bottom of Now while that page is upright (pages/now.js gives it a slot), else at
// the top of this page.  A search made down there opens this page on its results,
// under a "‹ Search results" head whose ‹ goes back to Now.
P.ensureMounted = () => {
    if (!P.mounted) { P.mounted = true; P.mount(P.body); P.refreshStatus(); }
};
P.dockedOnNow = () => !!P.form && P.form.parentElement !== P.formHome;
K.qobuzDock = slot => {
    P.ensureMounted();
    if (P.form.parentElement === slot) return;
    P.openFilters(false);                          // folded down there until asked for
    slot.append(P.preview, P.form);
    P.form.classList.add('qz-docked');
};
P.undock = () => {
    if (!P.dockedOnNow()) return;
    P.form.classList.remove('qz-docked');
    P.preview.remove();
    P.formHome.append(P.form);
};

// An explicit search (the button, Enter).  From Now it stays there: the results show
// as a preview above the box, and › opens them here.
P.go = () => {
    P.input.blur();
    P.showSuggestions([]);
    P.search();
};
// No smooth scroll: the keyboard closing meanwhile resizes the page, and a resize
// puts the pager back on the page it is on (main.js).
P.openResults = () => {
    P.input.blur();
    P.fromNow = true;
    K.showPage(P.id, false);
    P.el.scrollTop = 0;
};

// The app shrinks the page by the keyboard: keep the field (and at the bottom of Now,
// the completions above it) in sight once it has.
P.revealInput = () => {
    [120, 450].forEach(ms => setTimeout(() => {
        if (document.activeElement === P.input) P.input.scrollIntoView({ block: 'nearest' });
    }, ms));
};
addEventListener('resize', () => { if (P.input && document.activeElement === P.input) P.revealInput(); });

// The field keeps the focus only while it is being used.  A focused field is what
// Android brings the keyboard back for, on the next touch anywhere in the page (the
// cover's seek ring cancels the touch's default, so the browser does not blur it
// then); the keyboard then shrinks the page and the field is scrolled into view.
// So: a touch outside the search box, or the keyboard closing (the Android back
// gesture, which blurs nothing), ends the typing.
document.addEventListener('pointerdown', e => {
    if (P.input && document.activeElement === P.input && !(e.target.closest && e.target.closest('.qz-searchwrap')))
        P.input.blur();
}, { capture: true, passive: true });
// The keyboard has gone when the page, having shrunk for it, is back to its height from
// before the field took the focus.  Not merely "grew again": in the app the page may
// shrink twice as the keyboard comes up (the WebView's own step, then the app's padding),
// and the step back between the two is not the keyboard going.
if (window.visualViewport) {
    let base = visualViewport.height, low = base;    // before the focus; the lowest since
    document.addEventListener('focusin', e => {
        if (e.target === P.input) { base = visualViewport.height; low = base; }
    });
    visualViewport.addEventListener('resize', () => {
        if (!P.input || document.activeElement !== P.input) return;
        const hgt = visualViewport.height;
        low = Math.min(low, hgt);
        if (low < base - 120 && hgt > base - 60) P.input.blur();
    });
}

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
    P.form.classList.toggle('qz-disabled', !P.usable() && P.dockedOnNow());   // on Now, outside P.main
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
    P.paintSummary();
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
        kids.push(h('div', { class: 'win-row qz-span' },
            P.yearSpinner('from'), h('span', { class: 'muted' }, 'to'), P.yearSpinner('to')));
    }
    K.clear(P.dateBox).append(...kids);
    P.paintSummary();
};

// ── year spinners (the from – to span) ───────────────────────────────────────
// A year is changed by dragging it up or down, faster the further the finger
// goes (a quarter of a screen is decades); a tap opens a picker instead.
// "to" can also be "today" (no upper bound), one step above this year.
const YEAR_MIN = 1900;
const TODAY = thisYear + 1;                  // "to" only: the open end
const yearOf = key => {
    const v = parseInt(pref(key, key === 'from' ? thisYear - 5 : ''), 10);
    return Number.isFinite(v) ? K.clamp(v, YEAR_MIN, thisYear) : key === 'from' ? thisYear - 5 : TODAY;
};
const yearText = y => y === TODAY ? 'today' : String(y);

// Store one end, and move the other when the span would turn inside out.
P.setYear = (key, y) => {
    setPref(key, y === TODAY ? '' : y);
    const from = yearOf('from'), to = yearOf('to');
    if (to !== TODAY && from > to) setPref(key === 'from' ? 'to' : 'from', key === 'from' ? from : to);
    P.paintDate();
    P.searchSoon();
};

P.yearSpinner = key => {
    const top = key === 'to' ? TODAY : thisYear;
    const value = h('span', { class: 'qz-yval' }, yearText(yearOf(key)));
    const box = h('div', {
        class: 'qz-yspin tap', role: 'spinbutton', tabindex: 0,
        'aria-label': key === 'from' ? 'From year' : 'To year', 'aria-valuenow': yearOf(key),
        title: 'Drag up or down, or tap to pick',
    }, h('span', { class: 'qz-yarrow' }, '▲'), value, h('span', { class: 'qz-yarrow' }, '▼'));
    let drag = null;
    const show = y => { value.textContent = yearText(y); box.setAttribute('aria-valuenow', y); };
    box.addEventListener('pointerdown', e => {
        e.stopPropagation();          // a diagonal drag must not turn the page
        drag = { id: e.pointerId, y: e.clientY, start: yearOf(key), now: yearOf(key), moved: false };
        try { box.setPointerCapture(e.pointerId); } catch {}
        box.classList.add('on');
    });
    box.addEventListener('pointermove', e => {
        if (!drag || e.pointerId !== drag.id) return;
        const dy = drag.y - e.clientY;                   // up = later
        if (Math.abs(dy) > 6) drag.moved = true;
        // 1 year per 12 px at first, then faster: 50 px ≈ 5 years, 150 px ≈ 25
        const steps = Math.sign(dy) * Math.floor(Math.pow(Math.abs(dy) / 12, 1.35));
        drag.now = K.clamp(drag.start + steps, YEAR_MIN, top);
        show(drag.now);
    });
    const end = e => {
        if (!drag || e.pointerId !== drag.id) return;
        const d = drag; drag = null;
        box.classList.remove('on');
        if (!d.moved) P.pickYear(key);
        else if (d.now !== d.start) P.setYear(key, d.now);
    };
    box.addEventListener('pointerup', end);
    box.addEventListener('pointercancel', e => { if (drag && e.pointerId === drag.id) { drag = null; box.classList.remove('on'); show(yearOf(key)); } });
    box.addEventListener('wheel', e => {                  // a mouse on the desktop kiosk
        e.preventDefault();
        P.setYear(key, K.clamp(yearOf(key) + (e.deltaY < 0 ? 1 : -1), YEAR_MIN, top));
    }, { passive: false });
    box.addEventListener('keydown', e => {
        const step = { ArrowUp: 1, ArrowDown: -1, PageUp: 10, PageDown: -10 }[e.key];
        if (step) { e.preventDefault(); P.setYear(key, K.clamp(yearOf(key) + step, YEAR_MIN, top)); }
        else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); P.pickYear(key); }
    });
    return box;
};

// The picker: the year large with ±1 and ±10, a decade of years to tap, and
// for "to" a Today button.  Typing a year works too.
P.pickYear = key => {
    const top = key === 'to' ? TODAY : thisYear;
    let year = yearOf(key);
    let decade = Math.floor(Math.min(year, thisYear) / 10) * 10;
    const field = h('input', {
        type: 'text', class: 'qz-yfield', inputmode: 'numeric', maxlength: 4, 'aria-label': 'Year',
        oninput: e => {
            const v = parseInt(e.target.value, 10);
            if (e.target.value.length === 4 && v >= YEAR_MIN && v <= thisYear) { year = v; decade = Math.floor(v / 10) * 10; paint(false); }
        },
        onkeydown: e => { if (e.key === 'Enter') { e.preventDefault(); done(true); } },
    });
    const grid = h('div', { class: 'qz-ygrid' });
    const decadeLabel = h('span', { class: 'qz-ydecade' });
    const step = n => { year = K.clamp(year + n, YEAR_MIN, top); if (year !== TODAY) decade = Math.floor(year / 10) * 10; paint(); };
    const btn = (label, fn, cls = '') => h('button', { type: 'button', class: 'btn ' + cls, onclick: fn }, label);
    function paint(setField = true) {
        if (setField) field.value = year === TODAY ? '' : String(year);
        field.placeholder = year === TODAY ? 'today' : '';
        decadeLabel.textContent = `${decade}s`;
        K.clear(grid).append(...Array.from({ length: 10 }, (_, i) => decade + i).map(y =>
            h('button', { type: 'button', class: 'btn qz-ycell' + (y === year ? ' active' : ''), disabled: y < YEAR_MIN || y > thisYear,
                onclick: () => { year = y; done(true); } }, y)));
    }
    const close = () => scrim.remove();
    function done(apply) { close(); if (apply) P.setYear(key, year); }
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet qz-ysheet' },
            h('h2', {}, key === 'from' ? 'Released from' : 'Released until'),
            h('div', { class: 'qz-yrow' }, btn('−10', () => step(-10), 'step'), btn('−1', () => step(-1), 'step'),
                field, btn('+1', () => step(1), 'step'), btn('+10', () => step(10), 'step')),
            h('div', { class: 'qz-yrow' },
                btn('‹', () => { decade = Math.max(Math.floor(YEAR_MIN / 10) * 10, decade - 10); paint(false); }, 'step'),
                decadeLabel,
                btn('›', () => { decade = Math.min(Math.floor(thisYear / 10) * 10, decade + 10); paint(false); }, 'step')),
            grid,
            h('div', { class: 'sheet-actions' },
                key === 'to' ? btn('Today', () => { year = TODAY; done(true); }) : null,
                h('span', { class: 'spacer' }),
                btn('Cancel', () => done(false)),
                btn('Set', () => done(true), 'primary'))));
    paint();
    document.getElementById('overlay-root').append(scrim);
};

// ── filter summary ───────────────────────────────────────────────────────────
P.filterSummary = () => {
    const mode = pref('date', 'any');
    const to = yearOf('to');
    const when = mode === 'last' ? (pref('lastN', 2) === 1 ? 'last year' : `last ${pref('lastN', 2)} years`)
        : mode === 'span' ? `${yearOf('from')}–${to === TODAY ? 'today' : to}` : 'any time';
    return [P.selected.size ? [...P.selected].join(', ') : 'all labels', when,
        pref('order', 'relevance') === 'date' ? 'newest first' : 'Qobuz order'].join(' · ');
};

P.paintSummary = () => {
    if (!P.fsum) return;
    K.clear(P.fsum).append(h('span', { class: 'qz-fsum-text' }, P.filterSummary()),
        h('span', { class: 'qz-fsum-mark' }, P.filtersOpen ? 'Hide filters ▴' : 'Filters ▾'));
};

P.openFilters = open => {
    P.filtersOpen = open;
    P.filters.hidden = !open;
    P.paintSummary();
    if (open && P.dockedOnNow()) setTimeout(() => P.form.scrollIntoView({ block: 'end', behavior: 'smooth' }), 30);
};

P.paintSort = () => {
    P.paintSummary();
    K.clear(P.sortBox).append(K.segmented([
        { value: 'relevance', label: 'Qobuz order' }, { value: 'date', label: 'Newest first' },
    ], pref('order', 'relevance'), v => { setPref('order', v); P.paintSort(); P.searchSoon(); }, 'small'));
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
    q.set('sort', pref('order', 'relevance'));
    if (scan) q.set('scan', scan);
    return q;
};

// A filter changed: search again, but only once a search has been made (the
// first one is always asked for), and not on every step of a quick change.
let soon = null;
P.searchSoon = () => {
    clearTimeout(soon);
    P.paintStale();
    if (P.dockedOnNow()) return;                  // down on Now: the next Search uses them
    if (P.last || P.request) soon = setTimeout(() => P.search(), 450);
};

P.search = async (scan = 0, { quiet = false } = {}) => {
    clearTimeout(soon);
    const params = P.params(scan);
    if (!params.has('q') && !params.has('label')) {
        K.toast('Type something or tick a label', 'error');
        return;
    }
    setPref('q', P.input.value.trim());
    if (!scan) P.searchedKey = params.toString();
    const seq = ++P.searching;
    P.request = params.toString();
    if (!quiet) P.paintWorking(scan ? 'Reading further…' : 'Searching…', !!scan);
    if (!scan && !quiet) { P.paintPreview('Searching…'); P.revealPreview(); }
    // a new search streams its partial results into the preview; "Load more" just asks
    const d = scan ? await K.api('/qobuz/search?' + params, { timeout: 120000 })
        : await P.streamSearch(params, seq);
    if (seq !== P.searching) return;
    P.request = null;
    if (!d.ok) {
        if (d.renderer === false) P.refreshStatus();
        P.paintError(d.error || 'search failed');
        P.paintPreview(d.error || 'search failed');
        return;
    }
    // A new search may read on by itself at the end of its list; a further page
    // only if the previous one brought albums (see watchMore).
    P.autoMore = !scan || (!!P.last && d.count > P.last.count);
    P.last = d;
    P.paintSeen();
    P.paintResults();
    P.paintPreview();
    P.paintStale();
    // Results fold the filters away: the list gets the screen.
    if (!quiet) P.openFilters(false);
};

P.paintWorking = (text, more_) => {
    // A "Load more" keeps the list on screen and only turns its button into a spinner.
    const more = more_ && P.results.querySelector('.qz-more');
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
        d.sort === 'date' ? ' · newest first' : ' · Qobuz order',
        d.unstreamable ? ` · ${d.unstreamable} not available` : '',
        P.lowCount(d) ? ` · ${P.lowCount(d)} lowered` : '')];
    failed.forEach(q => kids.push(h('div', { class: 'errbox warn small' }, `“${q.query}”: stopped after ${q.fetched} albums: ${q.error}`)));
    if (!d.results.length) kids.push(h('p', { class: 'muted' }, d.more
        ? 'Nothing matches yet among the albums read so far.'
        : 'Nothing matches.'));
    const shown = d.results.filter(c => !c.lowered), low = d.results.filter(c => c.lowered);
    kids.push(h('div', { class: 'qz-list' }, shown.map(P.row)));
    if (low.length) kids.push(h('details', { class: 'qz-lowgroup', open: P.lowOpen || null,
        ontoggle: e => { P.lowOpen = e.target.open; } },
        h('summary', { class: 'small muted' }, `${low.length} lowered result${low.length === 1 ? '' : 's'}`),
        h('div', { class: 'qz-list' }, low.map(P.row))));
    if (d.more) {
        const more = h('button', { type: 'button', class: 'btn qz-more', onclick: () => P.search(d.next_scan) }, 'Load more');
        kids.push(more);
        P.watchMore(more, d);
    }
    K.clear(P.results).append(...kids);
};

// The search as server-sent events: partial results (already in their final order)
// repaint the preview while Qobuz is being read; the promise gets the whole answer.
// A newer search ends the older one's stream.
P.streamSearch = (params, seq) => new Promise(resolve => {
    if (P.streamEnd) P.streamEnd({ ok: false, error: 'replaced' });
    const es = new EventSource('/qobuz/search/stream?' + params);
    const end = d => {
        if (P.streamEnd !== end) return;
        P.streamEnd = null;
        clearTimeout(timer);
        es.close();
        resolve(d);
    };
    P.streamEnd = end;
    const timer = setTimeout(() => end({ ok: false, error: 'the search took too long' }), 120000);
    es.onmessage = ev => {
        let d;
        try { d = JSON.parse(ev.data); } catch { return; }
        if (d.ok && d.partial) { if (seq === P.searching) P.paintPreview(null, d); return; }
        end(d);
    };
    es.onerror = () => end({ ok: false, error: 'the search was cut off' });
});

// The preview on Now: one line of text per album, the lowered ones last and dimmed;
// or, with text given, just that (searching, an error).  `d` is a partial answer
// while the search runs (see streamSearch), else the last one.
P.paintPreview = (text, d = P.last) => {
    const partial = !!(d && d.partial);
    P.form.classList.toggle('qz-has-res', !text && !partial && !!d);
    P.preview.hidden = false;
    if (text) { K.clear(P.preview).append(h('div', { class: 'qz-pv-line muted' }, text)); return; }
    if (!d) { P.preview.hidden = true; return; }
    // the artist only if the search was not for them ("rolling stones" leaves out
    // "The Rolling Stones"); then the year and the label
    const asked = P.plain(P.input.value);
    const named = a => { const x = P.plain(a); return !!x && !!asked && (asked.includes(x) || (asked.length > 2 && x.includes(asked))); };
    const line = c => h('div', { class: 'qz-pv-line' + (c.lowered ? ' low' : '') },
        [c.title + (c.version ? ` (${c.version})` : ''), named(c.artist) ? '' : c.artist, c.year, c.label]
            .filter(Boolean).join(' · '));
    const n = `${d.count} album${d.count === 1 ? '' : 's'}`;
    const head = partial ? `Searching… ${n} so far (${d.considered} looked at)` : n + (d.more ? ' so far' : '');
    const top = P.preview.scrollTop;
    K.clear(P.preview).append(h('div', { class: 'qz-pv-line muted' }, head), ...d.results.map(line));
    P.preview.scrollTop = partial ? top : 0;     // the live list stays where it is being read
};

// Down on Now the preview has a height of its own (kiosk.css); the page scrolls so it
// and the box are in view.  Again once the keyboard has gone: that resizes the page.
P.revealPreview = () => {
    if (!P.dockedOnNow()) return;
    [0, 450].forEach(ms => setTimeout(() => P.form.scrollIntoView({ block: 'end', inline: 'nearest' }), ms));
};

// Down on Now, once there are results, › takes Search's place; Search comes back when
// the text or a filter no longer matches the search those results answer.
P.paintStale = () => {
    if (P.form) P.form.classList.toggle('qz-stale', !P.last || P.params(0).toString() !== P.searchedKey);
};

// A name or a search text, compared loosely: no case, accents, punctuation or leading "the".
P.plain = t => fold(String(t || '')).replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/^the /, '');

// Reaching the end of the list reads on by itself, as scrolling does in Qobuz's
// app -- but only while that keeps bringing albums: a filter that matches
// nothing further down waits for a tap instead of reading thousands.
P.watchMore = (button, d) => {
    if (P.moreObserver) P.moreObserver.disconnect();
    if (!P.autoMore || !d.results.length || !('IntersectionObserver' in window)) return;
    P.moreObserver = new IntersectionObserver(entries => {
        if (!entries.some(e => e.isIntersecting) || P.request || P.last !== d) return;
        P.moreObserver.disconnect();
        P.search(d.next_scan);
    }, { root: P.el, rootMargin: '0px 0px 200px 0px' });
    P.moreObserver.observe(button);
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
    } else if (e.key === 'Enter' && n && P.active >= 0) {
        e.preventDefault();                       // a completion, not a search
        P.pick(P.active);
    }                                             // else Enter submits the form: P.go()
};

// ── lowering ─────────────────────────────────────────────────────────────────
P.lowCount = d => d.results.filter(c => c.lowered).length;

P.lowerApi = body => K.api('/qobuz/lowered', { json: body });

// − : the album at once (the row goes, and joins the folded group at the end),
// then a bar to undo it or to lower its whole label or artist instead.
P.lower = async (c, row) => {
    row.classList.add('going');
    const d = await P.lowerApi({ action: 'add', kind: 'album', key: c.id, name: c.title });
    if (!d.ok) { row.classList.remove('going'); K.toast(d.error || 'could not lower it', 'error'); return; }
    c.lowered = { kind: 'album', key: c.id, name: c.title };
    P.lowCountCache = d.entries.length;
    const inResults = P.last && P.last.results.includes(c);
    if (inResults) {
        // to the end, in the order the server would give it
        P.last.results = [...P.last.results.filter(x => x !== c && !x.lowered), c, ...P.last.results.filter(x => x !== c && x.lowered)];
        P.paintResults();
    } else row.remove();
    P.paintLowLink();
    P.snack(`Lowered “${c.title}”`, [
        { label: 'Undo', run: () => P.restore(c.lowered) },
        c.label ? { label: `All of ${c.label}`, run: () => P.lowerMore('label', c.label, c) } : null,
        c.artist ? { label: `All by ${c.artist}`, run: () => P.lowerMore('artist', c.artist, c) } : null,
    ]);
};

// A whole label or artist: the album entry is replaced by the wider one.
P.lowerMore = async (kind, name, c) => {
    await P.lowerApi({ action: 'remove', kind: 'album', key: c.id });
    const d = await P.lowerApi({ action: 'add', kind, key: name, name });
    if (!d.ok) { K.toast(d.error || 'could not lower it', 'error'); return; }
    K.toast(`Lowered all ${kind === 'label' ? 'of' : 'by'} ${name}`);
    P.lowCountCache = d.entries.length;
    P.paintLowLink();
    P.refreshResults();
};

P.restore = async entry => {
    const d = await P.lowerApi({ action: 'remove', kind: entry.kind, key: entry.key });
    if (!d.ok) { K.toast(d.error || 'could not restore it', 'error'); return; }
    P.lowCountCache = d.entries.length;
    P.paintLowLink();
    P.refreshResults();
};

// Ask again, as deep as the list on screen: the pages come from the server's cache.
P.refreshResults = () => { if (P.last && !P.request) P.search(P.last.scan > 50 ? P.last.scan : 0, { quiet: true }); };

// A bar above the player strip: a message and a few actions, gone after 10 s.
P.snack = (text, actions) => {
    clearTimeout(P.snackTimer);
    if (P.snackEl) P.snackEl.remove();
    const close = () => { clearTimeout(P.snackTimer); if (P.snackEl === bar) P.snackEl = null; bar.remove(); };
    const bar = h('div', { class: 'qz-snack' },
        h('div', { class: 'qz-snack-text' }, text),
        h('div', { class: 'qz-snack-acts' }, actions.filter(Boolean).map(a =>
            h('button', { type: 'button', class: 'btn', onclick: () => { close(); a.run(); } }, a.label)),
            h('button', { type: 'button', class: 'btn qz-snack-x', title: 'Close', onclick: close }, '✕')));
    P.snackEl = bar;
    P.player.before(bar);
    P.snackTimer = setTimeout(close, 10000);
};

// The Lowered list: every entry with its kind, restore one, or clear them all.
P.paintLowLink = () => {
    if (!P.lowLink) return;
    const n = P.lowCountCache;
    P.lowLink.textContent = n ? `Lowered list (${n}) ›` : 'Lowered list ›';
};

P.openLowered = async () => {
    const d = await K.api('/qobuz/lowered');
    if (!d.ok) { K.toast(d.error || 'could not read the lowered list', 'error'); return; }
    const KIND = { album: 'Album', label: 'Label', artist: 'Artist' };
    const list = h('div', { class: 'qz-lowlist' });
    const close = () => scrim.remove();
    const paint = entries => {
        P.lowCountCache = entries.length;
        P.paintLowLink();
        K.clear(list).append(...(entries.length ? entries.map(e => h('div', { class: 'qz-lowrow' },
            h('span', { class: 'chip dim' }, KIND[e.kind] || e.kind),
            h('span', { class: 'qz-lowname' }, e.name),
            h('button', { type: 'button', class: 'btn', onclick: async () => {
                const r = await P.lowerApi({ action: 'remove', kind: e.kind, key: e.key });
                if (r.ok) { paint(r.entries); P.changedLow = true; }
            } }, '↺ Restore'))) : [h('p', { class: 'muted' }, 'Nothing is lowered.')]));
    };
    P.changedLow = false;
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) done(); } },
        h('div', { class: 'sheet qz-lowsheet' },
            h('h2', {}, 'Lowered'),
            h('p', {}, 'These come last in every result list, folded away. Restore one to see it in its place again.'),
            list,
            h('div', { class: 'sheet-actions' },
                h('button', { type: 'button', class: 'btn danger-soft', onclick: async () => {
                    if (!P.lowCountCache) return;
                    const yes = await K.confirm({ title: 'Clear the lowered list?', message: 'Every album, label and artist in it gets its place back.', ok: 'Clear', danger: true });
                    if (!yes) return;
                    const r = await P.lowerApi({ action: 'clear' });
                    if (r.ok) { paint(r.entries); P.changedLow = true; }
                } }, 'Clear all'),
                h('span', { class: 'spacer' }),
                h('button', { type: 'button', class: 'btn primary', onclick: () => done() }, 'Done'))));
    function done() { close(); if (P.changedLow) P.refreshResults(); }
    paint(d.entries);
    document.getElementById('overlay-root').append(scrim);
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
    const off = c.streamable === false;
    const row = h('div', { class: 'qz-row' + (off ? ' off' : '') + (c.lowered ? ' lowered' : '') },
        c.image ? h('img', { class: 'qz-cover', src: c.image, alt: '', loading: 'lazy' }) : h('div', { class: 'qz-cover' }),
        body,
        h('div', { class: 'qz-act' },
            h('button', { type: 'button', class: 'btn primary qz-play', disabled: off, title: off ? 'Not available on Qobuz' : 'Replace the queue and play', onclick: () => P.play(c, 'replace') }, '▶'),
            h('button', { type: 'button', class: 'btn qz-add', disabled: off, title: off ? 'Not available on Qobuz' : 'Add to the queue', onclick: () => P.play(c, 'append') }, '+'),
            c.lowered
                ? h('button', { type: 'button', class: 'btn qz-low', title: `Lowered (${c.lowered.kind}: ${c.lowered.name}): restore`, onclick: () => P.restore(c.lowered) }, '↺')
                : h('button', { type: 'button', class: 'btn qz-low', title: 'Lower: show it last, folded away', onclick: () => P.lower(c, row) }, '−')),
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
    // results searched from Now: back there, to what is now playing
    if (P.visible && P.fromNowShown) K.showPage('now');
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
    v.prog = h('div', { class: 'qz-pprog' }, h('i'));    // upright: a thin line instead of the slider
    P.views.push(v);
    K.clear(P.player).append(v.prog,
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
    // no slider here: the cover's seek ring (widgets/seekring.js), shown for a moment
    // on opening so it is known to be there (paintTime, once the track's length is)
    v.cover.classList.add('seek-zone');
    v.ring = new K.SeekRing(v.cover, { usable: P.seekable, elapsed: () => P.elapsedNow(),
        duration: () => P.base.duration, seek: s => P.seekTo(s) });
    P.ringHint = true;
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
    [...box.children].forEach(c => { if (!c.classList.contains('seek-ring')) c.remove(); });   // the ring stays
    box.prepend(src ? h('img', { src, alt: '' }) : h('span', { class: 'qz-nocover' }, '♪'));
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

P.seekable = () => !!(P.base && Number.isFinite(P.base.duration) && P.base.duration > 0 && Number.isFinite(P.base.elapsed));
P.elapsedNow = () => {
    const b = P.base;
    return K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration);
};

P.paintTime = () => {
    if (P.ringHint && P.fullView && P.seekable()) { P.ringHint = false; P.fullView.ring.flash(1000); }
    const b = P.base;
    const known = b && Number.isFinite(b.duration) && b.duration > 0 && Number.isFinite(b.elapsed);
    const e = known ? K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration) : 0;
    for (const v of P.views) {
        v.seek.disabled = !known;
        v.total.textContent = b && Number.isFinite(b.duration) ? K.fmtClock(b.duration) : '–:––';
        if (v.prog) v.prog.firstChild.style.width = known ? `${(e / b.duration * 100).toFixed(1)}%` : '0';
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
    // Scroll the queue list alone (landscape, where it scrolls by itself): upright
    // the whole sheet scrolls, and the cover must stay where the sheet opened.
    const box = P.queueBox;
    if (current && id !== P.markedId && box.scrollHeight > box.clientHeight + 2) {
        const top = current.offsetTop - box.offsetTop;
        if (top < box.scrollTop || top + current.offsetHeight > box.scrollTop + box.clientHeight)
            box.scrollTop = Math.max(0, top - box.clientHeight / 3);
    }
    P.markedId = id;
};

K.registerPage(P);
})();
