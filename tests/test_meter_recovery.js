'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
let now = 10000;
const timers = [], streams = [], tapped = [], drawn = [];
const K = { state: { spectrum: { enabled: true } }, sync: { delayMs: () => 200 },
    streamTap: (_, d, t) => tapped.push([d.id, t]) };
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/core.js', 'utf8');
vm.runInNewContext(source.slice(source.indexOf('K.streams ='), source.indexOf('// Code that needs the boot config')), {
    K, document: { hidden: false, addEventListener() {} }, Date: { now: () => now },
    EventSource: class { constructor() { streams.push(this); } close() {} },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); }, clearTimeout() {},
    requestAnimationFrame: fn => { timers.push({ fn, ms: 0 }); return 1; }, encodeURIComponent
});
K.streams.open('vu', d => drawn.push(d.id));
const send = (id, lag, es = streams.at(-1)) => { now += 50; es.onmessage({ data: JSON.stringify({ id, sent: now - lag }) }); };
send(1, 20);
assert.equal(timers[0].ms, 200);
send(2, 120);
assert.equal(timers[1].ms, 100, 'network jitter consumes the existing wait');
for (let i = 0; i < 5; i++) send(10 + i, 1400);
assert.equal(streams.length, 2);
assert.equal(tapped.length, 2, 'stale frames never enter calibration');
send(20, 20);
for (let i = 0; i < timers.length; i++) timers[i].fn();
assert.deepEqual(drawn, [20], 'replacement discards old delayed frames and restores display');
const sync = fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/sync.js', 'utf8');
// Load just the detector with its constants, avoiding UI lifecycle dependencies.
const detector = { sync: {} };
vm.runInNewContext('const S=K.sync; const LAG_MIN=-600,LAG_MAX=2500;\n' + sync.slice(sync.indexOf('S.estimateClicks ='), sync.indexOf('// ── the log')), { K: detector });
const starts = [700,1230,2090,2700,3640,4120,4890,5540,6430,6990,7710,8540,9130,9830];
const frames = [{ t: 0, p: -120 }];
const db = Array(1100).fill(-70);
for (const t of starts) { frames.push({ t, p: -65 }, { t: t + 50, p: -120 }); db[(t + 200) / 10] = -40; }
const result = detector.sync.estimateClicks(frames, { t0: 0, step: 10, db });
assert.equal(result.ok, true);
assert.equal(result.lagMs, 200);
console.log('Meter recovery and attenuated click detection: passed');
