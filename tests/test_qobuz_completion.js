/* Run with node tests/test_qobuz_completion.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/pages/qobuz.js', 'utf8');
const code = source.slice(source.indexOf('const FOLD1 = '), source.indexOf('P.suggestKey = '));
function launch(words) {
    const input = { value: '', selectionStart: 0, blurred: 0,
        setSelectionRange(a) { this.selectionStart = a; }, focus() {}, blur() { this.blurred++; } };
    const P = { input, plain: t => t.toLowerCase(), scoped: () => false, syncLabels() {}, paintStale() {} };
    const K = { clear() {} };
    const context = { P, K, h: () => ({}), document: { activeElement: input } };
    vm.createContext(context);
    vm.runInContext(code + '\nP.words = (' + JSON.stringify(words) + ').map(w => ({ ...entry(w), term: w }));', context);
    P.showSuggestions = list => { P.shown = list; };
    return P;
}
const type = (P, text) => { P.input.value = text; P.input.selectionStart = text.length; };

let P = launch(['Beethoven', 'Piano Concerto']);
type(P, 'beet');
P.showSuggestions(P.suggestions(P.input.value, 4));
type(P, 'beethov');                      // typed on before the list followed
P.pick(0);
assert.equal(P.input.value, 'Beethoven ', 'letters typed after the list was drawn are replaced');

type(P, 'Mahler piano co');
P.showSuggestions(P.suggestions(P.input.value, P.input.value.length));
P.pick(P.shown.findIndex(s => s.e.text === 'Piano Concerto'));
assert.equal(P.input.value, 'Mahler Piano Concerto ', 'a multi-word fragment is replaced whole');

P = launch(['Beethoven']);
type(P, 'beet');
P.showSuggestions(P.suggestions(P.input.value, 4));
type(P, 'beetx');                        // no longer a prefix of the entry
P.pick(0);
assert.equal(P.input.value, 'Beethoven ', 'the word being typed is replaced even when it stopped matching');

type(P, 'beet sonata');
P.input.selectionStart = 2;              // caret inside the word
P.showSuggestions(P.suggestions(P.input.value, 2));
P.pick(0);
assert.equal(P.input.value, 'Beethoven sonata', 'the rest of the word after the caret goes too');

type(P, 'beet');
P.showSuggestions(P.suggestions(P.input.value, 4));
P.composing = true;
P.pick(0);
assert.equal(P.input.blurred, 1, 'a word still being composed is ended first');
console.log('PASS: a completion replaces what is typed, however late it is picked');
