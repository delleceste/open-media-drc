/* Run with node tests/test_kiosk_screen_awake.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/main.js', 'utf8');
const screenCode = source.slice(source.indexOf('K.inApp ='), source.indexOf('// The app reloads'));
const requests = [];
let now = 100000;
const K = { pages: [{ id: 'now' }], awake: { sync() {} } };
const context = {
    K,
    Date: { now: () => now },
    window: { OmdrcApp: {
        setPageWantsScreenOn: on => requests.push(on),
        setHoldScreenOn() {},
    } },
    document: { hidden: false, addEventListener() {} },
    setInterval() {},
};
vm.createContext(context);
vm.runInContext(`let cur = 0; ${screenCode}\nthis.syncAppScreen = syncAppScreen;`, context);

context.syncAppScreen();
assert.equal(requests.at(-1), false, 'Unknown playback must not hold the screen on');
K.setPlaybackState('play');
assert.equal(requests.at(-1), true, 'Playing with recent audio holds the screen on');
K.setPlaybackState('pause');
assert.equal(requests.at(-1), false, 'Pause releases the screen immediately');
K.markSound();
context.syncAppScreen();
assert.equal(requests.at(-1), false, 'Late meter frames cannot undo pause');
K.setPlaybackState('play');
K.setPlaybackState('stop');
assert.equal(requests.at(-1), false, 'Stop releases the screen immediately');
K.setPlaybackState('play');
now += 30001;
context.syncAppScreen();
assert.equal(requests.at(-1), false, 'A stalled meter stream releases the screen');
K.markSound();
assert.equal(requests.at(-1), true, 'Fresh audio holds the screen on again');
console.log('PASS: playback state and meter activity control the app screen flag');
