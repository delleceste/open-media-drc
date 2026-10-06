/* A listening guide shared by Now and the Qobuz full player. */
(() => {
'use strict';
const { h } = K;
const G = K.listening = { active: false, busy: false, collapsed: false, serial: 0, listeners: new Set() };
const norm = value => String(value || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\bpt\.?\b/g, 'part').replace(/\bpart\s+(iv|iii|ii|i)\b/g,
        (_, roman) => 'part ' + ({ i: 1, ii: 2, iii: 3, iv: 4 })[roman])
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
    if (G.userSelected) { G.paint(); return; }
    const n = G.trackNumber();
    const section = G.guide.compositions.findIndex(s => s.tracks.includes(n));
    G.selected = section;
    G.paint();
};
G.observe = t => {
    if (!G.active || !t || !t.ok || !t.title) return;
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
        G.selectedTrack = null;
        G.userSelected = false;
        G.research();
    } else if (G.track?.title !== t.title || G.track?.track_id !== t.track_id) {
        G.track = t;
        G.selectPlaying();
    }
};
G.open = async (t, source) => {
    if (G.busy) { G.close(); return; }
    if (G.active) { G.collapsed = false; G.paint(); return; }
    const sourceRect = source?.getBoundingClientRect();
    const settings = await G.settings();
    if (!settings.ok || !settings.configured) {
        if (K.openAISettings) K.openAISettings(true);
        else K.toast(settings.error || 'Configure an AI provider in Qobuz settings', 'error');
        return;
    }
    G.active = true;
    G.collapsed = true;
    G.makePanel();
    G.observe(t);
    G.poll = setInterval(async () => { if (G.active) G.observe(await K.fetchTrack()); }, 3000);
    G.paint();
    G.animateToStrip(sourceRect);
};
G.animateToStrip = source => {
    if (!source?.width || !G.panel?.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const destination = G.panel.querySelector('.listening-strip-open .listening-icon')?.getBoundingClientRect();
    if (!destination) return;
    G.panel.animate([
        { transform: 'translateY(100%)', opacity: .35 },
        { transform: 'translateY(0)', opacity: 1 },
    ], { duration: 480, easing: 'cubic-bezier(.2,.8,.2,1)' });
    const flyer = h('div', {}, G.icon());
    Object.assign(flyer.style, {
        position: 'fixed', zIndex: '68', pointerEvents: 'none',
        left: `${source.left + source.width / 2 - 18}px`,
        top: `${source.top + source.height / 2 - 18}px`,
        width: '36px', height: '36px', display: 'grid', placeItems: 'center',
        borderRadius: '50%', color: 'var(--text)', background: 'var(--bg)',
        border: '1px solid var(--accent)', boxShadow: '0 0 18px var(--accent)',
    });
    document.getElementById('overlay-root').append(flyer);
    const dx = destination.left + destination.width / 2 - source.left - source.width / 2;
    const dy = destination.top + destination.height / 2 - source.top - source.height / 2;
    const flight = flyer.animate([
        { transform: 'translate(0, 0) scale(1)', opacity: 1 },
        { transform: `translate(${dx * .5}px, ${dy * .45 - 30}px) scale(1.12)`, opacity: 1, offset: .5 },
        { transform: `translate(${dx}px, ${dy}px) scale(.55)`, opacity: 0 },
    ], { duration: 540, easing: 'cubic-bezier(.2,.7,.25,1)' });
    flight.onfinish = flight.oncancel = () => flyer.remove();
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
    G.swipePreview?.remove();
    G.swipePreview = null;
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
        composer: '', label: '', year: '', genre: '', release_type: '' };
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
                composer: album.composer, label: album.label, year: album.year,
                genre: album.genre, release_type: album.release_type },
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
    let touch = null;
    const nestedScroller = target => {
        for (let el = target; el && el !== G.panel; el = el.parentElement) {
            if (el.matches('.listening-tabs, .listening-track-tabs')) return true;
            const style = getComputedStyle(el);
            if (/auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1) return true;
            if (/auto|scroll/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1) return true;
        }
        return false;
    };
    G.panel.addEventListener('touchstart', e => {
        const point = e.touches[0];
        touch = point && G.panel.scrollTop <= 1 && !nestedScroller(e.target)
            && !e.target.closest('button, a, input, select, textarea')
            ? { x: point.clientX, y: point.clientY, distance: 0, dragging: false } : null;
    }, { passive: true });
    G.panel.addEventListener('touchmove', e => {
        if (!touch || G.collapsed || e.touches.length !== 1) return;
        const point = e.touches[0], dy = point.clientY - touch.y, dx = point.clientX - touch.x;
        if (!touch.dragging && (dy < 8 || dy < Math.abs(dx) || G.panel.scrollTop > 1)) return;
        if (!touch.dragging) {
            touch.dragging = true;
            const preview = h('div', { class: 'listening-panel collapsed' },
                h('div', { class: 'listening-strip-open' }, G.stripIcon(), h('span', {}, G.label())),
                h('span', { class: 'listening-close' }, '×'));
            Object.assign(preview.style, { zIndex: '66', pointerEvents: 'none' });
            document.getElementById('overlay-root').append(preview);
            G.swipePreview = preview;
        }
        e.preventDefault();
        touch.distance = Math.max(0, dy);
        G.panel.style.transform = `translate3d(0, ${touch.distance}px, 0)`;
    }, { passive: false });
    G.panel.addEventListener('touchend', e => {
        if (!touch) return;
        const distance = e.changedTouches[0].clientY - touch.y;
        if (touch.dragging) {
            const panel = G.panel, preview = G.swipePreview, finish = distance > 75;
            const animation = panel.animate([
                { transform: `translate3d(0, ${touch.distance}px, 0)` },
                { transform: finish ? 'translate3d(0, 100%, 0)' : 'translate3d(0, 0, 0)' },
            ], { duration: finish ? 280 : 200, easing: 'cubic-bezier(.2,.8,.2,1)' });
            animation.onfinish = animation.oncancel = () => {
                panel.style.transform = '';
                if (finish && G.panel === panel) G.minimize();
                preview?.remove();
                if (G.swipePreview === preview) G.swipePreview = null;
            };
        } else if (distance < -45 && G.collapsed) { G.collapsed = false; G.paint(); }
        touch = null;
    }, { passive: true });
    G.panel.addEventListener('touchcancel', () => {
        G.panel.style.transform = '';
        G.swipePreview?.remove();
        G.swipePreview = null;
        touch = null;
    }, { passive: true });
};
G.minimize = () => { G.collapsed = true; G.paint(); };
G.label = () => {
    const active = G.guide?.compositions?.[G.selected];
    return G.selectedTrack ? G.tracks?.[G.selectedTrack - 1]?.title :
        active?.title || G.track?.title || G.track?.album || 'Listening guide';
};
G.stripIcon = () => G.busy && G.provider?.startsWith('claude')
    ? h('span', { class: 'listening-icon busy listening-claude-pulse', 'aria-hidden': 'true' })
    : G.icon(G.busy ? 'busy' : '');
G.paint = () => {
    G.changed();
    if (!G.panel) return;
    const active = G.guide?.compositions?.[G.selected];
    const label = G.label();
    G.panel.classList.toggle('collapsed', G.collapsed);
    K.clear(G.panel);
    const icon = G.collapsed ? G.stripIcon() : G.icon(G.busy ? 'busy' : '');
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
    items.forEach((item, index) => tabs.append(h('button', { type: 'button', class: 'chip' + (!G.selectedTrack && G.selected === index - 1 ? ' on' : ''),
        role: 'tab', 'aria-selected': String(!G.selectedTrack && G.selected === index - 1),
        onclick: () => { G.userSelected = true; G.selectedTrack = null; G.selected = index - 1; G.paint(); } }, item.title)));
    G.panel.append(tabs);
    if (G.guide.research_status) G.panel.append(h('p', { class: 'muted small', role: 'status' }, G.guide.research_status));
    const shown = G.selectedTrack ? {
        title: G.tracks[G.selectedTrack - 1]?.title || `Track ${G.selectedTrack}`,
        text: G.guide.track_notes?.find(n => n.track === G.selectedTrack)?.text ||
            active?.text || G.guide.overview,
    } : active || items[0];
    const content = h('div', { class: 'listening-content' },
        h('h2', {}, shown.title), ...String(shown.text || '').split(/\n\s*\n/).filter(Boolean).map(p => h('p', {}, p)));
    if (!G.selectedTrack) G.panel.append(content);
    if (G.tracks?.length) {
        const trackTabs = h('div', { class: 'listening-track-tabs', 'aria-label': 'Track details' });
        G.tracks.forEach((track, index) => trackTabs.append(h('button', {
            type: 'button', class: 'chip' + (G.selectedTrack === index + 1 ? ' on' : ''),
            'aria-current': G.trackNumber() === index + 1 ? 'true' : 'false',
            onclick: () => {
                G.userSelected = true;
                G.selectedTrack = index + 1;
                G.selected = G.guide.compositions.findIndex(s => s.tracks.includes(index + 1));
                G.paint();
            },
        }, `${index + 1}. ${track.title}`)));
        G.panel.append(h('div', { class: 'listening-track-heading' }, 'Tracks'), trackTabs);
    }
    if (G.selectedTrack) G.panel.append(content);
    const number = G.trackNumber();
    const note = G.guide.track_notes?.find(n => n.track === number);
    if (!G.userSelected && number && G.tracks[number - 1]) {
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
