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

// The recent list, offered on an empty box or over a selected one: a pick searches.
P = launch([]);
P.go = () => { P.searched = (P.searched || 0) + 1; P.valueAtGo = P.input.value; };
P.learn(['Bach Mass in B minor']);
type(P, 'Mahler Symphony');
P.input.selectionStart = 0;              // the tap that focuses the box selects its text
P.showSuggestions(P.suggestions(P.input.value, 0));
P.pick(0);
assert.equal(P.valueAtGo, 'Bach Mass in B minor', 'a recent entry replaces the box whole');
assert.equal(P.searched, 1, 'a recent entry starts its search');

P.request = 'q=Mahler';
P.stopSearch = () => { P.stopped = true; P.request = null; };
P.input.value = ''; P.input.selectionStart = 0;
P.showSuggestions(P.suggestions('', 0));
P.pick(0);
assert.ok(P.stopped && P.searched === 2, 'a search under way gives way to the entry, not just stops');

// The arrow only fills: the whole entry and a space, ready for more words, no search.
P.input.value = 'Mahler Symphony'; P.input.selectionStart = 0;
P.showSuggestions(P.suggestions(P.input.value, 0));
P.pick(0, { fill: true });
assert.equal(P.input.value, 'Bach Mass in B minor ', 'the arrow leaves the entry and a space');
assert.equal(P.input.selectionStart, P.input.value.length, 'the caret ends after the space');
assert.equal(P.searched, 2, 'the arrow does not search');

// Something played before, offered while typing: the tap completes and searches, the arrow only completes.
P.learn(['Karajan']);
type(P, 'Mahler kar');
P.showSuggestions(P.suggestions(P.input.value, 10));
P.pick(0);
assert.equal(P.valueAtGo, 'Mahler Karajan', 'a played entry completes what is typed, then searches');
assert.equal(P.searched, 3);
type(P, 'Mahler kar');
P.showSuggestions(P.suggestions(P.input.value, 10));
P.pick(0, { fill: true });
assert.equal(P.input.value, 'Mahler Karajan ', 'its arrow only completes');
assert.equal(P.searched, 3);

// A word of the shipped lists only completes: more words may follow.
P = launch(['Beethoven']);
P.go = () => assert.fail('a completion must not search');
type(P, 'beet');
P.showSuggestions(P.suggestions(P.input.value, 4));
P.pick(0);
assert.equal(P.input.value, 'Beethoven ');

// The labels of an artist typed: P.artists entries are flat ({ ...entry, term, labels }).
// A throw here, on every pick ending in an artist, left the stale completions up.
P = { plain: t => t.toLowerCase() };
const hints = vm.createContext({ P });
vm.runInContext(code.slice(0, code.indexOf('P.words = []'))
    + source.slice(source.indexOf('P.hintsFor = '), source.indexOf('P.paintHints = '))
    + `\nP.artists = [{ ...entry('The Beatles'), term: 'the beatles', labels: ['Apple'] }]; P.artistPairs = [];`, hints);
assert.deepEqual(JSON.parse(JSON.stringify(P.hintsFor('beethoven the beatles'))),
    [{ artist: 'The Beatles', labels: ['Apple'], term: 'the beatles' }], 'an artist at the end offers its labels');
console.log('PASS: a completion replaces what is typed, however late it is picked');
