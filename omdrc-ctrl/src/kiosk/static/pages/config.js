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
    const value = h('strong', { class: 'timing-value' }, `${K.sync.delayMs()} ms`);
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
                if (res.ok || /microphone|app/.test(res.error || '')) break;
            }
        } finally { unsub(); }
        P.lastCal = P.applyCal(res);
        result.textContent = P.lastCal;
        value.textContent = `${K.sync.delayMs()} ms`;
        sheet.status(P.lastCal, true);
    };
    return K.card('Meter timing',
        h('p', { class: 'muted small' }, 'Extra delay for this screen, on top of the box’s own chain delay: the meters and spectrum are drawn this long after they arrive. Stored on this device only.'),
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
        } }, 'Calibration log')));
};

// A calibration result into the delay; the sentence that says what happened.
P.applyCal = res => {
    if (res.ok && res.lagMs >= 0) {
        K.sync.setDelayMs(res.lagMs);
        return `Calibrated: the meters led the sound by ${res.lagMs} ms (match ${res.r}); this device now waits ${res.lagMs} ms.`;
    }
    if (res.ok) {
        K.sync.setDelayMs(0);
        return `The meters arrive ${-res.lagMs} ms after the sound (match ${res.r}): this device cannot catch up. Raise drc_delay_margin_ms in the box's [spectrum] configuration by about that much.`;
    }
    return `Calibration failed: ${res.error}${res.r !== undefined ? ` (match ${res.r})` : ''}.`;
};

// Tuning with the click track, meters in view.  The level bars run only while this
// sheet is open.  Start: the clicks play with the current delay (the "before"), the
// phone measures and sets the delay, then the clicks play again (the "after"),
// measured once more, with frames timed when drawn.  Play again repeats the check.
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
    const againBtn = h('button', { class: 'btn', type: 'button', hidden: true, onclick: () => run(false) }, 'Play again');
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

    const verify = async label => {
        status.textContent = `${label}: playing the bursts with ${K.sync.delayMs()} ms — watch the bars`;
        const res = await K.sync.calibrate({ seconds: 14, verify: true, onTick: left =>
            status.textContent = `${label}: playing the bursts with ${K.sync.delayMs()} ms — ${left} s` });
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
                const r = await K.sync.playClicks({ onTick: left => { status.textContent = `Playing the bursts with ${K.sync.delayMs()} ms — ${left} s`; } });
                status.textContent = r.ok ? 'Done. Adjust the delay and play again until the bars flick with each click.' : `Could not play: ${r.error}`;
                return;
            }
            if (!first) {
                const res = await verify('Check');
                if (res.ok && Math.abs(res.lagMs) > VERIFY_TOL_MS && K.sync.delayMs() + res.lagMs >= 0) {
                    K.sync.setDelayMs(K.sync.delayMs() + res.lagMs); show();
                    note(`Corrected to ${K.sync.delayMs()} ms — Play again to see it.`);
                }
                status.textContent = 'Done.';
                return;
            }
            // before: the clicks with the current delay, measured on arrival
            status.textContent = `Before: playing the bursts with ${K.sync.delayMs()} ms — watch the bars`;
            const res = await K.sync.calibrate({ seconds: 14, clicks: true, onTick: left =>
                status.textContent = `Before: playing the bursts with ${K.sync.delayMs()} ms — ${left} s` });
            P.lastCal = P.applyCal(res);
            note(`Before: ${P.lastCal}`);
            show();
            if (!res.ok || closed) { status.textContent = 'Stopped.'; return; }
            // after: the same clicks with the new delay
            const after = await verify('After');
            status.textContent = after.ok && Math.abs(after.lagMs) <= VERIFY_TOL_MS
                ? 'Done. Play again whenever you want to re-check.'
                : 'Done, but not in sync: Play again re-measures and corrects.';
        } finally {
            if (!closed) { setBusy(false); againBtn.hidden = false; }
        }
    };
    // Close waits for a run to end; leaving the page (force) does not: the level
    // bars stop at once, and a run in progress finishes on its own.
    const close = (force = false) => {
        if (busy && !force) return;
        closed = true; P.tuneClose = null;
        level.close(); vu.destroy(); scrim.remove();
        if (P.left && !force) P.render();
    };
    P.tuneClose = close;
    const scrim = h('div', { class: 'scrim' }, h('div', { class: 'sheet cal-sheet tune-sheet' },
        h('div', { class: 'cal-title' }, 'Meter timing'),
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

P.render = function render() {
    if (!P.left) return;
    const opt = (key, dflt, options) => K.segmented(options, K.pref(key, dflt), v => { K.setPref(key, v); P.render(); K.applyPrefs && K.applyPrefs(); });

    K.clear(P.left).append(...[      // (append() would print a null as "null")
        window.OmdrcApp ? K.card('Android app',
            h('p', { class: 'muted small' }, 'View (kiosk or full web page), keeping the screen on while “Now playing” is showing, hiding the Android bars, and the server address are set in the app’s own settings.'),
            h('button', { class: 'btn', type: 'button', onclick: () => window.OmdrcApp.openSettings() }, 'App settings')) : null,
        K.card('Now page',
            h('div', { class: 'lbl' }, 'Level display'),
            opt('now.level', 'needles', [{ value: 'needles', label: 'Needles' }, { value: 'bars', label: 'Bars' }, { value: 'spectrum', label: 'Bars + spectrum' }, { value: 'off', label: 'Off' }]),
            K.pref('now.level', 'needles') === 'off' ? h('p', { class: 'muted small' }, 'Off keeps the meters idle: the Now page shows the audio chain instead, and the level stream is opened only while Balance is switched on.') : null,
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
