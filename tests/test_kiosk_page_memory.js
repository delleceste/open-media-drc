/* Run with node tests/test_kiosk_page_memory.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/main.js', 'utf8');
const pagerCode = source.slice(source.indexOf('let cur = -1;'), source.indexOf('function safe('));
const prefs = {};
function launch(ids = ['now', 'cover', 'qobuz', 'drc', 'config']) {
    const K = {
        pages: ids.map(id => ({ id, mounted: true, label: id })),
        state: { features: {} },
        pref: (key, fallback) => prefs[key] ?? fallback,
        setPref: (key, value) => { prefs[key] = value; },
        setTopExtra() {},
    };
    const context = { K, $: () => ({ children: [], textContent: '' }),
        safe: fn => fn(), syncBarMode() {}, syncAppScreen() {}, reportScroll() {}, applyOrientation() {},
        history: { replaceState() {} } };
    vm.createContext(context);
    vm.runInContext(pagerCode + '\nthis.restore = rememberedPage; this.select = id => activate(K.pages.findIndex(p => p.id === id));', context);
    return context;
}
assert.equal(launch().restore(), 'now');
for (const id of ['now', 'cover', 'qobuz']) {
    const app = launch();
    app.select(id);
    app.select('drc');
    app.select('config');
    assert.equal(launch().restore(), id, 'Configuration must not replace the last main page');
}
prefs.lastMainPage = 'cover';
assert.equal(launch(['now', 'qobuz', 'config']).restore(), 'now', 'Disabled Cover falls back to Now');
prefs.lastMainPage = 'config';
assert.equal(launch().restore(), 'now', 'Invalid saved pages fall back to Now');
console.log('PASS: main page survives recreation and configuration; missing pages fall back safely');
