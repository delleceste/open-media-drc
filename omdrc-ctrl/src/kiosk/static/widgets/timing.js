/* Shared by the kiosk and full panel. Network profiles stay on this device. */
(() => {
'use strict';
const T = window.OmdrcTiming = {};
const bridge = window.OmdrcApp;
const read = key => { try { return JSON.parse(localStorage.getItem('omdrc-kiosk.' + key)); } catch { return null; } };
const write = (key, value) => { try { localStorage.setItem('omdrc-kiosk.' + key, JSON.stringify(value)); } catch {} };
const profiles = () => {
    try { return (bridge && bridge.timingProfiles ? JSON.parse(bridge.timingProfiles()) : read('sync.profiles')) || {}; }
    catch { return {}; }
};
const save = value => {
    write('sync.profiles', value);
    if (bridge && bridge.saveTimingProfiles) bridge.saveTimingProfiles(JSON.stringify(value));
};
let latestSettings = null;
let margin = null, revision = 0, lastNetwork = null, configuration = null, configuredMargin = null;
const clamp = ms => Math.max(0, Math.min(3000, Math.round(Number(ms) || 0)));
T.network = () => {
    let n = { key: 'unknown', label: 'Network unidentified' };
    try { if (bridge && bridge.timingNetwork) n = JSON.parse(bridge.timingNetwork()); } catch {}
    if (n.key === 'unknown') {
        const manual = read('sync.networkName');
        if (manual) n = { key: manual === 'wired' ? 'wired' : 'wifi:' + manual, label: manual === 'wired' ? 'Wired' : manual };
    }
    return n;
};
T.setNetworkName = name => { write('sync.networkName', String(name || '').trim()); };
T.context = () => {
    const key = T.network().key;
    if (lastNetwork !== null && key !== lastNetwork) revision++;
    lastNetwork = key;
    return key + ':' + configuration + ':' + revision;
};
setInterval(T.context, 1000);
const profileKey = () => configuration === null ? T.network().key : JSON.stringify([T.network().key, configuration]);
const audioConfig = () => configuration === null ? null : JSON.parse(configuration);
const family = c => {
    if (!c || !c.drc || !c.config) return null;
    const parts = c.config.split('/'), match = parts.pop().match(/^brutefir-(\d+)(.*)\.conf$/);
    return match ? JSON.stringify([c.source, parts.join('/'), match[2] || 'default']) : null;
};
const selected = () => {
    const all = profiles(), exact = all[profileKey()];
    if (exact) return { profile: exact, fallback: false };
    const c = audioConfig(), f = family(c);
    if (!f || !c.rate) return null;
    const candidates = [];
    for (const [key, profile] of Object.entries(all)) {
        try {
            const [network, encoded] = JSON.parse(key), other = JSON.parse(encoded);
            // Same-rate filter edits must not recover an obsolete calibration.
            if (network === T.network().key && family(other) === f && other.rate && other.rate !== c.rate)
                candidates.push({ profile, fallback: true, rate: other.rate, distance: Math.abs(Math.log(other.rate / c.rate)) });
        } catch {} // legacy network-only profiles are not rate fallbacks
    }
    candidates.sort((a, b) => a.distance - b.distance || (b.profile.calibratedAt || 0) - (a.profile.calibratedAt || 0));
    return candidates[0] || null;
};
T.status = () => {
    const n = T.network(), p = selected();
    return `${n.label} · ${!p ? 'Not calibrated' : p.fallback ? `Provisional timing from ${p.rate / 1000} kHz — calibrate this rate` : 'Saved timing'}`;
};
T.delayMs = () => {
    const p = selected()?.profile;
    // Legacy delay is retained only for unidentified browsers / old app versions.
    if (!p) return T.network().key === 'unknown' ? clamp(read('sync.delayMs')) : 0;
    return clamp(p.delayMs + (margin === null || p.marginMs === null ? 0 : margin - p.marginMs));
};
T.setDelayMs = ms => {
    const n = T.network();
    if (n.key === 'offline') return;
    if (n.key === 'unknown') { write('sync.delayMs', clamp(ms)); return; }
    const all = profiles();
    all[profileKey()] = { delayMs: clamp(ms), marginMs: margin, calibratedAt: Date.now() };
    save(all);
};
T.details = () => ({
    network: T.network(), configuration: configuration === null ? null : JSON.parse(configuration),
    saved: profiles()[profileKey()] || null, delayMs: T.delayMs(),
    fallback: selected()?.fallback ? selected() : null,
    marginMs: margin, settings: latestSettings
});
T.updateSettings = settings => {
    const value = settings && settings.drc_delay_terms_ms && settings.drc_delay_terms_ms.margin;
    if (typeof value !== 'number' || !Number.isFinite(value)) return;
    latestSettings = { ...latestSettings, ...settings };
    const next = settings.timing_configuration === undefined ? configuration : JSON.stringify(settings.timing_configuration);
    const requested = settings.drc_delay_margin_ms ?? settings.margin_ms ?? configuredMargin ?? value;
    if (configuration !== next || (configuredMargin !== null && configuredMargin !== requested)) revision++;
    const changedConfiguration = configuration !== next;
    configuration = next;
    configuredMargin = requested;
    // Stopping the calibration partition empties the DAC and reports zero.
    // Keep the playback margin so the just-measured result is saved against
    // the timing actually used for the clicks, rather than the idle device.
    if (value !== 0 || margin === null || changedConfiguration || settings.drc_delay_margin_ms === undefined)
        margin = value;
    // First known server margin anchors adjustments made before settings arrived.
    const all = profiles();
    let changed = false;
    const active = all[profileKey()];
    if (active && active.marginMs === null) { active.marginMs = margin; changed = true; }
    if (changed) save(all);
};
T.refresh = async () => {
    try {
        const r = await fetch('/spectrum/settings');
        if (r.ok) { T.updateSettings(await r.json()); return true; }
    } catch {}
    return false;
};
T.refresh();
setInterval(T.refresh, 5000);
})();
