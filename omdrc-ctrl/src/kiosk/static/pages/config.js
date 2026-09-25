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

P.render = function render() {
    if (!P.left) return;
    const opt = (key, dflt, options) => K.segmented(options, K.pref(key, dflt), v => { K.setPref(key, v); P.render(); K.applyPrefs && K.applyPrefs(); });

    K.clear(P.left).append(
        K.card('Now page',
            h('div', { class: 'lbl' }, 'Level display'),
            opt('now.level', 'needles', [{ value: 'needles', label: 'Needles' }, { value: 'bars', label: 'Bars' }, { value: 'spectrum', label: 'Bars + spectrum' }, { value: 'off', label: 'Off' }]),
            K.pref('now.level', 'needles') === 'off' ? h('p', { class: 'muted small' }, 'Off keeps the analyzer idle on the box: no level stream is opened, balance is unavailable, and the Now page shows the audio chain instead.') : null,
            h('div', { class: 'lbl' }, 'Dynamic range bar'),
            opt('now.dr', true, [{ value: true, label: 'Show' }, { value: false, label: 'Hide' }]),
            h('div', { class: 'lbl' }, 'Balance'),
            opt('now.balance', true, [{ value: true, label: 'Show' }, { value: false, label: 'Hide' }]),
            h('p', { class: 'muted small' }, 'Everything shown on the Now page is live; anything hidden here is not computed.')),
        K.card('Screen',
            h('div', { class: 'lbl' }, 'Screensaver after'),
            opt('saver.minutes', 0, [{ value: 0, label: 'Never' }, { value: 5, label: '5 min' }, { value: 15, label: '15 min' }, { value: 30, label: '30 min' }]),
            window.OmdrcApp ? h('div', {},
                h('p', { class: 'muted small' }, 'The app keeps the screen on only while “Now playing” is showing. Change that, the view (kiosk / full web page) and the server address in the app’s settings.'),
                h('div', { class: 'btn-row' }, h('button', { class: 'btn', type: 'button', onclick: () => window.OmdrcApp.openSettings() }, 'App settings'))) : null,
            window.OmdrcApp ? null : h('div', { class: 'lbl' }, 'Keep the screen on'),
            window.OmdrcApp ? null : opt('awake.on', true, [{ value: true, label: 'On' }, { value: false, label: 'Off' }]),
            window.OmdrcApp ? null : h('p', { class: 'muted small' }, K.awake.enabled()
                ? `Active method: ${K.awake.mode === 'off' ? 'none yet — tap the screen once' : K.awake.mode}. Over plain http the browser only allows the video fallback, which starts on the first tap.`
                : 'The phone or panel may switch its display off on its own timeout.'),
            !window.OmdrcApp && K.awake.enabled() && K.awake.mode !== 'wake lock' ? h('p', { class: 'muted small' }, P.awakeTip()) : null,
            h('div', { class: 'btn-row' }, h('button', { class: 'btn', type: 'button', onclick: () => K.toggleFullscreen() }, 'Toggle fullscreen'),
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
