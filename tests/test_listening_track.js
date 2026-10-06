const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const saved = new Map();
const localStorage = {
    getItem: key => saved.get(key) || null,
    setItem: (key, value) => saved.set(key, value),
    removeItem: key => saved.delete(key),
};
const K = { h: () => ({}), api: async () => ({ ok: true, provider: 'claude_account' }) };
const source = fs.readFileSync(path.join(__dirname, '../omdrc-ctrl/src/kiosk/static/widgets/listening.js'), 'utf8');
vm.runInNewContext(source, { K, console, localStorage });
const guide = K.listening;

guide.tracks = [
    { id: 'a', title: 'Another Brick in the Wall, Pt. 1' },
    { id: 'b', title: 'Another Brick in the Wall, Pt. 2' },
    { id: 'c', title: 'Prokofiev: Piano Concerto No. 5: II. Moderato' },
];
guide.track = { title: 'Another Brick in the Wall (Part II)' };
assert.equal(guide.trackNumber(), 2, 'part numbers must distinguish neighboring songs');
guide.track = { title: 'Prokofiev: Piano Concerto No. 5: II. Moderato', track_id: 'c' };
assert.equal(guide.trackNumber(), 3, 'Qobuz track ID takes priority');
guide.track = { title: 'Unknown track' };
assert.equal(guide.trackNumber(), 0, 'unknown tracks must not select an unrelated composition');
let requested = 0;
guide.research = () => { requested++; };
guide.active = true;
guide.track = null;
guide.observe({ ok: true, state: 'stop', title: 'Pezzi di vetro', album: 'Rimmel', artist: 'Francesco De Gregori' });
assert.equal(requested, 1, 'the last known track must remain researchable while stopped');
guide.guide = { compositions: [{ tracks: [1] }, { tracks: [2] }] };
guide.tracks = [{ id: 'one', title: 'First movement' }, { id: 'two', title: 'Second movement' }];
guide.albumKey = 'q:album-one';
guide.track = { ok: true, qobuz_album: 'album-one', track_id: 'one', title: 'First movement' };
guide.userSelected = true;
guide.selected = 1;
guide.selectedTrack = 2;
guide.observe({ ok: true, qobuz_album: 'album-one', track_id: 'two', title: 'Second movement' });
assert.equal(guide.selected, 1, 'playback must preserve a manually selected composition');
assert.equal(guide.selectedTrack, 2, 'playback must preserve a manually selected track');
guide.selected = -1;
guide.selectedTrack = null;
guide.observe({ ok: true, qobuz_album: 'album-one', track_id: 'one', title: 'First movement' });
assert.equal(guide.selected, -1, 'playback must preserve a manually selected overview');
guide.observe({ ok: true, qobuz_album: 'album-two', track_id: 'one', title: 'New album' });
assert.equal(guide.userSelected, false, 'a new album starts with automatic selection');
guide.guide = { overview: 'Album overview', compositions: [{ title: 'New work', tracks: [1], text: 'Work details' }] };
guide.tracks = [{ id: 'one', title: 'New album' }];
guide.selected = 0;
guide.selectedTrack = 1;
guide.userSelected = true;
guide.remember();
const remembered = JSON.parse(saved.get('omdrc.listening.v1'));
assert.equal(remembered.guide.overview, 'Album overview', 'completed research must survive a restart');
assert.equal(remembered.selectedTrack, 1, 'the selected tab must survive a restart');
guide.forget();
assert.equal(saved.has('omdrc.listening.v1'), false, 'closing the guide removes the saved session');
guide.tracks = [{ title: 'One' }, { title: 'Two' }, { title: 'Three' }];
guide.guide = { compositions: [{ tracks: [2] }, { tracks: [1] }, { tracks: [3] }] };
assert.equal(guide.singleTrackSections(), true, 'one section per track needs just the top tabs');
guide.guide.compositions[0].tracks = [1, 2];
assert.equal(guide.singleTrackSections(), false, 'a multi-track work keeps separate track navigation');
guide.cacheResult('claude:album-one', { ok: true, compositions: [{ title: 'Work', tracks: [1] }] });
assert.equal(guide.cachedResult('claude:album-one').compositions[0].title, 'Work', 'same query reuses the result');
assert.equal(guide.cachedResult('claude:album-two'), null, 'a changed query misses the cache');
guide.cacheResult(null, { ok: true, compositions: [{ title: 'Earlier guide' }] }, 'same-material');
assert.equal(guide.cachedResult('new-exact-key', 'same-material').compositions[0].title, 'Earlier guide',
    'a guide saved before exact query keys existed remains reusable');
assert.equal(guide.cachedResult('new-exact-key', 'other-material'), null,
    'a changed material cannot reuse the earlier guide');
guide.forget();
assert.ok(saved.has('omdrc.listening.last-result.v1'), 'closing keeps the last completed result');
console.log('listening track matching OK');
