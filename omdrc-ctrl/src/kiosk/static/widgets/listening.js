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
const STORAGE_KEY = 'omdrc.listening.v1';
const CACHE_KEY = 'omdrc.listening.last-result.v2';
G.materialKey = (albumKey, tracks) => JSON.stringify({ albumKey, provider: G.provider, model: G.model,
    tracks: tracks.map(t => ({ title: t.title, work: t.work, composer: t.composer })) });
G.cachedResult = (key, materialKey) => {
    try {
        const saved = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
        const same = saved?.key === key || (!saved?.key && saved?.materialKey === materialKey);
        return same && Array.isArray(saved.answer?.compositions) ? saved.answer : null;
    } catch (_) { return null; }
};
G.cacheResult = (key, answer, materialKey) => {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify({ key, materialKey, answer })); } catch (_) {}
};
G.remember = () => {
    if (!G.active || !G.track?.title) return;
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            active: true, albumKey: G.albumKey, track: G.track, tracks: G.tracks,
            guide: G.guide, selected: G.selected, selectedTrack: G.selectedTrack,
            userSelected: G.userSelected, collapsed: G.collapsed, busy: G.busy, queryKey: G.queryKey,
        }));
    } catch (_) { /* The guide remains usable if browser storage is unavailable. */ }
};
G.forget = () => { try { localStorage.removeItem(STORAGE_KEY); } catch (_) {} };
G.restore = () => {
    if (G.active) return;
    let saved;
    try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch (_) { return; }
    if (!saved?.active || !saved.track?.title || typeof saved.albumKey !== 'string') return;
    G.active = true;
    G.track = saved.track;
    G.tracks = Array.isArray(saved.tracks) ? saved.tracks : null;
    G.albumKey = saved.albumKey;
    G.guide = Array.isArray(saved.guide?.compositions) ? saved.guide : null;
    G.queryKey = typeof saved.queryKey === 'string' ? saved.queryKey : null;
    G.selected = Number.isInteger(saved.selected) && saved.selected >= -1
        && saved.selected < (G.guide?.compositions.length || 0) ? saved.selected : -1;
    G.selectedTrack = Number.isInteger(saved.selectedTrack) && saved.selectedTrack > 0
        && G.tracks?.[saved.selectedTrack - 1] ? saved.selectedTrack : null;
    G.userSelected = !!saved.userSelected;
    G.collapsed = saved.collapsed !== false;
    G.busy = false;
    G.error = G.guide ? '' : 'Research was interrupted. Tap Retry to continue.';
    G.makePanel();
    G.paint();
    G.poll = setInterval(async () => { if (G.active) G.observe(await K.fetchTrack()); }, 3000);
    K.fetchTrack().then(G.observe).catch(() => {});
};
G.onChange = fn => { G.listeners.add(fn); return () => G.listeners.delete(fn); };
G.changed = () => G.listeners.forEach(fn => fn());
G.icon = (className = '') => G.provider && G.provider !== 'ai'
    ? h('img', { src: '/k/static/img/' + (G.provider.startsWith('claude') ? 'claude' : 'openai') + '.svg',
        alt: '', class: 'listening-icon ' + className })
    : h('span', { class: 'listening-icon ' + className }, 'AI');
G.settings = async () => {
    const d = await K.api('/qobuz/ai/settings');
    if (d.ok) { G.provider = d.provider; G.model = d.model; }
    G.changed();
    return d;
};
G.trackNumber = () => {
    if (!G.tracks || !G.track) return 0;
    const byId = G.tracks.findIndex(t => G.track.track_id && String(t.id) === String(G.track.track_id));
    if (byId >= 0) return byId + 1;
    const title = norm(G.track.title);
    return title ? G.tracks.findIndex(t => norm(t.title) === title ||
        norm(t.title + ' ' + (t.version || '')) === title ||
        (t.work && norm(t.work + ' ' + t.title) === title)) + 1 : 0;
};
G.selectPlaying = () => {
    if (!G.guide) return;
    if (G.userSelected) { G.paint(); return; }
    const n = G.trackNumber();
    const section = G.guide.compositions.findIndex(s => s.tracks.includes(n));
    G.selected = section;
    G.remember();
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
        G.tabScroll = 0;
        const oldTabs = G.panel?.querySelector('.listening-tabs');
        if (oldTabs) oldTabs.scrollLeft = 0;
        G.albumKey = key;
        G.track = t;
        G.guide = null;
        G.tracks = null;
        G.queryKey = null;
        G.selected = -1;
        G.selectedTrack = null;
        G.userSelected = false;
        G.research();
    } else if (norm(G.track?.title) !== norm(t.title)
        || (t.track_id && String(t.track_id) !== String(G.track?.track_id || ''))) {
        // Now, the Qobuz player and this guide's own poll all report the track, not all with its id:
        // a report without one is the same track, not a change (which flickered the guide to Overview).
        G.track = t;
        G.selectPlaying();
        G.remember();
    }
};
G.open = async (t, source) => {
    if (G.busy) { G.close(); return; }
    if (G.active) { G.collapsed = false; G.remember(); G.paint(); return; }
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
    G.remember();
    G.animateToStrip(sourceRect);
};
// The research starts out of sight, in the strip: the flight from the tapped button
// to it must be seen, or the tap looks like it did nothing.  A ring bursts off the
// button, a glowing badge with a trail arcs over the page to the strip, and the strip
// flashes where it lands.
G.animateToStrip = source => {
    if (!source?.width || !G.panel?.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const destination = G.panel.querySelector('.listening-strip-open .listening-icon')?.getBoundingClientRect();
    if (!destination) return;
    const root = document.getElementById('overlay-root');
    const ring = (x, y, size, delay) => {
        const el = h('div', {});
        Object.assign(el.style, {
            position: 'fixed', zIndex: '68', pointerEvents: 'none', left: `${x - size / 2}px`, top: `${y - size / 2}px`,
            width: `${size}px`, height: `${size}px`, borderRadius: '50%', border: '3px solid var(--accent)',
            boxShadow: '0 0 24px var(--accent)', opacity: '0',
        });
        root.append(el);
        const burst = el.animate([
            { transform: 'scale(.25)', opacity: .95 },
            { transform: 'scale(1)', opacity: 0 },
        ], { duration: 850, delay, easing: 'cubic-bezier(.1,.7,.3,1)' });
        burst.onfinish = burst.oncancel = () => el.remove();
    };
    const sx = source.left + source.width / 2, sy = source.top + source.height / 2;
    const ex = destination.left + destination.width / 2, ey = destination.top + destination.height / 2;
    ring(sx, sy, 150, 0); ring(sx, sy, 150, 180);
    G.panel.animate([
        { transform: 'translateY(100%)', opacity: .35 },
        { transform: 'translateY(0)', opacity: 1 },
    ], { duration: 700, easing: 'cubic-bezier(.2,.8,.2,1)' });
    // A quadratic arc whose top is well above both ends; the badge pops up, then
    // shrinks into the strip's icon.
    const cx = (sx + ex) / 2, cy = Math.max(60, Math.min(sy, ey) - 160);
    const steps = 12, size = 64, duration = 1500;
    const frames = Array.from({ length: steps + 1 }, (_, i) => {
        const t = i / steps, u = 1 - t;
        const x = u * u * sx + 2 * u * t * cx + t * t * ex, y = u * u * sy + 2 * u * t * cy + t * t * ey;
        const scale = t < .15 ? .5 + t / .15 * .9 : 1.4 - (t - .15) / .85 * .9;
        return { transform: `translate(${x - sx}px, ${y - sy}px) scale(${scale})`, offset: t };
    });
    const flyer = (n) => {
        const el = h('div', {}, G.icon());
        Object.assign(el.style, {
            position: 'fixed', zIndex: '68', pointerEvents: 'none',
            left: `${sx - size / 2}px`, top: `${sy - size / 2}px`, width: `${size}px`, height: `${size}px`,
            display: 'grid', placeItems: 'center', borderRadius: '50%', color: 'var(--text)',
            background: n ? 'var(--accent)' : 'var(--bg)', border: '3px solid var(--accent)',
            boxShadow: '0 0 28px 6px var(--accent)', opacity: n ? String(.45 - n * .07) : '1',
        });
        if (n) el.firstChild.style.visibility = 'hidden';   // the trail: glowing dots only
        root.append(el);
        const flight = el.animate(frames, { duration, delay: n * 70, easing: 'cubic-bezier(.45,.05,.35,1)', fill: 'backwards' });
        flight.onfinish = flight.oncancel = () => el.remove();
        return flight;
    };
    for (let n = 5; n > 0; n--) flyer(n);
    flyer(0).onfinish = function () {
        this.effect.target.remove();
        if (!G.panel) return;
        ring(ex, ey, 170, 0);
        G.panel.animate([
            { boxShadow: '0 0 0 0 var(--accent)', filter: 'brightness(1)' },
            { boxShadow: '0 -4px 36px 6px var(--accent)', filter: 'brightness(1.6)', offset: .2 },
            { boxShadow: '0 0 0 0 var(--accent)', filter: 'brightness(1)', offset: .5 },
            { boxShadow: '0 -4px 28px 4px var(--accent)', filter: 'brightness(1.35)', offset: .7 },
            { boxShadow: '0 0 0 0 var(--accent)', filter: 'brightness(1)' },
        ], { duration: 1300, easing: 'ease-in-out' });
    };
};
G.close = () => {
    if (G.guide && !G.queryKey && G.tracks?.length) {
        G.cacheResult(null, G.guide, G.materialKey(G.albumKey, G.tracks));
    }
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
    G.tabsObserver?.disconnect();
    G.sizeObserver?.disconnect();
    G.panel?.remove();
    G.panel = null;
    document.documentElement.removeAttribute('data-ls-strip');
    G.albumKey = '';
    G.guide = null;
    G.queryKey = null;
    G.forget();
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
    G.remember();
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
    const query = {
        album: { title: album.title, artist: album.artist, composer: album.composer,
            label: album.label, year: album.year, genre: album.genre, release_type: album.release_type },
        tracks: tracks.map(t => ({ title: t.title, work: t.work, composer: t.composer })),
    };
    const cacheKey = JSON.stringify({ provider: G.provider, model: G.model, ...query });
    const materialKey = G.materialKey(G.albumKey, tracks);
    G.queryKey = cacheKey;
    const cached = G.cachedResult(cacheKey, materialKey);
    if (cached) {
        G.guide = cached;
        G.busy = false;
        G.controller = null;
        G.selectPlaying();
        G.remember();
        return;
    }
    G.job = newJob();
    try {
        const response = await fetch('/qobuz/ai/listening', { method: 'POST', signal: G.controller.signal,
            headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
            body: JSON.stringify({ job: G.job, ...query }) });
        const answer = await response.json();
        if (serial !== G.serial || !G.active) return;
        if (!answer.ok) throw new Error(answer.error || 'AI research failed');
        G.guide = answer;
        if (answer.model_notice) K.toast(answer.model_notice);
        G.cacheResult(cacheKey, answer, materialKey);
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
        if (serial === G.serial) { G.job = null; G.busy = false; G.remember(); G.paint(); }
    }
};
G.makePanel = () => {
    G.panel = h('div', { class: 'listening-panel' });
    document.getElementById('overlay-root').append(G.panel);
    // The Qobuz player strip rides on the minimized strip (--ls-h). The strip is hidden off Now and Qobuz, as at
    // launch while the restored guide waits for its page, so its height is taken whenever it shows or changes.
    G.sizeObserver?.disconnect();
    G.sizeObserver = new ResizeObserver(() => {
        if (G.panel?.classList.contains('collapsed') && G.panel.offsetHeight)
            document.documentElement.style.setProperty('--ls-h', `${G.panel.offsetHeight}px`);
    });
    G.sizeObserver.observe(G.panel);
    let touch = null;
    const nestedScroller = target => {
        for (let el = target; el && el !== G.panel; el = el.parentElement) {
            if (el.matches('.listening-tabs')) return true;
            const style = getComputedStyle(el);
            if (/auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1) return true;
            if (/auto|scroll/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1) return true;
        }
        return false;
    };
    G.wireStripDrawer();
    // A sideways swipe on the open sheet steps through the tabs, wrapping round at either end.
    let side = null;
    G.panel.addEventListener('touchstart', e => {
        const point = e.touches[0];
        side = point && e.touches.length === 1 && !G.collapsed && !e.target.closest('.listening-tabs-wrap')
            ? { x: point.clientX, y: point.clientY } : null;
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
        const end = e.changedTouches[0];
        if (side && end && !touch?.dragging) {
            const dx = end.clientX - side.x, dy = end.clientY - side.y;
            if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) G.step(dx < 0 ? 1 : -1);
        }
        side = null;
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
        }
        touch = null;
    }, { passive: true });
    G.panel.addEventListener('touchcancel', () => {
        G.panel.style.transform = '';
        G.swipePreview?.remove();
        G.swipePreview = null;
        touch = null;
    }, { passive: true });
};
// The minimized strip opens like the Qobuz player strip: the sheet takes the strip's place and rises with the finger
// (a tap rises on its own); released short of the threshold it sinks back into the strip.
G.wireStripDrawer = () => {
    const panel = G.panel;
    let drag = null, rose = false;
    const open = top => {
        G.collapsed = false;
        G.remember();
        G.paint();
        panel.style.transform = `translateY(${top}px)`;
        panel.classList.add('rising');
        K.riseVeil(panel, top, top);
    };
    const settle = (to, then) => {
        panel.style.transition = 'transform 200ms cubic-bezier(.2,.8,.2,1), background-color 200ms';
        panel.style.transform = `translateY(${to}px)`;
        if (to === 0) panel.style.backgroundColor = '';
        setTimeout(() => { if (G.panel !== panel) return; panel.style.transition = ''; panel.style.transform = ''; panel.style.backgroundColor = ''; panel.style.backdropFilter = ''; panel.classList.remove('rising'); then?.(); }, 220);
    };
    panel.addEventListener('pointerdown', e => {
        if (!G.collapsed || e.button !== 0 || e.target.closest('.listening-close')) return;
        drag = { id: e.pointerId, y: e.clientY, top: Math.round(panel.getBoundingClientRect().top), dist: 0 };
        rose = false;
    });
    panel.addEventListener('pointermove', e => {
        if (!drag || e.pointerId !== drag.id) return;
        drag.dist = Math.max(0, drag.y - e.clientY);
        if (!rose && drag.dist > 8) {
            rose = true;
            try { panel.setPointerCapture(e.pointerId); } catch {}
            open(drag.top);
        }
        if (rose) {
            const at = Math.max(0, drag.top - drag.dist);
            panel.style.transform = `translateY(${at}px)`;
            K.riseVeil(panel, at, drag.top);
        }
    });
    const end = e => {
        if (!drag || e.pointerId !== drag.id) return;
        const d = drag;
        drag = null;
        if (!rose) {
            if (e.type === 'pointerup' && G.collapsed && !e.target.closest('.listening-close')) {
                open(d.top);
                requestAnimationFrame(() => requestAnimationFrame(() => { if (G.panel === panel) settle(0); }));
            }
            return;
        }
        if (e.type !== 'pointercancel' && d.dist > Math.min(100, innerHeight * .2)) settle(0);
        else settle(d.top, () => { G.collapsed = true; G.remember(); G.paint(); });
    };
    panel.addEventListener('pointerup', end);
    panel.addEventListener('pointercancel', end);
};
// The tabs in their strip order: Overview, the compositions, then the tracks when they have tabs of their own.
G.tabStops = () => {
    if (!G.guide) return [];
    const stops = [-1, ...G.guide.compositions.keys()].map(selected => ({ selected, track: null }));
    if (G.tracks?.length && !G.singleTrackSections()) G.tracks.forEach((_, index) => stops.push({
        selected: G.guide.compositions.findIndex(s => s.tracks.includes(index + 1)), track: index + 1 }));
    return stops;
};
G.step = dir => {
    const stops = G.tabStops();
    if (stops.length < 2) return;
    const at = stops.findIndex(stop => G.selectedTrack ? stop.track === G.selectedTrack
        : !stop.track && stop.selected === G.selected);
    const next = stops[((at < 0 ? 0 : at) + dir + stops.length) % stops.length];
    G.userSelected = true;
    G.selected = next.selected;
    G.selectedTrack = next.track;
    G.remember();
    G.paint();
    G.panel.scrollTop = 0;
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) G.panel.querySelector('.listening-content')?.animate([
        { transform: `translateX(${dir * 2.5}rem)`, opacity: 0 }, { transform: 'translateX(0)', opacity: 1 },
    ], { duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)' });
};
G.minimize = () => { G.collapsed = true; G.remember(); G.paint(); };
G.singleTrackSections = () => {
    if (!G.tracks?.length || G.guide?.compositions?.length !== G.tracks.length) return false;
    const numbers = G.guide.compositions.map(section => section.tracks?.length === 1 ? section.tracks[0] : 0);
    return new Set(numbers).size === G.tracks.length
        && numbers.every(number => Number.isInteger(number) && number > 0 && number <= G.tracks.length);
};
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
    G.tabsObserver?.disconnect();
    G.tabScroll = G.panel.querySelector('.listening-tabs')?.scrollLeft ?? G.tabScroll ?? 0;
    if (G.singleTrackSections() && G.selectedTrack) {
        G.selected = G.guide.compositions.findIndex(section => section.tracks[0] === G.selectedTrack);
        G.selectedTrack = null;
        G.remember();
    }
    const active = G.guide?.compositions?.[G.selected];
    const label = G.label();
    G.panel.classList.toggle('collapsed', G.collapsed);
    document.documentElement.toggleAttribute('data-ls-strip', G.collapsed);
    K.clear(G.panel);
    const icon = G.collapsed ? G.stripIcon() : G.icon(G.busy ? 'busy' : '');
    if (G.collapsed) {
        G.panel.append(h('button', { type: 'button', class: 'listening-strip-open',
            onclick: () => { G.collapsed = false; G.remember(); G.paint(); } }, icon,
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
    const leftHint = h('span', { class: 'listening-tab-hint left', hidden: true, 'aria-hidden': 'true' }, '<');
    const rightHint = h('span', { class: 'listening-tab-hint right', hidden: true, 'aria-hidden': 'true' }, '>');
    const items = [{ title: 'Overview', text: G.guide.overview }, ...G.guide.compositions];
    const singleTrackSections = G.singleTrackSections();
    items.forEach((item, index) => tabs.append(h('button', { type: 'button', class: 'chip' + (!G.selectedTrack && G.selected === index - 1 ? ' on' : ''),
        role: 'tab', 'aria-selected': String(!G.selectedTrack && G.selected === index - 1),
        onclick: () => { G.userSelected = true; G.selectedTrack = null; G.selected = index - 1; G.remember(); G.paint(); } }, item.title)));
    if (G.tracks?.length && !singleTrackSections) {
        G.tracks.forEach((track, index) => tabs.append(h('button', {
            type: 'button', class: 'chip' + (G.selectedTrack === index + 1 ? ' on' : ''),
            role: 'tab', 'aria-selected': String(G.selectedTrack === index + 1),
            'aria-current': G.trackNumber() === index + 1 ? 'true' : 'false',
            onclick: () => {
                G.userSelected = true;
                G.selectedTrack = index + 1;
                G.selected = G.guide.compositions.findIndex(s => s.tracks.includes(index + 1));
                G.remember();
                G.paint();
            },
        }, `${index + 1}. ${track.title}`)));
    }
    G.panel.append(h('div', { class: 'listening-tabs-wrap' }, tabs, leftHint, rightHint));
    const updateHints = () => {
        if (!tabs.isConnected) return;
        G.tabScroll = tabs.scrollLeft;
        leftHint.hidden = tabs.scrollLeft < 2;
        rightHint.hidden = tabs.scrollLeft + tabs.clientWidth >= tabs.scrollWidth - 2;
    };
    tabs.addEventListener('scroll', updateHints, { passive: true });
    G.tabsObserver = new ResizeObserver(updateHints);
    G.tabsObserver.observe(tabs);
    requestAnimationFrame(() => {
        tabs.scrollLeft = G.tabScroll || 0;
        const on = tabs.querySelector('.chip.on');
        if (on) {
            const left = on.offsetLeft - tabs.offsetLeft, right = left + on.offsetWidth;
            if (left < tabs.scrollLeft) tabs.scrollLeft = left;
            else if (right > tabs.scrollLeft + tabs.clientWidth) tabs.scrollLeft = right - tabs.clientWidth;
        }
        updateHints();
    });
    const shown = G.selectedTrack ? {
        title: G.tracks[G.selectedTrack - 1]?.title || `Track ${G.selectedTrack}`,
        text: G.guide.track_notes?.find(n => n.track === G.selectedTrack)?.text ||
            active?.text || G.guide.overview,
    } : active || items[0];
    const content = h('div', { class: 'listening-content' },
        h('h2', {}, shown.title), ...String(shown.text || '').split(/\n\s*\n/).filter(Boolean).map(p => h('p', {}, p)));
    if (shown === items[0]) [...(G.guide.composers || []), ...(G.guide.performers || [])].forEach(c => content.append(
        h('h3', {}, c.name), ...String(c.text || '').split(/\n\s*\n/).filter(Boolean).map(p => h('p', {}, p))));
    if (G.guide.research_status) G.panel.append(h('p', { class: 'muted small', role: 'status' }, G.guide.research_status));
    G.panel.append(content);
    if (singleTrackSections && !G.selectedTrack && G.selected >= 0) {
        const detail = G.guide.track_notes?.find(note => note.track === active.tracks[0])?.text;
        if (detail && detail.trim() !== String(active?.text || '').trim()) {
            G.panel.append(h('div', { class: 'listening-track' },
                h('strong', {}, 'Track detail'), h('p', {}, detail)));
        }
    }
    const number = G.trackNumber();
    const note = G.guide.track_notes?.find(n => n.track === number);
    if (!singleTrackSections && !G.userSelected && number && G.tracks[number - 1]) {
        G.panel.append(h('div', { class: 'listening-track' },
            h('strong', {}, G.tracks[number - 1].title),
            note ? h('p', {}, note.text) : h('p', { class: 'muted' }, 'Track ' + number)));
    }
    if (G.guide.sources?.length) G.panel.append(h('div', { class: 'listening-sources' },
        h('strong', {}, 'Sources'), ...G.guide.sources.map(s => h('a', { href: s.url,
            target: '_blank', rel: 'noopener noreferrer' }, s.title || s.url))));
};
G.settings().then(G.restore).catch(() => G.restore());
})();
