/* Run with node tests/test_now_clips.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('omdrc-ctrl/src/kiosk/static/pages/now.js', 'utf8');
function freshPage() {
    let page;
    const context = {
        K: {
            h() {},
            pref(key) { if (key === 'now.clipChannels') return { left: 9, right: 4 }; },
            setPref() { throw new Error('Clip history must not be persisted'); },
            registerPage(p) { page = p; },
        },
        document: { addEventListener() {} },
        matchMedia: () => ({ addEventListener() {} }),
    };
    vm.runInNewContext(source, context);
    page.vu = { setClips(value) { this.clips = { ...value }; }, setSuspects(value) { this.suspects = value; } };
    page.initClips();
    return page;
}
const album = (id, title = 'Album A', extra = {}) => ({ ok: true, state: 'play', qobuz_album: id, album: title, edition: '', ...extra });
const page = freshPage();
assert.equal(page.clipChannels.left, 0, 'Old saved clips must not return on app launch');
page.observeClipAlbum(album('a'));
page.clipChannels = { left: 3, right: 2 };
page.clipSignal = { left: true, right: true };
page.observeClipAlbum(album('a', 'Album A', { title: 'Next track', artist: 'Another performer' }));
assert.equal(page.clipChannels.left, 3, 'Same-album tracks must keep clips');
page.observeClipAlbum(album('a', 'Album A', { state: 'pause' }));
assert.equal(page.clipChannels.right, 2, 'Pause must keep clips');
page.observeClipAlbum(album('', '', { state: 'stop' }));
page.observeClipAlbum({ ok: false, state: 'stop' });
page.observeClipAlbum(album('a'));
assert.equal(page.clipChannels.left, 3, 'Stop and missing metadata must keep clips');
page.observeClipAlbum(album('b', 'Album A'));
assert.equal(page.clipChannels.left, 0, 'Different album IDs with the same title must reset clips');
assert.equal(page.clipChannels.right, 0);
assert.equal(page.clipSignal.left, false, 'The new album must count its own clip episodes');
assert.equal(page.vu.clips.left, 0, 'The visible meter must reset');
page.observeClipAlbum(album('', 'Local album'));
page.clipChannels.left = 1;
page.observeClipAlbum(album('', 'Next local album'));
assert.equal(page.clipChannels.left, 0, 'Local albums without catalogue IDs must reset by title');
page.clipChannels.left = 5;
assert.equal(freshPage().clipChannels.left, 0, 'A new app process must start without the previous latch');
console.log('PASS: clip reset on album change and fresh app launch; retained across same-album tracks, pause and stop');
