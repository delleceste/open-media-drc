'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/sync.js', 'utf8');
let now = 1000000, delay = 100, saved = true, context = 'home:192';
const prefs = new Map();
const K = { pref: (k, d) => prefs.has(k) ? prefs.get(k) : d,
    setPref: (k, v) => prefs.set(k, v), nowShown: () => true, toast() {} };
const window = { OmdrcApp: { startMicEnvelope() {} }, OmdrcTiming: {
    delayMs: () => delay, setDelayMs: x => { delay = x; saved = true; },
    context: () => context, details: () => ({ saved })
} };
vm.runInNewContext(source, { K, window, Date: { now: () => now }, setInterval() {} });
const S = K.sync;
const starts = [700,1230,2090,2700,3640,4120,4890,5540,6430,6990,7710,8540,9130,9830];
const clicks = offsets => {
    const frames = [{ t: 0, p: -120 }], db = Array(1200).fill(-70);
    starts.forEach((t, i) => { frames.push({ t, p: -65 }, { t: t + 50, p: -120 }); db[Math.round((t + offsets[i]) / 10)] = -40; });
    return { frames, mic: { t0: 0, step: 10, db } };
};
let data = clicks(starts.map(() => 200));
assert.equal(S.estimateClicks(data.frames, data.mic).ok, true);
data = clicks(starts.map((_, i) => i < 7 ? 180 : 220));
let res = S.estimateClicks(data.frames, data.mic);
assert.equal(res.ok, false, 'a moving offset must not become a saved calibration');
assert.ok(Math.abs(res.diag.driftMs) > 30);
// Duplicate meter events must not increase confidence by pairing one mic event twice.
data = clicks(starts.map(() => 200));
const duplicate = data.frames.flatMap(f => f.p > -120 ? [f, { t: f.t + 1, p: -120 }, { t: f.t + 2, p: f.p }] : [f]);
res = S.estimateClicks(duplicate, data.mic);
assert.ok(res.matched <= 14 && res.r <= 1);
// Check the split-recording guard independently of correlation details.
const original = S.estimate;
let answers = [{ ok: true, lagMs: 200, diag: {} }, { ok: true, lagMs: 180 }, { ok: true, lagMs: 240 }];
S.estimate = () => answers.shift();
assert.equal(S.estimateStable([], { t0: 0, step: 10, db: Array(1000) }).ok, false);
S.estimate = original;
(async () => {
    prefs.set('sync.auto', true);
    S.calibrate = async () => ({ ok: true, lagMs: 200, r: .9, context });
    await S.autoRun('test'); assert.equal(delay, 100);
    now += 120001; await S.autoRun('test'); assert.equal(delay, 100);
    now += 120001; await S.autoRun('test'); assert.equal(delay, 120, 'existing profiles move at most 20 ms after three agreeing runs');
    context = 'home:96'; saved = false; delay = 0;
    for (let i = 0; i < 2; i++) { now += 120001; await S.autoRun('test'); assert.equal(delay, 0); }
    now += 120001; await S.autoRun('test'); assert.equal(delay, 200, 'a new context learns its own consensus');
    context = 'studio:96'; delay = 100;
    for (const lagMs of [170, 240, 190]) { S.calibrate = async () => ({ ok: true, lagMs, r: .9, context }); now += 120001; await S.autoRun('test'); }
    assert.equal(delay, 100, 'disagreeing recent measurements must not drift a profile');
    console.log('Calibration stability and persistent automatic adjustment: passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
// Exercise the real correlation estimator with irregular music dynamics.
let seed = 7;
const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
const pulses = [];
for (let t = 200; t < 12000;) { pulses.push({ t, width: 80 + Math.floor(rand() * 160), db: -35 + rand() * 15 }); t += 350 + Math.floor(rand() * 400); }
const signal = t => pulses.find(p => t >= p.t && t < p.t + p.width)?.db ?? -60;
const musicFrames = [];
for (let t = 0; t <= 12000; t += 40) musicFrames.push({ t, p: Math.max(...Array.from({ length: 5 }, (_, i) => signal(t - i * 10))) });
const stableMic = { t0: 0, step: 10, db: Array.from({ length: 1200 }, (_, i) => signal(i * 10 - 200)) };
assert.equal(S.estimateStable(musicFrames, stableMic).ok, true, 'stable irregular music should pass split-recording checks');
const driftingMic = { ...stableMic, db: Array.from({ length: 1200 }, (_, i) => signal(i * 10 - (i < 600 ? 180 : 240))) };
assert.equal(S.estimateStable(musicFrames, driftingMic).ok, false, 'real correlations must reject a changing offset');
