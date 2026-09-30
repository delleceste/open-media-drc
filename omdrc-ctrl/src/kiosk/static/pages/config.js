/* Page 8 — Config: how this screen behaves, and the doors to the panel's own
 * configuration pages (opened inside the kiosk, with a Close button). */
(() => {
'use strict';
const { h } = K;

const P = { id: 'config', label: 'Config', title: 'Configuration' };

P.mount = el => {
    P.el = el;
    P.left = h('div', { class: 'col' });
    P.right = h('div', { class: 'col' });
    el.append(h('div', { class: 'two-col' }, P.left, P.right));
    P.render();
};
// How to get the real Wake Lock on a plain-http address, per browser.
P.awakeTip = () => /Firefox/i.test(navigator.userAgent)
    ? `Still going to sleep? In Firefox open about:config, search for dom.securecontext.allowlist and add ${location.hostname} to it (comma-separated); then reload. The real Wake Lock then works on this http address. (If your Firefox has no about:config, use a Chrome-based browser or HTTPS.)`
    : `Still going to sleep? In Chrome open chrome://flags/#unsafely-treat-insecure-origin-as-secure, add ${location.origin}, set it to Enabled and relaunch: the browser then allows the real Wake Lock on this http address.`;

// Meter timing: this device's extra display delay, and its microphone calibration.
P.timingCard = () => {
    const network = h('p', { class: 'muted small' }, window.OmdrcTiming.status());
    const value = h('strong', { class: 'timing-value' }, `${K.sync.delayMs()} ms`);
    const timer = setInterval(() => {
        if (!network.isConnected) { clearInterval(timer); return; }
        network.textContent = window.OmdrcTiming.status();
        value.textContent = `${K.sync.delayMs()} ms`;
    }, 1000);
    const set = ms => { K.sync.setDelayMs(ms); value.textContent = `${K.sync.delayMs()} ms`; };
    const step = d => h('button', { class: 'btn step', type: 'button', onclick: () => set(K.sync.delayMs() + d) }, (d > 0 ? '+' : '−') + Math.abs(d));
    const result = h('p', { class: 'muted small' }, P.lastCal || '');
    const onMusic = async () => {
        let res = null;
        // the log is shown live while it runs, and stays up afterwards to be copied
        const sheet = P.logSheet('Calibrating…');
        const unsub = K.sync.onLog(line => sheet.append(line));
        try {
            for (let attempt = 1; attempt <= 3; attempt++) {
                if (attempt > 1) sheet.append(`\n==================== attempt ${attempt} of 3 ====================\n`);
                res = await K.sync.calibrate({ seconds: 10, onTick: left =>
                    sheet.status(`Attempt ${attempt} of 3 · listening for ${left} s — hold the phone near the speakers while the music plays`) });
                if (res.ok || /microphone|app|network name/.test(res.error || '')) break;
            }
        } finally { unsub(); }
        P.lastCal = await P.applyCal(res);
        result.textContent = P.lastCal;
        value.textContent = `${K.sync.delayMs()} ms`;
        sheet.status(P.lastCal, true);
    };
    return K.card('Meter timing',
        network,
        h('div', { class: 'btn-row' },
            window.OmdrcApp && window.OmdrcApp.identifyTimingNetwork ? h('button', { class: 'btn', onclick: () => window.OmdrcApp.identifyTimingNetwork() }, 'Identify Wi-Fi') : null,
            h('button', { class: 'btn', onclick: () => {
                const name = window.prompt('Wi-Fi profile name, or wired for a wired connection. Leave empty for automatic detection.', K.pref('sync.networkName', ''));
                if (name !== null) window.OmdrcTiming.setNetworkName(name);
            } }, 'Set network name')),
        h('p', { class: 'muted small' }, 'Wi-Fi identification requires Android location permission and Location enabled. If the name is unavailable, set a profile name manually when switching networks.'),
        h('p', { class: 'muted small' }, 'Extra delay for this screen, on top of the box’s own chain delay: the meters and spectrum are drawn this long after they arrive. Stored on this device for the active network. New networks start at 0 ms until calibrated.'),
        h('div', { class: 'att-row' }, step(-50), step(-10), value, step(10), step(50),
            h('button', { class: 'btn', type: 'button', onclick: () => set(0) }, 'Reset')),
        h('div', { class: 'btn-row' },
            h('button', { class: 'btn primary', type: 'button', onclick: () => P.tuneSheet() }, 'Tune with clicks'),
            K.sync.canCalibrate() ? h('button', { class: 'btn', type: 'button', onclick: onMusic }, 'Calibrate on the music') : null),
        h('p', { class: 'muted small' }, K.sync.canCalibrate()
            ? 'Tune with clicks shows the level bars while the box plays a train of tone bursts: the phone measures and sets the delay, then plays them again so you can see the bars land on the sound.'
            : 'Tune with clicks shows the level bars while the box plays a train of tone bursts; set the delay by eye. The OMDRC Android app can also measure it with the phone’s microphone.'),
        K.sync.canCalibrate() ? h('div', {},
            h('div', { class: 'lbl' }, 'Recalibrate automatically when a track starts or playback resumes'),
            K.segmented([{ value: true, label: 'On' }, { value: false, label: 'Off' }], !!K.pref('sync.auto', false),
                v => { K.setPref('sync.auto', v); P.render(); }),
            h('p', { class: 'muted small' }, 'On the music, for 8 s at most every two minutes, only on Now playing; a blue light blinks over the meters meanwhile, and Android shows its microphone indicator.')) : null,
        result,
        h('div', { class: 'btn-row' }, h('button', { class: 'btn', type: 'button', onclick: () => {
            const text = K.sync.lastLog();
            if (!text) { K.toast('No calibration has run on this device yet'); return; }
            P.logSheet('Last calibration log (kept on this device)').set(text);
        } }, 'Calibration log')),
        K.cardOpts({ actions: P.infoBtn() }));
};

// How the timing works and what the calibration does, with diagrams.
P.infoBtn = () => h('button', { class: 'btn info-btn', type: 'button', title: 'How meter timing works',
    'aria-label': 'How meter timing works', onclick: () => K.frame('/k/static/help/meter-timing.html', 'How meter timing works') }, 'ⓘ How it works');

// Meters that reach this screen AFTER the sound: a screen can only wait, so the
// box has to send them sooner.  It holds the frames back by the chain it measures
// minus a margin; raising the margin by the lateness (plus a little, so the frames
// land just early and this screen waits the rest) takes that much back, for every
// screen - the others were early already and simply wait a little longer.
// Returns {given, late, left}: ms taken back, ms this screen still waits, ms that
// nothing can recover (the box was holding back less than the lateness).
const TAKE_BACK_CUSHION_MS = 20;
P.takeBack = async lateMs => {
    const st = await K.api('/spectrum/settings');
    if (!st.ok) return { error: st.error || 'the box did not answer' };
    window.OmdrcTiming.updateSettings(st);
    const network = window.OmdrcTiming.network().key;
    const held = Math.round(st.drc_delay_base_ms || 0);
    const given = Math.min(held, lateMs + TAKE_BACK_CUSHION_MS);
    if (given > 0) {
        const r = await K.api('/spectrum/margin', { json: { margin_ms: (st.drc_delay_margin_ms || 0) + given } });
        if (!r.ok) return { error: r.error || 'the box refused the change' };
        window.OmdrcTiming.updateSettings(r);
        if (network !== window.OmdrcTiming.network().key) return { error: 'network changed during adjustment; calibrate again', changedNetwork: true };
    }
    return { given, wait: Math.max(0, given - lateMs), left: Math.max(0, lateMs - given) };
};

// A calibration result into the delay; the sentence that says what happened.
P.applyCal = async res => {
    if (res.ok && res.context !== window.OmdrcTiming.context()) return 'Network or server timing changed; calibrate again.';
    if (res.ok && res.lagMs >= 0) {
        K.sync.setDelayMs(res.lagMs);
        return `Calibrated: the meters led the sound by ${res.lagMs} ms (match ${res.r}); this device now waits ${res.lagMs} ms.`;
    }
    if (res.ok) {
        const late = -res.lagMs, tb = await P.takeBack(late);
        if (!tb.changedNetwork) K.sync.setDelayMs(tb.wait || 0);
        if (tb.error) return `The meters arrived ${late} ms after the sound (match ${res.r}), and the box could not be adjusted: ${tb.error}.`;
        if (!tb.given) return `The meters arrived ${late} ms after the sound (match ${res.r}). The box is not holding them back at all, so this is the network or this screen being slow: nothing can be taken back.`;
        return `The meters arrived ${late} ms after the sound (match ${res.r}): the box now sends them ${tb.given} ms sooner` +
            (tb.left ? `, all it had; the last ${tb.left} ms is the network or this screen and cannot be recovered.` : `, and this device waits ${tb.wait} ms.`);
    }
    return `Calibration failed: ${res.error}${res.r !== undefined ? ` (match ${res.r})` : ''}.`;
};

// Tuning with the click track, meters in view.  The level bars run only while this
// sheet is open.  Start: step 1 plays the clicks with the current delay, and the
// phone measures and sets the delay; step 2 plays them again with the new one,
// measured once more, with frames timed when drawn.  Check again repeats step 2.
// Without the app's microphone the clicks just play and the delay is set by eye.
const VERIFY_TOL_MS = 30;
P.tuneSheet = () => {
    const mic = K.sync.canCalibrate();
    const meterHost = h('div', { class: 'tune-meter' });
    const status = h('div', { class: 'cal-status' }, mic
        ? 'Hold the phone near the speakers at your usual volume. The box stops what is playing and plays about 11 s of quiet tone bursts (−30 dBFS).'
        : 'The box stops what is playing and plays about 11 s of quiet tone bursts (−30 dBFS). Watch the bars and adjust the delay until they flick with each click.');
    const lines = h('div', { class: 'tune-results' });
    const value = h('strong', { class: 'timing-value' }, `${K.sync.delayMs()} ms`);
    const show = () => { value.textContent = `${K.sync.delayMs()} ms`; };
    const step = d => h('button', { class: 'btn step', type: 'button', onclick: () => { K.sync.setDelayMs(K.sync.delayMs() + d); show(); } }, (d > 0 ? '+' : '−') + Math.abs(d));
    const note = text => lines.append(h('div', {}, text));
    let busy = false, closed = false;
    const startBtn = h('button', { class: 'btn primary', type: 'button', onclick: () => run(true) }, mic ? 'Start' : 'Play the clicks');
    const againBtn = h('button', { class: 'btn', type: 'button', hidden: true, onclick: () => run(false) }, 'Check again');
    const logBtn = h('button', { class: 'btn', type: 'button', hidden: !mic, onclick: () => {
        const text = K.sync.lastLog();
        if (text) P.logSheet('Last calibration log (kept on this device)').set(text); else K.toast('No log yet');
    } }, 'Log');
    const closeBtn = h('button', { class: 'btn', type: 'button', onclick: () => close() }, 'Close');
    const setBusy = b => { busy = b; startBtn.disabled = againBtn.disabled = closeBtn.disabled = b; };

    const vu = new K.VuMeter(meterHost, 'bars');
    const level = K.streams.open('vu', d => {
        if (d.ok && d.state === 'running') vu.update(d.vu); else vu.snap({});
    });

    // Each run is one or two numbered steps, and the status line always says which
    // one is running now; the lines below keep what each finished step found.
    const verify = async (label, title) => {
        status.textContent = `${title}: listening while the bursts play with ${K.sync.delayMs()} ms…`;
        const res = await K.sync.calibrate({ seconds: 14, verify: true, onTick: left =>
            status.textContent = `${title}: listening while the bursts play with ${K.sync.delayMs()} ms — ${left} s left` });
        if (!res.ok) { note(`${label}: could not measure (${res.error}).`); return res; }
        const off = res.lagMs, spread = res.spreadMs ? ` (clicks ${res.spreadMs.join(' to ')} ms)` : '';
        note(Math.abs(off) <= VERIFY_TOL_MS
            ? `${label}: in sync — the bars land within ${Math.abs(off)} ms of the sound${spread}.`
            : `${label}: the bars are ${off > 0 ? `${off} ms ahead of` : `${-off} ms behind`} the sound${spread}.`);
        return res;
    };
    const run = async first => {
        if (busy) return;
        setBusy(true);
        try {
            if (!mic) {
                const r = await K.sync.playClicks({ onTick: left => { status.textContent = `Playing the bursts with ${K.sync.delayMs()} ms — ${left} s left`; } });
                status.textContent = r.ok ? 'Done. Adjust the delay and play again until the bars flick with each click.' : `Could not play: ${r.error}`;
                return;
            }
            if (!first) {
                const res = await verify('Check', 'Checking');
                if (res.ok && Math.abs(res.lagMs) > VERIFY_TOL_MS) {
                    if (K.sync.delayMs() + res.lagMs >= 0) {
                        K.sync.setDelayMs(K.sync.delayMs() + res.lagMs);
                        note(`Corrected to ${K.sync.delayMs()} ms — Check again to see it.`);
                    } else {
                        // later than this screen's own wait can absorb: the box sends sooner
                        const late = -(K.sync.delayMs() + res.lagMs), tb = await P.takeBack(late);
                        if (!tb.changedNetwork) K.sync.setDelayMs(tb.wait || 0);
                        note(tb.error ? `Could not adjust the box: ${tb.error}.`
                            : tb.given ? `The box now sends the meters ${tb.given} ms sooner; this device waits ${K.sync.delayMs()} ms — Check again to see it.`
                            : 'The box is not holding the meters back at all: the rest is the network or this screen.');
                    }
                    show();
                }
                status.textContent = !res.ok ? 'Check failed — the delay was not changed.'
                    : Math.abs(res.lagMs) <= VERIFY_TOL_MS ? 'Finished: in sync.' : 'Finished.';
                return;
            }
            lines.replaceChildren();
            // step 1: measure with the current delay, and set the new one
            const title1 = 'Step 1 of 2 · Measuring';
            status.textContent = `${title1}: listening while the bursts play with ${K.sync.delayMs()} ms…`;
            const res = await K.sync.calibrate({ seconds: 14, clicks: true, onTick: left =>
                status.textContent = `${title1}: listening while the bursts play with ${K.sync.delayMs()} ms — ${left} s left` });
            P.lastCal = await P.applyCal(res);
            note(`Step 1 · ${P.lastCal}`);
            show();
            if (closed) return;
            if (!res.ok) { status.textContent = 'Failed at step 1 — the delay was not changed. Start to try again, or open the Log.'; return; }
            // step 2: the same clicks with the new delay, to confirm it
            const after = await verify('Step 2', 'Step 2 of 2 · Checking');
            status.textContent = !after.ok ? 'Finished, but step 2 could not check the new delay: Check again.'
                : Math.abs(after.lagMs) <= VERIFY_TOL_MS ? 'Finished: in sync. Check again whenever you want.'
                : 'Finished, but not in sync yet: Check again re-measures and corrects.';
        } finally {
            if (!closed) { setBusy(false); againBtn.hidden = false; }
        }
    };
    // Close waits for a run to end; leaving the page (force) does not: the level
    // bars stop at once, and a run in progress finishes on its own.
    const close = (force = false) => {
        if (busy && !force) return;
        closed = true; P.tuneClose = null;
        K.holdScreen('tune', false);
        level.close(); vu.destroy(); scrim.remove();
        if (P.left && !force) P.render();
    };
    P.tuneClose = close;
    K.holdScreen('tune', true);        // the phone must not sleep in the middle of a run (main.js)
    const scrim = h('div', { class: 'scrim' }, h('div', { class: 'sheet cal-sheet tune-sheet' },
        h('div', { class: 'cal-title tune-title' }, h('span', {}, 'Meter timing'), P.infoBtn()),
        meterHost,
        h('div', { class: 'att-row' }, step(-50), step(-10), value, step(10), step(50)),
        status, lines,
        h('div', { class: 'btn-row' }, startBtn, againBtn, logBtn, closeBtn)));
    document.getElementById('overlay-root').append(scrim);
};

// A sheet with the calibration log: live while a run appends to it, selectable,
// with Copy (clipboard where the browser allows it on plain http, else a hidden
// textarea and execCommand) and Select all for a manual long-press copy.
P.logSheet = title => {
    const status = h('div', { class: 'cal-status' }, '');
    const pre = h('pre', { class: 'cal-log' });
    const copy = async () => {
        const text = pre.textContent;
        let ok = false;
        try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; } } catch {}
        if (!ok) {
            const ta = h('textarea', { class: 'cal-copy' });
            ta.value = text;
            document.body.append(ta);
            ta.focus(); ta.select();
            try { ok = document.execCommand('copy'); } catch {}
            ta.remove();
        }
        K.toast(ok ? `Log copied (${text.length} characters)` : 'Copy did not work here: use Select all, then copy');
    };
    const selectAll = () => {
        const r = document.createRange(); r.selectNodeContents(pre);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    };
    const close = () => scrim.remove();
    const scrim = h('div', { class: 'scrim' }, h('div', { class: 'sheet cal-sheet' },
        h('div', { class: 'cal-title' }, title), status, pre,
        h('div', { class: 'btn-row' },
            h('button', { class: 'btn primary', type: 'button', onclick: copy }, 'Copy'),
            h('button', { class: 'btn', type: 'button', onclick: selectAll }, 'Select all'),
            h('button', { class: 'btn', type: 'button', onclick: close }, 'Close'))));
    document.getElementById('overlay-root').append(scrim);
    return {
        append(line) {
            const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
            pre.append(line + '\n');
            if (atEnd) pre.scrollTop = pre.scrollHeight;
        },
        set(text) { pre.textContent = text; },
        status(text, done = false) { status.textContent = text; status.classList.toggle('done', done); },
        close,
    };
};

const LEVEL_OPTIONS = [{ value: 'needles', label: 'Needles' }, { value: 'bars', label: 'Bars' }, { value: 'spectrum', label: 'Bars + spectrum' }, { value: 'off', label: 'Off' }];

P.render = function render() {
    if (!P.left) return;
    const opt = (key, dflt, options) => K.segmented(options, K.pref(key, dflt), v => { K.setPref(key, v); P.render(); K.applyPrefs && K.applyPrefs(); });

    K.clear(P.left).append(...[      // (append() would print a null as "null")
        window.OmdrcApp ? K.card('Android app',
            h('p', { class: 'muted small' }, 'View (kiosk or full web page), keeping the screen on while “Now playing” is showing, hiding the Android bars, and the server address are set in the app’s own settings.'),
            h('button', { class: 'btn', type: 'button', onclick: () => window.OmdrcApp.openSettings() }, 'App settings')) : null,
        K.card('Theme',
            K.segmented([{ value: 'auto', label: 'Automatic' }, { value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }],
                K.themeChoice(), v => { K.setTheme(v); P.render(); }),
            h('p', { class: 'muted small' }, 'Automatic follows this device’s own light or dark setting, and switches when it does. Kept for this screen only, so the panel behind the amplifier can stay dark whatever its system says.')),
        K.card('Now page',
            // one choice per orientation (pages/now.js): upright bars, landscape needles by default
            ...[[false, 'Level display in landscape'], [true, 'Level display upright']].map(([portrait, title]) => [
                h('div', { class: 'lbl' }, title),
                K.segmented(LEVEL_OPTIONS, K.levelMode(portrait), v => { K.setLevelMode(v, portrait); P.render(); K.applyPrefs && K.applyPrefs(); }),
            ]).flat(),
            [K.levelMode(false), K.levelMode(true)].includes('off') ? h('p', { class: 'muted small' }, 'Off keeps the meters idle: the Now page shows the audio chain instead, and the level stream is opened only while Balance is switched on.') : null,
            h('div', { class: 'lbl' }, 'Keep the DR estimate running when Now is not shown'),
            K.segmented([{ value: 2, label: '2 min' }, { value: 5, label: '5 min' }, { value: 10, label: '10 min' }],
                [2, 5, 10].includes(Number(K.pref('dr.keepMinutes', 5))) ? Number(K.pref('dr.keepMinutes', 5)) : 5,
                v => { K.setPref('dr.keepMinutes', v); P.render(); }),
            h('p', { class: 'muted small' }, 'On another page or with the app in the background. Then: in front, a dialog asks whether to keep estimating (no answer within a minute stops it); in the background it just stops.'),
            h('div', { class: 'lbl' }, 'Balance'),
            opt('now.balance', true, [{ value: true, label: 'Show' }, { value: false, label: 'Hide' }]),
            h('p', { class: 'muted small' }, 'Everything shown on the Now page is live; anything hidden here is not computed. The DR value and bar follow the Estimate switch on the DR page.')),
        K.card('Cover art',
            h('div', { class: 'lbl' }, 'Behind the meters on Now playing'),
            opt('now.cover', false, [{ value: true, label: 'On' }, { value: false, label: 'Off' }]),
            h('p', { class: 'muted small' }, 'Same as “Art” in the top bar. The cover fills the meter area, slid so its busiest part is in view; drag diagonally on the meters to make them more or less see-through. With the level display off, the whole cover takes the audio chain’s place.'),
            h('div', { class: 'lbl' }, 'Cover page'),
            opt('cover.page', false, [{ value: true, label: 'Show' }, { value: false, label: 'Hide' }]),
            h('p', { class: 'muted small' }, 'A page after Now playing with the cover as large as the screen allows and the track beside it; DR or Lvl in its top bar adds a narrow DR or level column where there is room.')),
        P.timingCard(),
        K.card('Screen',
            h('div', { class: 'lbl' }, 'Screensaver after'),
            opt('saver.minutes', 0, [{ value: 0, label: 'Never' }, { value: 5, label: '5 min' }, { value: 15, label: '15 min' }, { value: 30, label: '30 min' }]),
            window.OmdrcApp ? null : h('div', { class: 'lbl' }, 'Keep the screen on (while Now playing is shown)'),
            window.OmdrcApp ? null : opt('awake.on', true, [{ value: true, label: 'On' }, { value: false, label: 'Off' }]),
            window.OmdrcApp ? null : h('p', { class: 'muted small' }, K.awake.enabled()
                ? `Active method: ${K.awake.mode === 'off' ? 'none yet — tap the screen once' : K.awake.mode}. Over plain http the browser only allows the video fallback, which starts on the first tap.`
                : 'The phone or panel may switch its display off on its own timeout.'),
            !window.OmdrcApp && K.awake.enabled() && K.awake.mode !== 'wake lock' ? h('p', { class: 'muted small' }, P.awakeTip()) : null,
            h('div', { class: 'btn-row' }, window.OmdrcApp ? null : h('button', { class: 'btn', type: 'button', onclick: () => K.toggleFullscreen() }, 'Toggle fullscreen'),
                h('button', { class: 'btn', type: 'button', onclick: () => location.reload() }, 'Reload kiosk')))].filter(Boolean));

    const link = (label, url) => h('button', { class: 'btn link', type: 'button', onclick: () => K.frame(url, label) }, label + ' ↗');
    K.clear(P.right).append(
        K.card('System configuration',
            h('div', { class: 'btn-col' },
                link('Configuration', '/configuration'),
                link('Bit-perfect check', '/bitperfect'),
                link('BruteFIR configuration', '/brutefir-config'),
                link('Filter response', '/filter-response'),
                K.state.features.drdb ? link('DR versions', '/dr-alternatives') : null)),
        K.card('Documentation',
            h('div', { class: 'btn-col' }, link('Manual', '/manual'), link('README', '/readme'))),
        K.card('Full panel',
            h('p', { class: 'muted small' }, 'The desktop UI has everything, including the Qobuz sign-in flow.'),
            h('button', { class: 'btn', type: 'button', onclick: () => { location.href = '/'; } }, 'Open the full panel')));
};

P.hide = () => { if (P.tuneClose) P.tuneClose(true); };

P.show = () => { P.render(); setTimeout(() => { if (P.left && P.left.isConnected) P.render(); }, 1500); };   // the keep-awake method settles just after load

K.registerPage(P);
})();
