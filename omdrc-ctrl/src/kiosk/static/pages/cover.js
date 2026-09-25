/* Page 2 — Cover (optional, Config → Cover art): the album cover, whole and
 * square, as big as the screen height allows, the track beside it, and nothing
 * else.  Where there is room, one narrow vertical extra at the right: the DR
 * estimate or the level bars (top bar: DR / Lvl, either one or neither). */
(() => {
'use strict';
const { h } = K;

const P = {
    id: 'cover', label: 'Cover', title: 'Cover',
    // only in the pager when switched on (main.js, K.refreshPages)
    optional: () => !!K.pref('cover.page', false),
    level: null, drSub: null, base: null,
};

P.mount = el => {
    P.el = el;
    P.img = h('img', { alt: '', hidden: true, onload: e => { e.target.hidden = false; P.none.hidden = true; }, onerror: e => { e.target.hidden = true; P.none.hidden = false; } });
    P.none = h('div', { class: 'cov-none' }, '♪');
    P.artBox = h('div', { class: 'cov-art' }, P.none, P.img);

    P.title = h('div', { class: 'cov-title' }, '—');
    P.artist = h('div', { class: 'cov-artist' });
    P.album = h('div', { class: 'cov-album' });
    P.edition = h('div', { class: 'cov-edition' });
    P.fmt = h('div', { class: 'cov-fmt' });
    P.time = h('div', { class: 'cov-time' });
    P.prog = h('i');
    P.info = h('div', { class: 'cov-info' }, P.title, P.artist, P.album, P.edition,
        h('div', { class: 'cov-foot' }, P.fmt, h('div', { class: 'cov-prog' }, P.prog), P.time));

    P.sideHost = h('div', { class: 'cov-side-body' });
    P.side = h('div', { class: 'cov-side', hidden: true }, P.sideHost);
    P.box = h('div', { class: 'cov' }, P.artBox, P.info, P.side);
    el.append(P.box);

    P.drBtn = h('button', { class: 'chip tog', type: 'button', title: 'DR estimate beside the cover', onclick: () => P.pick('dr') }, 'DR');
    P.lvlBtn = h('button', { class: 'chip tog', type: 'button', title: 'Level bars beside the cover', onclick: () => P.pick('levels') }, 'Lvl');
    P.topBtns = h('span', { class: 'top-toggles' }, P.drBtn, P.lvlBtn);

    new ResizeObserver(() => P.fit()).observe(el);
    P.trackPoll = new K.Poller(P.pollTrack, 3000);
    P.tick = new K.Poller(P.paintTime, 1000);
};

// ── layout ───────────────────────────────────────────────────────────────────
// The cover is a square as tall as the page, but never more than 55% of the width
// (the text needs room); the extra column shows only if the text still keeps 16rem.
const SIDE_REM = 6.5, TEXT_MIN_REM = 16;
P.sideWanted = () => { const s = K.pref('cover.side', 'none'); return s === 'dr' || s === 'levels' ? s : 'none'; };
P.fit = () => {
    if (!P.el || K.portrait()) { if (P.box) P.box.style.removeProperty('--cov-sq'); P.setRoom(false); return; }
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const cs = getComputedStyle(P.el);
    const W = P.el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const H = P.el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const sq = Math.max(0, Math.floor(Math.min(H, W * 0.55)));
    P.box.style.setProperty('--cov-sq', `${sq}px`);
    P.setRoom(W - sq - 2.4 * rem - SIDE_REM * rem >= TEXT_MIN_REM * rem);
};
P.setRoom = room => {
    if (P.room === room) return;
    P.room = room;
    if (P.visible) P.syncSide();
};

P.pick = which => {
    K.setPref('cover.side', P.sideWanted() === which ? 'none' : which);
    P.syncSide();
    if (P.sideWanted() !== 'none' && !P.room) K.toast('Not enough room beside the cover on this screen');
};

// Exactly one of: nothing, the DR listener, the level stream.  Both close on hide().
P.syncSide = () => {
    const want = P.sideWanted();
    P.drBtn.classList.toggle('on', want === 'dr');
    P.lvlBtn.classList.toggle('on', want === 'levels');
    const show = P.room && P.visible ? want : 'none';
    if (show === P.showing) return;
    P.closeSide();
    P.showing = show;
    P.side.hidden = show === 'none';
    P.side.className = 'cov-side ' + show;
    if (show === 'levels') {
        P.vl = new K.VLevels(P.sideHost);
        P.level = K.streams.open('vu', d => {
            if (d.ok && d.state === 'running') {
                const v = d.vu || {};
                if (Math.max(Number(v.left_peak ?? -120), Number(v.right_peak ?? -120)) > -60) K.markSound();
                P.vl.update(v);
            } else P.vl.clear();
        });
    } else if (show === 'dr') {
        P.vdr = K.VDr(P.sideHost);
        P.drSub = K.drEstimate.listen(E => P.vdr.paint(E));
        P.vdr.paint(K.drEstimate);
    }
};
P.closeSide = () => {
    if (P.level) { P.level.close(); P.level = null; }
    if (P.drSub) { P.drSub.close(); P.drSub = null; }
    if (P.vl) { P.vl.destroy(); P.vl = null; }
    P.vdr = null;
    K.clear(P.sideHost);
    P.showing = 'none';
    P.side.hidden = true;
};

// ── track ────────────────────────────────────────────────────────────────────
P.pollTrack = async () => {
    const t = await K.fetchTrack();
    P.title.textContent = t.ok ? (t.title || '—') : 'Nothing playing';
    P.title.classList.toggle('idle', !t.ok);
    P.artist.textContent = t.artist || '';
    P.album.textContent = t.album || '';
    P.edition.textContent = t.edition || '';
    P.fmt.textContent = t.format || '';
    P.fmt.style.color = K.formatColor(t.format);
    P.base = { elapsed: t.elapsed, duration: t.duration, at: performance.now(), playing: t.state === 'play' };
    if (t.state === 'play' && !P.level) K.markSound();
    if (t.art) { if (P.img.getAttribute('src') !== t.art) P.img.setAttribute('src', t.art); }
    else { P.img.hidden = true; P.img.removeAttribute('src'); P.none.hidden = false; }
    P.paintTime();
};

P.paintTime = () => {
    const b = P.base;
    if (!b || !Number.isFinite(b.elapsed) || !Number.isFinite(b.duration) || b.duration <= 0) {
        P.time.textContent = ''; P.prog.style.width = '0%'; P.prog.parentNode.hidden = true; return;
    }
    P.prog.parentNode.hidden = false;
    const e = K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration);
    P.time.textContent = `${K.fmtClock(e)} / ${K.fmtClock(b.duration)}`;
    P.prog.style.width = `${(e / b.duration * 100).toFixed(1)}%`;
};

// ── lifecycle ────────────────────────────────────────────────────────────────
P.prefetch = () => P.pollTrack();

P.show = () => {
    P.visible = true;
    K.setTopExtra(P.topBtns);
    P.fit();
    P.syncSide();
    P.trackPoll.start();
    P.tick.start(false);
};

P.hide = () => {
    P.visible = false;
    P.closeSide();
    P.trackPoll.stop();
    P.tick.stop();
};

K.registerPage(P);
})();
