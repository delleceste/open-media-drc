/* Page 1 — Now: the fancy one.  Level meters (needles, bars, or bars plus
 * spectrum), the DR estimate, channel balance, the track, and one line saying which
 * room correction is being applied.  Everything here is live, so nothing runs
 * unless this page is on screen. */
(() => {
'use strict';
const { h } = K;
const MODES = ['needles', 'bars', 'spectrum', 'off'];
const MODE_LABEL = { needles: 'Needles', bars: 'Bars', spectrum: 'Bars + spectrum', off: 'Level off' };
const WINDOWS = [60, 300, 900, 1800, 3600, 5400];

const P = {
    id: 'now', label: 'Now', title: 'Now playing',
    orientation: 'landscape',   // the one page laid out for it; every other is upright (main.js)
    mode: 'needles', showDr: true, showBalance: true,
    level: null, drSub: null, track: null, base: null,
};

P.mount = el => {
    P.el = el;
    P.art = h('img', { alt: '', hidden: true,
        onload: e => { e.target.hidden = false; P.artBox.classList.remove('empty'); P.paintIdle(); },
        onerror: e => { e.target.hidden = true; P.artBox.classList.add('empty'); P.paintIdle(); } });
    // the cover's box: small beside the title in landscape, a third of the height upright,
    // where a touch brings up the ring to seek along (see "seek ring" below)
    P.artBox = h('div', { class: 'now-art empty' }, P.art);
    P.titleText = h('span', {}, '—');
    P.t1 = h('div', { class: 'now-title', tabindex: '0',
        onpointerdown: () => P.t1.classList.add('reading'),
        onfocus: () => P.t1.classList.add('reading'),
        onblur: () => { P.t1.classList.remove('reading'); P.t1.scrollLeft = 0; }
    }, P.titleText);
    P.t2 = h('div', { class: 'now-sub' });
    // upright, the details are one per line instead of the one line under the title
    P.pArtist = h('div', { class: 'now-partist' });
    P.pAlbum = h('div', { class: 'now-palbum' });
    P.awards = h('div', { class: 'now-awards' });      // the album's prizes, in gold (widgets/albuminfo.js)
    P.fmt = h('span', { class: 'now-fmt' });
    // upright, under the format: all Qobuz says about the release, booklet first (widgets/albuminfo.js)
    P.infoBtn = h('button', { type: 'button', class: 'chip now-info', hidden: true, onclick: () => P.track && K.albumInfo(P.track.qobuz_album) }, 'Album details ›');
    P.state = h('button', { class: 'chip state', type: 'button', title: 'Tap: play / pause · hold: stop' });
    P.time = h('div', { class: 'now-time' });
    P.prog = h('i');
    // a meter-timing calibration in progress (automatic ones included): a blinking blue light
    P.calLed = h('i', { class: 'cal-led', hidden: true, title: 'Calibrating the meter timing' });
    const trackBox = h('div', { class: 'now-track' }, P.artBox,
        h('div', { class: 'now-meta' }, h('div', { class: 'now-subrow' }, P.t2, P.fmt), P.pArtist, P.awards, P.pAlbum, P.infoBtn), P.calLed,
        // play/pause/stop chip and the small time sit above the progress bar, at the right
        // (upright, previous and next track either side of it)
        h('div', { class: 'now-timebox' },
            h('span', { class: 'now-ctl' },
                h('button', { class: 'chip now-skip', type: 'button', title: 'Previous track', 'aria-label': 'Previous track', onclick: () => P.transport('prev') }, K.tIcon('prev')),
                P.state,
                h('button', { class: 'chip now-skip', type: 'button', title: 'Next track', 'aria-label': 'Next track', onclick: () => P.transport('next') }, K.tIcon('next'))),
            P.time, h('div', { class: 'now-prog' }, P.prog)), P.t1);

    // level area
    P.meterHost = h('div', { class: 'lvl-meter' });
    P.specCanvas = h('canvas', { class: 'lvl-spectrum' });
    // One chip in the top bar while this page is showing (see show()): a menu with the
    // level display and the Art, DR and Balance switches (DR and Balance are the
    // remembered switches of the DR page's Estimate and Config's Balance: off hides the
    // card and also stops what feeds it).  Its title says what the meters are reading.
    P.modeBtn = h('button', { class: 'chip lvl-mode', type: 'button', 'aria-haspopup': 'menu', onclick: () => P.openViewMenu() }, 'View ▾');
    P.topBtns = h('span', { class: 'top-toggles' }, P.modeBtn);
    // The cover behind the meters (off unless switched on: see "cover art" below)
    P.coverLayer = h('div', { class: 'cover-layer', hidden: true });
    P.coverScrim = h('div', { class: 'cover-scrim', hidden: true });
    P.coverReadout = h('div', { class: 'cover-readout', hidden: true });
    P.lvlBody = h('div', { class: 'lvl-body' }, P.coverLayer, P.coverScrim, P.meterHost, P.specCanvas, P.coverReadout);
    P.levelBox = h('div', { class: 'now-level' }, P.lvlBody);

    // With the level display off, the audio chain takes the meters' place: no
    // analyzer stream is opened for it, only a light poll of /audio/chain.
    P.chainHost = h('div', { class: 'cf' });
    P.chainSummary = h('span', { class: 'cf-summary' });
    P.chainBox = h('div', { class: 'now-chain' },
        h('div', { class: 'cf-head' }, h('span', { class: 'lbl' }, 'Audio chain'), P.chainSummary), P.chainHost);
    // With the level display off and the cover on, the whole cover, square, takes the chain's place.
    P.coverSqImg = h('img', { alt: '' });
    P.coverSqBox = h('div', { class: 'now-cover', hidden: true }, P.coverSqImg);
    P.leftCell = h('div', { class: 'now-left' }, P.levelBox, P.chainBox, P.coverSqBox);

    // DR block
    P.drValue = h('strong', { class: 'dr-value' }, '—');
    P.drStatus = h('span', { class: 'dr-status' });
    P.drWin = h('button', { class: 'chip', type: 'button', onclick: ev => P.cycleWindow(ev) });
    P.drGaugeHost = h('div', { class: 'drg' });
    P.drBox = h('div', { class: 'now-dr card' },
        h('div', { class: 'dr-head' }, h('span', { class: 'lbl' }, 'DR'), P.drValue, P.drStatus, P.drWin), P.drGaugeHost, P.drModeBox());
    P.gauge = K.drGauge(P.drGaugeHost);

    P.balHost = h('div', { class: 'now-bal card' });
    P.balance = new K.Balance(P.balHost);
    // vertical handle between the meters and the side column (side by side only)
    P.vsplit = h('div', { class: 'vsplit', title: 'Drag to widen the meters or the DR/balance column · double-tap the meters to restore' }, h('i'));
    P.side = h('div', { class: 'now-side' }, P.vsplit, P.drBox, P.balHost);

    // the DRC line
    P.drcLine = h('button', { class: 'now-drc', type: 'button', onclick: () => K.goto('drc') });

    P.mainBox = h('div', { class: 'now-main' }, P.leftCell, P.side);
    // upright, with the Qobuz search on: the Qobuz page's search box sits at the
    // bottom, a preview of the results above it (pages/qobuz.js moves both here and back)
    P.qzSlot = h('div', { class: 'now-search', hidden: true });
    el.append(h('div', { class: 'now' }, trackBox, P.mainBox, P.drcLine, P.qzSlot));
    P.wireVSplit();
    P.wireResetTap();
    P.wireMeterTap();
    P.wireCoverGesture();
    P.ring = new K.SeekRing(P.artBox, { usable: P.ringUsable, elapsed: () => P.elapsedNow(),
        duration: () => P.base.duration, seek: s => P.seek(s) });
    new ResizeObserver(() => { if (P.coverMode) P.placeCover(); }).observe(P.lvlBody);
    new ResizeObserver(() => { if (P.coverMode === 'square') P.applyCols(); }).observe(P.mainBox);
    P.vu = new K.VuMeter(P.meterHost, 'needles');
    P.initClips();
    P.vu.onClipReset = ch => {
        P.clipChannels[ch] = 0;
        P.vu.setClips(P.clipChannels);
    };
    P.spec = new K.Spectrum(P.specCanvas);
    // Frames dropped because the network lagged: "LAG!" for a few seconds where CLIP shows.
    K.streams.onLag(mode => {
        if (mode === 'dr') return;
        P.vu.setLag(true); P.spec.setLag(true);
        clearTimeout(P.lagTimer);
        P.lagTimer = setTimeout(() => { P.vu.setLag(false); P.spec.setLag(false); }, 4000);
    });
    if (K.sync) K.sync.onRunning(on => { P.calLed.hidden = !on; P.vu.setCalibrating(on); });

    P.wireTransport();
    P.chainPoll = new K.Poller(P.pollChain, 4000);
    P.trackPoll = new K.Poller(P.pollTrack, 3000);
    P.tick = new K.Poller(P.paintTime, 1000);
    K.drcState.onChange(P.paintDrc);
};

// Clip history belongs to this running page, not the saved preferences. An
// Android process restart creates a fresh page; pause/stop/background do not.
P.initClips = () => {
    P.clipChannels = { left: 0, right: 0 };
    P.clipSignal = { left: false, right: false };
    P.clipAlbum = null;
    P.vu.setClips(P.clipChannels);
};

P.observeClipAlbum = t => {
    // A stopped renderer may temporarily drop its album ID and edition. Keep
    // the last known album across stop and unavailable metadata.
    if (!t.ok || t.state === 'stop' || (!t.qobuz_album && !t.album)) return;
    const album = { id: t.qobuz_album || '', title: t.album || '', edition: t.edition || '' };
    const previous = P.clipAlbum;
    const changed = previous && (previous.id && album.id
        ? previous.id !== album.id
        : previous.title !== album.title || previous.edition !== album.edition);
    if (changed) {
        P.clipChannels = { left: 0, right: 0 };
        P.clipSignal = { left: false, right: false };
        P.vu.setSuspects({});
        P.vu.setClips(P.clipChannels);
    }
    P.clipAlbum = album;
};

// ── level display, one choice per orientation ────────────────────────────────
// Upright there is no room for needles: bars unless needles are picked while
// upright.  Turned to landscape the needles come back, unless bars (or another
// style) were picked there.  The older single setting ('now.level') seeds both,
// needles never seeding the upright one.
K.levelKey = (portrait = K.portrait()) => portrait ? 'now.level.portrait' : 'now.level.landscape';
K.levelMode = (portrait = K.portrait()) => {
    const old = K.pref('now.level', null);
    const dflt = portrait ? (old && old !== 'needles' ? old : 'bars') : (old || 'needles');
    const mode = K.pref(K.levelKey(portrait), dflt);
    return MODES.includes(mode) ? mode : dflt;
};
K.setLevelMode = (mode, portrait = K.portrait()) => K.setPref(K.levelKey(portrait), mode);

// The phone turned: the other orientation's choice, and the streams it needs.
matchMedia('(orientation: portrait)').addEventListener('change', () => {
    if (!P.mounted) return;
    P.artBox.classList.toggle('seek-zone', K.portrait());
    if (P.visible) P.syncDock();
    if (K.levelMode() === P.mode) { P.applyCols(); return; }   // the columns stack upright
    P.applyLayout();
    if (P.visible) P.syncMode();
});

// ── layout ───────────────────────────────────────────────────────────────────
P.applyLayout = () => {
    P.mode = K.levelMode();
    P.showDr = K.pref('now.dr', true);
    P.showBalance = K.pref('now.balance', true);
    const off = P.mode === 'off';
    P.levelBox.className = `now-level mode-${P.mode}`;
    P.levelBox.hidden = off;
    // '' = no cover (the layout as without the feature), 'behind' = under the meters,
    // 'square' = the whole cover where the audio chain would be (level display off)
    P.coverMode = P.coverWanted() && P.artReady ? (off ? 'square' : 'behind') : '';
    const square = P.coverMode === 'square';
    // the big cover is on screen: the small one beside the title would only repeat it
    P.el.firstChild.classList.toggle('cover-big', square);
    P.chainBox.hidden = !off || square;
    P.coverSqBox.hidden = !square;
    P.qzSlot.hidden = !K.state.features.qobuz_search;   // shown upright only (kiosk.css)
    P.artBox.classList.toggle('seek-zone', K.portrait());   // the seek ring's: no page swipe, no top bar
    P.el.firstChild.classList.toggle('cover-sq', square);
    if (!off) P.vu.setMode(P.mode === 'needles' ? 'needles' : 'bars');
    // needles and bars swap on a tap (a control then, so the tap leaves the top bar alone)
    P.lvlBody.classList.toggle('tap', P.mode === 'needles' || P.mode === 'bars');
    P.el.firstChild.classList.toggle('lvl-off', off);
    P.drBox.hidden = !P.showDr;
    P.balHost.hidden = !P.showBalance;
    P.side.hidden = !P.showDr && !P.showBalance;
    P.side.classList.toggle('single', P.showDr !== P.showBalance);   // one card alone: make it bigger
    P.el.firstChild.classList.toggle('no-side', P.side.hidden);
    P.el.firstChild.classList.add('no-drbar');
    // DR hidden but Balance on: no side column, the balance slides under the meters
    P.el.firstChild.classList.toggle('bal-below', !P.showDr && P.showBalance);
    // upright, something switched off leaves room: the cover grows and the track goes under it
    P.el.firstChild.classList.toggle('roomy', off || !P.showDr || !P.showBalance);
    P.paintCover();
    P.applySplit();
    P.applyCols();
    P.drWin.textContent = K.dr.windowLabel(K.drEstimate.windowSeconds).replace(' minutes', ' min').replace(' minute', ' min');
};

P.setMode = mode => {
    K.setLevelMode(mode);
    P.applyLayout();
    P.syncMode();           // spectrum needs the FFT stream; off needs no stream at all
    if (P.menuPaint) P.menuPaint();
};

// The View menu: the level display (one of) and the switches (any of).  It stays open
// for another pick; a tap outside it, or leaving the page, closes it.
P.openViewMenu = () => {
    if (P.menu) { P.closeViewMenu(); return; }
    const item = (on, text, fn, radio = false) => h('button', {
        type: 'button', class: 'menu-item', role: radio ? 'menuitemradio' : 'menuitemcheckbox', 'aria-checked': String(on),
        onclick: () => { fn(); paint(); K.showBar(true); },   // picking keeps the bar up
    }, h('span', { class: 'mk' }, on ? (radio ? '●' : '✓') : ''), text);
    const menu = P.menu = h('div', { class: 'menu-pop', role: 'menu' });
    const paint = () => K.clear(menu).append(
        ...MODES.map(m => item(P.mode === m, MODE_LABEL[m], () => P.setMode(m), true)),
        h('div', { class: 'menu-sep' }),
        item(P.coverWanted(), 'Album cover', () => P.flipCover()),
        item(P.showDr, 'Dynamic range (DR)', () => P.flip('now.dr')),
        item(P.showBalance, 'Balance', () => P.flip('now.balance')));
    paint();
    P.menuPaint = paint;
    document.body.append(menu);
    const r = P.modeBtn.getBoundingClientRect();
    menu.style.top = `${r.bottom + 4}px`;
    menu.style.right = `${Math.max(8, innerWidth - r.right)}px`;
    P.menuOutside = e => { if (!menu.contains(e.target) && e.target !== P.modeBtn) P.closeViewMenu(); };
    document.addEventListener('pointerdown', P.menuOutside, true);
    K.onBarHidden = P.closeViewMenu;
};
P.closeViewMenu = () => {
    if (!P.menu) return;
    document.removeEventListener('pointerdown', P.menuOutside, true);
    P.menu.remove();
    P.menu = P.menuPaint = null;
};

P.cycleWindow = ev => {
    if (ev) ev.stopPropagation();          // the chip is not the shortcut
    const cur = WINDOWS.indexOf(K.drEstimate.windowSeconds);
    K.drEstimate.setWindow(WINDOWS[(cur + 1) % WINDOWS.length]);
    P.applyLayout();
};

// The DR estimator listens only while DR is on: turning it off closes its stream.
// The server keeps DR history only while a listener is connected, so leaving Now
// must not close the stream at once: it stays open for P.keepMinutes() (Config),
// without repainting while away, and closes as soon as DR is switched off.
P.syncDr = () => {
    if (P.showDr && !P.drSub) P.drSub = K.drEstimate.listen(E => { if (P.visible && !document.hidden) P.paintDr(E); });
    else if (!P.showDr) P.releaseDr();
};
P.releaseDr = () => {
    clearTimeout(P.drKeep); P.drKeep = null;
    if (P.drAsk) { P.drAsk.close(false); P.drAsk = null; }
    if (P.drSub) { P.drSub.close(); P.drSub = null; }
};

// A top-bar shortcut: flip the remembered switch, then start or stop what feeds the card.
P.flip = key => {
    K.setPref(key, !K.pref(key, true));
    P.applyLayout();
    if (key === 'now.balance') P.balance.clear();
    P.syncMode();       // level stream: needed by the meters or by Balance, else closed
    P.syncDr();         // DR stream: open only while DR is on
};

// ── level stream ─────────────────────────────────────────────────────────────
P.openLevel = () => {
    if (P.level) { P.level.close(); P.level = null; }
    // Balance is computed from the level frames, so with the meters off the level stream runs
    // only while Balance itself is switched on; with both off, nothing is opened at all.
    if (P.mode === 'off' && !P.showBalance) return;
    P.vu.snap({}); P.spec.clear(); P.balance.clear();
    P.vu.setSuspects({});
    P.modeBtn.title = K.state.spectrum.enabled ? '' : 'analyzer disabled';
    // Bars/needles need only the RMS/peak reader (no FFT); the spectrum mode
    // asks for the full frame, which carries the same levels.
    const stream = P.mode === 'spectrum' ? 'music-clip' : P.mode === 'off' ? 'vu' : 'vu-clip';
    P.level = K.streams.open(stream, d => {
        if (d.ok && d.state === 'running') {
            const v = d.vu || {};
            const pk = Math.max(Number(v.left_peak ?? -120), Number(v.right_peak ?? -120));
            if (pk > -60) K.markSound();
            if (K.sync) K.sync.onLevel(pk);
            if (P.mode !== 'off') P.vu.update(d.vu);
            P.vu.setSuspects(P.mode === 'off' ? {} : {
                left: Number(v.left_peak) >= -1, right: Number(v.right_peak) >= -1,
            });
            let newClip = false;
            for (const ch of ['left', 'right']) {
                const signal = P.mode !== 'off' && !!v[`${ch}_clip`];
                // One hit per detected episode, not one for every overlapping
                // 25 Hz meter frame containing the same clipped samples.
                if (signal && !P.clipSignal[ch]) {
                    P.clipChannels[ch] += 1;
                    newClip = true;
                }
                P.clipSignal[ch] = signal;
            }
            if (newClip) {
                P.vu.setClips(P.clipChannels);
            }
            if (P.showBalance) P.balance.update(d.vu);
            if (P.mode === 'spectrum') P.spec.update(d);
            P.modeBtn.title = [d.source_label, d.rate ? `${d.rate} Hz` : ''].filter(Boolean).join(' · ');
        } else {
            if (P.mode !== 'off') P.vu.snap({});
            P.vu.setSuspects({});
            P.spec.clear(); P.balance.clear();
            P.modeBtn.title = d.state === 'idle' || !d.error ? (d.state || '') : d.error;
        }
    });
};

P.applySplit = () => { P.mainBox.style.flex = ''; };

// One keep-alive, dr.keepMinutes (2, 5 or 10 min; anything else is 5), for both ways
// of not looking at Now: another page, or the app in the background (core.js keeps the
// DR stream through a hidden page).  At the end, in front: a dialog asks; in the
// background: it just stops.  Coming back to Now in time cancels it and the bar is
// repainted with what arrived meanwhile.
P.keepMinutes = () => { const m = Number(K.pref('dr.keepMinutes', 5)); return [2, 5, 10].includes(m) ? m : 5; };
document.addEventListener('visibilitychange', () => {
    // back in front on Now after the background keep-alive ran out: start again
    if (!document.hidden && P.visible && !P.drSub) { P.syncDr(); return; }
    if (!P.drSub) return;
    clearTimeout(P.drKeep); P.drKeep = null;
    if (P.drAsk) { const a = P.drAsk; P.drAsk = null; a.close(true); }
    if (document.hidden) {
        P.drKeep = setTimeout(P.askKeepDr, P.keepMinutes() * 60000);
    } else if (P.visible) {
        P.paintDr(K.drEstimate);                  // on Now: no deadline
    } else {
        P.drKeep = setTimeout(P.askKeepDr, P.keepMinutes() * 60000);   // another page: start over
    }
});

// The keep-alive ran out while away from Now: ask, and stop unless told otherwise
// within a minute.  Keep = another period; Stop = close the stream (the history
// starts over next time; the DR switch itself stays on).
P.askKeepDr = async () => {
    P.drKeep = null;
    if (!P.drSub || (P.visible && !document.hidden)) return;
    // Not in front (another app, screen off): nobody to ask - just stop.
    if (document.hidden) { P.releaseDr(); return; }
    const minutes = P.keepMinutes();
    P.drAsk = K.confirm({
        title: 'Keep estimating DR?',
        message: `The dynamic range estimate has kept running for ${minutes} min since you left Now playing. Keep collecting it, or stop and let the history start over?`,
        ok: 'Keep estimating', cancel: 'Stop estimate', timeoutS: 60,
    });
    const keep = await P.drAsk;
    if (!P.drAsk) return;                       // closed by coming back to Now, or released
    P.drAsk = null;
    if (keep) P.drKeep = setTimeout(P.askKeepDr, minutes * 60000);
    else { P.releaseDr(); K.toast('DR estimate stopped'); }
};

// ── meters / side column splitter ────────────────────────────────────────────
// c = the meters' share of the width.  Unset: the stylesheet's default columns.
P.applyCols = () => {
    const c = K.pref('now.col', null);
    // upright the columns stack (kiosk.css): a width dragged in landscape must not squeeze the meters
    const sideBySide = !P.side.hidden && !P.el.firstChild.classList.contains('bal-below') && !K.portrait();
    if (P.coverMode === 'square' && sideBySide && typeof c !== 'number' && !K.portrait()) {
        // the cover's column is as wide as the row is tall (a square), at most 62% of the width
        const w = Math.round(Math.min(P.mainBox.clientHeight, P.mainBox.clientWidth * 0.62));
        if (w > 0) { P.mainBox.style.gridTemplateColumns = `minmax(0, ${w}px) minmax(0, 1fr)`; return; }
    }
    P.mainBox.style.gridTemplateColumns = sideBySide && typeof c === 'number'
        ? `minmax(0, ${c}fr) minmax(0, ${1 - c}fr)` : '';
};
P.wireVSplit = () => {
    const sp = P.vsplit;
    let drag = null;
    sp.addEventListener('pointerdown', e => {
        e.preventDefault(); e.stopPropagation();
        const r = P.mainBox.getBoundingClientRect();
        drag = { id: e.pointerId, left: r.left, width: r.width };
        try { sp.setPointerCapture(e.pointerId); } catch {}
        sp.classList.add('active');
    });
    sp.addEventListener('pointermove', e => {
        if (!drag || e.pointerId !== drag.id) return;
        K.setPref('now.col', K.clamp((e.clientX - drag.left) / drag.width, 0.35, 0.8));
        P.applyCols();
    });
    const end = e => { if (drag && e.pointerId === drag.id) { drag = null; sp.classList.remove('active'); P.hintReset(); } };
    sp.addEventListener('pointerup', end);
    sp.addEventListener('pointercancel', end);
};

// After a drag, remind once in a while how to undo it.
P.hintReset = () => {
    const now = Date.now();
    if (P.hintAt && now - P.hintAt < 30000) return;
    P.hintAt = now;
    K.toast('Double-tap the meters to restore the default layout');
};

// Double tap on the meters: both splitters back to their defaults.
P.resetSplits = () => {
    K.setPref('now.split', null); K.setPref('now.col', null);
    P.applySplit(); P.applyCols();
    K.toast('Layout restored');
};
P.wireResetTap = () => {
    let last = null;
    P.leftCell.addEventListener('pointerup', e => {
        const now = performance.now();
        if (last && now - last.t < 350 && Math.hypot(e.clientX - last.x, e.clientY - last.y) < 30) {
            last = null; P.resetSplits(); return;
        }
        last = { t: now, x: e.clientX, y: e.clientY };
    });
};

// A single tap on the meters swaps needles and bars; it waits out a double tap (which
// resets the splits, above) and is not a tap once the finger has moved (a swipe, the
// cover's diagonal drag).
P.wireMeterTap = () => {
    let down = null, timer = null;
    P.lvlBody.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
    P.lvlBody.addEventListener('pointerup', e => {
        const d = down; down = null;
        if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 10) return;
        if (P.mode !== 'needles' && P.mode !== 'bars') return;
        if (timer) { clearTimeout(timer); timer = null; return; }          // the second of a double tap
        timer = setTimeout(() => { timer = null; P.setMode(P.mode === 'needles' ? 'bars' : 'needles'); }, 360);
    });
};

// Level stream and chain poll follow the chosen display: exactly one is running.
P.syncMode = () => {
    P.openLevel();
    P.syncChain();
};
P.syncChain = () => {
    if (P.mode === 'off' && P.coverMode !== 'square') P.chainPoll.start(); else P.chainPoll.stop();
};

P.pollChain = async () => {
    const d = await K.api('/audio/chain');
    K.chainFlow.paint(P.chainHost, d);
    P.chainSummary.textContent = d && d.ok ? (d.summary || '') : '';
};

// ── DR ───────────────────────────────────────────────────────────────────────
// Per song (the window restarts at each track change) or continuous (a rolling
// window across tracks): the same setting as "Detect song change" on the DR page.
P.drModeBox = () => {
    const radio = (detect, text) => h('button', {
        type: 'button', class: 'dr-radio', role: 'radio', dataset: { detect: String(detect) },
        onclick: ev => { ev.stopPropagation(); K.drEstimate.setDetect(detect); P.paintDrMode(); },
    }, text);
    P.drModeEl = h('span', { class: 'dr-mode', role: 'radiogroup', 'aria-label': 'DR window' },
        radio(true, 'Per song'), radio(false, 'Continuous'));
    P.drModeEl.addEventListener('click', ev => ev.stopPropagation());   // not the "open DR page" shortcut
    return P.drModeEl;
};
P.paintDrMode = () => {
    if (!P.drModeEl) return;
    P.drModeEl.querySelectorAll('.dr-radio').forEach(b => {
        const on = String(K.drEstimate.detect) === b.dataset.detect;
        b.classList.toggle('on', on);
        b.setAttribute('aria-checked', String(on));
    });
};

P.paintDr = E => {
    P.paintDrMode();
    const s = E.summary();
    P.drValue.textContent = s.value === null ? '—' : (s.value > 14 ? 'DR14+' : `DR${s.value}`);
    P.drValue.style.color = s.value === null ? '' : K.dr.color(s.value).bg;
    P.drStatus.textContent = s.value === null ? s.short : `${s.sampled}s sampled`;
    P.gauge.set(s.value === null ? null : s.value);
};

// The play/pause/stop chip: a line icon (widgets/icons.js) and the state in words.
P.paintState = state => {
    const [icon, word] = { play: ['play', 'Playing'], pause: ['pause', 'Paused'], stop: ['stop', 'Stopped'] }[state] || ['play', 'Play'];
    K.clear(P.state).append(K.tIcon(icon), h('span', {}, word));
};

// ── track ────────────────────────────────────────────────────────────────────
P.pollTrack = async () => {
    const t = await K.fetchTrack();
    P.observeClipAlbum(t);
    if (P.track && t.ok && P.track.title !== t.title && K.sync) K.sync.onTrackChange();
    P.track = t;
    P.base = { elapsed: t.elapsed, duration: t.duration, at: performance.now(), playing: t.state === 'play' };
    if (t.state === 'play' && !P.level) K.markSound();     // no level stream to listen to: trust the player
    const title = t.ok ? (t.title || '—') : 'Nothing playing';
    if (P.titleText.textContent !== title) {
        P.titleText.textContent = title;
        P.t1.classList.remove('reading'); P.t1.scrollLeft = 0;
    }
    P.t1.title = title;
    P.t1.classList.toggle('idle', !t.ok);
    P.t2.textContent = [t.artist, [t.album, t.edition].filter(Boolean).join(' · ')].filter(Boolean).join(' — ');
    P.pArtist.textContent = t.artist || '';
    P.pAlbum.textContent = [t.album, t.edition].filter(Boolean).join(' · ');
    P.fmt.textContent = P.shortFormat(t.format);
    P.fmt.style.color = K.formatColor(t.format);
    P.paintState(t.state);
    P.state.className = 'chip state ' + t.state;
    if (t.art) { if (P.art.getAttribute('src') !== t.art) { P.art.hidden = true; P.art.setAttribute('src', t.art); } }
    else { P.art.hidden = true; P.art.removeAttribute('src'); P.artBox.classList.add('empty'); }
    if ((t.art || '') !== P.artUrl) P.setArt(t.art || '');
    P.infoBtn.hidden = !t.qobuz_album;
    if (P.awardsFor !== (t.qobuz_album || '')) {
        P.awardsFor = t.qobuz_album || '';
        K.clear(P.awards);
        if (P.awardsFor) P.awards.append(K.awardsBox({ id: P.awardsFor, title: t.album || t.title, artist: t.artist }));
    }
    P.paintIdle();
    P.paintTime();
};

// Stopped (or nothing at all) with no cover: no empty cover and no ⏮ Stopped ⏭ row,
// the space goes to the rest (kiosk.css).
P.paintIdle = () => {
    const t = P.track;
    P.el.firstChild.classList.toggle('idle', P.artBox.classList.contains('empty') && (!t || !t.ok || t.state === 'stop'));
};

// ── cover art ────────────────────────────────────────────────────────────────
// Off by default ("Art" in the top bar).  Off, or on with no cover for the track,
// the page is exactly the layout without it.  On:
//  - with meters: the cover fills the level area behind them, anchored at its top
//    left and slid so the cover's visual weight is in view (widgets/cover.js); the
//    meters are drawn see-through on top.  A diagonal drag on the meters sets how
//    see-through: towards the bottom right more opaque, up to hiding the cover.
//  - level display off: the whole cover, square, where the audio chain would be;
//    the splitters work as with the meters.
const GLASS_MIN = 0.1, GLASS_DEFAULT = 0.55;
P.artUrl = ''; P.artReady = false; P.focal = null; P.coverMode = '';
P.coverWanted = () => !!K.pref('now.cover', false);
P.glass = () => K.clamp(Number(K.pref('now.coverGlass', GLASS_DEFAULT)), GLASS_MIN, 1);

P.flipCover = () => {
    K.setPref('now.cover', !P.coverWanted());
    if (P.coverWanted() && P.artUrl && !P.artReady) P.measureArt();
    P.coverChanged();
    if (P.coverWanted() && !P.artUrl) K.toast('No cover for this track: it shows when one is available');
};

// A new cover.  Only remembered while the feature is off: nothing is measured and
// the layout is not touched.  On, it is measured before it is shown, so it never
// jumps into place.
P.setArt = url => {
    P.artUrl = url; P.artReady = false; P.focal = null;
    if (P.coverWanted()) P.measureArt();
};
P.measureArt = () => {
    const url = P.artUrl;
    if (P.coverMode) P.coverChanged();         // the previous cover goes at once
    if (!url) return;
    K.cover.analyze(url).then(f => {
        if (P.artUrl !== url) return;              // the track changed meanwhile
        P.focal = f; P.artReady = !!f;
        P.coverChanged();
    });
};

P.coverChanged = () => {
    if (!P.el) return;
    const was = P.coverMode;
    P.applyLayout();
    if ((was === 'square') !== (P.coverMode === 'square') && P.visible) P.syncChain();
};

P.paintCover = () => {
    const behind = P.coverMode === 'behind';
    P.levelBox.classList.toggle('has-cover', behind);
    P.coverLayer.hidden = P.coverScrim.hidden = !behind;
    if (P.coverMode === 'square') { if (P.coverSqImg.getAttribute('src') !== P.artUrl) P.coverSqImg.src = P.artUrl; }
    else P.coverSqImg.removeAttribute('src');
    if (!behind) { if (P.vu && P.vu.glass !== 1) { P.vu.setGlass(1); P.vu.setBackdrop(null); } return; }
    P.coverLayer.style.backgroundImage = `url("${P.artUrl}")`;
    P.placeCover();
    P.applyGlass(P.glass());
};

P.placeCover = () => {
    if (P.coverMode !== 'behind') return;
    const W = P.lvlBody.clientWidth, H = P.lvlBody.clientHeight;
    if (!W || !H) return;
    const p = K.cover.place(W, H, P.focal);
    P.coverLayer.style.backgroundSize = `${p.size}px ${p.size}px`;
    P.coverLayer.style.backgroundPosition = `${p.x}px ${p.y}px`;
    P.coverPlace = p;
    P.applyBackdrop();
};

// How bright the cover is behind each meter (from the analysis' 48x48 brightness grid),
// under the dark veil and the face at the current opacity: the meters pick their ink
// from it, light on dark and dark on light.
P.coverLumUnder = el => {
    const f = P.focal, p = P.coverPlace;
    if (!f || !f.lum || !p) return 0;
    const box = P.lvlBody.getBoundingClientRect(), r = el.getBoundingClientRect();
    const n = f.n, cell = p.size / n;
    const x0 = r.left - box.left - p.x, y0 = r.top - box.top - p.y;
    let sum = 0, cnt = 0;
    // the part of the face the scale and needle cross: the middle band
    for (let y = y0 + r.height * .15; y < y0 + r.height * .95; y += cell) {
        for (let x = x0 + r.width * .08; x < x0 + r.width * .92; x += cell) {
            const i = Math.floor(x / cell), j = Math.floor(y / cell);
            if (i >= 0 && j >= 0 && i < n && j < n) { sum += f.lum[j * n + i]; cnt++; }
        }
    }
    return cnt ? sum / cnt : 0;
};
P.applyBackdrop = (live) => {
    if (!P.vu || P.coverMode !== 'behind') return;
    const a = live !== undefined ? live : P.glass(), FACE = .012;               // the face's own black, linear
    P.vu.setBackdrop(P.vu.canvases.map(c =>
        P.coverLumUnder(c) * (1 - a ** 1.5) * (1 - a) + FACE * a));
};

// The meters' faces at `a`, and a dark veil over the cover that closes as `a` nears 1,
// so at the top of the range the cover is gone.
P.applyGlass = a => {
    P.vu.setGlass(a);
    P.applyBackdrop(a);
    P.coverScrim.style.opacity = String(a ** 1.5);
};

P.wireCoverGesture = () => {
    let g = null;
    const el = P.lvlBody;
    el.addEventListener('pointerdown', e => {
        if (P.coverMode !== 'behind') return;
        g = { id: e.pointerId, x: e.clientX, y: e.clientY, a: P.glass(), on: false };
    });
    el.addEventListener('pointermove', e => {
        if (!g || e.pointerId !== g.id) return;
        const dx = e.clientX - g.x, dy = e.clientY - g.y;
        if (!g.on) {
            // only a diagonal (top left <-> bottom right): a horizontal swipe is the pager's
            const r = Math.abs(dx) / Math.max(1, Math.abs(dy));
            if (Math.abs(dx) < 12 || Math.abs(dy) < 12) return;
            if (Math.sign(dx) !== Math.sign(dy) || r < 0.5 || r > 1.4) { g = null; return; }
            g.on = true;
            try { el.setPointerCapture(e.pointerId); } catch {}
            try { window.OmdrcApp && window.OmdrcApp.setPageScrolled(true); } catch {}
        }
        const diag = Math.hypot(el.clientWidth, el.clientHeight) * 0.6;
        const a = K.clamp(g.a + (dx + dy) / Math.SQRT2 / diag, GLASS_MIN, 1);
        g.last = a;
        P.applyGlass(a);
        clearTimeout(P.readoutTimer);
        P.coverReadout.hidden = false;
        P.coverReadout.textContent = a >= 0.99 ? 'Meters opaque · cover hidden' : `Meters ${Math.round(a * 100)} %`;
    });
    const end = e => {
        if (!g || e.pointerId !== g.id) return;
        if (g.on && g.last !== undefined) K.setPref('now.coverGlass', g.last);
        if (g.on) try { window.OmdrcApp && window.OmdrcApp.setPageScrolled(false); } catch {}
        g = null;
        P.readoutTimer = setTimeout(() => { P.coverReadout.hidden = true; }, 900);
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
};

// ── transport: tap = play/pause, hold = stop ────────────────────────────────
P.transport = async action => {
    if (['play', 'pause', 'stop'].includes(action)) P.paintState(action);              // optimistic; the next poll confirms
    const d = await K.api('/k/api/transport', { json: { action } });
    if (!d.ok) K.toast(d.error || `${action} failed`, 'error');
    [700, 2500].forEach(ms => setTimeout(() => { if (P.trackPoll.running) P.trackPoll.now(); }, ms));
};
P.wireTransport = () => {
    let timer = null, held = false;
    const cancel = () => { clearTimeout(timer); timer = null; };
    P.state.addEventListener('pointerdown', () => {
        held = false; cancel();
        timer = setTimeout(() => { held = true; timer = null; P.transport('stop'); K.toast('Stopped'); }, 650);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(t => P.state.addEventListener(t, cancel));
    P.state.addEventListener('click', () => {
        if (held) { held = false; return; }            // the long press already stopped it
        if (!P.track || !P.track.ok) { P.transport('play'); return; }
        P.transport(P.track.state === 'play' ? 'pause' : 'play');
    });
    P.state.addEventListener('contextmenu', e => e.preventDefault());   // long-press menu on touch browsers
};

// ── seek ring (upright only): widgets/seekring.js ─────────────────────────────
P.ringUsable = () => K.portrait() && !!P.base && Number.isFinite(P.base.duration) && P.base.duration > 0;
P.seek = async seconds => {
    seconds = Math.floor(seconds);
    P.base = { ...P.base, elapsed: seconds, at: performance.now() };   // optimistic; the next poll confirms
    P.paintTime();
    const d = await K.api('/k/api/transport', { json: { action: 'seek', seconds } });
    if (!d.ok) K.toast(d.error || 'seek failed', 'error');
    setTimeout(() => { if (P.trackPoll.running) P.trackPoll.now(); }, 700);
};

// "24 bit / 192 kHz / stereo" -> "24/192" (bits/kHz; stereo is the norm and is left out)
P.shortFormat = line => {
    const m = String(line || '').match(/(\d+)\s*bit.*?([\d.]+)\s*kHz/i);
    return m ? `${m[1]}/${+parseFloat(m[2]).toFixed(1)}` : String(line || '').replace(/\s*\/?\s*stereo/i, '');
};

P.paintTime = () => {
    const b = P.base;
    if (!b || !Number.isFinite(b.elapsed) || !Number.isFinite(b.duration) || b.duration <= 0) {
        P.time.textContent = ''; P.prog.style.width = '0%'; return;
    }
    const e = P.elapsedNow();
    P.time.textContent = `${K.fmtClock(e)} / ${K.fmtClock(b.duration)}`;
    P.prog.style.width = `${(e / b.duration * 100).toFixed(1)}%`;
};
P.elapsedNow = () => {
    const b = P.base;
    return K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration);
};

// ── the DRC line ─────────────────────────────────────────────────────────────
P.paintDrc = () => {
    if (!P.drcLine) return;
    const s = K.drcState.summary();
    const chips = [];
    const chip = (cls, text, title) => chips.push(h('span', { class: 'chip ' + cls, title: title || null }, text));
    if (!s.known) chip('warn', s.text);
    else {
        chip(s.power === 'on' ? 'ok' : s.power === 'off' ? 'off' : 'warn',
            s.power === 'on' ? 'DRC ON' : s.power === 'off' ? 'DRC OFF · direct' : 'DRC STOPPED');
        if (s.running) {
            if (s.rate) chip('', K.fmtRate(s.rate));
            if (s.geometry) chip('', s.geometry);
            if (s.design) chip('', s.design);
            if (s.attenuation !== null) chip('', `−${s.attenuation.toFixed(1)} dB`, 'attenuation');
            chip(s.verification === 'verified' ? 'ok' : s.verification === 'mismatch' ? 'bad' : 'warn',
                s.verification === 'verified' ? '✓ verified' : s.verification === 'mismatch' ? '✗ mismatch' : s.verification, s.message);
        } else if (s.session && s.session.geometry) {
            chip('dim', `saved: ${s.session.geometry} · ${s.design}${s.session.rate ? ' · ' + K.fmtRate(Number(s.session.rate)) : ''}`);
        }
    }
    K.clear(P.drcLine).append(h('i', { class: 'dot ' + K.drcLedClass(s), title: s.message || s.text }), ...chips, h('span', { class: 'more' }, '›'));
};

// The Qobuz search box comes down here while this page is upright; in landscape the
// slot is hidden and the Qobuz page takes the box back when it is shown.
P.syncDock = () => {
    if (K.qobuzDock && !P.qzSlot.hidden && K.portrait()) K.qobuzDock(P.qzSlot);
};

// ── lifecycle ────────────────────────────────────────────────────────────────
P.show = () => {
    P.visible = true;
    clearTimeout(P.drKeep); P.drKeep = null;     // back before the keep-alive ran out
    if (P.drAsk) { const a = P.drAsk; P.drAsk = null; a.close(true); }   // back on Now: keep it
    P.applyLayout();
    if (P.coverWanted() && P.artUrl && !P.artReady) P.measureArt();   // switched on in Config meanwhile
    K.setTopExtra(P.topBtns);
    P.syncDock();
    P.syncMode();
    P.syncDr();
    if (P.drSub) P.paintDr(K.drEstimate);        // what was collected while away
    P.trackPoll.start();
    P.tick.start(false);
    P.paintDrc();
};

P.hide = () => {
    P.closeViewMenu();
    if (P.level) { P.level.close(); P.level = null; }
    P.vu.setSuspects({});
    P.chainPoll.stop();
    P.visible = false;
    if (P.drSub) {
        P.drKeep = setTimeout(P.askKeepDr, P.keepMinutes() * 60000);
    }
    P.trackPoll.stop();
    P.tick.stop();
};

K.registerPage(P);
})();
