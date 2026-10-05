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
    P.covers = d.covers || {}; P.order = d.order || {};
    while (P.path.length && P.pathName() !== 'Qobuz' &&
           !P.folders.some(f => f.path === P.pathName() || f.path.startsWith(P.pathName() + '/')))
        P.path.pop();
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
P.collage = (path, covers = P.covers || {}) => {
    const urls = (covers[path] || []).slice(0, 3);
    const variant = [...path].reduce((n, ch) => n + ch.charCodeAt(0), 0) % 3;
    return h('span', { class: `fav-collage fav-collage-${urls.length} fav-pattern-${variant}`, 'aria-hidden': 'true' },
        urls.length ? urls.map(url => h('img', { src: url, alt: '', loading: 'lazy' }))
                    : h('span', { class: 'fav-empty-art' }, path === 'Qobuz' ? '♡' : '♪'));
};
P.folderTile = (name, fullPath, caption) => {
    const tile = h('div', { class: 'fav-folder fav-item' },
        h('button', { type: 'button', class: 'fav-folder-open', onclick: () => P.enter(name),
            title: `Open ${name}` }, P.collage(fullPath), h('strong', {}, name), h('small', {}, caption)));
    return P.movable(tile, 'f:' + fullPath);
};
P.movable = (tile, key) => {
    tile.dataset.key = key;
    const grip = h('button', { type: 'button', class: 'fav-grip', title: 'Drag to arrange tile',
        'aria-label': 'Drag to arrange tile' }, '⠿');
    grip.addEventListener('pointerdown', e => P.dragStart(e, tile, grip));
    tile.append(grip);
    return tile;
};
P.ordered = (path, tiles) => {
    const saved = P.order[path] || [];
    const positions = new Map(saved.map((key, i) => [key, i]));
    return tiles.sort((a, b) => (positions.get(a.dataset.key) ?? Infinity) -
                                 (positions.get(b.dataset.key) ?? Infinity));
};
P.appendOrdered = (path, tiles) => P.grid.replaceChildren(...P.ordered(path, tiles));
P.paint = async () => {
    const path = P.pathName(), seq = P.paintSeq = (P.paintSeq || 0) + 1;
    P.loading = false;
    P.message.textContent = '';
    K.clear(P.head).append(
        P.path.length ? h('button', { type: 'button', class: 'btn', onclick: P.back }, '‹ Back') : null,
        h('strong', {}, path || 'Qobuz Library'),
        h('button', { type: 'button', class: 'btn', title: 'Refresh Qobuz Library', onclick: P.refresh }, '↻'));
    K.clear(P.grid);
    const children = new Map();
    for (const folder of P.folders) {
        const parts = folder.path.split('/');
        if (parts.slice(0, P.path.length).join('/') !== path || parts.length < P.path.length + 1) continue;
        children.set(parts[P.path.length], (children.get(parts[P.path.length]) || 0) + 1);
    }
    const tiles = [];
    for (const [name, count] of [...children].sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))) {
        const full = path ? path + '/' + name : name;
        tiles.push(P.folderTile(name, full, `${count} playlist${count === 1 ? '' : 's'}`));
    }
    if (!path) tiles.push(P.folderTile('Qobuz', 'Qobuz', `${P.albums.length} album favorites`));
    if (path === 'Qobuz') {
        for (const album of P.albums) tiles.push(P.albumTile(album, null));
        P.appendOrdered(path, tiles);
        return;
    }
    P.appendOrdered(path, tiles);
    const exact = P.folders.filter(f => f.path === path);
    P.loading = !!exact.length;
    if (P.loading) P.message.textContent = 'Reading albums…';
    for (const folder of exact) {
        const albums = await P.openPlaylist(folder);
        if (P.paintSeq !== seq) return;
        for (const album of albums) tiles.push(P.albumTile(album, folder));
    }
    P.loading = false;
    P.appendOrdered(path, tiles);
    if (!P.grid.children.length) P.message.textContent = 'This folder is empty.';
    else P.message.textContent = '';
};
P.albumTile = (a, folder) => P.movable(h('div', { class: 'fav-album fav-item' },
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
            onclick: () => P.remove(a, folder) }, '−'))), 'a:' + a.id);
P.dragStart = (e, tile, grip) => {
    if (e.button || P.loading || !P.grid.contains(tile)) return;
    e.preventDefault(); e.stopPropagation();
    const start = [...P.grid.children].map(item => item.dataset.key).join('\n');
    tile.classList.add('dragging');
    grip.setPointerCapture(e.pointerId);
    const move = event => {
        event.preventDefault(); event.stopPropagation();
        const target = document.elementsFromPoint(event.clientX, event.clientY)
            .map(el => el.closest && el.closest('.fav-item'))
            .find(el => el && el !== tile && el.parentNode === P.grid);
        if (!target) return;
        const rect = target.getBoundingClientRect();
        const before = event.clientY < rect.top ? true
            : event.clientY > rect.bottom ? false
            : event.clientX < rect.left + rect.width / 2;
        P.grid.insertBefore(tile, before ? target : target.nextSibling);
    };
    const end = async event => {
        event.stopPropagation();
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', end);
        grip.removeEventListener('pointercancel', end);
        tile.classList.remove('dragging');
        if (event.type === 'pointercancel') { P.paint(); return; }
        const keys = [...P.grid.children].map(item => item.dataset.key);
        if (keys.join('\n') === start) return;
        const parent = P.pathName();
        const d = await K.api('/qobuz/favorites/order', { json: { parent, keys } });
        if (d.ok) P.order[parent] = keys;
        else { K.toast(d.error || 'Could not save tile order', 'error'); P.paint(); }
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', end);
    grip.addEventListener('pointercancel', end);
};
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
    const d = await K.api('/qobuz/favorites', { timeout: 60000 });
    if (!d.ok) { K.toast(d.error || 'Could not read folders', 'error'); return; }
    const parts = P.visible && P.pathName() !== 'Qobuz' ? [...P.path] : [];
    const pathName = () => parts.join('/');
    const title = h('strong', {}, 'Library');
    const grid = h('div', { class: 'fav-grid fav-picker-grid' });
    const existing = h('div', { class: 'fav-picker-existing' });
    const back = h('button', { type: 'button', class: 'btn', onclick: () => {
        if (parts.length) { parts.pop(); paint(); }
    } }, '‹ Back');
    const newInput = h('input', { type: 'text', maxlength: 80, placeholder: 'New folder name',
        'aria-label': 'New folder name' });
    const newRow = h('div', { class: 'fav-new-row', hidden: true }, newInput,
        h('button', { type: 'button', class: 'btn primary', onclick: () => createFolder() }, 'Open folder'));
    const saveButton = h('button', { type: 'button', class: 'btn primary', onclick: () => save() });
    const newButton = h('button', { type: 'button', class: 'btn', onclick: () => {
        newRow.hidden = !newRow.hidden;
        if (!newRow.hidden) newInput.focus();
    } }, '+ New folder');
    const close = () => scrim.remove();
    const save = async () => {
        const path = pathName();
        if (!path) return;
        saveButton.disabled = true;
        const body = path === 'Qobuz'
            ? { action: 'favorite', album_id: a.id }
            : { action: 'add', album_id: a.id, path };
        const r = await K.api('/qobuz/favorites', { json: body, timeout: 60000 });
        saveButton.disabled = false;
        if (!r.ok) { K.toast(r.error || 'Could not save album', 'error'); return; }
        close(); K.toast(r.already_present ? 'Already in that folder' : `Saved in ${path}`);
        if (P.visible) P.refresh();
    };
    const createFolder = () => {
        const name = newInput.value.trim();
        if (!name || name === '.' || name === '..' || name.includes('/')) {
            K.toast('Enter one folder name without /', 'error'); newInput.focus(); return;
        }
        if (pathName() === 'Qobuz') { K.toast('Create folders from Library, not Qobuz', 'error'); return; }
        parts.push(name);
        newInput.value = ''; newRow.hidden = true;
        paint();
    };
    newInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); createFolder(); } });
    const scrim = h('div', { class: 'scrim fav-picker', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet fav-picker-sheet' },
            h('div', { class: 'fav-picker-top' },
                a.image ? h('img', { src: a.image, alt: '' }) : null,
                h('div', {}, h('div', { class: 'muted small' }, 'Save album'), h('h2', {}, a.title || 'Current album')),
                h('button', { type: 'button', class: 'btn', title: 'Close', onclick: close }, '✕')),
            h('div', { class: 'fav-picker-path' }, back, title),
            grid, existing, newRow,
            h('div', { class: 'sheet-actions' }, newButton, h('span', { class: 'spacer' }), saveButton)));
    let paintToken = 0;
    const paint = async () => {
        const path = pathName(), token = ++paintToken;
        title.textContent = path || 'Library';
        back.hidden = !parts.length;
        newButton.hidden = path === 'Qobuz';
        newRow.hidden = true;
        saveButton.hidden = !path;
        saveButton.textContent = path === 'Qobuz' ? 'Add to Qobuz favorites' : `Save in ${parts.at(-1)}`;
        K.clear(grid); K.clear(existing);
        const names = new Set();
        for (const folder of d.folders) {
            const segments = folder.path.split('/');
            if (segments.slice(0, parts.length).join('/') === path && segments.length > parts.length)
                names.add(segments[parts.length]);
        }
        if (!path) names.add('Qobuz');
        for (const name of [...names].sort((x, y) => x.localeCompare(y, undefined, { numeric: true }))) {
            const full = path ? path + '/' + name : name;
            grid.append(h('button', { type: 'button', class: 'fav-folder fav-folder-open',
                onclick: () => { parts.push(name); paint(); } },
                P.collage(full, d.covers || {}), h('strong', {}, name)));
        }
        const exact = d.folders.filter(f => f.path === path);
        if (!exact.length) return;
        existing.append(h('div', { class: 'lbl' }, 'Albums already here'));
        const samples = h('div', { class: 'fav-picker-samples' });
        existing.append(samples);
        for (const folder of exact) {
            const result = await K.api('/qobuz/favorites/playlist/' + folder.id, { timeout: 60000 });
            if (token !== paintToken) return;
            if (!result.ok) continue;
            for (const album of result.albums.slice(0, 18)) samples.append(h('div', { class: 'fav-picker-sample', title: album.title },
                album.image ? h('img', { src: album.image, alt: '' }) : null,
                h('small', {}, album.title)));
        }
    };
    document.getElementById('overlay-root').append(scrim);
    paint();
};
K.registerPage(P);
})();
