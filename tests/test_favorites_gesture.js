const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function setup() {
    let nextTimer = 0;
    const timers = new Map();
    const window = new EventTarget();
    const K = { h: () => ({}) };
    const code = fs.readFileSync('omdrc-ctrl/src/kiosk/static/pages/favorites.js', 'utf8');
    vm.runInNewContext(code, {
        K, window,
        setTimeout: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
        clearTimeout: id => timers.delete(id),
    });
    const page = K.library;
    page.visible = true;
    let menus = 0;
    page.folderMenu = () => { menus++; };
    const button = new EventTarget();
    const fire = (target, type, x = 100) => {
        const event = new Event(type, { cancelable: true });
        Object.assign(event, { button: 0, pointerId: 1, clientX: x, clientY: 100 });
        target.dispatchEvent(event);
    };
    const runTimers = () => {
        for (const [id, callback] of [...timers]) {
            timers.delete(id);
            callback();
        }
    };
    page.folderHold(button, 'Classical/BIS', () => {});
    return { K, page, window, button, fire, runTimers, menuCount: () => menus };
}

test('a pager swipe cannot trigger a folder long press or context menu', () => {
    const app = setup();
    app.fire(app.button, 'pointerdown');
    app.fire(app.window, 'pointermove', 140); // pager has captured the pointer
    app.fire(app.window, 'pointerup', 140);
    app.runTimers();
    app.fire(app.button, 'contextmenu', 140);
    assert.equal(app.menuCount(), 0);
});

test('a stationary hold opens its menu once', () => {
    const app = setup();
    app.fire(app.button, 'pointerdown');
    app.runTimers();
    app.fire(app.button, 'contextmenu');
    assert.equal(app.menuCount(), 1);
});

test('leaving Library cancels a pending hold', () => {
    const app = setup();
    app.fire(app.button, 'pointerdown');
    app.page.hide();
    app.runTimers();
    app.fire(app.button, 'contextmenu');
    assert.equal(app.menuCount(), 0);
});

test('refresh reads Qobuz before opening the drop destination', async () => {
    const app = setup();
    const events = [];
    app.page.path = ['Source'];
    app.page.message = { textContent: '' };
    app.page.paint = async () => { events.push(`paint:${app.page.pathName()}`); };
    app.page.reveal = () => { events.push('reveal'); };
    app.K.api = async () => {
        events.push('read');
        return { ok: true, folders: [{ path: 'Target/Child' }], albums: [], covers: {}, order: {} };
    };
    assert.equal(await app.page.refresh('Target'), true);
    assert.deepEqual(events, ['read', 'paint:Target', 'reveal']);
    assert.equal(app.page.pathName(), 'Target');
});

test('failed refresh leaves the selected folder unchanged', async () => {
    const app = setup();
    app.page.path = ['Source'];
    app.page.message = { textContent: '' };
    app.K.api = async () => ({ ok: false, error: 'Qobuz unavailable' });
    assert.equal(await app.page.refresh('Target'), false);
    assert.equal(app.page.pathName(), 'Source');
    assert.equal(app.page.message.textContent, 'Qobuz unavailable');
});

test('list arrows save sibling order under the correct parent', async () => {
    const app = setup();
    const saved = [];
    const rows = ['f:Classical/BIS', 'f:Classical/ECM', 'a:42']
        .map(key => ({ dataset: { key } }));
    app.page.tree = { querySelectorAll: () => [{ dataset: { parent: 'Classical' }, children: rows }] };
    app.page.order = {};
    app.page.paint = async () => {};
    app.K.api = async (url, options) => { saved.push({ url, ...options.json }); return { ok: true }; };
    await app.page.moveEntry('Classical', 'f:Classical/ECM', -1);
    assert.deepEqual([...app.page.order.Classical], ['f:Classical/ECM', 'f:Classical/BIS', 'a:42']);
    assert.equal(saved[0].url, '/qobuz/favorites/order');
    assert.equal(saved[0].parent, 'Classical');
    assert.deepEqual([...saved[0].keys], [...app.page.order.Classical]);
});

test('list arrows restore order when saving fails', async () => {
    const app = setup();
    const rows = ['f:A', 'f:B'].map(key => ({ dataset: { key } }));
    app.page.tree = { querySelectorAll: () => [{ dataset: { parent: '' }, children: rows }] };
    app.page.order = { '': ['f:A', 'f:B'] };
    app.page.paint = async () => {};
    app.K.api = async () => ({ ok: false, error: 'save failed' });
    app.K.toast = () => {};
    await app.page.moveEntry('', 'f:B', -1);
    assert.deepEqual([...app.page.order['']], ['f:A', 'f:B']);
});
