'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/timing.js', 'utf8');
const memory = new Map();
let network = { key: 'wifi:HotSpot1', label: 'HotSpot1' }, native = '{}';
const callbacks = [];
const window = { OmdrcApp: {
    timingNetwork: () => JSON.stringify(network),
    timingProfiles: () => native,
    saveTimingProfiles: value => { native = value; }
} };
const localStorage = { getItem: key => memory.get(key) || null, setItem: (key, value) => memory.set(key, value) };
vm.runInNewContext(source, { window, localStorage, setInterval: fn => callbacks.push(fn), fetch: async () => ({ ok: false }) });
const T = window.OmdrcTiming;
const margin = value => T.updateSettings({ drc_delay_terms_ms: { margin: value } });
assert.equal(T.delayMs(), 0);
margin(20); T.setDelayMs(100);
network = { key: 'wifi:HotSpot2', label: 'HotSpot2' };
assert.equal(T.delayMs(), 0);
assert.match(T.status(), /Not calibrated/);
T.setDelayMs(220);
margin(60);
assert.equal(T.delayMs(), 260);
network = { key: 'wifi:HotSpot1', label: 'HotSpot1' };
assert.equal(T.delayMs(), 140);
T.setDelayMs(145);
margin(40);
assert.equal(T.delayMs(), 125);
network = { key: 'wired', label: 'Wired' }; T.setDelayMs(30);
network = { key: 'wifi:HotSpot1', label: 'HotSpot1' };
assert.equal(T.delayMs(), 125);
network = { key: 'wired', label: 'Wired' };
assert.equal(T.delayMs(), 30);
const before = T.context();
network = { key: 'wifi:HotSpot1', label: 'HotSpot1' }; callbacks[0]();
network = { key: 'wired', label: 'Wired' }; callbacks[0]();
assert.notEqual(T.context(), before, 'a switch away and back invalidates a calibration');
network = { key: 'unknown', label: 'Hidden' }; T.setNetworkName('HotSpot1');
assert.equal(T.delayMs(), 125);
T.setNetworkName('wired'); assert.equal(T.delayMs(), 30);
T.setNetworkName(''); T.setDelayMs(55); assert.equal(T.delayMs(), 55);
network = { key: 'offline', label: 'Disconnected' }; T.setDelayMs(900);
assert.equal(T.delayMs(), 0);
// A new page origin uses the native profile store, rather than its localStorage.
network = { key: 'wifi:HotSpot1', label: 'HotSpot1' };
memory.clear();
const otherWindow = { OmdrcApp: window.OmdrcApp };
vm.runInNewContext(source, { window: otherWindow, localStorage, setInterval: () => {}, fetch: async () => ({ ok: false }) });
otherWindow.OmdrcTiming.updateSettings({ drc_delay_terms_ms: { margin: 40 } });
assert.equal(otherWindow.OmdrcTiming.delayMs(), 125);
console.log('Network timing profiles: passed');
// Real settings use a stable audio identity and the requested margin. Buffer
// fill/effective margin can change as the click test stops and starts playback.
const settings = (config, effective = 40, requested = 150) => T.updateSettings({
    timing_configuration: config, drc_delay_margin_ms: requested,
    drc_delay_terms_ms: { margin: effective }
});
settings({ drc: false });
assert.equal(T.delayMs(), 0, 'legacy network-only timing must not silently cover a new configuration');
T.setDelayMs(90);
const idleContext = T.context();
settings({ drc: false }, 0);
assert.equal(T.context(), idleContext, 'idle buffer fill must not invalidate calibration');
assert.equal(T.delayMs(), 90, 'stopping the click partition must not re-anchor the calibration to zero margin');
settings({ drc: true, rate: 192000, filters: ['A'] });
assert.equal(T.delayMs(), 0);
T.setDelayMs(210);
settings({ drc: true, rate: 96000, filters: ['A'] });
assert.equal(T.delayMs(), 0);
T.setDelayMs(120);
settings({ drc: true, rate: 192000, filters: ['B'] });
assert.equal(T.delayMs(), 0);
settings({ drc: true, rate: 192000, filters: ['A'] });
assert.equal(T.delayMs(), 210);
const marginContext = T.context();
settings({ drc: true, rate: 192000, filters: ['A'] }, 40, 170);
assert.notEqual(T.context(), marginContext);
console.log('Audio configuration timing profiles: passed');
