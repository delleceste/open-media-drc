/* Run with node tests/test_track_art.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let response;
const K = { api: async () => response };
vm.runInNewContext(fs.readFileSync('omdrc-ctrl/src/kiosk/static/widgets/track.js', 'utf8'), { K });
(async () => {
    for (const state of ['play', 'pause', 'stop']) {
        response = { ok: true, line1: '[stopped] Artist - Song · Album', playback_state: state,
            art: '/qconnect/art?v=album', edition: '2026', qobuz_album: 'album' };
        const track = await K.fetchTrack();
        assert.equal(track.state, state);
        assert.equal(track.art, response.art, 'Artwork must survive stop');
        assert.equal(track.edition, response.edition);
    }
    response = { ok: true, line1: 'New song', playback_state: 'play' };
    assert.equal((await K.fetchTrack()).art, '', 'A new track without artwork must not inherit the old cover');
    console.log('PASS: artwork and edition retained during play, pause and stop');
})().catch(error => { console.error(error); process.exitCode = 1; });
