/* Settled phone canvases need no new animation for an identical analyzer frame. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let scheduled = 0;
const K = { pref: (_key, fallback) => fallback, clamp: (n, lo, hi) => Math.max(lo, Math.min(hi, n)) };
const context = { K, requestAnimationFrame: () => ++scheduled };
vm.createContext(context);
for (const name of ['vu', 'spectrum'])
    vm.runInContext(fs.readFileSync(`omdrc-ctrl/src/kiosk/static/widgets/${name}.js`, 'utf8'), context);

const vu = Object.create(K.VuMeter.prototype);
vu.tgt = { left_rms: -120, right_rms: -120, left_peak: -120, right_peak: -120 };
vu.raf = null;
vu.update({ left_rms: -120, right_rms: -120, left_peak: -120, right_peak: -120 });
assert.equal(scheduled, 0);
vu.update({ left_rms: -20, right_rms: -21, left_peak: -4, right_peak: -5 });
assert.equal(scheduled, 1);
vu.raf = null; // settled
vu.update({ left_rms: -20, right_rms: -21, left_peak: -4, right_peak: -5 });
assert.equal(scheduled, 1);

const spec = Object.create(K.Spectrum.prototype);
spec.bands = []; spec.l = []; spec.r = []; spec.tl = []; spec.tr = [];
spec.peaks = { left: -120, right: -120 }; spec.raf = null;
const bands = [{ label: '100' }];
const frame = { bands, left: [-20], right: [-22], vu: { left_peak: -4, right_peak: -5 } };
spec.update(frame);
assert.equal(scheduled, 2);
spec.raf = null; // settled
spec.update(frame);
assert.equal(scheduled, 2);
spec.update({ ...frame, bands: [{ label: '125' }] });
assert.equal(scheduled, 3, 'a new band label requires a repaint');
console.log('Settled meter paint: passed');
