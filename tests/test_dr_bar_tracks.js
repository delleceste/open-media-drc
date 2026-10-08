/* Run with node tests/test_dr_bar_tracks.js.  The DR bar stays continuous
 * across tracks but never lets a segment straddle a track start or a seek. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const h = (tag, attrs, ...kids) => ({
    tag, attrs: attrs || {}, kids: kids.flat(), style: {}, clientWidth: 280,
    classList: { toggle() {} }, addEventListener() {},
    append(...items) { this.kids.push(...items); }, remove() { this.removed = true; },
});
let painted = [];
let frameReceived;
const overlay = h('div');
const host = {
    clientWidth: 2000, classList: { toggle() {} }, addEventListener() {},
    append: (...cells) => { painted = cells; },
};
const K = {
    h, clamp: (v, a, b) => Math.min(b, Math.max(a, v)), clear: el => el,
    pref: (_k, d) => d, setPref() {}, streams: { open: (_mode, fn) => { frameReceived = fn; return { close() {} }; } },
};
const context = { K, document: { getElementById: () => overlay }, ResizeObserver: class { observe() {} disconnect() {} }, Math };
vm.createContext(context);
vm.runInContext(fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/dr.js', 'utf8'), context);

const loud = [[0.1, 0.1], [0.9, 0.9]];
const blocks = Array.from({ length: 20 }, () => loud);
const bar = new K.DrBar(host);
// 20 blocks, the oldest is block 100; a track starts at 110 with a seek at 115
bar.render(blocks, 60, { origin: 100, tracks: [
    { start: 90, end: 110, title: 'One', dr: 9.4, complete: true, kept: true, seeks: [] },
    { start: 110, end: null, title: 'Two', dr: 7.1, complete: null, kept: null, seeks: [115] },
] });
const cls = painted.map(c => c.attrs.class);
const at = name => painted.findIndex(c => c.attrs.class.includes(name));
assert.equal(cls.filter(c => c.includes('cut-track')).length, 1);
assert.equal(cls.filter(c => c.includes('cut-seek')).length, 1);
assert.ok(at('cut-track') < at('cut-seek'));
// a segment names its track and that track's DR
assert.match(painted[0].attrs.title, /One: track DR9\.4 \(heard whole\)/);
assert.match(painted[painted.length - 1].attrs.title, /Two: track DR7\.1 \(so far\)/);

// without marks the bar is what it always was: no cuts
bar.render(blocks, 60);
assert.ok(painted.every(c => !/cut-/.test(c.attrs.class)));

// the stored-figure badge: exact solid, estimate with ≈
const exact = K.drLogBadge({ dr: 12, kind: 'exact', basis: 'report', heard: 0 });
assert.equal(exact.kids[0], 'DR12');
assert.match(exact.attrs.title, /dr14\.txt/);
const est = K.drLogBadge({ dr: 9, kind: 'estimate', basis: 'listened', heard: 3, track_count: 11 });
assert.equal(est.kids[0], '≈DR9');
assert.match(est.attrs.class, /est/);
assert.match(est.attrs.title, /3 of 11 tracks heard/);
assert.equal(K.drLogBadge(null), null);

// Reset and navigation change the painted viewport, never the live estimate's
// selected blocks. Earlier blocks remain available for looking back.
const estimate = K.drEstimate;
const listener = estimate.listen(() => {});
frameReceived({ ok: true, source: 'mpd', dr: { state: 'ready', track_age_blocks: 100, total_blocks: 100 }, dr_blocks: Array.from({ length: 100 }, () => loud) });
const liveValue = estimate.summary().value;
assert.equal(estimate.viewBlocks().length, 20);
estimate.resetView();
assert.equal(estimate.viewBlocks().length, 0);
assert.equal(estimate.selected().length, 20);
assert.equal(estimate.summary().value, liveValue);
estimate.extendView('start');
assert.equal(estimate.viewBlocks().length, 20);
estimate.resetView();
frameReceived({ ok: true, source: 'mpd', dr: { state: 'ready', track_age_blocks: 101, total_blocks: 101 }, dr_blocks: Array.from({ length: 101 }, () => loud) });
assert.equal(estimate.viewBlocks().length, 1);
estimate.setView(80, 101);
assert.equal(estimate.viewBlocks().length, 21);
estimate.setView(80, 90);
estimate.extendView('end');
assert.equal(estimate.viewRange().end, 101);
estimate.fullView();
assert.equal(estimate.viewBlocks().length, 101);
assert.equal(estimate.summary().value, liveValue);
listener.close();

// Cancel restores the opening viewport even after Reset; Done keeps Full window.
estimate.setView(60, 80);
const controls = () => {
    const found = [];
    const walk = el => { if (!el || typeof el !== 'object') return; if (el.tag === 'button') found.push(el); (el.kids || []).forEach(walk); };
    walk(overlay.kids.at(-1));
    return Object.fromEntries(found.map(b => [b.kids[0], b]));
};
K.drViewPopup();
controls().Reset.attrs.onclick();
assert.equal(estimate.viewBlocks().length, 0);
controls().Cancel.attrs.onclick();
assert.equal(estimate.viewRange().start, 60);
assert.equal(estimate.viewRange().end, 80);
K.drViewPopup();
controls()['Full window'].attrs.onclick();
controls().Done.attrs.onclick();
assert.equal(estimate.viewBlocks().length, 101);
console.log('ok');
