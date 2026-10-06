const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const K = { h: () => ({}), api: async () => ({ ok: true, provider: 'claude_account' }) };
const source = fs.readFileSync(path.join(__dirname, '../omdrc-ctrl/src/kiosk/static/widgets/listening.js'), 'utf8');
vm.runInNewContext(source, { K, console });
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
console.log('listening track matching OK');
