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
            facts ? h('div', { class: 'muted small' }, facts) : null)));
    // the booklet first: the reason to come here, often
    if (a.booklets && a.booklets.length) out.push(h('div', { class: 'ai-booklets' }, a.booklets.map(b =>
        h('button', { type: 'button', class: 'btn primary ai-booklet', title: b.description || b.name, onclick: () => K.openExternal(b.url) },
            h('span', { class: 'ai-bk-icon' }, '📖'),
            h('span', { class: 'ai-bk-text' }, h('strong', {}, 'Booklet'), h('span', { class: 'small' }, b.name))))));
    if (a.catchline) out.push(h('p', { class: 'ai-catch' }, a.catchline));
    if (a.awards && a.awards.length) out.push(h('div', { class: 'ai-awards' }, a.awards.map(w =>
        h('span', { class: 'chip ai-award', title: w.date || '' }, '🏆 ', w.name))));

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
