/* The Pi's own display, through omdrc-display-helper running on the Pi (see
 * omdrc-ctrl/kiosk-pi/README.md).  Used only when the kiosk was opened with
 * ?display=<host:port> (remembered afterwards), so phones and desktop browsers
 * never probe their own localhost. */
(() => {
'use strict';

const D = K.display = { base: '', ok: false, method: '' };

try {
    const q = new URLSearchParams(location.search).get('display');
    if (q === 'off') K.setPref('display.helper', '');
    else if (q) K.setPref('display.helper', /^https?:\/\//.test(q) ? q : 'http://' + q);
} catch {}
D.base = K.pref('display.helper', '');

const call = async (path, method = 'GET') => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 4000);
    try {
        const r = await fetch(D.base + path, { method, cache: 'no-store', signal: ctl.signal });
        return await r.json();
    } catch { return { ok: false, error: 'display helper unreachable' }; }
    finally { clearTimeout(t); }
};

D.probe = async () => {
    if (!D.base) return false;
    const d = await call('/status');
    D.ok = !!d.ok; D.method = d.method || '';
    return D.ok;
};
D.off = () => call('/display/off', 'POST');
D.on = () => call('/display/on', 'POST');
})();
