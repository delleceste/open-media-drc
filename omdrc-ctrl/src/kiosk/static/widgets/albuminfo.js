/* Album details: everything Qobuz tells about a release (/qobuz/album/<id>), on a
 * page of its own over the kiosk, closed with ‹ at the top left (or Escape).  The
 * digital booklet, when there is one, comes first, as a big button: a PDF the
 * phone's own viewer opens (K.openExternal). */
(() => {
const h = K.h;

// A link outside the box: the app hands it to Android (a PDF viewer, the browser);
// a browser opens a new tab.
K.openExternal = url => {
    try { if (window.OmdrcApp && window.OmdrcApp.openExternal) { window.OmdrcApp.openExternal(url); return; } } catch {}
    window.open(url, '_blank', 'noopener');
};

// ── awards and my rating ─────────────────────────────────────────────────────
// What is known of an album's merit: the prizes Qobuz lists (Gramophone, Diapason,
// BBC Music Magazine, ...), the ones the user added (the magazines' choices Qobuz
// misses), and the user's own rating of the recording -- 1 to 3 green circles,
// never a bad mark: one circle is already a very good recording.  All kept on the
// box (/qobuz/awarded) and shown wherever the album is: in full on Now, the player
// and the details page, as one badge on a result row; a tap on either opens the
// popup with all of it, where awards are added or removed and the rating set.
//
// A search carries awards and rating for the albums it enriched; for the others
// they are asked for (/qobuz/awards, up to 30 albums a request), once per album and
// only when its line comes into view.
const awardCache = new Map();          // album id -> {awards, rating}
const awardWait = new Map();           // album id -> [resolve]
let awardTimer = null;
const flushAwards = async () => {
    awardTimer = null;
    const ids = [...awardWait.keys()].slice(0, 30);
    const waiting = ids.map(id => [id, awardWait.get(id)]);
    ids.forEach(id => awardWait.delete(id));
    if (awardWait.size) awardTimer = setTimeout(flushAwards, 0);
    const d = await K.api('/qobuz/awards?ids=' + ids.map(encodeURIComponent).join(','), { timeout: 30000 });
    for (const [id, fns] of waiting) {
        const list = d.ok && d.awards && d.awards[id];
        const info = { awards: list || [], rating: (d.ok && d.ratings && d.ratings[id]) || 0 };
        if (list) awardCache.set(id, info);
        fns.forEach(fn => fn(info));
    }
};
K.qobuzAwards = id => {
    if (!id) return Promise.resolve({ awards: [], rating: 0 });
    if (awardCache.has(id)) return Promise.resolve(awardCache.get(id));
    return new Promise(res => {
        if (!awardWait.has(id)) awardWait.set(id, []);
        awardWait.get(id).push(res);
        if (!awardTimer) awardTimer = setTimeout(flushAwards, 80);
    });
};

// The rating: n circles, each a deeper green.
K.ratingDots = (n, cls = '') => h('span', { class: 'rating ' + cls, title: `My rating: ${n} of 3 (the recording)` },
    ...[1, 2, 3].slice(0, n).map(i => h('i', { class: 'dot-r r' + i })));

// Full: every award and the rating.  Compact (a result row): one badge -- the only
// award's name, or how many there are -- and the rating.  Either opens the popup.
K.awardLine = (info, { compact = false, album = null } = {}) => {
    const awards = (info && info.awards) || [], rating = (info && info.rating) || 0;
    if (!awards.length && !rating) return null;
    const open = e => { e.stopPropagation(); if (album) K.awardsPopup(album); };
    if (compact) return h('div', { class: 'awards' }, h('button', {
        type: 'button', class: 'award award-chip' + (awards.length && awards.every(a => a.mine) ? ' mine' : ''),
        title: awards.map(a => a.name).join(' · ') || 'My rating', onclick: open },
        awards.length ? h('span', {}, '🏆 ', awards.length === 1 ? awards[0].name : `${awards.length} awards`) : null,
        rating ? K.ratingDots(rating) : null));
    return h('div', { class: 'awards' }, ...awards.map(a => h('button', {
        type: 'button', class: 'award' + (a.mine ? ' mine' : ''), onclick: open,
        title: [a.mine ? 'added by you' : a.publication, a.date].filter(Boolean).join(' · ') }, '🏆 ', a.name)),
        rating ? h('button', { type: 'button', class: 'award award-rating', onclick: open }, K.ratingDots(rating)) : null);
};
// An album's awards or rating changed: every box showing it follows.
K.setAwards = (id, info) => {
    awardCache.set(id, info);
    document.querySelectorAll('.awards-box').forEach(b => { if (b.dataset.awardId === id && b.__fill) b.__fill(info); });
};
const seen = 'IntersectionObserver' in window ? new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    seen.unobserve(e.target);
    e.target.__load();
}), { rootMargin: '200px' }) : null;
// A box that fills with the album's awards and rating: at once when they are known
// ({awards, rating}), else when it comes into view.  `album` ({id, title, artist})
// is what the popup names.
K.awardsBox = (album, known, opts = {}) => {
    const id = album && album.id || '';
    const box = h('div', { class: 'awards-box', dataset: { awardId: id } });
    const fill = box.__fill = info => { K.clear(box); const l = K.awardLine(info, { ...opts, album }); if (l) box.append(l); };
    if (known && Array.isArray(known.awards)) { if (id) awardCache.set(id, known); fill(known); return box; }
    box.__load = () => K.qobuzAwards(id).then(fill);
    if (seen && id) seen.observe(box); else if (id) box.__load();
    return box;
};

// ── the popup: awards and my rating ──────────────────────────────────────────
const PRESETS = ['Gramophone', 'Diapason', 'BBC Music Magazine', 'Stereophile', 'Hi-Fi News'];
K.awardsPopup = async album => {
    const id = album.id;
    let info = await K.qobuzAwards(id);
    const send = async body => {
        const d = await K.api('/qobuz/awarded', { json: { album_id: id, ...body } });
        if (!d.ok) { K.toast(d.error || 'could not save it', 'error'); return false; }
        info = { awards: d.awards, rating: d.rating };
        K.setAwards(id, info);
        paint();
        return true;
    };
    // my rating: tap a circle to set it, the same one again to take it away
    const ratingRow = h('div', { class: 'rate-row' });
    const listBox = h('div', { class: 'ai-mine' });
    const paint = () => {
        K.clear(ratingRow).append(...[1, 2, 3].map(i => h('button', {
            type: 'button', class: 'rate-dot r' + i + (i <= info.rating ? ' on' : ''),
            title: i === info.rating ? 'Take the rating away' : `${i} of 3`, 'aria-label': `${i} of 3`,
            onclick: () => send({ action: 'rate', rating: i === info.rating ? 0 : i }) })),
            h('span', { class: 'muted small' }, info.rating ? ['', 'very good', 'excellent', 'reference'][info.rating] : 'not rated'));
        K.clear(listBox).append(...(info.awards.length ? info.awards.map(a => h('div', { class: 'ai-mine-row' },
            h('span', { class: 'award' + (a.mine ? ' mine' : '') }, '🏆 ', a.name),
            a.mine ? h('button', { type: 'button', class: 'btn small', title: 'Remove this award',
                onclick: () => send({ action: 'unmark', name: a.name }) }, '✕')
                : h('span', { class: 'muted small' }, 'Qobuz'))) : [h('span', { class: 'muted small' }, 'No awards yet.')]));
    };
    // adding an award: a publication (or one's own text), and what and when
    let pub = PRESETS[0];
    const custom = h('input', { type: 'text', class: 'ai-input', placeholder: 'Publication or prize', hidden: true });
    const detail = h('input', { type: 'text', class: 'ai-input', placeholder: 'What, when (optional): Editor’s Choice, March 2026' });
    const chips = h('div', { class: 'qz-chips' });
    const paintChips = () => K.clear(chips).append(...[...PRESETS, 'Custom…'].map(p => h('button', {
        type: 'button', class: 'chip tog qz-chip' + (p === pub ? ' on' : ''),
        onclick: () => { pub = p; custom.hidden = p !== 'Custom…'; if (!custom.hidden) custom.focus(); paintChips(); } }, p)));
    const add = async () => {
        const publication = pub === 'Custom…' ? custom.value.trim() : pub;
        if (!publication) { K.toast('Name the publication or the prize', 'error'); return; }
        const what = detail.value.trim();
        if (await send({ action: 'mark', publication, name: what ? `${publication}: ${what}` : publication })) {
            detail.value = ''; custom.value = '';
        }
    };
    paint(); paintChips();
    const close = () => scrim.remove();
    const scrim = h('div', { class: 'scrim ai-markscrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet ai-marksheet' },
            h('h2', {}, 'Awards and my rating'),
            album.title ? h('p', {}, album.title + (album.artist ? ` — ${album.artist}` : '')) : null,
            h('div', { class: 'lbl' }, 'My rating of the recording'),
            ratingRow,
            h('div', { class: 'lbl' }, 'Awards'),
            listBox,
            h('div', { class: 'lbl' }, 'Add an award'),
            chips, custom, detail,
            h('div', { class: 'btn-row' },
                h('button', { type: 'button', class: 'btn primary', onclick: add }, 'Add'),
                h('button', { type: 'button', class: 'btn', onclick: close }, 'Close'))));
    document.getElementById('overlay-root').append(scrim);
};

const dur = s => {
    if (!Number.isFinite(s) || s <= 0) return '';
    const m = Math.round(s / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
};
const clock = s => Number.isFinite(s) && s > 0 ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}` : '';

let open = null;
const close = () => {
    if (!open) return;
    open.remove();
    open = null;
    document.removeEventListener('keydown', onKey);
};
const onKey = e => { if (e.key === 'Escape') close(); };

K.albumInfo = async albumId => {
    close();
    const body = h('div', { class: 'ai-body' }, h('div', { class: 'qz-working' }, h('div', { class: 'spinner' }), h('div', { class: 'muted' }, 'Asking Qobuz…')));
    open = h('div', { class: 'scrim ai' },
        h('div', { class: 'ai-top' },
            h('button', { type: 'button', class: 'btn ai-close', title: 'Close', 'aria-label': 'Close', onclick: close }, '‹'),
            h('span', { class: 'ai-label' }, 'Album details')),
        body);
    const mine = open;
    document.getElementById('overlay-root').append(open);
    document.addEventListener('keydown', onKey);
    const d = await K.api('/qobuz/album/' + encodeURIComponent(albumId), { timeout: 20000 });
    if (open !== mine) return;
    K.clear(body);
    if (!d.ok) { body.append(h('div', { class: 'errbox' }, d.error || 'Qobuz did not answer.')); return; }
    body.append(...page(d.album));
};

const page = a => {
    const out = [];
    const facts = [a.label, a.date || a.year, a.genre].filter(Boolean).join(' · ');
    out.push(h('div', { class: 'ai-head' },
        a.image_large || a.image ? h('img', { class: 'ai-cover', src: a.image_large || a.image, alt: '' }) : null,
        h('div', { class: 'ai-headtext' },
            h('div', { class: 'ai-title' }, a.title, a.version ? h('span', { class: 'muted' }, ` (${a.version})`) : null),
            a.artist ? h('div', { class: 'ai-artist' }, a.artist) : null,
            a.composer && a.composer !== a.artist && !/^various/i.test(a.composer) ? h('div', { class: 'muted' }, a.composer) : null,
            h('div', { class: 'ai-award-actions' },
                h('button', { type: 'button', class: 'chip ai-award-add', onclick: () => K.awardsPopup(a) }, 'Add award or rate'),
                K.awardsBox(a, { awards: a.awards || [], rating: a.rating || 0 }, { compact: true })),
            facts ? h('div', { class: 'muted small' }, facts) : null)));
    // the booklet first: the reason to come here, often
    if (a.booklets && a.booklets.length) out.push(h('div', { class: 'ai-booklets' }, a.booklets.map(b =>
        h('button', { type: 'button', class: 'btn primary ai-booklet', title: b.description || b.name, onclick: () => K.openExternal(b.url) },
            h('span', { class: 'ai-bk-icon' }, '📖'),
            h('span', { class: 'ai-bk-text' }, h('strong', {}, 'Booklet'), h('span', { class: 'small' }, b.name))))));
    if (a.catchline) out.push(h('p', { class: 'ai-catch' }, a.catchline));

    const rows = [
        ['Release', [a.release_type, a.date].filter(Boolean).join(', ')],
        ['Streaming since', a.released_stream && a.released_stream !== a.date ? a.released_stream : ''],
        ['Label', a.label],
        ['Genre', (a.genres && a.genres.length ? a.genres[a.genres.length - 1].replace(/→/g, ' › ') : a.genre) || ''],
        ['Quality', a.technical || (a.bits && a.rate ? `${a.bits} bit / ${a.rate} kHz` : '')],
        ['Tracks', [a.tracks ? `${a.tracks}` : '', a.media_count > 1 ? `${a.media_count} discs` : '', dur(a.duration)].filter(Boolean).join(' · ')],
        ['UPC', a.upc],
    ].filter(r => r[1]);
    out.push(h('div', { class: 'ai-facts' }, rows.map(([k, v]) => K.kv(k, v))));

    if (a.performers && a.performers.length) out.push(h('div', { class: 'lbl' }, 'Performers'),
        h('div', { class: 'ai-perf' }, a.performers.map(p => h('div', {}, h('strong', {}, p.name), p.roles.length ? h('span', { class: 'muted' }, ' · ' + p.roles.join(', ')) : null))));
    if (a.description) out.push(h('div', { class: 'lbl' }, 'About'), h('div', { class: 'ai-desc' }, a.description));
    if (a.recording) out.push(h('div', { class: 'lbl' }, 'Recording'), h('div', { class: 'ai-desc' }, a.recording));

    if (a.track_list && a.track_list.length) {
        const discs = new Set(a.track_list.map(t => t.disc)).size > 1;
        let work = null, disc = null;
        const list = [];
        for (const t of a.track_list) {
            if (discs && t.disc !== disc) { disc = t.disc; list.push(h('div', { class: 'lbl ai-disc' }, `Disc ${disc}`)); }
            if (t.work && t.work !== work) { work = t.work; list.push(h('div', { class: 'ai-work' }, t.work)); }
            else if (!t.work) work = null;
            list.push(h('div', { class: 'ai-track' + (t.streamable ? '' : ' off') },
                h('span', { class: 'muted ai-num' }, t.number ?? ''),
                h('span', { class: 'ai-ttitle' }, t.title, t.version ? h('span', { class: 'muted' }, ` (${t.version})`) : null),
                h('span', { class: 'muted ai-dur' }, clock(t.duration))));
        }
        out.push(h('div', { class: 'lbl' }, 'Tracks'), h('div', { class: 'ai-tracks' }, list));
    }
    if (a.copyright) out.push(h('p', { class: 'muted small ai-copy' }, a.copyright));
    out.push(h('button', { type: 'button', class: 'btn link ai-qobuz', onclick: () => K.openExternal(a.url) }, 'Open in Qobuz ›'));
    return out;
};
})();
