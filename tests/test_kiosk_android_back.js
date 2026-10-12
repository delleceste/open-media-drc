/* Run with node tests/test_kiosk_android_back.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/main.js', 'utf8');
const backCode = source.slice(source.indexOf('K.handleAndroidBack = () => {'),
    source.indexOf('// Commit the page only once'));

// overlays: [{ name, z, scrim, back }] in DOM order.  `back` registers backClose;
// a scrim without it records the backdrop tap.
function back(page, { overlays = [], qobuz = true } = {}) {
    const calls = [];
    const pages = [{ id: 'now' }, ...(qobuz ? [{ id: 'qobuz' }] : []), { id: 'drc' }];
    const els = overlays.map(o => ({
        z: o.z,
        classList: { contains: c => c === 'scrim' && !!o.scrim },
        backClose: o.back ? () => calls.push(o.name + ':back') : null,
        click: () => calls.push(o.name + ':tap'),
    }));
    const K = { pages, showPage: id => calls.push(id) };
    const context = {
        K, cur: pages.findIndex(p => p.id === page),
        document: { querySelectorAll: () => els },
        getComputedStyle: el => ({ zIndex: String(el.z) }),
    };
    vm.runInNewContext(backCode, context);
    return { handled: K.handleAndroidBack(), calls };
}

const player = { name: 'player', z: 55, scrim: true, back: true };
const details = { name: 'details', z: 58, scrim: true, back: true };
const confirm = { name: 'confirm', z: 50, scrim: true };
const strip = { name: 'strip', z: 67 };                     // collapsed guide: no backClose
const guide = { name: 'guide', z: 67, back: true };

assert.deepEqual(back('qobuz', { overlays: [player, details] }),
    { handled: true, calls: ['details:back'] });
assert.deepEqual(back('qobuz', { overlays: [details, player] }),
    { handled: true, calls: ['details:back'] }, 'the higher z-index wins over DOM order');
assert.deepEqual(back('qobuz', { overlays: [confirm, { ...confirm, name: 'confirm2' }] }),
    { handled: true, calls: ['confirm2:tap'] }, 'equal z: the later one is on top');
assert.deepEqual(back('qobuz', { overlays: [strip, confirm] }),
    { handled: true, calls: ['confirm:tap'] }, 'a minimized guide strip is not a popup');
assert.deepEqual(back('qobuz', { overlays: [player, guide] }),
    { handled: true, calls: ['guide:back'] });
assert.deepEqual(back('now', { overlays: [strip] }),
    { handled: true, calls: ['qobuz'] });
assert.deepEqual(back('now', { qobuz: false }),
    { handled: false, calls: [] });
assert.deepEqual(back('drc'),
    { handled: false, calls: [] });
console.log('PASS: Android Back closes the topmost popup, then leaves Now for Qobuz');
