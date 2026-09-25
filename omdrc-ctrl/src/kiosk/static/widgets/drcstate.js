/* What DRC is doing right now, shared by the top bar, the Now page and the DRC
 * page: one poll of /drc/session + /drc/attenuation instead of one per view. */
(() => {
'use strict';

const S = K.drcState = {
    session: null,       // /drc/session reply
    att: null,           // /drc/attenuation reply
    at: 0,
    subs: new Set(),
    poller: null,
};

S.onChange = fn => { S.subs.add(fn); if (S.at) fn(S); return () => S.subs.delete(fn); };

S.refresh = async () => {
    const [session, att] = await Promise.all([K.api('/drc/session'), K.api('/drc/attenuation')]);
    S.session = session; S.att = att; S.at = Date.now();
    S.subs.forEach(f => f(S));
    return S;
};

S.start = () => {
    if (!S.poller) S.poller = new K.Poller(S.refresh, 10000);
    S.poller.start();
};

// A flat, display-ready view of the two replies.
S.summary = () => {
    const s = S.session;
    if (!s || !s.ok) return { known: false, power: 'unknown', text: s ? (s.error || 'DRC status unavailable') : 'reading DRC state…' };
    const a = s.active || {}, ses = s.session || {};
    const v = a.verification || {};
    const running = !!a.running;
    const att = S.att && S.att.ok ? Number(S.att.db) : null;
    const out = {
        known: true,
        running,
        power: running ? 'on' : (ses.power === 'off' ? 'off' : 'stopped'),
        rate: running ? Number(a.rate) : null,
        geometry: running ? a.geometry : ses.geometry,
        design: String(running ? (a.design || '') : (ses.design || '')).replace(/^@/, ''),
        attenuation: att,
        verification: v.status || 'unknown',
        message: v.message || '',
        session: ses,
        active: a,
        matches: !!ses.matches_active,
        input: a.input || null,
    };
    out.text = running
        ? `DRC ${out.rate ? out.rate / 1000 + ' kHz' : ''} · ${out.geometry || ''}`
        : (out.power === 'off' ? 'DRC OFF · direct' : 'DRC stopped');
    return out;
};

// e.g. "44.1 kHz", "192 kHz"
K.fmtRate = hz => Number.isFinite(hz) && hz > 0 ? `${+(hz / 1000).toFixed(1)} kHz` : '';
})();
