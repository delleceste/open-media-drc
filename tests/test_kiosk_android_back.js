/* Run with node tests/test_kiosk_android_back.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/main.js', 'utf8');
const backCode = source.slice(source.indexOf("// The page menu's order, top to bottom"),
    source.indexOf('// Commit the page only once'));

// The pager's order; the menu lists Experimental last and Bass depth last within it,
// and a group's pages where its first page stands.
const menu = [
    { id: 'now' }, { id: 'qobuz' }, { id: 'drc' },
    { id: 'bass', menuGroup: 'Experimental' }, { id: 'system' },
    { id: 'lab', menuGroup: 'Experimental' },
    { id: 'dr', menuGroup: 'DR' }, { id: 'logs' }, { id: 'history', menuGroup: 'DR' },
];

// overlays: [{ name, z, scrim, back }] in DOM order.  `back` registers backClose;
// a scrim without it records the backdrop tap.
function back(page, { overlays = [], menuOpen = false, pages = menu } = {}) {
    const calls = [];
    const els = overlays.map(o => ({
        z: o.z,
        classList: { contains: c => c === 'scrim' && !!o.scrim },
        backClose: o.back ? () => calls.push(o.name + ':back') : null,
        click: () => calls.push(o.name + ':tap'),
    }));
    const K = { pages, showPage: id => calls.push(id) };
    const context = {
        K, cur: pages.findIndex(p => p.id === page),
        document: {
            querySelector: () => menuOpen ? { closeMenu: () => calls.push('menu:close') } : null,
            querySelectorAll: () => els,
        },
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
assert.deepEqual(back('qobuz', { menuOpen: true, overlays: [player] }),
    { handled: true, calls: ['menu:close'] }, 'the open page menu closes first');

// The menu reads now, qobuz, drc, system, dr, history, logs, lab, bass.
const walk = [['bass', 'lab'], ['lab', 'logs'], ['logs', 'history'], ['history', 'dr'],
    ['dr', 'system'], ['system', 'drc'], ['drc', 'qobuz'], ['qobuz', 'now']];
for (const [from, to] of walk)
    assert.deepEqual(back(from), { handled: true, calls: [to] }, `${from} goes up to ${to}`);
assert.deepEqual(back('now'), { handled: false, calls: [] }, 'Now leaves the app');
assert.deepEqual(back('now', { overlays: [strip] }), { handled: false, calls: [] },
    'a minimized guide strip does not hold the app back');
console.log('PASS: Android Back closes the topmost popup, then walks the page menu up to Now');
