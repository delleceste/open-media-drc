/* A listening guide shared by Now and the Qobuz full player. */
(() => {
'use strict';
const { h } = K;
const G = K.listening = { active: false, busy: false, collapsed: false, serial: 0, listeners: new Set() };
const norm = value => String(value || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\b(remaster(ed)?|live|version)\b/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
const newJob = () => {
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 15) | 64;
    b[8] = (b[8] & 63) | 128;
    const hex = [...b].map(n => n.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
const keyOf = t => t && (t.qobuz_album ? 'q:' + t.qobuz_album :
    'a:' + norm(t.album || t.title) + '|' + norm(t.artist) + '|' + norm(t.title));
G.onChange = fn => { G.listeners.add(fn); return () => G.listeners.delete(fn); };
G.changed = () => G.listeners.forEach(fn => fn());
G.icon = (className = '') => G.provider && G.provider !== 'ai'
    ? h('img', { src: '/k/static/img/' + (G.provider.startsWith('claude') ? 'claude' : 'openai') + '.svg',
        alt: '', class: 'listening-icon ' + className })
    : h('span', { class: 'listening-icon ' + className }, 'AI');
G.settings = async () => {
    const d = await K.api('/qobuz/ai/settings');
    if (d.ok) G.provider = d.provider;
    G.changed();
    return d;
};
G.trackNumber = () => {
    if (!G.tracks || !G.track) return 0;
    const byId = G.tracks.findIndex(t => G.track.track_id && String(t.id) === String(G.track.track_id));
    if (byId >= 0) return byId + 1;
    const title = norm(G.track.title);
    return title ? G.tracks.findIndex(t => norm(t.title) === title ||
        norm(t.title + ' ' + (t.version || '')) === title) + 1 : 0;
};
G.selectPlaying = () => {
    if (!G.guide) return;
    const n = G.trackNumber();
    const section = G.guide.compositions.findIndex(s => s.tracks.includes(n));
    G.selected = section;
    G.paint();
};
G.observe = t => {
    if (!G.active || !t || !t.ok || t.state === 'stop' || !t.title) return;
    if (!t.qobuz_album && G.track?.qobuz_album && norm(t.album) === norm(G.track.album)) {
        t = { ...t, qobuz_album: G.track.qobuz_album,
            track_id: t.track_id || (t.title === G.track.title ? G.track.track_id : '') };
    }
    const key = keyOf(t);
    if (!key) return;
    if (key !== G.albumKey) {
        G.albumKey = key;
        G.track = t;
        G.guide = null;
        G.selected = -1;
        G.research();
    } else if (G.track?.title !== t.title || G.track?.track_id !== t.track_id) {
        G.track = t;
        G.selectPlaying();
    }
};
G.open = async t => {
    if (G.busy) { G.close(); return; }
    if (G.active) { G.collapsed = false; G.paint(); return; }
    const settings = await G.settings();
    if (!settings.ok || !settings.configured) {
        if (K.openAISettings) K.openAISettings(true);
        else K.toast(settings.error || 'Configure an AI provider in Qobuz settings', 'error');
        return;
    }
    G.active = true;
    G.collapsed = false;
    G.makePanel();
    G.observe(t);
    G.poll = setInterval(async () => { if (G.active) G.observe(await K.fetchTrack()); }, 3000);
    G.paint();
};
G.close = () => {
    G.active = false;
    G.serial++;
    clearTimeout(G.retryTimer);
    G.cancelJob();
    G.controller?.abort();
    G.controller = null;
    G.busy = false;
    clearInterval(G.poll);
    G.panel?.remove();
    G.panel = null;
    G.albumKey = '';
    G.guide = null;
    G.changed();
};
G.cancelJob = () => {
    if (!G.job) return;
    const job = G.job;
    G.job = null;
    fetch('/qobuz/ai/listening/cancel', { method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
        body: JSON.stringify({ job }) }).catch(() => {});
};
G.research = async () => {
    const serial = ++G.serial;
    clearTimeout(G.retryTimer);
    G.cancelJob();
    G.controller?.abort();
    G.controller = new AbortController();
    G.busy = true;
    G.error = '';
    G.paint();
    const track = G.track;
    let album = { title: track.album || track.title, artist: track.artist || '',
        composer: '', label: '', year: '' };
    let tracks = [{ id: track.track_id || '', title: track.title, work: '', composer: '' }];
    if (track.qobuz_album) {
        try {
            const d = await K.api('/qobuz/album/' + encodeURIComponent(track.qobuz_album), { timeout: 20000 });
            if (d.ok && d.album?.track_list?.length) {
                album = d.album;
                tracks = d.album.track_list;
            }
        } catch (_) { /* Current-track metadata still permits a useful guide. */ }
    }
    if (serial !== G.serial || !G.active) return;
    G.tracks = tracks;
    G.selectPlaying();
    G.job = newJob();
    try {
        const response = await fetch('/qobuz/ai/listening', { method: 'POST', signal: G.controller.signal,
            headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
            body: JSON.stringify({ job: G.job, album: { title: album.title, artist: album.artist,
                composer: album.composer, label: album.label, year: album.year },
                tracks: tracks.map(t => ({ title: t.title, work: t.work, composer: t.composer })) }) });
        const answer = await response.json();
        if (serial !== G.serial || !G.active) return;
        if (!answer.ok) throw new Error(answer.error || 'AI research failed');
        G.guide = answer;
        G.selectPlaying();
    } catch (error) {
        if (serial !== G.serial || !G.active) return;
        if (/already running/.test(error.message)) {
            G.error = 'Waiting for the previous research to stop…';
            G.retryTimer = setTimeout(() => { if (serial === G.serial && G.active) G.research(); }, 2500);
            return;
        }
        G.error = error.name === 'AbortError' ? 'Research stopped' : error.message;
    } finally {
        if (serial === G.serial) { G.job = null; G.busy = false; G.paint(); }
    }
};
G.makePanel = () => {
    G.panel = h('div', { class: 'listening-panel' });
    document.getElementById('overlay-root').append(G.panel);
    let startY = 0;
    G.panel.addEventListener('touchstart', e => { startY = e.touches[0].clientY; }, { passive: true });
    G.panel.addEventListener('touchend', e => {
        const distance = e.changedTouches[0].clientY - startY;
        if (distance > 65 && !G.collapsed && G.panel.scrollTop < 20) G.minimize();
        if (distance < -45 && G.collapsed) { G.collapsed = false; G.paint(); }
    }, { passive: true });
};
G.minimize = () => { G.collapsed = true; G.paint(); };
G.paint = () => {
    G.changed();
    if (!G.panel) return;
    const active = G.guide?.compositions?.[G.selected];
    const label = active?.title || G.track?.title || G.track?.album || 'Listening guide';
    G.panel.classList.toggle('collapsed', G.collapsed);
    K.clear(G.panel);
    const icon = G.icon(G.busy ? 'busy' : '');
    if (G.collapsed) {
        G.panel.append(h('button', { type: 'button', class: 'listening-strip-open',
            onclick: () => { G.collapsed = false; G.paint(); } }, icon,
            h('span', {}, label)),
        h('button', { type: 'button', class: 'listening-close', title: 'Close listening guide',
            'aria-label': 'Close listening guide', onclick: G.close }, '×'));
        return;
    }
    G.panel.append(h('div', { class: 'listening-head' }, icon,
        h('strong', {}, 'Listening guide'),
        h('button', { type: 'button', class: 'chip', onclick: G.minimize }, '⌄ Minimize'),
        h('button', { type: 'button', class: 'chip', onclick: G.close }, G.busy ? 'Stop & close' : 'Close')),
        h('div', { class: 'listening-subtitle' }, label));
    if (G.busy) G.panel.append(h('p', { role: 'status' }, 'Researching this album…'));
    if (G.error) G.panel.append(h('p', { role: 'alert' }, G.error),
        h('button', { type: 'button', class: 'chip', onclick: G.research }, 'Retry'));
    if (!G.guide) return;
    const tabs = h('div', { class: 'listening-tabs', role: 'tablist' });
    const items = [{ title: 'Overview', text: G.guide.overview }, ...G.guide.compositions];
    items.forEach((item, index) => tabs.append(h('button', { type: 'button', class: 'chip' + (G.selected === index - 1 ? ' on' : ''),
        role: 'tab', 'aria-selected': String(G.selected === index - 1),
        onclick: () => { G.selected = index - 1; G.paint(); } }, item.title)));
    G.panel.append(tabs);
    const shown = active || items[0];
    G.panel.append(h('div', { class: 'listening-content' },
        h('h2', {}, shown.title), ...String(shown.text || '').split(/\n\s*\n/).filter(Boolean).map(p => h('p', {}, p))));
    const number = G.trackNumber();
    const note = G.guide.track_notes?.find(n => n.track === number);
    if (number && G.tracks[number - 1]) {
        G.panel.append(h('div', { class: 'listening-track' },
            h('strong', {}, G.tracks[number - 1].title),
            note ? h('p', {}, note.text) : h('p', { class: 'muted' }, 'Track ' + number)));
    }
    if (G.guide.sources?.length) G.panel.append(h('div', { class: 'listening-sources' },
        h('strong', {}, 'Sources'), ...G.guide.sources.map(s => h('a', { href: s.url,
            target: '_blank', rel: 'noopener noreferrer' }, s.title || s.url))));
};
G.settings();
})();
