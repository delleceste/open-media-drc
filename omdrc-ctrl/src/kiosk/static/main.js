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

// Small line icons for the top bar (SVG, so they are sharp at any size).
function icon(name) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', 'top-icon');
    const shapes = {
        search: [['circle', { cx: 10.5, cy: 10.5, r: 6.3 }], ['path', { d: 'M15.3 15.3 L20.5 20.5' }]],
        // a phone turning: an upright phone and an arrow round its corner
        rotate: [['rect', { x: 4, y: 7.5, width: 9, height: 14, rx: 1.6 }],
                 ['path', { d: 'M13.5 3.5 A7.5 7.5 0 0 1 20.5 10.5' }], ['path', { d: 'M20.5 10.5 L22.3 7.8 M20.5 10.5 L17.7 9.1' }]],
    }[name];
    for (const [tag, attrs] of shapes) {
        const el = document.createElementNS(ns, tag);
        Object.entries({ fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', ...attrs })
            .forEach(([k, v]) => el.setAttribute(k, v));
        svg.append(el);
    }
    return svg;
}

// ── pager ────────────────────────────────────────────────────────────────────
let cur = -1;
const pager = $('#pager'), tabs = $('#tabs');

function activate(i) {
    if (i === cur || i < 0 || i >= K.pages.length) return;
    if (cur >= 0) safe(() => K.pages[cur].hide && K.pages[cur].hide());
    K.setTopExtra(null);
    cur = i;
    if (K.awake) K.awake.sync();
    const page = K.pages[i];
    if (!page.mounted) {
        page.mounted = true;
        safe(() => page.mount(page.body), page);
    }
    if (!K.saverActive) safe(() => page.show && page.show(), page);
    [...tabs.children].forEach((b, n) => b.classList.toggle('on', n === i));
    $('#top-page').textContent = page.label;
    syncAppScreen();
    reportScroll();
    applyOrientation(page);
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
const NO_SWIPE = 'input, select, textarea, .scrim, .splitter, .vsplit, .seek-zone';
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
// "Now playing" is on screen (not behind the screensaver, not another page) and
// there has been sound lately (see SILENCE_MS).
K.inApp = !!window.OmdrcApp;
// Sound: the Now page marks the time of the last frame with audio in it (or, with no
// level stream, the last poll that said "playing").  After 30 s of silence the screen is
// no longer held on, and comes back under the phone's own timeout.
const SILENCE_MS = 30000;
K.lastSoundAt = Date.now();
K.markSound = () => { const was = K.silent(); K.lastSoundAt = Date.now(); if (was) { syncAppScreen(); if (K.awake) K.awake.sync(); } };
K.silent = () => Date.now() - K.lastSoundAt > SILENCE_MS;
K.nowShown = () => cur >= 0 && K.pages[cur].id === 'now' && !K.saverActive && !document.hidden && !K.silent();
// Held on regardless (and no screensaver) while something the user is watching runs
// without a touch: the meter-timing sheet, a calibration (pages/config.js).
K.screenHolds = new Set();
K.holdScreen = (key, on) => {
    if (on) K.screenHolds.add(key); else K.screenHolds.delete(key);
    try { if (window.OmdrcApp && window.OmdrcApp.setHoldScreenOn) window.OmdrcApp.setHoldScreenOn(K.screenHolds.size > 0); } catch {}
    syncAppScreen();
    if (K.awake) K.awake.sync();
    armSaver();
};
K.screenHeld = () => K.screenHolds.size > 0 && !document.hidden;
setInterval(() => { syncAppScreen(); if (K.awake) K.awake.sync(); }, 5000);
function syncAppScreen() {
    if (!K.inApp) return;
    // K.nowShown(): Now on screen, no screensaver, and sound within the last SILENCE_MS -
    // still meters let the phone sleep again, in the app as in a browser.
    // (an older app without setHoldScreenOn: a hold asks as Now playing does)
    const hold = K.screenHeld() && !window.OmdrcApp.setHoldScreenOn;
    try { window.OmdrcApp.setPageWantsScreenOn(K.nowShown() || hold); } catch {}
}
document.addEventListener('visibilitychange', syncAppScreen);

// The app reloads on a swipe down, but only when the page is at its top: the kiosk
// scrolls inside its pages, which the WebView cannot see, so tell it.
// "At the top" means every scrolling box under the finger is at its top, not only the
// page: a page may scroll inside a column of its own (the Qobuz page's filters), and
// dragging that back up must not reload.  Under an overlay (a sheet, the full player)
// the app never reloads.  Reported on each touch, before the app judges the drag.
function scrolledAt(el) {
    for (; el && el !== document.body; el = el.parentElement)
        if (el.scrollTop > 2 && el.scrollHeight > el.clientHeight) return true;
    return false;
}
// ── orientation (a phone) ────────────────────────────────────────────────────
// Every page is upright except Now, which is laid out for landscape; the top bar's
// rotate button flips the page on screen and remembers that for it.  The Android
// app rotates on request (apiVersion 5); a browser only in fullscreen, where the
// orientation can be locked.  The 7" panel is neither, and never turns.
// With the phone's auto-rotate on (app apiVersion 6) the app follows the phone like
// any other app and the pages adapt to how it is held (Now: needles in landscape,
// bars upright, pages/now.js); the rotate button is then hidden.
K.autoRotate = () => { try { return !!(K.inApp && window.OmdrcApp.autoRotate && window.OmdrcApp.autoRotate()); } catch { return false; } };
K.canRotate = () => K.inApp ? !!window.OmdrcApp.setPageOrientation && !K.autoRotate()
    : !!(document.fullscreenElement && screen.orientation && screen.orientation.lock);
// the app: auto-rotate was switched on or off while the kiosk is open
K.onAutoRotate = () => applyOrientation(cur >= 0 ? K.pages[cur] : null);
K.orientationOf = page => K.pref('orient.' + page.id, page.orientation || 'portrait');
function applyOrientation(page) {
    if (!page) return;
    const want = K.orientationOf(page);
    if (K.inApp) {
        try { if (window.OmdrcApp.setPageOrientation) window.OmdrcApp.setPageOrientation(want); } catch {}
    } else if (K.canRotate()) {
        screen.orientation.lock(want).catch(() => {});
    }
    paintRotate();
}
function paintRotate() {
    const btn = $('#top-rotate');
    btn.hidden = !K.canRotate();
    const page = cur >= 0 ? K.pages[cur] : null;
    if (page) btn.title = K.orientationOf(page) === 'portrait' ? 'Turn this page to landscape' : 'Turn this page upright';
}
function rotate() {
    const page = cur >= 0 ? K.pages[cur] : null;
    if (!page) return;
    K.setPref('orient.' + page.id, K.orientationOf(page) === 'portrait' ? 'landscape' : 'portrait');
    applyOrientation(page);
}

function reportScroll(target) {
    if (!K.inApp || !window.OmdrcApp.setPageScrolled) return;
    const body = cur >= 0 ? K.pages[cur].body : null;
    // a page may opt out of pull-to-reload altogether (pages/qobuz.js: pulling down there
    // means "up to the search box", never "reload the kiosk")
    const scrolled = $('#overlay-root').children.length > 0 || scrolledAt(target) || (!!body && body.scrollTop > 2)
        || (cur >= 0 && !!K.pages[cur].noPullReload);
    try { window.OmdrcApp.setPageScrolled(scrolled); } catch {}
}
document.addEventListener('scroll', e => {
    if (e.target instanceof Element) reportScroll(e.target);
}, { capture: true, passive: true });
document.addEventListener('pointerdown', e => reportScroll(e.target), { capture: true, passive: true });

// ── page menu (phones: replaces the bottom tab bar) ──────────────────────────
$('#top-menu').addEventListener('click', () => {
    const close = () => scrim.remove();
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet' }, h('div', { class: 'pick-grid' }, K.pages.map((p, n) =>
            h('button', { type: 'button', class: 'btn' + (n === cur ? ' active' : ''), onclick: () => { close(); K.showPage(p.id); } }, p.title)))));
    $('#overlay-root').append(scrim);
});

// ── top bar ──────────────────────────────────────────────────────────────────
// The DRC status LED lives in the DRC line at the bottom of Now (pages/now.js), not up here.
K.drcLedClass = s => !s.known ? 'warn' : s.power === 'on'
    ? (s.verification === 'verified' ? 'ok' : s.verification === 'mismatch' ? 'bad' : 'warn')
    : s.power === 'off' ? 'off' : 'warn';

// The top bar is an overlay that slides away, so every page has the whole screen.  A tap on
// the page (not on a control, not a swipe) or the mouse near the top edge brings it back; it
// hides again after a few seconds, or on the next such tap.
const BAR_MS = 5000;
let barTimer = null;
K.showBar = (show = true) => {
    document.body.classList.toggle('bar-shown', show);
    clearTimeout(barTimer);
    if (show) barTimer = setTimeout(() => K.showBar(false), BAR_MS);
    else if (K.onBarHidden) K.onBarHidden();     // a menu opened from the bar goes with it
};
const CONTROLS = 'button, a, input, select, textarea, label, summary, .tap, .seg, .chip, .scrim, #topbar, #tabs, .dr-bar:not(.static), .splitter, .vsplit, .seek-zone, [role=switch]';
let tapStart = null;
document.addEventListener('pointerdown', e => { tapStart = { x: e.clientX, y: e.clientY }; }, true);
document.addEventListener('pointerup', e => {
    const s = tapStart; tapStart = null;
    if (!s || Math.hypot(e.clientX - s.x, e.clientY - s.y) > 10) return;   // a swipe or drag
    if (K.saverActive || (e.target.closest && e.target.closest(CONTROLS))) return;
    K.showBar(!document.body.classList.contains('bar-shown'));
});
// A real mouse only: after a tap Android's WebView sends a mousemove at the finger,
// and a tap on a control near the top (the Qobuz search field) must not pull the bar in.
document.addEventListener('pointermove', e => { if (e.pointerType === 'mouse' && e.clientY < 30) K.showBar(true); }, { passive: true });
$('#topbar').addEventListener('pointerdown', () => K.showBar(true));   // using it keeps it up

// The top bar has no clock any more (the phone/panel already shows one); only the
// screensaver does.
function paintClock() {
    const d = new Date();
    $('#saver-clock').textContent = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// A page may put one control in the top bar (the Now page's level-display button).
K.setTopExtra = el => { const box = $('#top-extra'); K.clear(box); if (el) box.append(el); };

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

// Fullscreen, and - where the browser allows it, which is only in fullscreen and not in Firefox or
// iOS Safari - landscape.  The Android app locks landscape natively instead.
K.toggleFullscreen = () => {
    if (document.fullscreenElement) {
        try { screen.orientation && screen.orientation.unlock(); } catch {}
        document.exitFullscreen().then(paintRotate, paintRotate);
    } else if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen()
            .then(() => applyOrientation(K.pages[cur]))
            .catch(() => {});
    }
};

// ── screensaver ──────────────────────────────────────────────────────────────
// Dims to a clock and the track title.  While it is up the current page is
// hidden, so the analyzer streams are closed and nothing is being computed.
K.saverActive = false;
let idleTimer = null, saverPoll = null;
function armSaver() {
    clearTimeout(idleTimer);
    const minutes = Number(K.pref('saver.minutes', 0));
    if (minutes > 0 && !K.saverActive) idleTimer = setTimeout(() => K.screenHolds.size ? armSaver() : startSaver(), minutes * 60000);
}
async function paintSaverTrack() {
    const t = await K.fetchTrack();
    $('#saver-track').textContent = t.ok && t.state === 'play' ? [t.title, t.artist].filter(Boolean).join(' — ') : '';
}
// dark = the Pi's "display off now": pure black, no clock, and the helper switches the
// screen off.  Either way the current page is hidden, so nothing is computed meanwhile.
function startSaver(dark = false) {
    K.saverActive = true;
    K.saverDark = dark;
    $('#saver').classList.toggle('dark', dark);
    if (dark) K.display.off().then(d => { if (!d.ok) console.warn('display off:', d.error); });
    syncAppScreen();
    if (K.awake) K.awake.sync();
    $('#saver').hidden = false;
    if (cur >= 0) safe(() => K.pages[cur].hide && K.pages[cur].hide());
    paintSaverTrack();
    saverPoll = setInterval(paintSaverTrack, 15000);
}
function stopSaver() {
    if (!K.saverActive) return;
    K.saverActive = false;
    syncAppScreen();
    if (K.awake) K.awake.sync();
    $('#saver').hidden = true;
    clearInterval(saverPoll);
    if (K.saverDark) { K.saverDark = false; K.display.on(); }
    if (cur >= 0) safe(() => K.pages[cur].show && K.pages[cur].show());
}
K.applyPrefs = () => { armSaver(); K.awake.sync(); K.refreshPages(); };
['pointerdown', 'keydown', 'wheel'].forEach(ev => document.addEventListener(ev, () => {
    if (K.saverActive) { stopSaver(); armSaver(); return; }
    armSaver();
}, { capture: true, passive: true }));

// (keeping the screen on lives in widgets/keepawake.js; K.awake.sync() below)

// ── background preparation ───────────────────────────────────────────────────
// Pages are built the first time they are shown, and fetch their data then, so on
// a slow network a first swipe showed an empty page that filled in afterwards.
// Once the first page is up, build every other page and fetch its data once, one
// page at a time.  No poller or stream is started: that still happens only in
// show(), which also refreshes the data straight away.
async function prepareOtherPages() {
    for (const page of K.pages) {
        if (page.mounted || document.hidden) continue;
        page.mounted = true;
        safe(() => page.mount(page.body), page);
        const fetchOnce = page.prefetch || (page.poll && (() => page.poll.now()));
        if (fetchOnce) { try { await fetchOnce(); } catch {} }
        await new Promise(r => setTimeout(r, 250));       // spread the requests out
    }
}

// ── optional pages ───────────────────────────────────────────────────────────
// A page with optional() is in the pager and the tab bar only while that says so
// (the Cover page: Config → Cover art).  Switching one on or off rebuilds the
// order in place and keeps the current page on screen.
const enabledPages = () => K.allPages.filter(p => !p.optional || p.optional());
function buildPage(p) {
    p.body = h('div', { class: 'page-body' });
    p.section = h('section', { class: 'page', id: 'page-' + p.id, 'data-id': p.id }, p.body);
    p.tab = h('button', { type: 'button', class: 'tab', onclick: () => K.showPage(p.id) }, p.label);
}
K.refreshPages = () => {
    if (!K.allPages) return;
    const next = enabledPages();
    if (next.length === K.pages.length && next.every((p, i) => p === K.pages[i])) return;
    const current = cur >= 0 ? K.pages[cur] : null;
    for (const p of K.allPages) {
        if (!p.section) buildPage(p);
        if (!next.includes(p)) { p.section.remove(); p.tab.remove(); }
    }
    next.forEach(p => { pager.append(p.section); tabs.append(p.tab); });
    K.pages = next;
    cur = Math.max(0, next.indexOf(current));
    [...tabs.children].forEach((b, n) => b.classList.toggle('on', n === cur));
    pager.scrollTo({ left: cur * pager.clientWidth, behavior: 'instant' });
};

// ── new code on the box: reload by itself ────────────────────────────────────
// Every script is asked for with ?v=<asset version> (kiosk/__init__.py), which changes
// only when the box's kiosk files do.  An open page asks the box for the current one
// now and then, and when it differs, reloads -- but only at a quiet moment: no sheet
// or player open, nothing being typed, no calibration and no search running.  The
// page comes back where it was (the hash), the Qobuz results with it (kept on this
// device).  In the app the screen is frozen meanwhile (reloadQuietly), so the new page
// just replaces the old one.
const MY_VERSION = (() => { try { return new URL(document.currentScript.src).searchParams.get('v') || ''; } catch { return ''; } })();
const VERSION_EVERY_MS = 60000;
let newCode = false, lastVersionCheck = 0;
const quietNow = () => $('#overlay-root').children.length === 0 && !K.saverActive
    && !(document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName))
    && !(K.sync && K.sync.running) && !(K.qobuzBusy && K.qobuzBusy());
function reloadForNewCode() {
    if (!newCode || document.hidden || !quietNow()) return;
    newCode = false;
    try { if (K.inApp && window.OmdrcApp.reloadQuietly) { window.OmdrcApp.reloadQuietly(); return; } } catch {}
    location.reload();
}
async function checkVersion() {
    if (!MY_VERSION || newCode || document.hidden || Date.now() - lastVersionCheck < VERSION_EVERY_MS / 2) return;
    lastVersionCheck = Date.now();
    const d = await K.api('/k/version', { timeout: 5000 });
    if (d && d.ok && d.version && d.version !== MY_VERSION) newCode = true;
    reloadForNewCode();
}
setInterval(checkVersion, VERSION_EVERY_MS);
setInterval(reloadForNewCode, 3000);       // waiting for a quiet moment
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkVersion(); });   // back in the app

// ── boot ─────────────────────────────────────────────────────────────────────
async function boot() {
    const [cfg, spec] = await Promise.all([K.api('/k/api/config'), K.api('/spectrum/settings')]);
    if (cfg.ok) { K.state.commands = cfg.commands; K.state.features = cfg.features || {}; }
    if (spec && spec.ok) K.state.spectrum = spec;
    else K.state.spectrum = { enabled: false };

    document.body.classList.toggle('in-app', K.inApp);
    K.allPages = K.pages.slice();
    K.pages = enabledPages();
    K.pages.forEach(p => {
        buildPage(p);
        pager.append(p.section);
        tabs.append(p.tab);
    });
    K.markReady();

    K.drcState.start();
    paintClock(); setInterval(paintClock, 10000);
    pollAlerts(); setInterval(() => { if (!document.hidden) pollAlerts(); }, 20000);
    $('#top-full').addEventListener('click', K.toggleFullscreen);
    $('#top-rotate').append(icon('rotate'));
    $('#top-rotate').addEventListener('click', rotate);
    if (K.state.features.qobuz_search) {
        const search = $('#top-search');
        search.hidden = false;
        search.append(icon('search'));
        search.addEventListener('click', () => { K.showBar(false); K.goto('qobuz'); });
    }
    paintRotate();
    // Top-right: release the screen.  Off = the OS may switch the display off after its own
    // timeout (in the app that is the keep-awake flag, in a browser the wake lock / video).
    const awakeBtn = $('#top-awake');
    const wantsAwake = () => K.inApp ? !!window.OmdrcApp.keepScreenOn() : K.pref('awake.on', true);
    const paintAwake = () => {
        if (K.display.ok) {
            awakeBtn.textContent = '⏻';
            awakeBtn.classList.remove('on');
            awakeBtn.title = `Switch the display off now (${K.display.method}) — tap the screen to wake it`;
            return;
        }
        const on = wantsAwake();
        awakeBtn.textContent = on ? '☀' : '☾';
        awakeBtn.classList.toggle('on', on);
        awakeBtn.title = on ? 'Screen stays on while Now playing is shown — tap to let it sleep'
                            : 'Screen may switch off on its own timeout — tap to keep it on';
    };
    awakeBtn.addEventListener('click', () => {
        if (K.display.ok) { startSaver(true); return; }    // on the Pi: switch the display off now
        const next = !wantsAwake();
        if (K.inApp) { try { window.OmdrcApp.setKeepScreenOn(next); } catch {} }
        else { K.setPref('awake.on', next); K.awake.sync(); }
        paintAwake();
        K.toast(next ? 'Screen stays on while Now playing is shown' : 'Screen released: it may switch off on its own timeout');
    });
    document.addEventListener('visibilitychange', paintAwake);
    K.paintAwake = paintAwake;
    paintAwake();
    K.display.probe().then(ok => { if (ok) paintAwake(); });   // the Pi's display helper, if configured
    $('#top-alert').addEventListener('click', () => K.showPage('logs'));
    armSaver(); K.awake.sync();

    const wanted = (location.hash || '').slice(1) || new URLSearchParams(location.search).get('page') || K.pages[0].id;
    K.showPage(K.pages.some(p => p.id === wanted) ? wanted : K.pages[0].id, false);
    // the app's splash covers the page until now: tell it once this first page is painted
    requestAnimationFrame(() => requestAnimationFrame(() => {
        try { if (K.inApp && window.OmdrcApp.pageReady) window.OmdrcApp.pageReady(); } catch {}
    }));
    setTimeout(prepareOtherPages, 1500);   // after the first page has painted
}
boot();
})();
