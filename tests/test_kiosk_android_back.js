/* Run with node tests/test_kiosk_android_back.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/main.js', 'utf8');
const backCode = source.slice(source.indexOf('K.handleAndroidBack = () => {'),
    source.indexOf('// Commit the page only once'));

function back(page, { album = false, player = false, qobuz = true } = {}) {
    const calls = [];
    const pages = [{ id: 'now' }, ...(qobuz ? [{ id: 'qobuz' }] : []), { id: 'drc' }];
    const K = {
        pages,
        closeAlbumInfo: () => { calls.push('album'); return album; },
        closeQobuzPlayer: () => { calls.push('player'); return player; },
        showPage: id => calls.push(id),
    };
    const context = { K, cur: pages.findIndex(p => p.id === page) };
    vm.runInNewContext(backCode, context);
    return { handled: K.handleAndroidBack(), calls };
}

assert.deepEqual(back('qobuz', { album: true, player: true }),
    { handled: true, calls: ['album'] });
assert.deepEqual(back('qobuz', { player: true }),
    { handled: true, calls: ['album', 'player'] });
assert.deepEqual(back('now'),
    { handled: true, calls: ['album', 'player', 'qobuz'] });
assert.deepEqual(back('now', { qobuz: false }),
    { handled: false, calls: ['album', 'player'] });
assert.deepEqual(back('drc'),
    { handled: false, calls: ['album', 'player'] });
console.log('PASS: Android Back closes details and player, then leaves Now for Qobuz');
