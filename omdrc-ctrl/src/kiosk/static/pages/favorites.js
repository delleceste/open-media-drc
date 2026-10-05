/* Qobuz Library: playlist names are folder paths, playlist tracks are album
 * anchors. This page runs in the Android WebView as well as the browser. */
(() => {
'use strict';
const { h } = K;
const P = { id: 'favorites', label: 'Library', title: 'Qobuz Library',
    optional: () => !!K.state.features.qobuz_search, path: [], folders: [], albums: [], loaded: new Map() };

P.mount = el => {
    P.el = el;
    P.head = h('div', { class: 'fav-head' });
    P.grid = h('div', { class: 'fav-grid' });
    P.message = h('div', { class: 'muted' });
    el.append(h('div', { class: 'fav-page' }, P.head, P.message, P.grid));
};
P.show = () => { P.visible = true; P.refresh(); };
P.hide = () => { P.visible = false; };
P.refresh = async () => {
    P.message.textContent = 'Reading Qobuz Library…';
    const d = await K.api('/qobuz/favorites', { timeout: 60000 });
    if (!d.ok) { P.message.textContent = d.error || 'Could not read Qobuz Library'; return; }
    P.folders = d.folders; P.albums = d.albums;
    P.loaded.clear();
    P.message.textContent = '';
    P.paint();
};
P.enter = name => { P.path.push(name); P.paint(); P.el.scrollTo(0, 0); };
P.back = () => { P.path.pop(); P.paint(); };
P.pathName = () => P.path.join('/');
P.openPlaylist = async folder => {
    if (P.loaded.has(folder.id)) return P.loaded.get(folder.id);
    const d = await K.api('/qobuz/favorites/playlist/' + folder.id, { timeout: 60000 });
    if (!d.ok) { K.toast(d.error || 'Could not read folder', 'error'); return []; }
    P.loaded.set(folder.id, d.albums);
    return d.albums;
};
P.paint = async () => {
    const path = P.pathName(), seq = P.paintSeq = (P.paintSeq || 0) + 1;
    P.message.textContent = '';
    K.clear(P.head).append(
        P.path.length ? h('button', { type: 'button', class: 'btn', onclick: P.back }, '‹ Back') : null,
        h('strong', {}, path || 'Qobuz Library'),
        h('button', { type: 'button', class: 'btn', title: 'Refresh Qobuz Library', onclick: P.refresh }, '↻'));
    K.clear(P.grid);
    const children = new Map(), here = [];
    for (const folder of P.folders) {
        const parts = folder.path.split('/');
        if (parts.slice(0, P.path.length).join('/') !== path || parts.length < P.path.length + 1) continue;
        if (parts.length === P.path.length + 1) here.push(folder);
        else children.set(parts[P.path.length], (children.get(parts[P.path.length]) || 0) + 1);
    }
    for (const [name, count] of [...children].sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))) {
        P.grid.append(h('button', { type: 'button', class: 'fav-folder', onclick: () => P.enter(name) },
            h('span', { class: 'fav-folder-icon' }, '▣'), h('strong', {}, name),
            h('small', {}, `${count} folder${count === 1 ? '' : 's'}`)));
    }
    for (const folder of here.sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }))) {
        const name = folder.path.split('/').at(-1);
        P.grid.append(h('button', { type: 'button', class: 'fav-folder', onclick: () => P.enter(name) },
            h('span', { class: 'fav-folder-icon' }, '▣'), h('strong', {}, name),
            h('small', {}, `${folder.tracks} tracks`)));
    }
    if (!path) P.grid.append(h('button', { type: 'button', class: 'fav-folder', onclick: () => P.enter('Qobuz') },
        h('span', { class: 'fav-folder-icon' }, '♡'), h('strong', {}, 'Qobuz'),
        h('small', {}, `${P.albums.length} album favorites`)));
    if (path === 'Qobuz') {
        for (const album of P.albums) P.grid.append(P.albumTile(album, null));
        return;
    }
    for (const folder of P.folders.filter(f => f.path === path)) {
        const albums = await P.openPlaylist(folder);
        if (P.paintSeq !== seq) return;
        for (const album of albums) P.grid.append(P.albumTile(album, folder));
    }
    if (!P.grid.children.length) P.message.textContent = 'This folder is empty.';
    else P.message.textContent = '';
};
P.albumTile = (a, folder) => h('div', { class: 'fav-album' },
    h('button', { type: 'button', class: 'fav-art', title: `Details: ${a.title}`, onclick: () => K.albumInfo(a.id, {
        off: a.streamable === false, play: () => P.play(a, 'replace'), add: () => P.play(a, 'append'),
    }) }, a.image ? h('img', { src: a.image, alt: '', loading: 'lazy' }) : '♪'),
    h('strong', { title: a.title }, a.title), h('small', {}, a.artist || ''),
    h('div', { class: 'fav-actions' },
        h('button', { type: 'button', class: 'btn', title: 'Play album', disabled: a.streamable === false,
            onclick: () => P.play(a, 'replace') }, '▶'),
        h('button', { type: 'button', class: 'btn', title: 'Add album to queue', disabled: a.streamable === false,
            onclick: () => P.play(a, 'append') }, '+'),
        h('button', { type: 'button', class: 'btn', title: 'Add to a folder', onclick: () => K.saveQobuzFavorite(a) }, '♡'),
        h('button', { type: 'button', class: 'btn', title: folder ? 'Remove from folder' : 'Remove Qobuz favorite',
            onclick: () => P.remove(a, folder) }, '−')));
P.play = async (a, mode) => {
    const d = await K.api('/qobuz/play', { json: { album_id: a.id, mode }, timeout: 90000 });
    K.toast(d.ok ? (mode === 'append' ? 'Added to queue' : 'Playing') : d.error || 'Could not play album', d.ok ? 'ok' : 'error');
};
P.remove = async (a, folder) => {
    const yes = await K.confirm({ title: folder ? 'Remove from folder?' : 'Remove Qobuz favorite?',
        message: a.title, ok: 'Remove', danger: true });
    if (!yes) return;
    const body = { action: folder ? 'remove' : 'unfavorite', album_id: a.id };
    if (folder) body.playlist_id = folder.id;
    const d = await K.api('/qobuz/favorites', { json: body, timeout: 60000 });
    if (!d.ok) { K.toast(d.error || 'Could not remove album', 'error'); return; }
    await P.refresh();
};
K.saveQobuzFavorite = async a => {
    const d = P.folders.length ? { ok: true, folders: P.folders } : await K.api('/qobuz/favorites', { timeout: 60000 });
    if (!d.ok) { K.toast(d.error || 'Could not read folders', 'error'); return; }
    const names = [...new Set(d.folders.map(f => f.path))].sort();
    const input = h('input', { type: 'text', list: 'fav-paths', placeholder: 'Blow Up/2026/September',
        value: P.pathName() === 'Qobuz' ? '' : P.pathName() });
    const options = h('datalist', { id: 'fav-paths' }, names.map(n => h('option', { value: n })));
    const close = () => scrim.remove();
    const save = async () => {
        const path = input.value.trim();
        if (!path) { input.focus(); return; }
        saveButton.disabled = true;
        const r = await K.api('/qobuz/favorites', { json: { action: 'add', album_id: a.id, path }, timeout: 60000 });
        saveButton.disabled = false;
        if (!r.ok) { K.toast(r.error || 'Could not save album', 'error'); return; }
        close(); K.toast(r.already_present ? 'Already in that folder' : `Saved in ${r.path}`);
        if (P.visible) P.refresh();
    };
    const saveButton = h('button', { type: 'button', class: 'btn primary', onclick: save }, 'Save');
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet fav-sheet' }, h('h2', {}, `Save “${a.title}”`),
            h('p', {}, 'Choose an existing path or type a new one. Use / between folder names.'),
            input, options,
            h('div', { class: 'sheet-actions' }, h('button', { type: 'button', class: 'btn', onclick: close }, 'Cancel'), saveButton)));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
    document.getElementById('overlay-root').append(scrim);
    input.focus();
};
K.registerPage(P);
})();
