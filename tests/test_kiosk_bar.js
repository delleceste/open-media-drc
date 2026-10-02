/* Run with node tests/test_kiosk_bar.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/main.js', 'utf8');
const barCode = source.slice(source.indexOf('const BAR_MS ='), source.indexOf('const CONTROLS ='));
const classes = new Set();
let landscape = true;
let timer;
const reported = [];
const context = {
    K: { inApp: true, pages: [{ id: 'now' }, { id: 'cover' }] },
    window: {
        matchMedia: () => ({ matches: landscape }),
        OmdrcApp: { setNowPage: value => reported.push(value) },
    },
    document: { body: { classList: {
        toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
    } } },
    clearTimeout: () => { timer = null; },
    setTimeout: fn => { timer = fn; return 1; },
};
vm.createContext(context);
vm.runInContext(`let cur = 0; ${barCode}
    this.select = i => { cur = i; syncBarMode(); };
    this.sync = syncBarMode;
    this.show = K.showBar;`, context);

context.sync();
assert(!classes.has('bar-shown') && !classes.has('bar-persistent'));
context.show();
assert(classes.has('bar-shown') && timer, 'Now landscape appears temporarily on touch');
timer();
assert(!classes.has('bar-shown'), 'Now landscape hides again');

landscape = false;
context.sync();
assert(classes.has('bar-shown') && classes.has('bar-persistent'));
context.show(false);
assert(classes.has('bar-shown') && !timer, 'Now portrait stays visible');

landscape = true;
context.select(1);
assert(classes.has('bar-shown') && classes.has('bar-persistent'), 'other landscape pages stay visible');
assert.deepEqual(reported, [true, true, false]);
console.log('PASS: only Now landscape uses the temporary app bar');
