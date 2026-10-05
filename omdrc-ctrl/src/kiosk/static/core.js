/* Kiosk core: DOM helper, API client, prefs, overlays, pollers, shared SSE
 * streams and the page registry.  No dependency on the desktop UI's scripts. */
(() => {
'use strict';
const K = window.K = { pages: [], state: { spectrum: { enabled: true }, commands: [], features: {} } };

// ── DOM ──────────────────────────────────────────────────────────────────────
// h('div', {class:'x', onclick: fn, style:{color:'red'}, dataset:{a:1}}, child, ...)
// Text always goes in as text nodes: log lines and track names are never HTML.
K.h = function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k === 'dataset') Object.assign(el.dataset, v);
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (v === true) el.setAttribute(k, '');
        else el.setAttribute(k, v);
    }
    const add = kid => {
        if (kid === null || kid === undefined || kid === false) return;
        if (Array.isArray(kid)) kid.forEach(add);
        else el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    };
    kids.forEach(add);
    return el;
};
K.$ = (sel, root = document) => root.querySelector(sel);
K.clear = el => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
K.clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
K.portrait = () => matchMedia('(orientation: portrait)').matches;

// ── preferences (per browser; the kiosk is one device) ───────────────────────
K.pref = (key, dflt) => {
    try {
        const raw = localStorage.getItem('omdrc-kiosk.' + key);
        return raw === null ? dflt : JSON.parse(raw);
    } catch { return dflt; }
};
K.setPref = (key, value) => {
    try { localStorage.setItem('omdrc-kiosk.' + key, JSON.stringify(value)); } catch {}
};

// ── theme ────────────────────────────────────────────────────────────────────
// 'auto' (the default: the device's own light/dark setting, followed live), 'dark' or
// 'light', per screen (Config -> Theme).  The shell's first script already set it
// before the first paint; kiosk.css has the palettes.  What is drawn on canvases
// reads its colours through K.css(), so it follows too.
const LIGHT = matchMedia('(prefers-color-scheme: light)');
let cssCache = {};
K.themeSubs = new Set();
K.onTheme = fn => K.themeSubs.add(fn);
K.themeChoice = () => { const t = K.pref('theme', 'auto'); return t === 'dark' || t === 'light' ? t : 'auto'; };
K.theme = () => document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
K.css = name => cssCache[name] ?? (cssCache[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim());
K.applyTheme = () => {
    const choice = K.themeChoice();
    const theme = choice === 'auto' ? (LIGHT.matches ? 'light' : 'dark') : choice;
    const changed = document.documentElement.dataset.theme !== theme;
    document.documentElement.dataset.theme = theme;
    cssCache = {};
    const meta = document.querySelector('meta[name=theme-color]');
    if (meta) meta.content = K.css('--bg') || (theme === 'light' ? '#f3f5f8' : '#0d1117');
    if (changed) K.themeSubs.forEach(fn => { try { fn(theme); } catch (e) { console.warn(e); } });
};
K.setTheme = choice => { K.setPref('theme', choice); K.applyTheme(); };
LIGHT.addEventListener('change', () => { if (K.themeChoice() === 'auto') K.applyTheme(); });
K.applyTheme();

// ── formatting ───────────────────────────────────────────────────────────────
K.fmtDb = db => Number.isFinite(db) ? db.toFixed(1) : '−∞';
K.fmtClock = sec => {
    if (!Number.isFinite(sec) || sec < 0) return '–:––';
    sec = Math.floor(sec);
    const m = Math.floor(sec / 60), s = sec % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
};
K.fmtBytes = n => {
    if (!Number.isFinite(n)) return '–';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};
// dB → 0..100 along the linear-amplitude scale the desktop meters use.
K.voltagePct = (db, floor, ceiling = 0) => {
    const c = Math.max(floor, Math.min(ceiling, Number(db)));
    const a = v => Math.pow(10, v / 20);
    return (a(c) - a(floor)) / (a(ceiling) - a(floor)) * 100;
};

// ── API ──────────────────────────────────────────────────────────────────────
// Resolves to the parsed JSON, or {ok:false, error} - it never rejects, so
// callers can paint the failure instead of wrapping every call in try/catch.
K.api = async (url, opts = {}) => {
    const { json, method, timeout = 15000 } = opts;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    try {
        const init = { signal: ctl.signal, cache: 'no-store', method: method || (json !== undefined ? 'POST' : 'GET') };
        if (json !== undefined) {
            init.headers = { 'Content-Type': 'application/json' };
            init.body = JSON.stringify(json);
        }
        const r = await fetch(url, init);
        try { return await r.json(); }
        catch { return { ok: false, error: `HTTP ${r.status}` }; }
    } catch (e) {
        return { ok: false, error: e.name === 'AbortError' ? 'timed out' : 'network error' };
    } finally { clearTimeout(timer); }
};

// ── toast ────────────────────────────────────────────────────────────────────
let toastTimer = null;
K.toast = (msg, type = 'ok', raised = false) => {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = 'show ' + type + (raised ? ' raised' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = ''; }, type === 'error' ? 6000 : 2600);
};

// ── overlays ─────────────────────────────────────────────────────────────────
const root = () => document.getElementById('overlay-root');

// Resolves true (ok) or false (cancel / tap outside).  With `timeoutS` the cancel
// button counts down and is chosen when it reaches zero.  The returned promise also
// has .close(value) so the caller can dismiss it (e.g. the situation went away).
K.confirm = ({ title, message, ok = 'Confirm', cancel = 'Cancel', danger = false, timeoutS = 0 }) => {
    let done;
    const p = new Promise(resolve => {
        let timer = null;
        done = v => { if (!scrim.isConnected) return; clearInterval(timer); scrim.remove(); resolve(v); };
        const cancelBtn = K.h('button', { class: 'btn', onclick: () => done(false) }, cancel);
        const scrim = K.h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) done(false); } },
            K.h('div', { class: 'sheet' },
                K.h('h2', {}, title || 'Are you sure?'),
                message ? K.h('p', {}, message) : null,
                K.h('div', { class: 'sheet-actions' },
                    cancelBtn,
                    K.h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), onclick: () => done(true) }, ok))));
        root().append(scrim);
        if (timeoutS > 0) {
            let left = timeoutS;
            const paint = () => { cancelBtn.textContent = `${cancel} (${left})`; };
            paint();
            timer = setInterval(() => { left -= 1; if (left <= 0) done(false); else paint(); }, 1000);
        }
    });
    p.close = v => done(v);
    return p;
};

// A modal spinner for the long, chain-rebuilding operations (a filter switch
// takes tens of seconds).  Touches are swallowed so nothing is pressed twice.
K.busy = text => {
    const label = K.h('div', {}, text);
    const scrim = K.h('div', { class: 'scrim busy' },
        K.h('div', { class: 'sheet centre' }, K.h('div', { class: 'spinner' }), label));
    root().append(scrim);
    return { text: t => { label.textContent = t; }, done: () => scrim.remove() };
};

// The desktop pages (configuration, filter response, ...) open inside the
// kiosk, so there is always a Close button and no browser chrome is needed.
K.frame = (url, title) => {
    const close = () => scrim.remove();
    const scrim = K.h('div', { class: 'scrim frame' },
        K.h('div', { class: 'frame-bar' },
            K.h('strong', {}, title || url),
            K.h('span', { class: 'spacer' }),
            K.h('button', { class: 'btn', onclick: close }, '✕ Close')),
        K.h('iframe', { src: url, title: title || url }));
    root().append(scrim);
};

// ── periodic work, owned by a page ───────────────────────────────────────────
// Never overlaps itself, never runs in a hidden tab, and only runs between the
// page's start() and stop() - a page that is off screen computes nothing.
K.Poller = class {
    constructor(fn, ms) { this.fn = fn; this.ms = ms; this.timer = null; this.running = false; this.busy = false; }
    async tick() {
        if (this.busy || document.hidden) return;
        this.busy = true;
        try { await this.fn(); } catch (e) { console.warn('poll', e); }
        this.busy = false;
    }
    start(immediately = true) {
        if (this.running) return;
        this.running = true;
        if (immediately) this.tick();
        this.timer = setInterval(() => this.tick(), this.ms);
    }
    stop() { this.running = false; clearInterval(this.timer); this.timer = null; }
    now() { return this.tick(); }
};

// ── shared analyzer streams ──────────────────────────────────────────────────
// One EventSource per mode, however many widgets listen; closed when the last
// listener leaves and while the tab is hidden.  The server disables MPD's idle
// analyzer output when the last stream goes, so an unwatched stream costs the
// host nothing.
K.streams = (() => {
    const S = {};
    // DR outlives a hidden page (app in the background, screen off): its history lives
    // on the box only while a listener is attached, and pages/now.js ends it after the
    // configured keep-alive.  Everything else stops the moment nothing can be seen.
    const BACKGROUND = new Set(['dr']);
    // A slow or stalled network, or a busy main thread, must never leave the meters
    // playing catch-up:
    //  - each frame says when the box produced it (`published`) and sent it (`sent`).
    //    The lowest arrival - sent seen on this connection is the network at its best
    //    (plus the clocks' offset), so published + that floor is when the frame would
    //    have arrived with nothing in the way, and + this screen's delay is when it is
    //    due.  A frame past due by more than STALE_MS is dropped - on arrival, and
    //    again when it is about to be drawn - however it got late: a backlog queued in
    //    the network, timers or a main thread that fell behind;
    //  - level/spectrum frames are drawn at most once per screen refresh, the newest
    //    one; whatever arrived in between is dropped (DR is not: its frames carry the
    //    history only when it changed, so every one of them counts);
    //  - once frames keep arriving LAG_MS late, they are coming out of a backlog
    //    queued in the network, and the stream is opened again, which drops it.  The
    //    new stream is opened before the old one is closed, so the box never sees its
    //    last listener leave (that would turn its analyzer off).
    // The floor relaxes by DRIFT (100 ppm) so two clocks running apart cannot, over
    // hours, make every frame look late.
    const STALE_MS = 100, LAG_MS = 500, LAG_FRAMES = 5, DRIFT = 1e-4;
    const lagFns = new Set();     // told when a backlog was dropped (meters show "LAG!")
    const connect = mode => {
        const s = S[mode];
        if (!s || s.es || (document.hidden && !BACKGROUND.has(mode)) || !s.subs.size) return;
        open(mode);
    };
    const open = (mode, replacing = null) => {
        const s = S[mode];
        const es = new EventSource('/spectrum/stream?mode=' + encodeURIComponent(mode));
        const tapMode = mode.endsWith('-clip') ? mode.slice(0, -5) : mode;
        s.es = es;
        if (replacing) setTimeout(() => replacing.close(), 1500);
        let best = Infinity, bestAt = 0, late = 0;
        const deliver = d => {
            if (K.drawTap) K.drawTap(tapMode, d, Date.now(), mode);     // the post-calibration check
            s.subs.forEach(f => f(d));
        };
        const draw = (d, due) => {
            if (s.es !== es) return;                          // replaced or closed meanwhile
            if (mode === 'dr') { deliver(d); return; }
            const live = d.ok && d.state === 'running';
            if (live && Date.now() > due + STALE_MS) return;  // the timer itself ran late
            s.next = d; s.nextDue = live ? due : Infinity;
            if (!s.raf) s.raf = requestAnimationFrame(() => {
                s.raf = 0;
                const x = s.next, xDue = s.nextDue;
                s.next = null;
                if (x && s.es === es && Date.now() <= xDue + STALE_MS) deliver(x);
            });
        };
        es.onmessage = ev => {
            if (s.es !== es) return;
            let d;
            try { d = JSON.parse(ev.data); } catch { return; }
            const now = Date.now();
            let origin = now;
            if (mode !== 'dr' && Number.isFinite(d.sent)) {
                best = Math.min(now - d.sent, best + (bestAt ? (now - bestAt) * DRIFT : 0));
                bestAt = now;
                origin = (Number.isFinite(d.published) ? d.published : d.sent) + best;
                const age = now - origin;
                late = age > LAG_MS ? late + 1 : 0;
                if (late >= LAG_FRAMES && !s.replacing) {
                    s.replacing = true;
                    setTimeout(() => { s.replacing = false; }, 5000);   // at most one reopen per 5 s
                    lagFns.forEach(f => f(mode));
                    open(mode, es);
                    return;
                }
                // Schedule from server send time plus the best transit time seen.
                // A burst after Wi-Fi stalls must not shift the saved alignment.
                d = { ...d };
                d._timingArrival = origin;
            }
            // Level and spectrum frames are drawn this device's extra delay after they
            // would have arrived (widgets/sync.js); DR is not time-critical.
            const wait = mode === 'dr' || !K.sync ? 0 : K.sync.delayMs();
            const due = origin + wait;
            // Stale before taps and drawing.  A status frame (waiting, an error) is
            // news however old: the box publishes it once and keeps it until it changes.
            const live = mode !== 'dr' && d.ok && d.state === 'running';
            if (live && now > due + STALE_MS) return;
            if (K.streamTap) K.streamTap(tapMode, d, origin, mode);
            if (due > now) setTimeout(() => draw(d, due), due - now);
            else draw(d, due);
        };
        es.onerror = () => {
            es.close();
            if (s.es !== es) return;                          // the one being replaced
            s.es = null;
            s.subs.forEach(f => f({ ok: false, state: 'reconnecting', error: 'stream disconnected' }));
            clearTimeout(s.retry);
            s.retry = setTimeout(() => connect(mode), 1500);
        };
    };
    const teardown = mode => {
        const s = S[mode];
        if (!s) return;
        clearTimeout(s.retry);
        if (s.es) { s.es.close(); s.es = null; }
    };
    document.addEventListener('visibilitychange', () => {
        for (const mode of Object.keys(S)) {
            if (!document.hidden) connect(mode);
            else if (!BACKGROUND.has(mode)) teardown(mode);
        }
    });
    return {
        /** fn(mode) runs whenever late frames were discarded because the network lagged. */
        onLag(fn) { lagFns.add(fn); return { close() { lagFns.delete(fn); } }; },
        open(mode, fn) {
            if (!K.state.spectrum.enabled) return { close() {} };
            const s = S[mode] || (S[mode] = { subs: new Set(), es: null, retry: null });
            s.subs.add(fn);
            connect(mode);
            return { close() { s.subs.delete(fn); if (!s.subs.size) teardown(mode); } };
        },
    };
})();

// Code that needs the boot config (commands, feature flags) waits here.
K.readyQueue = [];
K.onReady = fn => { if (K.ready) fn(); else K.readyQueue.push(fn); };
K.markReady = () => { K.ready = true; K.readyQueue.splice(0).forEach(fn => fn()); };

// ── commands (commands.conf, via the kiosk blueprint) ────────────────────────
K.commands = () => K.state.commands;
K.runCommand = async (cmd, { confirm = true } = {}) => {
    if (confirm && (cmd.confirm === 'yes' || cmd.group === 'system')) {   // never a bare tap on reboot/power off
        const yes = await K.confirm({
            title: cmd.what, message: cmd.confirm_message || `Run “${cmd.what}”?`,
            ok: cmd.button || 'Run', danger: true,
        });
        if (!yes) return { ok: false, cancelled: true };
    }
    const busy = K.busy(`${cmd.what}…`);
    const d = await K.api('/run/' + encodeURIComponent(cmd.id), { method: 'POST', timeout: 130000 });
    busy.done();
    K.toast(d.ok ? `${cmd.what}: done` : (d.error || 'failed'), d.ok ? 'ok' : 'error');
    return d;
};

// ── small UI parts shared by the pages ───────────────────────────────────────
K.card = (title, ...kids) => {
    const opts = kids.length && kids[kids.length - 1] && kids[kids.length - 1].__opts ? kids.pop() : {};
    return K.h('section', { class: 'card ' + (opts.class || '') },
        title ? K.h('div', { class: 'card-head' }, K.h('h3', {}, title), K.h('span', { class: 'spacer' }), opts.actions || null) : null,
        ...kids);
};
K.cardOpts = o => Object.assign({ __opts: true }, o);

// Big touch-friendly one-of-N selector.
K.segmented = (options, current, onPick, cls = '') => K.h('div', { class: 'seg ' + cls },
    options.map(o => K.h('button', {
        type: 'button', class: 'seg-btn' + (o.value === current ? ' on' : ''), disabled: !!o.disabled,
        onclick: () => onPick(o.value),
    }, o.label)));

// A label/value row.
K.kv = (k, v, cls = '') => K.h('div', { class: 'kv ' + cls }, K.h('span', { class: 'k' }, k), K.h('span', { class: 'v' }, v));

// Jump to a page by id (used by tappable summaries).
// ?embed=<page> (kiosk_shell.html): that page alone, no bars, no screen saver
K.embed = document.documentElement.dataset.embed || '';
K.goto = id => { if (K.showPage) K.showPage(id); };

// ── page registry ────────────────────────────────────────────────────────────
// A page is {id, label, title, mount(el), show(), hide()}.  mount() runs once,
// on first display; show()/hide() bracket the time the page is on screen, and
// are where streams and pollers are started and stopped.
K.registerPage = page => { K.pages.push(page); };
})();
