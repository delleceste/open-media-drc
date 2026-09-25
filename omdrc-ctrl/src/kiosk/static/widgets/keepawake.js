/* Keep the screen on.  The Wake Lock API is the right tool but browsers only
 * offer it on HTTPS or localhost, and the panel is plain http on the LAN.  The
 * fallback is the old trick of playing a tiny muted, looping, black video: a
 * playing video keeps the display awake on iOS and Android.  Browsers only allow
 * playback after a touch, so the video starts on the first tap.  Switchable in
 * Config; on by default because this is a kiosk. */
(() => {
'use strict';
const VIDEO = 'data:video/mp4;base64,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';

const A = K.awake = { lock: null, video: null, mode: 'off' };

// Wanted only while Now playing is showing (never on the other pages, never behind the screensaver),
// and only if the user hasn't released it with the top-right button.  The Android app does this natively.
A.enabled = () => !window.OmdrcApp && K.pref('awake.on', true) && !!(K.nowShown && K.nowShown());

A.release = () => {
    if (A.lock) { try { A.lock.release(); } catch {} A.lock = null; }
    if (A.video) A.video.pause();
    A.mode = 'off';
};

A.acquire = async () => {
    if (!A.enabled() || document.hidden) return;
    if ('wakeLock' in navigator) {
        try {
            A.lock = await navigator.wakeLock.request('screen');
            A.lock.addEventListener('release', () => { A.lock = null; });
            A.mode = 'wake lock';
            return;
        } catch { /* not allowed here (insecure origin, battery saver): use the video */ }
    }
    if (!A.video) {
        A.video = K.h('video', { muted: true, loop: true, playsinline: true, 'webkit-playsinline': '', tabindex: '-1', 'aria-hidden': 'true',
            // Big enough to count as a visible video, but behind the page and almost
            // transparent.  (A 2 px, 1 % video is ignored by some browsers.)
            style: { position: 'fixed', width: '160px', height: '90px', opacity: '0.03', pointerEvents: 'none', left: 0, top: 0, zIndex: -1 } });
        A.video.muted = true;
        A.video.src = VIDEO;
        document.body.append(A.video);
    }
    try { await A.video.play(); A.mode = A.video.paused ? 'waiting for a tap' : 'video'; }
    catch { A.mode = 'waiting for a tap'; }          // autoplay refused: the first touch retries
};

A.sync = () => { A.enabled() ? A.acquire() : A.release(); };

// a tap is what lets the video start, and re-acquires after the browser dropped the lock
// Touch pointerdown / touchstart are NOT user activations in Chrome; the end of a tap is.
['click', 'touchend', 'pointerup'].forEach(t => document.addEventListener(t, () => {
    if (A.enabled() && A.mode !== 'wake lock' && !(A.mode === 'video' && A.video && !A.video.paused)) A.acquire();
}, { capture: true, passive: true }));
document.addEventListener('visibilitychange', () => { if (!document.hidden) A.sync(); });
})();
