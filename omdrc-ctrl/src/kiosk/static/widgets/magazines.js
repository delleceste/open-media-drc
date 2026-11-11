/* Magazines: the recordings music magazines recommend (Gramophone, Diapason, BBC Music
 * Magazine, ...), read by the configured AI from issues uploaded here (qobuz_magazines.py).
 * A view of the Qobuz page, beside the Library's heart: a month at a time (this month,
 * else the latest read), the recordings several magazines agree on first.  Each one is a
 * result like any other (play, add, save, review later, details: pages/qobuz.js) with the
 * magazines' summaries of the performance and of the sound.  The circle beside it is the
 * reviews' rating of the recording quality, yellow to green (every one listed is a good
 * recording); a green Q marks an outstanding one.  Uploading starts the reading, shown
 * here as it goes; the month's document (digest.md) is kept beside the PDFs on the box. */
(() => {
'use strict';
const { h } = K;
const M = K.magazines = { d: null };
const pref = (k, d) => K.pref('magazines.' + k, d);
const setPref = (k, v) => K.setPref('magazines.' + k, v);
const abs = path => new URL(path, location.href).href;
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;

// ── the quality circle ───────────────────────────────────────────────────────
// Yellow (hue 52) at a sound rating of 5 or below, green (hue 130) at 10, every shade
// between; no rating: a hollow ring.  An outstanding recording: a green Q.
M.hue = q => 52 + Math.max(0, Math.min(1, (q - 5) / 5)) * 78;
M.dot = (m, cls = '') => {
    if (!m) return null;
    // always a colour: the sound's rating when a review describes it, else the performance's
    const rating = m.rating ?? m.quality ?? 7;
    const basis = m.rating_basis === 'performance' ? 'Performance' : 'Recording quality';
    const title = `${basis} ${rating}/10 in the reviews` + (m.q ? ' · Q: the recording is singled out' : '');
    const el = h('span', { class: 'mag-dot' + (m.q ? ' mag-q' : '') + (cls ? ' ' + cls : ''),
        title, 'aria-label': title, role: 'img' }, m.q ? 'Q' : '');
    el.style.setProperty('--mag-hue', M.hue(rating));
    return el;
};

// Which magazines recommend it, on its cover: G (Gramophone), D (Diapason), BBC, ...
M.marks = m => {
    const marks = m.marks || m.magazines.map(name => name.split(/\s+/).map(w => w[0]).join('').slice(0, 3).toUpperCase());
    return h('span', { class: 'mag-marks', title: 'Recommended by ' + m.magazines.join(', '), 'aria-label': 'Recommended by ' + m.magazines.join(', ') },
        marks.map(x => h('span', { class: 'mag-mark-letter' }, x)));
};

// What a result row shows of the magazines (pages/qobuz.js P.row), and the sheet a tile opens.
M.badges = m => h('div', { class: 'mag-badges' },
    M.dot(m),
    m.agreement > 1 ? h('span', { class: 'chip mag-agree', title: 'Recommended by ' + m.magazines.join(', ') },
        `${m.agreement} magazines agree`) : null,
    ...m.magazines.map(name => h('span', { class: 'chip dim mag-name' }, name)),
    m.awards.length ? h('span', { class: 'chip mag-award', title: m.awards.join('\n') }, '🏆 ' + m.awards.map(a => a.split(': ').slice(1).join(': ')).join(' · ')) : null);
M.block = (c, { open = false } = {}) => {
    const m = c.mag;
    const reviews = h('div', { class: 'mag-reviews', hidden: !open }, m.reviews.map(r => h('div', { class: 'mag-review' },
        h('div', { class: 'mag-review-head small' },
            h('strong', {}, r.magazine), h('span', { class: 'muted' }, ' · ' + r.issue),
            r.award ? h('span', { class: 'mag-review-award' }, ' · ' + r.award) : null,
            r.rating ? h('span', { class: 'muted' }, ' · ' + r.rating) : null,
            r.reviewer ? h('span', { class: 'muted' }, ' · ' + r.reviewer) : null,
            r.remote ? h('span', { class: 'muted', title: 'Read on another box: its PDF is there' }, ` · ${r.box}`) : null,
            r.page && !r.remote ? h('button', { type: 'button', class: 'btn link mag-page', title: 'Open the issue at the review',
                onclick: e => { e.stopPropagation(); M.openPdf(r.month, r.slug, r.page); } }, `p. ${r.page} ›`) : null),
        r.artistic ? h('p', {}, r.artistic) : null,
        r.sound ? h('p', { class: 'mag-sound' }, h('span', { class: 'mag-label' }, 'Sound '), r.sound) : null)));
    const toggle = h('button', { type: 'button', class: 'btn link mag-more',
        onclick: e => { e.stopPropagation(); reviews.hidden = !reviews.hidden; paintToggle(); } });
    const paintToggle = () => { toggle.textContent = reviews.hidden ? `${plural(m.reviews.length, 'review')} ›` : 'Hide the reviews'; };
    paintToggle();
    return h('div', { class: 'mag-block', onclick: e => e.stopPropagation() },
        M.badges(m),
        h('p', { class: 'mag-art' }, m.shared ? h('span', { class: 'mag-label' }, 'Shared verdict ') : null, m.artistic),
        m.sound ? h('p', { class: 'mag-sound' }, h('span', { class: 'mag-label' }, 'Sound '), m.sound) : null,
        !c.id ? h('p', { class: 'small muted' }, 'Not found on Qobuz: search for it.') : null,
        c.partial ? h('p', { class: 'small mag-partial' }, '◐ Only one track of this album is on Qobuz so far: the whole album is looked for again every day.') : null,
        toggle, reviews);
};
M.sheet = (c, actions) => {
    const close = () => scrim.remove();
    const run = fn => () => { close(); fn(); };
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet mag-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': c.title },
            h('div', { class: 'mag-sheet-top' },
                c.image ? h('img', { src: c.image_large || c.image, alt: '' }) : null,
                h('div', {}, h('h2', {}, c.title), h('div', { class: 'muted' }, [c.artist, c.label, c.year].filter(Boolean).join(' · '))),
                h('button', { type: 'button', class: 'btn', title: 'Close', 'aria-label': 'Close', onclick: close }, '✕')),
            M.block(c),
            h('div', { class: 'sheet-actions' },
                c.id ? [
                    h('button', { type: 'button', class: 'btn primary', disabled: c.streamable === false, onclick: run(actions.play) }, K.tIcon('play'), ' Play'),
                    h('button', { type: 'button', class: 'btn', disabled: c.streamable === false, onclick: run(actions.add) }, '+ Add'),
                    h('button', { type: 'button', class: 'btn', onclick: run(actions.details) }, 'Details ›'),
                ] : h('button', { type: 'button', class: 'btn primary', onclick: run(() => M.find(c)) }, K.tIcon('search'), ' Search Qobuz'))));
    document.getElementById('overlay-root').append(scrim);
};
M.find = c => K.qobuzFind && K.qobuzFind(c.mag.query || [c.artist, c.title].filter(Boolean).join(' '));
M.openPdf = (month, slug, page = 0) => K.openExternal(abs('/qobuz/magazines/pdf?' + new URLSearchParams({ month, slug }))
    + (page ? `#page=${page}` : ''));

// ── the view ─────────────────────────────────────────────────────────────────
M.mount = el => {
    M.el = el;
    M.file = h('input', { type: 'file', accept: 'application/pdf,.pdf', multiple: true, hidden: true,
        onchange: () => { const files = [...M.file.files]; M.file.value = ''; if (files.length) M.upload(files); } });
    M.head = h('div', { class: 'mag-head' });
    M.job = h('div', { class: 'mag-job', hidden: true });
    M.info = h('div', { class: 'mag-info' });
    M.list = h('div', { class: 'mag-list' });
    el.append(M.file, M.head, M.job, M.info, M.list);
};
M.show = () => { M.visible = true; M.refresh(); };
M.hide = () => { M.visible = false; clearTimeout(M.timer); M.closeMenu(); };
M.refresh = async () => {
    clearTimeout(M.timer);
    const seq = M.seq = (M.seq || 0) + 1;
    const month = pref('month', '');
    const d = await K.api('/qobuz/magazines' + (month ? '?' + new URLSearchParams({ month }) : ''), { timeout: 30000 });
    if (seq !== M.seq || !M.visible) return;
    if (!d.ok) { K.clear(M.info).append(h('div', { class: 'errbox' }, d.error || 'Could not read the magazines')); return; }
    const wasRunning = M.d && M.d.job && M.d.job.running;
    M.d = d;
    M.paintHead(); M.paintJob();
    const sig = JSON.stringify([d.month, d.entries, d.issues, d.refreshing]);
    if (sig !== M.sig || !M.list.firstChild) { M.sig = sig; M.paintList(); }
    if (d.job.running || d.refreshing) M.timer = setTimeout(M.refresh, d.job.running ? 2500 : 4000);
    else if (wasRunning && d.job.error) K.toast(d.job.error, 'error');
};

M.paintHead = () => {
    const d = M.d, list = d.months, i = list.findIndex(m => m.key === d.month);
    const go = key => { setPref('month', key); M.sig = null; M.refresh(); };
    const pick = h('select', { class: 'mag-month', 'aria-label': 'Month', disabled: !list.length,
        onchange: e => go(e.target.value) },
        list.length ? list.map(m => h('option', { value: m.key, selected: m.key === d.month }, M.short(m.key)))
            : h('option', {}, 'No issues yet'));
    K.clear(M.head).append(...[
        h('button', { type: 'button', class: 'btn mag-step', title: 'Earlier month', 'aria-label': 'Earlier month',
            disabled: i < 0 || i >= list.length - 1, onclick: () => go(list[i + 1].key) }, '‹'),
        pick,
        h('button', { type: 'button', class: 'btn mag-step', title: 'Later month', 'aria-label': 'Later month',
            disabled: i <= 0, onclick: () => go(list[i - 1].key) }, '›'),
        h('span', { class: 'spacer' }),
        d.month ? h('button', { type: 'button', class: 'btn mag-icon', title: 'The month’s document, cross-referenced',
            'aria-label': 'The month’s document', onclick: () => M.document() }, K.tIcon('document')) : null,
        h('button', { type: 'button', class: 'btn primary mag-icon', title: 'Upload magazine issues (PDF)',
            'aria-label': 'Upload magazine issues', disabled: !!M.uploading, onclick: () => M.file.click() }, K.tIcon('upload'))].filter(Boolean));
};
// "Nov 2026": the month picker, the arrows and the two buttons share one line on a phone
M.short = key => `${new Date(+key.slice(0, 4), +key.slice(5) - 1, 1).toLocaleString('en', { month: 'short' })} ${key.slice(0, 4)}`;

M.paintJob = () => {
    const j = M.d.job, items = j.items || [];
    const waiting = items.filter(x => x.state === 'waiting' || x.state === 'error');
    const shown = items.filter(x => j.running || x.state !== 'done' || Date.now() / 1000 - j.finished < 600);
    M.job.hidden = !shown.length && !M.uploading;
    if (M.job.hidden) return;
    const state = x => ({ queued: 'waiting its turn', waiting: 'not read yet', reading: x.detail || 'reading',
        done: x.detail, error: x.detail || 'failed' })[x.state] || x.state;
    K.clear(M.job).append(...[
        M.uploading ? h('div', { class: 'mag-upload' }, h('div', { class: 'small' }, M.uploading.text),
            h('div', { class: 'mag-bar' }, h('span', { style: `width:${M.uploading.pct}%` }))) : null,
        ...shown.map(x => h('div', { class: 'mag-item mag-' + x.state },
            x.state === 'reading' ? h('span', { class: 'spinner small' }) : h('span', { class: 'mag-mark' },
                { done: '✓', error: '!', queued: '…', waiting: '•' }[x.state] || '•'),
            h('div', { class: 'mag-item-text' }, h('div', { class: 'mag-item-name' }, x.name), h('div', { class: 'small muted' }, state(x))),
            x.state === 'done' && x.month ? h('button', { type: 'button', class: 'btn link', onclick: () => { setPref('month', x.month); M.sig = null; M.refresh(); } }, 'Show ›') : null,
            !j.running && (x.state === 'waiting' || x.state === 'error') ? h('button', { type: 'button', class: 'btn', title: 'Remove the upload',
                'aria-label': 'Remove the upload', onclick: () => M.removeUpload(x) }, '✕') : null)),
        h('div', { class: 'mag-job-actions' },
            j.running ? h('span', { class: 'small muted' }, j.detail || 'Reading…') : null,
            h('span', { class: 'spacer' }),
            j.running ? h('button', { type: 'button', class: 'btn', onclick: () => M.jobAction('stop') }, 'Stop')
                : waiting.length ? h('button', { type: 'button', class: 'btn primary', onclick: () => M.jobAction('start') },
                    `Read ${plural(waiting.length, 'issue')}`) : null)].filter(Boolean));
};

M.paintList = () => {
    const d = M.d;
    K.clear(M.info); K.clear(M.list);
    if (!d.month) {
        M.info.append(h('p', { class: 'muted' }, 'Upload issues of music magazines (Gramophone, Diapason, BBC Music Magazine, …) as PDF. '
            + 'The AI recognises each issue’s month, reads its editors’ choices and best reviews, and lists the recordings here in English, '
            + 'the ones several magazines agree on first.'));
        return;
    }
    const n = d.entries.length;
    M.info.append(...[
        h('div', { class: 'mag-issues' }, d.issues.map(i => h('button', { type: 'button', class: 'chip mag-issue',
            title: `${i.issue || ''} — ${i.picks} recommendations (tap: open or remove)`,
            onclick: e => M.issueMenu(i, e.currentTarget) }, `${i.magazine} · ${i.picks}` + (i.remote ? ` · ${i.box}` : '')))),
        h('div', { class: 'qz-summary small muted' }, `${d.label}: ${plural(n, 'recommendation')}`
            + (d.agreed ? `, ${d.agreed} agreed by several magazines` : '')),
        d.refreshing ? h('div', { class: 'small muted mag-refreshing' }, h('span', { class: 'spinner small' }),
            ` Looking on Qobuz again for ${plural(d.refreshing, 'album')} missing or incomplete a day ago…`) : null,
        h('div', { class: 'mag-legend small muted' },
            M.dot({ rating: 5 }), M.dot({ rating: 7.5 }), M.dot({ rating: 10 }),
            ' rating (sound, else performance) ·', M.dot({ rating: 9, q: true }), ' great sound')].filter(Boolean));
    const agreed = d.entries.filter(c => c.mag.agreement > 1), single = d.entries.filter(c => c.mag.agreement === 1);
    if (agreed.length) M.list.append(h('h3', { class: 'mag-section' }, 'Agreed by several magazines'), K.qobuzList(agreed, 'magazines'));
    if (single.length) M.list.append(h('h3', { class: 'mag-section' }, agreed.length ? 'Recommended by one magazine' : 'Recommended'),
        K.qobuzList(single, 'magazines'));
    if (!n) M.list.append(h('p', { class: 'muted' }, 'No recommendation was found in this month’s issues.'));
};

// ── actions ──────────────────────────────────────────────────────────────────
M.post = (url, json) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
    body: JSON.stringify(json) }).then(r => r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` })))
    .catch(() => ({ ok: false, error: 'network error' }));
M.jobAction = async action => {
    const d = await M.post('/qobuz/magazines/job', { action });
    if (!d.ok) K.toast(d.error || 'Could not ' + action, 'error');
    M.refresh();
};
M.removeUpload = async x => {
    const d = await M.post('/qobuz/magazines/remove', { upload: x.file });
    if (!d.ok) K.toast(d.error || 'Could not remove it', 'error');
    M.refresh();
};
M.upload = files => {
    const total = files.reduce((n, f) => n + f.size, 0);
    const mb = n => (n / 1048576).toFixed(0);
    M.uploading = { pct: 0, text: `Uploading ${plural(files.length, 'issue')} (${mb(total)} MB)…` };
    M.paintHead(); M.paintJob();
    const data = new FormData();
    files.forEach(f => data.append('files', f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/qobuz/magazines/upload');
    xhr.setRequestHeader('X-Qobuz-AI', '1');
    xhr.upload.onprogress = e => {
        if (!e.lengthComputable || !M.uploading) return;
        M.uploading.pct = Math.round(100 * e.loaded / e.total);
        M.uploading.text = e.loaded >= e.total ? 'Uploaded; saving on the box…'
            : `Uploading ${plural(files.length, 'issue')}: ${mb(e.loaded)} of ${mb(e.total)} MB`;
        if (M.visible) M.paintJob();
    };
    const done = d => {
        M.uploading = null;
        if (!d.ok || (d.errors && d.errors.length)) K.toast(d.error || 'Upload failed', 'error');
        else if (d.started) K.toast(`Reading ${plural(d.saved.length, 'issue')}: this takes a few minutes each`);
        M.refresh();
        if (M.d) { M.paintHead(); }
    };
    xhr.onload = () => { let d; try { d = JSON.parse(xhr.responseText); } catch { d = { ok: false, error: `HTTP ${xhr.status}` }; } done(d); };
    xhr.onerror = () => done({ ok: false, error: 'Network error during the upload' });
    xhr.send(data);
};
M.closeMenu = () => {
    if (!M.menuEl) return;
    document.removeEventListener('pointerdown', M.menuOutside, true);
    M.menuEl.remove(); M.menuEl = null;
};
M.issueMenu = (issue, anchor) => {
    M.closeMenu();
    const item = (label, run) => h('button', { type: 'button', class: 'menu-item', role: 'menuitem',
        onclick: () => { M.closeMenu(); run(); } }, label);
    const menu = M.menuEl = h('div', { class: 'menu-pop qz-tilemenu', role: 'menu', 'aria-label': issue.magazine },
        h('div', { class: 'qz-tilemenu-head' }, `${issue.magazine} — ${issue.issue || M.d.label}`),
        // read on another box (shared through the DR log's repository): its PDF stays there
        issue.remote ? h('div', { class: 'small muted mag-remote-note' }, `Read on ${issue.box}, shared through GitHub; the PDF is on ${issue.box}.`) : null,
        issue.remote ? null : item('Open the PDF', () => M.openPdf(M.d.month, issue.slug)),
        issue.remote ? null : item('Remove this issue', async () => {
            if (!await K.confirm({ title: 'Remove this issue?', message: `${issue.magazine}, ${issue.issue || M.d.label}: its PDF and its recommendations are deleted from the box.`, ok: 'Remove', danger: true })) return;
            const d = await M.post('/qobuz/magazines/remove', { month: M.d.month, slug: issue.slug });
            if (!d.ok) K.toast(d.error || 'Could not remove it', 'error');
            M.sig = null; M.refresh();
        }));
    document.body.append(menu);
    const r = anchor.getBoundingClientRect(), w = menu.offsetWidth, hgt = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
    menu.style.top = `${r.bottom + hgt + 8 > innerHeight ? Math.max(8, r.top - hgt - 4) : r.bottom + 4}px`;
    M.menuOutside = e => { if (!menu.contains(e.target) && e.target !== anchor) M.closeMenu(); };
    document.addEventListener('pointerdown', M.menuOutside, true);
};

// The month's document: a small Markdown reader (headings, lists, bold, italics, links).
M.markdown = text => {
    const inline = s => {
        const out = [];
        const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\((https?:[^)\s]+)\)/g;
        let at = 0, m;
        while ((m = re.exec(s))) {
            if (m.index > at) out.push(s.slice(at, m.index));
            if (m[1]) out.push(h('strong', {}, m[1]));
            else if (m[2]) out.push(h('em', {}, ...inline(m[2])));
            else out.push(h('a', { href: m[4], onclick: e => { e.preventDefault(); K.openExternal(m[4]); } }, m[3]));
            at = re.lastIndex;
        }
        if (at < s.length) out.push(s.slice(at));
        return out;
    };
    const nodes = [];
    let ul = null;
    for (const line of text.split('\n')) {
        const heading = /^(#{1,3})\s+(.*)$/.exec(line);
        if (line.startsWith('- ')) { if (!ul) nodes.push(ul = h('ul', {})); ul.append(h('li', {}, ...inline(line.slice(2)))); continue; }
        ul = null;
        if (heading) nodes.push(h('h' + (heading[1].length + 1), {}, ...inline(heading[2])));
        else if (line.trim()) nodes.push(h('p', {}, ...inline(line)));
    }
    return nodes;
};
M.document = async () => {
    const d = await K.api('/qobuz/magazines/document?' + new URLSearchParams({ month: M.d.month }), { timeout: 30000 });
    if (!d.ok) { K.toast(d.error || 'Could not make the document', 'error'); return; }
    const close = () => scrim.remove();
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet mag-doc', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Magazine recommendations' },
            h('div', { class: 'mag-doc-body' }, ...M.markdown(d.text)),
            h('div', { class: 'small muted' }, 'Saved on the box: ' + d.path),
            h('div', { class: 'sheet-actions' }, h('span', { class: 'spacer' }),
                h('button', { type: 'button', class: 'btn', onclick: () => {
                    const a = h('a', { href: URL.createObjectURL(new Blob([d.text], { type: 'text/markdown' })),
                        download: `magazines-${M.d.month}.md` });
                    document.body.append(a); a.click(); a.remove();
                } }, 'Download'),
                h('button', { type: 'button', class: 'btn primary', onclick: close }, 'Close'))));
    document.getElementById('overlay-root').append(scrim);
};
})();
