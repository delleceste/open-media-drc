/* Kiosk shell: boot, the swipe pager, the tab bar, the top bar, log-alert
 * badge, screensaver and wake lock.  Pages live in pages/*.js and are shown and
 * hidden by the pager, which is what starts and stops their work. */
(() => {
'use strict';
const { h, $ } = K;

// ── alerts (shared by the top bar and the Logs page) ─────────────────────────
K.alerts = [];
K.alertSubs = new Set();
K.onAlerts = fn => K.alertSubs.add(fn);

// ── pager ────────────────────────────────────────────────────────────────────
let cur = -1;
const pager = $('#pager'), tabs = $('#tabs');

function activate(i) {
    if (i === cur || i < 0 || i >= K.pages.length) return;
    if (cur >= 0) safe(() => K.pages[cur].hide && K.pages[cur].hide());
    cur = i;
    const page = K.pages[i];
    if (!page.mounted) {
        page.mounted = true;
        safe(() => page.mount(page.body), page);
    }
    if (!K.saverActive) safe(() => page.show && page.show(), page);
    [...tabs.children].forEach((b, n) => b.classList.toggle('on', n === i));
    $('#top-page').textContent = page.label;
    syncAppScreen();
    try { history.replaceState(null, '', '#' + page.id); } catch {}
}

function safe(fn, page) {
    try { fn(); } catch (e) {
        console.error(e);
        if (page) page.body.append(h('div', { class: 'errbox' }, `${page.title} failed to load: ${e.message}`));
    }
}

K.showPage = (id, smooth = true) => {
    const i = K.pages.findIndex(p => p.id === id);
    if (i < 0) return;
    pager.scrollTo({ left: i * pager.clientWidth, behavior: smooth ? 'smooth' : 'instant' });
    if (!smooth) activate(i);
};

// Commit the page only once the scroll settles, so tapping from page 1 to page
// 6 does not start and stop the four in between.
let settle = null;
pager.addEventListener('scroll', () => {
    clearTimeout(settle);
    settle = setTimeout(() => activate(Math.round(pager.scrollLeft / Math.max(1, pager.clientWidth))), 110);
}, { passive: true });
window.addEventListener('resize', () => { if (cur >= 0) pager.scrollTo({ left: cur * pager.clientWidth, behavior: 'instant' }); });
document.addEventListener('keydown', e => {
    if (e.target.closest && e.target.closest('input, textarea')) return;
    if (e.key === 'ArrowRight') K.showPage((K.pages[Math.min(K.pages.length - 1, cur + 1)] || {}).id);
    if (e.key === 'ArrowLeft') K.showPage((K.pages[Math.max(0, cur - 1)] || {}).id);
});

// ── touch swipe ──────────────────────────────────────────────────────────────
// Native horizontal panning is switched off for touch (touch-action: pan-y), and
// the swipe is done here: the same on iOS and Android, and it never fights the
// page's own vertical scroll.  The finger drags the pager; on release it goes to
// the next page if you moved a fifth of the width (at most 100 px) or flicked.
let sw = null;
const NO_SWIPE = 'input, select, textarea, .scrim';
pager.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'touch' || e.target.closest(NO_SWIPE)) return;
    sw = { id: e.pointerId, x: e.clientX, y: e.clientY, left: pager.scrollLeft, mode: null, lastX: e.clientX, lastT: performance.now(), v: 0 };
});
pager.addEventListener('pointermove', e => {
    if (!sw || e.pointerId !== sw.id) return;
    const dx = e.clientX - sw.x, dy = e.clientY - sw.y;
    if (!sw.mode) {
        if (Math.abs(dx) > 10 && Math.abs(dx) > 1.4 * Math.abs(dy)) {
            sw.mode = 'x';
            try { pager.setPointerCapture(e.pointerId); } catch {}
            pager.style.scrollSnapType = 'none';
        } else if (Math.abs(dy) > 10) { sw = null; return; }
        else return;
    }
    const now = performance.now();
    sw.v = (e.clientX - sw.lastX) / Math.max(1, now - sw.lastT);   // px per ms
    sw.lastX = e.clientX; sw.lastT = now;
    pager.scrollLeft = K.clamp(sw.left - dx, 0, pager.scrollWidth - pager.clientWidth);
});
function endSwipe(e) {
    if (!sw || e.pointerId !== sw.id) return;
    const s = sw; sw = null;
    if (s.mode !== 'x') return;
    const w = pager.clientWidth, dx = e.clientX - s.x, from = Math.round(s.left / w);
    let to = from;
    const far = Math.min(0.2 * w, 100);          // a fifth of the width, but never more than 100 px
    if (dx < -far || s.v < -0.5) to = from + 1;
    else if (dx > far || s.v > 0.5) to = from - 1;
    to = K.clamp(to, 0, K.pages.length - 1);
    pager.scrollTo({ left: to * w, behavior: 'smooth' });
    setTimeout(() => { pager.style.scrollSnapType = ''; }, 450);
}
pager.addEventListener('pointerup', endSwipe);
pager.addEventListener('pointercancel', e => {
    if (sw && sw.mode === 'x') endSwipe(e);
    else if (sw && e.pointerId === sw.id) sw = null;
});

// ── the Android app's bridge (window.OmdrcApp), when we run inside it ──────────
// The app keeps the screen on only while this page asks for it: exactly while
// "Now playing" is on screen (not behind the screensaver, not another page).
K.inApp = !!window.OmdrcApp;
function syncAppScreen() {
    if (!K.inApp) return;
    try { window.OmdrcApp.setPageWantsScreenOn(cur >= 0 && K.pages[cur].id === 'now' && !K.saverActive && !document.hidden); } catch {}
}
document.addEventListener('visibilitychange', syncAppScreen);

// ── page menu (phones: replaces the bottom tab bar) ──────────────────────────
$('#top-menu').addEventListener('click', () => {
    const close = () => scrim.remove();
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet' }, h('div', { class: 'pick-grid' }, K.pages.map((p, n) =>
            h('button', { type: 'button', class: 'btn' + (n === cur ? ' active' : ''), onclick: () => { close(); K.showPage(p.id); } }, p.title)))));
    $('#overlay-root').append(scrim);
});

// ── top bar ──────────────────────────────────────────────────────────────────
function paintTopDrc() {
    const s = K.drcState.summary();
    const el = $('#top-drc');
    K.clear(el);
    const cls = !s.known ? 'warn' : s.power === 'on' ? (s.verification === 'verified' ? 'ok' : s.verification === 'mismatch' ? 'bad' : 'warn') : s.power === 'off' ? 'off' : 'warn';
    el.append(h('i', { class: 'dot ' + cls }), s.text + (s.running && s.attenuation !== null ? ` · −${s.attenuation.toFixed(1)} dB` : ''));
}

function paintClock() {
    const d = new Date();
    const t = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    $('#top-clock').textContent = t;
    $('#saver-clock').textContent = t;
}

async function pollAlerts() {
    const d = await K.api('/logs/alerts');
    if (!d.ok && !Array.isArray(d.alerts)) return;
    K.alerts = d.alerts || [];
    const bad = K.alerts.filter(a => a.severity === 'error').length, warn = K.alerts.filter(a => a.severity === 'warn' || a.severity === 'warning').length;
    const btn = $('#top-alert');
    btn.hidden = !(bad || warn);
    btn.className = 'top-alert ' + (bad ? 'bad' : 'warn');
    btn.textContent = String(bad + warn);
    K.alertSubs.forEach(f => f());
}

K.toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
};

// ── screensaver ──────────────────────────────────────────────────────────────
// Dims to a clock and the track title.  While it is up the current page is
// hidden, so the analyzer streams are closed and nothing is being computed.
K.saverActive = false;
let idleTimer = null, saverPoll = null;
function armSaver() {
    clearTimeout(idleTimer);
    const minutes = Number(K.pref('saver.minutes', 0));
    if (minutes > 0 && !K.saverActive) idleTimer = setTimeout(startSaver, minutes * 60000);
}
async function paintSaverTrack() {
    const t = await K.fetchTrack();
    $('#saver-track').textContent = t.ok && t.state === 'play' ? [t.title, t.artist].filter(Boolean).join(' — ') : '';
}
function startSaver() {
    K.saverActive = true;
    syncAppScreen();
    $('#saver').hidden = false;
    if (cur >= 0) safe(() => K.pages[cur].hide && K.pages[cur].hide());
    paintSaverTrack();
    saverPoll = setInterval(paintSaverTrack, 15000);
}
function stopSaver() {
    if (!K.saverActive) return;
    K.saverActive = false;
    syncAppScreen();
    $('#saver').hidden = true;
    clearInterval(saverPoll);
    if (cur >= 0) safe(() => K.pages[cur].show && K.pages[cur].show());
}
K.applyPrefs = () => { armSaver(); K.awake.sync(); };
['pointerdown', 'keydown', 'wheel'].forEach(ev => document.addEventListener(ev, () => {
    if (K.saverActive) { stopSaver(); armSaver(); return; }
    armSaver();
}, { capture: true, passive: true }));

// (keeping the screen on lives in widgets/keepawake.js; K.awake.sync() below)

// ── boot ─────────────────────────────────────────────────────────────────────
async function boot() {
    const [cfg, spec] = await Promise.all([K.api('/k/api/config'), K.api('/spectrum/settings')]);
    if (cfg.ok) { K.state.commands = cfg.commands; K.state.features = cfg.features || {}; }
    if (spec && spec.ok) K.state.spectrum = spec;
    else K.state.spectrum = { enabled: false };

    document.body.classList.toggle('in-app', K.inApp);
    K.pages.forEach((p, i) => {
        p.body = h('div', { class: 'page-body' });
        pager.append(h('section', { class: 'page', id: 'page-' + p.id, 'data-id': p.id }, p.body));
        tabs.append(h('button', { type: 'button', class: 'tab', onclick: () => K.showPage(p.id) }, p.label));
    });
    K.markReady();

    K.drcState.onChange(paintTopDrc);
    paintTopDrc();
    K.drcState.start();
    paintClock(); setInterval(paintClock, 10000);
    pollAlerts(); setInterval(() => { if (!document.hidden) pollAlerts(); }, 20000);
    $('#top-full').addEventListener('click', K.toggleFullscreen);
    $('#top-drc').addEventListener('click', () => K.showPage('drc'));
    $('#top-alert').addEventListener('click', () => K.showPage('logs'));
    armSaver(); K.awake.sync();

    const wanted = (location.hash || '').slice(1) || new URLSearchParams(location.search).get('page') || K.pages[0].id;
    K.showPage(K.pages.some(p => p.id === wanted) ? wanted : K.pages[0].id, false);
}
boot();
})();
