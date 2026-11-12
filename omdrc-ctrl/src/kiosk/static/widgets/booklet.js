/* The digital booklet of a Qobuz release: a chip that opens the PDF, with a ▾ beside it for the menu (each PDF, and
 * an AI summary made from the booklet alone: the composers, the works' context, the works, the recording's technical
 * facts, a live recording called out).  Summaries are kept on the box (/qobuz/ai/booklet/<id>); one being made goes
 * on when its page is closed, and the chip shows it working. */
(() => {
'use strict';
const h = K.h;
const B = K.booklet = { lists: new Map(), jobs: new Map(), listeners: new Set() };

const newJob = () => {
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 15) | 64;
    b[8] = (b[8] & 63) | 128;
    const hex = [...b].map(n => n.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
const changed = () => B.listeners.forEach(fn => fn());

// The album's booklets ([{name, url, description}]), asked of Qobuz once per album.
B.list = albumId => {
    if (!albumId) return Promise.resolve({ booklets: [], album: null });
    if (!B.lists.has(albumId)) {
        B.lists.set(albumId, K.api('/qobuz/album/' + encodeURIComponent(albumId), { timeout: 20000 }).then(d => {
            if (!d.ok) { B.lists.delete(albumId); return { booklets: [], album: null }; }
            return { booklets: d.album.booklets || [], album: d.album };
        }));
    }
    return B.lists.get(albumId);
};

// Make (or fetch the kept) summary; one request per album at a time, shared by whoever asks.
B.make = (albumId, refresh = false) => {
    if (B.jobs.has(albumId)) return B.jobs.get(albumId).promise;
    const job = newJob();
    const entry = { job, promise: null };
    entry.promise = fetch('/qobuz/ai/booklet/' + encodeURIComponent(albumId), {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
        body: JSON.stringify({ job, refresh }) })
        .then(r => r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` })))
        .catch(() => ({ ok: false, error: 'network error' }))
        .then(d => {
            if (B.jobs.get(albumId) === entry) B.jobs.delete(albumId);
            if (entry.stopped) d = { ok: false, error: 'Summary stopped', stopped: true };
            if (d.ok && d.model_notice) K.toast(d.model_notice);
            changed();
            return d;
        });
    B.jobs.set(albumId, entry);
    changed();
    return entry.promise;
};
B.stop = albumId => {
    const entry = B.jobs.get(albumId);
    if (!entry) return;
    entry.stopped = true;
    fetch('/qobuz/ai/listening/cancel', { method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
        body: JSON.stringify({ job: entry.job }) }).catch(() => {});
};

// ── the chip ─────────────────────────────────────────────────────────────────
// B.chip() → { el, show(albumId, title) }: hidden unless the album has a booklet.
// { icon: true }: a line-drawn book alone, no word (the Now page, short of height)
B.chip = ({ icon = false } = {}) => {
    let albumId = '', title = '', booklets = [], token = 0;
    const open = h('button', { type: 'button', class: 'chip bk-open', title: 'Open the booklet (PDF)', 'aria-label': 'Open the booklet',
        onclick: () => booklets[0] && K.openExternal(booklets[0].url) },
        ...(icon ? [K.tIcon('book')] : [h('span', { 'aria-hidden': 'true' }, '📖'), ' Booklet']));
    const more = h('button', { type: 'button', class: 'chip bk-more', title: 'Booklet: PDF and AI summary',
        'aria-label': 'Booklet options', 'aria-haspopup': 'menu', onclick: () => B.menu(more, albumId, title, booklets) },
        h('span', { class: 'bk-caret', 'aria-hidden': 'true' }, '▾'));
    const el = h('span', { class: 'bk-split', hidden: true }, open, more);
    const paint = () => el.classList.toggle('busy', !!albumId && B.jobs.has(albumId));
    B.listeners.add(paint);
    const show = async (id, albumTitle = '') => {
        if (id === albumId) return;
        albumId = id || '';
        title = albumTitle;
        booklets = [];
        el.hidden = true;
        paint();
        if (!albumId) return;
        const mine = ++token;
        const d = await B.list(albumId);
        if (mine !== token) return;
        booklets = d.booklets;
        title = d.album?.title || title;
        el.hidden = !booklets.length;
        open.title = booklets[0] ? `Open ${booklets[0].name || 'the booklet'} (PDF)` : '';
    };
    return { el, show };
};

// ── the ▾ menu ───────────────────────────────────────────────────────────────
let menuEl = null, menuOutside = null;
const closeMenu = () => {
    if (!menuEl) return;
    document.removeEventListener('pointerdown', menuOutside, true);
    menuEl.remove();
    menuEl = null;
};
B.menu = async (anchor, albumId, title, booklets) => {
    if (menuEl) { closeMenu(); return; }
    const kept = await K.api('/qobuz/ai/booklet/' + encodeURIComponent(albumId), { timeout: 8000 });
    const has = kept.ok && kept.summary;
    const item = (icon, label, run) => h('button', { type: 'button', class: 'menu-item', role: 'menuitem',
        onclick: () => { closeMenu(); run(); } }, h('span', { class: 'mk' }, icon), label);
    menuEl = h('div', { class: 'menu-pop bk-menu', role: 'menu', 'aria-label': 'Booklet' },
        ...booklets.map((b, i) => item('📖', booklets.length > 1 ? `Open ${b.name || 'booklet ' + (i + 1)}` : 'Open the PDF',
            () => K.openExternal(b.url))),
        item('✦', B.jobs.has(albumId) ? 'AI summary (being made…)' : has ? 'AI summary (saved)' : 'AI summary of the booklet',
            () => B.summary(albumId, title)),
        has && !B.jobs.has(albumId) ? item('↻', 'Make the AI summary again', () => B.summary(albumId, title, true)) : null);
    menuEl.backClose = closeMenu;
    document.body.append(menuEl);
    const r = anchor.getBoundingClientRect(), w = menuEl.offsetWidth, hgt = menuEl.offsetHeight;
    menuEl.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`;
    menuEl.style.top = `${r.bottom + 4 + hgt <= innerHeight - 8 ? r.bottom + 4 : Math.max(8, r.top - 4 - hgt)}px`;
    menuOutside = e => { if (!menuEl.contains(e.target) && !anchor.contains(e.target)) closeMenu(); };
    document.addEventListener('pointerdown', menuOutside, true);
};

// ── the summary page ─────────────────────────────────────────────────────────
let page = null;
const closePage = () => {
    if (!page) return;
    page.remove();
    page = null;
    document.removeEventListener('keydown', onKey);
};
const onKey = e => { if (e.key === 'Escape') closePage(); };
const paras = text => String(text || '').split(/\n\s*\n/).map(p => p.trim()).filter(Boolean).map(p => h('p', {}, p));
const RECORDING = [['venue', 'Venue / studio'], ['dates', 'Recorded'], ['producer', 'Producer'],
    ['engineers', 'Engineers'], ['equipment', 'Equipment'], ['loudspeakers', 'Loudspeakers'],
    ['instruments', 'Instruments'], ['format', 'Format'], ['notes', 'Other notes']];

B.summary = async (albumId, title, refresh = false) => {
    closePage();
    const body = h('div', { class: 'ai-body bk-body' });
    page = h('div', { class: 'scrim ai bk-page' },
        h('div', { class: 'ai-top' },
            h('button', { type: 'button', class: 'btn ai-close', title: 'Close', 'aria-label': 'Close', onclick: closePage }, '‹'),
            h('span', { class: 'ai-label' }, 'Booklet summary')),
        body);
    const mine = page;
    page.backClose = closePage;
    document.getElementById('overlay-root').append(page);
    document.addEventListener('keydown', onKey);
    const working = (text, stop) => K.clear(body).append(h('div', { class: 'qz-working' }, h('div', { class: 'spinner' }),
        h('div', {}, text), h('div', { class: 'muted small' }, 'Read from the PDF only. A long booklet takes a minute or two; you can close this page, the summary goes on and is kept.'),
        stop ? h('button', { type: 'button', class: 'btn', onclick: () => { B.stop(albumId); } }, 'Stop') : null));
    let d = null;
    if (!refresh && !B.jobs.has(albumId)) {
        working('Looking for a saved summary…');
        d = await K.api('/qobuz/ai/booklet/' + encodeURIComponent(albumId), { timeout: 8000 });
        if (page !== mine) return;
        d = d.ok && d.summary ? d : null;
    }
    if (!d) {
        working('Reading the booklet and summarising it…', true);
        d = await B.make(albumId, refresh);
        if (page !== mine) {
            if (d.ok) K.toast('Booklet summary ready');
            return;
        }
    }
    K.clear(body);
    if (!d.ok) {
        body.append(h('div', { class: 'errbox' }, d.error || 'The summary failed.'),
            h('button', { type: 'button', class: 'btn', onclick: () => B.summary(albumId, title, true) }, 'Try again'));
        return;
    }
    body.append(...render(d.summary, title, albumId));
};

const render = (s, title, albumId) => {
    const out = [h('div', { class: 'ai-title' }, title || 'This album')];
    const rec = s.recording || {};
    if (rec.live) out.push(h('div', { class: 'bk-live', role: 'note' }, h('strong', {}, 'LIVE RECORDING'),
        rec.venue || rec.dates ? h('span', {}, ' · ' + [rec.venue, rec.dates].filter(Boolean).join(', ')) : null));
    const section = (letter, heading, ...content) => {
        const kids = content.flat().filter(Boolean);
        out.push(h('section', { class: 'bk-section' }, h('h2', {}, h('span', { class: 'bk-letter' }, letter), heading),
            kids.length ? kids : h('p', { class: 'muted' }, 'The booklet does not say.')));
    };
    section('a', s.composers.length > 1 ? 'The composers' : 'The composer',
        s.composers.map(c => [h('h3', {}, c.name), ...paras(c.text)]));
    section('b', 'Context of the work' + (s.works.length > 1 ? 's' : ''), paras(s.context));
    section('c', s.works.length > 1 ? 'The works' : 'The work', s.works.map(w => [h('h3', {}, w.title), ...paras(w.text)]));
    const facts = RECORDING.filter(([key]) => rec[key]).map(([key, label]) => K.kv(label, rec[key]));
    section('d', 'The recording',
        h('div', { class: 'bk-recorded ' + (rec.live ? 'live' : 'studio') }, rec.live ? 'Live recording' : 'Studio recording (no live performance stated)'),
        facts.length ? h('div', { class: 'ai-facts' }, facts) : null);
    const when = s.made_at ? new Date(s.made_at * 1000).toLocaleDateString() : '';
    out.push(h('p', { class: 'muted small bk-foot' },
        `From the booklet only${s.sources?.length ? ': ' + s.sources.map(b => b.title).join(', ') : ''}. ` +
        `Made${when ? ' on ' + when : ''} with ${s.provider === 'claude_account' ? 'Claude (account) ' : ''}${s.model || ''}.`),
        h('div', { class: 'bk-actions' },
            ...(s.sources || []).map(b => h('button', { type: 'button', class: 'btn', onclick: () => K.openExternal(b.url) }, '📖 Open the PDF')),
            h('button', { type: 'button', class: 'btn', onclick: () => B.summary(albumId, title, true) }, '↻ Make again')));
    return out;
};
})();
