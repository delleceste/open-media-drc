/* Run with node tests/test_dr_pages.js.  DR albums open as a separate child
 * list and return to the same parent list on touch. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const h = (tag, attrs = {}, ...kids) => ({
    tag, attrs, kids: kids.flat(Infinity).filter(x => x !== null && x !== undefined && x !== false),
    hidden: !!attrs.hidden, scrollTop: 0,
    append(...children) { this.kids.push(...children.flat(Infinity)); },
});
const summary = { dr: 11, kind: 'estimate', basis: 'listened', heard: 2, track_count: 10 };
const track = (album_key, number, title, at) => ({ album_key, number, title, at,
    album_title: album_key === 'local:a' ? 'Avalon' : 'Single album', artist: 'Roxy Music',
    source: 'local', image: '', dr: 10 + number, complete: false, seconds: 100, method: 'live' });
const fresh = { key: 'local:fresh', title: 'Unplugged', artist: 'Nirvana', image: '', source: 'local', ref: 'fresh',
    origin: '', report_dr: 9, report_tracks: 14, report_mtime: 2.5, tracks: [], dr: { dr: 9, kind: 'exact', basis: 'report' } };
const recent = { ok: true, count: 3, summaries: { 'local:a': summary }, tracks: [
    track('local:a', 2, 'Avalon', 3), track('local:b', 1, 'Only song', 2), track('local:a', 1, 'More Than This', 1),
], reports: [fresh, { ...fresh, key: 'local:a', report_mtime: 4 }] };
const ranked = { ok: true, count: 2, totals: {}, import: {}, albums: [{
    key: 'local:a', title: 'Avalon', artist: 'Roxy Music', image: '', source: 'local', ref: 'a',
    report_dr: null, dr: summary, tracks: recent.tracks.filter(t => t.album_key === 'local:a'),
}, {
    key: 'local:report', title: 'Report album', artist: 'Artist', image: '', source: 'local', ref: 'report',
    report_dr: 12, dr: { dr: 12, kind: 'exact', basis: 'report', origins: [] }, tracks: [],
}] };
const K = {
    pages: [], h, clear: el => { el.kids = []; return el; },
    registerPage(page) { this.pages.push(page); },
    card: (_title, ...kids) => h('section', {}, ...kids), cardOpts: () => null,
    dr: { color: () => ({ bg: '#fff', fg: '#000' }), basisText: () => 'estimate' },
    drLogBadge: value => value?.dr ? h('badge', {}, `DR${value.dr}`) : null,
    api: async url => url.startsWith('/dr/library/recent') ? recent
        : url.startsWith('/dr/library/album') ? { ok: true, album: { report_tracks: 1, track_count: 8, single_flac_cue: true, report_track_rows: [
            { number: 1, title: 'Reported Song', dr: 12 },
        ] } } : ranked,
};
vm.runInNewContext(fs.readFileSync('omdrc-ctrl/src/kiosk/static/pages/dr.js', 'utf8'), {
    K, document: { addEventListener() {} }, URLSearchParams, Date, Math,
});

(async () => {
    const R = K.pages.find(page => page.id === 'dr_recent');
    R.body = h('div'); R.mount(R.body); await R.load();
    assert.equal(R.groups.length, 3, 'a report of an album with saved tracks adds no row');
    assert.equal(R.list.kids[0].tag, 'button', 'two tracks become one album row');
    assert.match(R.list.kids[0].kids[0].attrs.src, /\/dr\/library\/art\?key=local%3Aa/);
    assert.equal(R.list.kids[1].kids[2].kids[0].kids[0], 'Unplugged', 'a fresh dr14.txt is listed by its time');
    assert.match(R.list.kids[1].kids[2].kids[2].kids[0], /dr14\.txt of 14 tracks/);
    assert.equal(R.list.kids[2].tag, 'div', 'isolated song stays in the first list');
    await R.list.kids[1].attrs.onclick();
    assert.equal(R.detail.kids[1].kids[1].kids[1].kids[0].kids[0], '1. Reported Song', 'report opens its per-song rows');
    R.detail.kids[0].attrs.onclick();
    R.list.kids[0].attrs.onclick();
    assert.equal(R.browse.hidden, true);
    assert.equal(R.detail.hidden, false);
    assert.equal(R.detail.kids[0].tag, 'button', 'album header is the back target');
    assert.equal(R.detail.kids[0].kids[0].kids[0], '‹');
    assert.equal(R.detail.kids[1].kids.length, 2, 'album child list contains both tracks');
    R.detail.kids[0].attrs.onclick();
    assert.equal(R.browse.hidden, false);

    const A = K.pages.find(page => page.id === 'dr_albums');
    A.body = h('div'); A.mount(A.body); await A.rankLoad();
    const albumButton = A.rankList.kids[0].kids[0].kids[1];
    albumButton.attrs.onclick();
    assert.equal(A.browse.hidden, true);
    assert.equal(A.detail.kids[0].tag, 'button');
    assert.equal(A.detail.kids[0].kids[0].kids[0], '‹');
    assert.equal(A.detail.kids[1].kids.length, 2);
    A.detail.kids[0].attrs.onclick();
    assert.equal(A.browse.hidden, false);
    const reportButton = A.rankList.kids[1].kids[0].kids[1];
    await reportButton.attrs.onclick();
    assert.match(A.detail.kids[1].kids[0].kids[0], /1 FLAC \+ 1 CUE: dr14.txt measured the whole FLAC/);
    assert.equal(A.detail.kids[1].kids[1].kids[1].kids[0].kids[0], '1. Reported Song');
    console.log('PASS: recent DR groups albums, leaves singles, and both DR lists drill down and back');
})().catch(e => { console.error(e); process.exitCode = 1; });
