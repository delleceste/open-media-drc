/* Run with node tests/test_dr_history_delta.js.  A compact DR stream sends the
 * history whole once, then only the blocks that follow (app.py, spectrum_stream);
 * the estimator must end up with exactly the box's history - the full 90-minute
 * window included - and must not guess when blocks do not follow on. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let frameReceived, opened = 0, closed = 0;
const pending = [];
const K = {
    h: () => ({}), clamp: (v, a, b) => Math.min(b, Math.max(a, v)), clear: el => el,
    pref: (_k, d) => d, setPref() {},
    streams: { open: (_mode, fn) => { opened++; frameReceived = fn; return { close() { closed++; } }; } },
};
const context = { K, setTimeout: fn => pending.push(fn), Math, JSON };
vm.createContext(context);
vm.runInContext(fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/dr.js', 'utf8'), context);
const E = K.drEstimate;
const plain = v => JSON.parse(JSON.stringify(v));       // across the vm's realm

// The box: a bounded window that grows at its end, and one connection's view of it.
const WINDOW = 1800;                                    // 5400 s of 3-s blocks
const box = { history: [], end: 0, gen: 0, conn: { gen: -1, end: 0 }, seq: 0 };
const block = i => [[i * 1e-6, i * 2e-6], [(i % 97) / 97, (i % 89) / 89]];
const grow = n => {
    for (let i = 0; i < n; i++) box.history.push(block(box.end++));
    if (box.history.length > WINDOW) box.history = box.history.slice(-WINDOW);
};
const reset = () => { box.history = []; box.end = 0; box.gen++; };
const send = () => {
    const start = box.end - box.history.length, c = box.conn;
    const f = { ok: true, state: 'running', source: 'mpd', dr: { state: 'ready', total_blocks: box.end, seq: box.seq++ },
                dr_blocks_end: box.end, dr_blocks_len: box.history.length };
    if (c.gen === box.gen && start <= c.end && c.end <= box.end) f.dr_blocks_add = box.history.slice(c.end - start);
    else f.dr_blocks = box.history.slice();
    box.conn = { gen: box.gen, end: box.end };
    frameReceived(f);
    return f;
};

E.listen(() => {});
assert.equal(send().dr_blocks.length, 0, 'a connection starts with the whole history');
let deltas = 0;
for (let t = 0; t < 3600; t++) {                        // three hours, a publish every 1-3 blocks
    grow(1 + (t % 3));
    if (t === 1000 || t === 1500) reset();              // the history started afresh
    const f = send();
    if (f.dr_blocks_add) deltas++;
    assert.deepEqual(plain(E.blocks), box.history, `in step after publish ${t}`);
}
assert.equal(E.blocks.length, WINDOW, 'the full 90-minute window is kept');
assert.ok(deltas > 3500, 'all but the resets went as additions');

// Blocks that do not follow on are not guessed at: the stream is opened again.
const before = plain(E.blocks);
frameReceived({ ok: true, state: 'running', source: 'mpd', dr: { state: 'ready' },
    dr_blocks_end: box.end + 5, dr_blocks_len: WINDOW, dr_blocks_add: [block(1)] });
assert.deepEqual(plain(E.blocks), before, 'a gap is not applied');
assert.equal(closed, 1);
pending.splice(0).forEach(fn => fn());
assert.equal(opened, 2, 'reopened, for the whole history');
box.conn = { gen: -1, end: 0 };                         // the new connection
assert.ok(send().dr_blocks);
assert.deepEqual(plain(E.blocks), box.history);

// A frame that changes nothing the estimator reads does not repaint.
let paints = 0;
E.listen(() => paints++);
paints = 0;
const same = { ok: true, state: 'running', source: 'mpd', dr: { state: 'ready', total_blocks: box.end, seq: box.seq - 1 } };
frameReceived(same);
assert.equal(paints, 0);
console.log('DR history deltas: passed');
