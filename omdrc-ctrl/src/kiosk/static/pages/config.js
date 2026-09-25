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
    const calibrate = async (clicks = false) => {
        let res = null;
        if (clicks && !(await K.confirm({
            title: 'Precise calibration',
            message: 'The box pauses what is playing, plays about 11 seconds of short clicks through the speakers, then puts the music back where it was. Check the volume is at a comfortable level, and hold the phone near the speakers.',
            ok: 'Play the clicks' }))) return;
        for (let attempt = 1; attempt <= (clicks ? 1 : 3); attempt++) {
            const b = K.busy('');
            res = await K.sync.calibrate({ seconds: clicks ? 14 : 10, clicks, onTick: left =>
                b.text(clicks ? `Listening for ${left} s — the box is playing the clicks`
                              : `Attempt ${attempt} of 3 · listening for ${left} s — hold the phone near the speakers while the music plays`) });
            b.done();
            if (res.ok || /microphone|app|click/.test(res.error || '')) break;
        }
        if (res.ok && res.lagMs >= 0) {
            set(res.lagMs);
            P.lastCal = `Calibrated: the meters led the sound by ${res.lagMs} ms (match ${res.r}); this device now waits ${res.lagMs} ms.`;
            K.toast(`Meter delay set to ${res.lagMs} ms`);
        } else if (res.ok) {
            set(0);
            P.lastCal = `The meters arrive ${-res.lagMs} ms after the sound (match ${res.r}): this device cannot catch up. Lower the server's DRC sync delta by about that much, or accept the lag.`;
        } else P.lastCal = `Calibration failed: ${res.error}${res.r !== undefined ? ` (match ${res.r})` : ''}.`;
        result.textContent = P.lastCal;
    };
    return K.card('Meter timing',
        h('p', { class: 'muted small' }, 'Extra delay for this screen, on top of the box’s own chain delay: the meters and spectrum are drawn this long after they arrive. Stored on this device only.'),
        h('div', { class: 'att-row' }, step(-50), step(-10), value, step(10), step(50),
            h('button', { class: 'btn', type: 'button', onclick: () => set(0) }, 'Reset')),
        K.sync.canCalibrate()
            ? h('div', {},
                h('div', { class: 'btn-row' },
                    h('button', { class: 'btn primary', type: 'button', onclick: () => calibrate(false) }, 'Calibrate on the music'),
                    h('button', { class: 'btn', type: 'button', onclick: () => calibrate(true) }, 'Precise (plays clicks)')),
                h('div', { class: 'lbl' }, 'Recalibrate automatically when a track starts or playback resumes'),
                K.segmented([{ value: true, label: 'On' }, { value: false, label: 'Off' }], !!K.pref('sync.auto', false),
                    v => { K.setPref('sync.auto', v); P.render(); }),
                h('p', { class: 'muted small' }, 'Uses the microphone for 8 s at most every two minutes, only on Now playing; Android shows its microphone indicator meanwhile.'))
            : h('p', { class: 'muted small' }, 'Automatic calibration needs the OMDRC Android app (it uses the phone’s microphone).'),
        result);
};

P.render = function render() {
    if (!P.left) return;
    const opt = (key, dflt, options) => K.segmented(options, K.pref(key, dflt), v => { K.setPref(key, v); P.render(); K.applyPrefs && K.applyPrefs(); });

    K.clear(P.left).append(
        window.OmdrcApp ? K.card('Android app',
            h('p', { class: 'muted small' }, 'View (kiosk or full web page), keeping the screen on while “Now playing” is showing, hiding the Android bars, and the server address are set in the app’s own settings.'),
            h('button', { class: 'btn', type: 'button', onclick: () => window.OmdrcApp.openSettings() }, 'App settings')) : null,
        K.card('Now page',
            h('div', { class: 'lbl' }, 'Level display'),
            opt('now.level', 'needles', [{ value: 'needles', label: 'Needles' }, { value: 'bars', label: 'Bars' }, { value: 'spectrum', label: 'Bars + spectrum' }, { value: 'off', label: 'Off' }]),
            K.pref('now.level', 'needles') === 'off' ? h('p', { class: 'muted small' }, 'Off keeps the meters idle: the Now page shows the audio chain instead, and the level stream is opened only while Balance is switched on.') : null,
            h('div', { class: 'lbl' }, 'Keep the DR estimate running after leaving Now'),
            opt('dr.keepMinutes', 5, [{ value: 1, label: '1 min' }, { value: 5, label: '5 min' }, { value: 10, label: '10 min' }, { value: 30, label: '30 min' }, { value: -1, label: 'While open' }]),
            h('p', { class: 'muted small' }, 'When the time is up a dialog asks whether to keep estimating; with no answer within a minute it stops.'),
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
                h('button', { class: 'btn', type: 'button', onclick: () => location.reload() }, 'Reload kiosk'))));

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

P.show = () => { P.render(); setTimeout(() => { if (P.left && P.left.isConnected) P.render(); }, 1500); };   // the keep-awake method settles just after load

K.registerPage(P);
})();
