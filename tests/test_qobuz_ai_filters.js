/* Run with node tests/test_qobuz_ai_filters.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/pages/qobuz.js', 'utf8');
const code = source.slice(source.indexOf('const DATE_KEYS = '), source.indexOf('// ── search ──'));
function launch(prefs) {
    const store = { ...prefs };
    const P = { selected: new Set(store.labels || []), paintLabels() {}, paintDate() {} };
    const context = { P, YEAR_MIN: 1900, thisYear: 2026,
        pref: (k, d) => (k in store ? store[k] : d), setPref: (k, v) => { store[k] = v; } };
    vm.createContext(context);
    vm.runInContext(code, context);
    return { P, store };
}

let { P, store } = launch({});
P.applyAIFilters({ labels: ['Pentatone'], last: 10 });
assert.deepEqual([...P.selected], ['Pentatone']);
assert.equal(store.date, 'last'); assert.equal(store.lastN, 10);
assert.equal(P.aiFiltersText({ labels: ['Pentatone'], last: 10 }), 'Pentatone · last 10 years');
P.revertAIFilters();                         // untouched: the next request starts clean
assert.deepEqual([...P.selected], []); assert.equal(store.date, 'any');

({ P, store } = launch({ date: 'last', lastN: 3 }));
P.applyAIFilters({ labels: ['Pentatone'] });
P.selected.add('Decca');                     // changed by hand: now the user's
P.revertAIFilters();
assert.deepEqual([...P.selected].sort(), ['Decca', 'Pentatone']);
assert.equal(store.lastN, 3);

({ P, store } = launch({}));
P.applyAIFilters({ last: 50 });               // beyond the slider: a span
assert.equal(store.date, 'span'); assert.equal(store.from, 1976);
P.applyAIFilters({ from: 2015 });
assert.equal(P.aiFiltersText({ from: 2015 }), '2015–today');
console.log('ok');
