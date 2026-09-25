/* Page 1 — Now: the fancy one.  Level meters (needles, bars, or bars plus
 * spectrum), the DR bar, channel balance, the track, and one line saying which
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
    mode: 'needles', showDr: true, showBalance: true,
    level: null, drSub: null, track: null, base: null,
};

P.mount = el => {
    P.el = el;
    P.art = h('img', { class: 'now-art', alt: '', hidden: true, onload: e => { e.target.hidden = false; }, onerror: e => { e.target.hidden = true; } });
    P.t1 = h('div', { class: 'now-title' }, '—');
    P.t2 = h('div', { class: 'now-sub' });
    P.fmt = h('span', { class: 'now-fmt' });
    P.state = h('button', { class: 'chip state', type: 'button', title: 'Tap: play / pause · hold: stop' });
    P.time = h('div', { class: 'now-time' });
    P.prog = h('i');
    const trackBox = h('div', { class: 'now-track' }, P.art,
        h('div', { class: 'now-meta' }, P.t1, h('div', { class: 'now-subrow' }, P.t2, P.fmt)),
        // play/pause/stop chip and the small time sit above the progress bar, at the right
        h('div', { class: 'now-timebox' }, P.state, P.time, h('div', { class: 'now-prog' }, P.prog)));

    // level area
    P.meterHost = h('div', { class: 'lvl-meter' });
    P.specCanvas = h('canvas', { class: 'lvl-spectrum' });
    // these live in the top bar while this page is showing (see show()).  DR and Bal are
    // shortcuts for the remembered switches (the DR page's Estimate, Config's Balance): off
    // hides the card and also stops what feeds it.
    P.modeBtn = h('button', { class: 'chip lvl-mode', type: 'button', onclick: () => P.cycleMode() });
    P.drToggle = h('button', { class: 'chip tog', type: 'button', title: 'Dynamic range estimate on / off', onclick: () => P.flip('now.dr') }, 'DR');
    P.balToggle = h('button', { class: 'chip tog', type: 'button', title: 'Balance on / off', onclick: () => P.flip('now.balance') }, 'Bal');
    P.topBtns = h('span', { class: 'top-toggles' }, P.drToggle, P.balToggle, P.modeBtn);
    P.levelBox = h('div', { class: 'now-level' },
        h('div', { class: 'lvl-body' }, P.meterHost, P.specCanvas));

    // With the level display off, the audio chain takes the meters' place: no
    // analyzer stream is opened for it, only a light poll of /audio/chain.
    P.chainHost = h('div', { class: 'cf' });
    P.chainSummary = h('span', { class: 'cf-summary' });
    P.chainBox = h('div', { class: 'now-chain' },
        h('div', { class: 'cf-head' }, h('span', { class: 'lbl' }, 'Audio chain'), P.chainSummary), P.chainHost);
    P.leftCell = h('div', { class: 'now-left' }, P.levelBox, P.chainBox);

    // DR block
    P.drValue = h('strong', { class: 'dr-value' }, '—');
    P.drStatus = h('span', { class: 'dr-status' });
    P.drWin = h('button', { class: 'chip', type: 'button', onclick: ev => P.cycleWindow(ev) });
    P.drGaugeHost = h('div', { class: 'drg' });
    P.drBarHost = h('div', { class: 'dr-bar' });
    P.drDetail = h('div', { class: 'dr-detail', hidden: true });
    P.drOldest = h('span', {});
    // value + gauge live in the side column; the segmented history is a
    // full-width strip along the bottom, where finer segments fit
    P.drBox = h('div', { class: 'now-dr card' },
        h('div', { class: 'dr-head' }, h('span', { class: 'lbl' }, 'DR'), P.drValue, P.drStatus, P.drWin), P.drGaugeHost);
    P.drBarBox = h('div', { class: 'now-drbar card' }, P.drBarHost,
        h('div', { class: 'dr-times' }, P.drOldest, P.drDetail, h('span', {}, 'Latest')));
    P.gauge = K.drGauge(P.drGaugeHost);
    // On Now the bar is a shortcut, not a control: touching it opens the DR page
    // (Estimate / Measure), where its segments can be inspected.
    P.drBar = new K.DrBar(P.drBarHost, { interactive: false, onDetail: (text, count) => {
        P.drDetail.hidden = !text; P.drDetail.textContent = text || '';
        P.drOldest.textContent = count ? `−${K.dr.elapsedLabel(count * 3)}` : '';
    } });
    [P.drBox, P.drBarBox].forEach(el => { el.classList.add('tap'); el.addEventListener('click', () => K.goto('dr')); });

    P.balHost = h('div', { class: 'now-bal card' });
    P.balance = new K.Balance(P.balHost);
    P.side = h('div', { class: 'now-side' }, P.drBox, P.balHost);

    // the DRC line
    P.drcLine = h('button', { class: 'now-drc', type: 'button', onclick: () => K.goto('drc') });

    el.append(h('div', { class: 'now' }, trackBox, h('div', { class: 'now-main' }, P.leftCell, P.side), P.drBarBox, P.drcLine));
    P.vu = new K.VuMeter(P.meterHost, 'needles');
    P.spec = new K.Spectrum(P.specCanvas);

    P.wireTransport();
    P.chainPoll = new K.Poller(P.pollChain, 4000);
    P.trackPoll = new K.Poller(P.pollTrack, 3000);
    P.tick = new K.Poller(P.paintTime, 1000);
    K.drcState.onChange(P.paintDrc);
};

// ── layout ───────────────────────────────────────────────────────────────────
P.applyLayout = () => {
    P.mode = K.pref('now.level', 'needles');
    if (!MODES.includes(P.mode)) P.mode = 'needles';
    P.showDr = K.pref('now.dr', true);
    P.showBalance = K.pref('now.balance', true);
    const off = P.mode === 'off';
    P.levelBox.className = `now-level mode-${P.mode}`;
    P.levelBox.hidden = off;
    P.chainBox.hidden = !off;
    P.modeBtn.textContent = MODE_LABEL[P.mode];
    if (!off) P.vu.setMode(P.mode === 'needles' ? 'needles' : 'bars');
    P.drToggle.classList.toggle('on', P.showDr);
    P.balToggle.classList.toggle('on', P.showBalance);
    P.el.firstChild.classList.toggle('lvl-off', off);
    P.drBox.hidden = !P.showDr;
    P.drBarBox.hidden = !P.showDr;
    P.balHost.hidden = !P.showBalance;
    P.side.hidden = !P.showDr && !P.showBalance;
    P.side.classList.toggle('single', P.showDr !== P.showBalance);   // one card alone: make it bigger
    P.el.firstChild.classList.toggle('no-side', P.side.hidden);
    P.el.firstChild.classList.toggle('no-drbar', !P.showDr);
    // DR hidden but Balance on: no side column, the balance slides under the meters
    P.el.firstChild.classList.toggle('bal-below', !P.showDr && P.showBalance);
    P.drWin.textContent = K.dr.windowLabel(K.drEstimate.windowSeconds).replace(' minutes', ' min').replace(' minute', ' min');
};

P.cycleMode = () => {
    K.setPref('now.level', MODES[(MODES.indexOf(P.mode) + 1) % MODES.length]);
    P.applyLayout();
    P.syncMode();           // spectrum needs the FFT stream; off needs no stream at all
};

P.cycleWindow = ev => {
    if (ev) ev.stopPropagation();          // the chip is not the shortcut
    const cur = WINDOWS.indexOf(K.drEstimate.windowSeconds);
    K.drEstimate.setWindow(WINDOWS[(cur + 1) % WINDOWS.length]);
    P.applyLayout();
};

// The DR estimator listens only while DR is on: turning it off closes its stream.
P.syncDr = () => {
    if (P.showDr && !P.drSub) P.drSub = K.drEstimate.listen(P.paintDr);
    else if (!P.showDr && P.drSub) { P.drSub.close(); P.drSub = null; }
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
    P.modeBtn.title = K.state.spectrum.enabled ? '' : 'analyzer disabled';
    // Bars/needles need only the RMS/peak reader (no FFT); the spectrum mode
    // asks for the full frame, which carries the same levels.
    const stream = P.mode === 'spectrum' ? 'music' : 'vu';
    P.level = K.streams.open(stream, d => {
        if (d.ok && d.state === 'running') {
            if (P.mode !== 'off') P.vu.update(d.vu);
            if (P.showBalance) P.balance.update(d.vu);
            if (P.mode === 'spectrum') P.spec.update(d);
            P.modeBtn.title = [d.source_label, d.rate ? `${d.rate} Hz` : ''].filter(Boolean).join(' · ');
        } else {
            if (P.mode !== 'off') P.vu.snap({});
            P.spec.clear(); P.balance.clear();
            P.modeBtn.title = d.state === 'idle' || !d.error ? (d.state || '') : d.error;
        }
    });
};

// Level stream and chain poll follow the chosen display: exactly one is running.
P.syncMode = () => {
    P.openLevel();
    if (P.mode === 'off') P.chainPoll.start(); else P.chainPoll.stop();
};

P.pollChain = async () => {
    const d = await K.api('/audio/chain');
    K.chainFlow.paint(P.chainHost, d);
    P.chainSummary.textContent = d && d.ok ? (d.summary || '') : '';
};

// ── DR ───────────────────────────────────────────────────────────────────────
P.paintDr = E => {
    const s = E.summary();
    P.drValue.textContent = s.value === null ? '—' : (s.value > 14 ? 'DR14+' : `DR${s.value}`);
    P.drValue.style.color = s.value === null ? '' : K.dr.color(s.value).bg;
    P.drStatus.textContent = s.value === null ? s.short : `${s.sampled}s sampled`;
    P.gauge.set(s.value === null ? null : s.value);
    P.drBar.render(E.selected(), E.windowSeconds);
};

// ── track ────────────────────────────────────────────────────────────────────
P.pollTrack = async () => {
    const t = await K.fetchTrack();
    P.track = t;
    P.base = { elapsed: t.elapsed, duration: t.duration, at: performance.now(), playing: t.state === 'play' };
    P.t1.textContent = t.ok ? (t.title || '—') : 'Nothing playing';
    P.t1.classList.toggle('idle', !t.ok);
    P.t2.textContent = [t.artist, [t.album, t.edition].filter(Boolean).join(' · ')].filter(Boolean).join(' — ');
    P.fmt.textContent = P.shortFormat(t.format);
    P.fmt.style.color = K.formatColor(t.format);
    P.state.textContent = { play: '▶ Playing', pause: '❚❚ Paused', stop: '■ Stopped' }[t.state] || '▶ Play';
    P.state.className = 'chip state ' + t.state;
    if (t.art) { if (P.art.getAttribute('src') !== t.art) { P.art.hidden = true; P.art.setAttribute('src', t.art); } }
    else { P.art.hidden = true; P.art.removeAttribute('src'); }
    P.paintTime();
};

// ── transport: tap = play/pause, hold = stop ────────────────────────────────
P.transport = async action => {
    const label = { play: '▶ Playing', pause: '❚❚ Paused', stop: '■ Stopped' }[action];
    P.state.textContent = label;                         // optimistic; the next poll confirms
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
    const e = K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration);
    P.time.textContent = `${K.fmtClock(e)} / ${K.fmtClock(b.duration)}`;
    P.prog.style.width = `${(e / b.duration * 100).toFixed(1)}%`;
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

// ── lifecycle ────────────────────────────────────────────────────────────────
P.show = () => {
    P.applyLayout();
    K.setTopExtra(P.topBtns);
    P.syncMode();
    P.syncDr();
    P.trackPoll.start();
    P.tick.start(false);
    P.paintDrc();
};

P.hide = () => {
    if (P.level) { P.level.close(); P.level = null; }
    P.chainPoll.stop();
    if (P.drSub) { P.drSub.close(); P.drSub = null; }
    P.trackPoll.stop();
    P.tick.stop();
};

K.registerPage(P);
})();
