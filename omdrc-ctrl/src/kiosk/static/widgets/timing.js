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
T.status = () => {
    const n = T.network(), p = profiles()[profileKey()];
    return `${n.label} · ${p ? 'Saved timing' : 'Not calibrated'}`;
};
T.delayMs = () => {
    const p = profiles()[profileKey()];
    // Legacy delay is retained only for unidentified browsers / old app versions.
    if (!p) return T.network().key === 'unknown' ? clamp(read('sync.delayMs')) : 0;
    return clamp(p.delayMs + (margin === null || p.marginMs === null ? 0 : margin - p.marginMs));
};
T.setDelayMs = ms => {
    const n = T.network();
    if (n.key === 'offline') return;
    if (n.key === 'unknown') { write('sync.delayMs', clamp(ms)); return; }
    const all = profiles();
    all[profileKey()] = { delayMs: clamp(ms), marginMs: margin };
    save(all);
};
T.updateSettings = settings => {
    const value = settings && settings.drc_delay_terms_ms && settings.drc_delay_terms_ms.margin;
    if (typeof value !== 'number' || !Number.isFinite(value)) return;
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
    for (const p of Object.values(all)) if (p.marginMs === null) { p.marginMs = margin; changed = true; }
    if (changed) save(all);
};
T.refresh = async () => {
    try {
        const r = await fetch('/spectrum/settings');
        if (r.ok) T.updateSettings(await r.json());
    } catch {}
};
T.refresh();
setInterval(T.refresh, 5000);
})();
