/* Qobuz Library: playlist names are folder paths, playlist tracks are album
 * anchors. This view runs inside Qobuz in the Android WebView and browser. */
(() => {
'use strict';
const { h } = K;
const P = { path: [], folders: [], albums: [], loaded: new Map(), expanded: new Set() };

P.mount = el => {
    P.el = el;
    P.head = h('div', { class: 'fav-head' });
    P.grid = h('div', { class: 'fav-grid' });
    P.tree = h('div', { class: 'fav-tree' });
    P.message = h('div', { class: 'muted' });
    P.layout = K.pref('favorites.layout', matchMedia('(max-width: 640px)').matches ? 'list' : 'grid');
    el.append(h('div', { class: 'fav-page' }, P.head, P.message, P.grid, P.tree));
};
P.show = () => { P.visible = true; P.refresh(); };
P.hide = () => { P.visible = false; if (P.activeHoldCancel) P.activeHoldCancel(); };
P.refresh = async selectedFolder => {
    P.message.textContent = 'Reading Qobuz Library…';
    const d = await K.api('/qobuz/favorites', { timeout: 60000 });
    if (!d.ok) { P.message.textContent = d.error || 'Could not read Qobuz Library'; return false; }
    P.folders = d.folders; P.albums = d.albums;
    P.covers = d.covers || {}; P.order = d.order || {};
    if (selectedFolder && P.folders.some(f => f.path === selectedFolder ||
        f.path.startsWith(selectedFolder + '/'))) P.path = selectedFolder.split('/');
    while (P.path.length && P.pathName() !== 'Qobuz' &&
           !P.folders.some(f => f.path === P.pathName() || f.path.startsWith(P.pathName() + '/')))
        P.path.pop();
    P.loaded.clear();
    P.message.textContent = '';
    await P.paint();
    if (selectedFolder && P.visible) P.reveal();
    return true;
};
P.reveal = () => {
    const scrollBox = P.el.closest('.page-body');
    if (scrollBox) scrollBox.scrollTop += P.el.getBoundingClientRect().top -
        scrollBox.getBoundingClientRect().top;
};
P.enter = name => { P.path.push(name); P.paint(); P.reveal(); };
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
    let heldUntil = 0;
    const open = h('button', { type: 'button', class: 'fav-folder-open', onclick: () => {
        if (Date.now() < heldUntil) return;
        P.enter(name);
    }, title: `Open ${name}; hold for folder actions` },
    P.collage(fullPath), h('strong', {}, name), h('small', {}, caption));
    const tile = h('div', { class: 'fav-folder fav-item' },
        open);
    if (fullPath !== 'Qobuz') P.folderHold(open, fullPath, () => { heldUntil = Date.now() + 700; });
    return P.movable(tile, 'f:' + fullPath);
};
P.folderHold = (button, path, onHeld) => {
    let timer, startX, startY, opened = false, moved = false, pointerId;
    const cancel = () => {
        clearTimeout(timer); timer = null;
        window.removeEventListener('pointermove', track, true);
        window.removeEventListener('pointerup', end, true);
        window.removeEventListener('pointercancel', end, true);
        if (P.activeHoldCancel === abort) P.activeHoldCancel = null;
    };
    const abort = () => { moved = true; cancel(); };
    const track = e => {
        if (e.pointerId === pointerId && Math.hypot(e.clientX - startX, e.clientY - startY) > 10)
            abort();
    };
    const end = e => { if (e.pointerId === pointerId) cancel(); };
    button.addEventListener('pointerdown', e => {
        if (e.button) return;
        if (P.activeHoldCancel) P.activeHoldCancel();
        startX = e.clientX; startY = e.clientY;
        pointerId = e.pointerId;
        opened = moved = false;
        P.activeHoldCancel = abort;
        window.addEventListener('pointermove', track, true);
        window.addEventListener('pointerup', end, true);
        window.addEventListener('pointercancel', end, true);
        timer = setTimeout(() => { timer = null; opened = true; onHeld(); P.folderMenu(path); }, 550);
    });
    button.addEventListener('contextmenu', e => {
        e.preventDefault();
        if (moved || !P.visible) return;
        cancel();
        if (!opened) { opened = true; onHeld(); P.folderMenu(path); }
    });
};
P.folderMenu = path => {
    const count = P.folders.filter(f => f.path === path || f.path.startsWith(path + '/')).length;
    const close = () => scrim.remove();
    const name = h('input', { type: 'text', maxlength: 80, value: path.split('/').at(-1),
        'aria-label': 'Folder name' });
    const rename = async () => {
        const changed = name.value.trim();
        if (!changed || changed.includes('/')) { K.toast('Enter one folder name without /', 'error'); return; }
        const d = await K.api('/qobuz/favorites/folder', {
            json: { action: 'rename', path, name: changed }, timeout: 60000 });
        if (!d.ok) { K.toast(d.error || 'Could not rename folder', 'error'); return; }
        if (P.pathName() === path || P.pathName().startsWith(path + '/'))
            P.path = (d.path + P.pathName().slice(path.length)).split('/');
        P.expanded = new Set([...P.expanded].map(value =>
            value === path || value.startsWith(path + '/') ? d.path + value.slice(path.length) : value));
        close(); K.toast(`Renamed ${count} playlist${count === 1 ? '' : 's'}`); P.refresh();
    };
    const remove = async () => {
        close();
        const yes = await K.confirm({ title: `Remove ${path}?`, danger: true, ok: 'Remove folder',
            message: `This deletes ${count} Qobuz playlist${count === 1 ? '' : 's'} in this folder and its subfolders. Existing Qobuz album favorites remain.` });
        if (!yes) return;
        const d = await K.api('/qobuz/favorites/folder', {
            json: { action: 'delete', path }, timeout: 60000 });
        if (!d.ok) { K.toast(d.error || 'Could not remove folder', 'error'); return; }
        if (P.pathName() === path || P.pathName().startsWith(path + '/')) P.path = path.split('/').slice(0, -1);
        P.expanded = new Set([...P.expanded].filter(value => value !== path && !value.startsWith(path + '/')));
        K.toast('Folder removed'); P.refresh();
    };
    const scrim = h('div', { class: 'scrim fav-picker', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet fav-folder-menu' },
            h('h2', {}, path), h('p', { class: 'muted' }, `${count} Qobuz playlist${count === 1 ? '' : 's'}`),
            h('label', {}, 'Rename folder', name),
            h('div', { class: 'sheet-actions' },
                h('button', { type: 'button', class: 'btn', onclick: close }, 'Cancel'),
                h('button', { type: 'button', class: 'btn primary', onclick: rename }, 'Rename'),
                h('button', { type: 'button', class: 'btn danger', onclick: remove }, 'Remove folder and subfolders'))));
    name.addEventListener('keydown', e => { if (e.key === 'Enter') rename(); });
    document.body.append(scrim);
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
P.setLayout = layout => {
    P.layout = layout;
    K.setPref('favorites.layout', layout);
    P.paint();
};
P.moveEntry = async (parent, key, direction) => {
    const rows = [...P.tree.querySelectorAll('.fav-children[data-parent]')]
        .find(group => group.dataset.parent === parent);
    if (!rows) return;
    const keys = [...rows.children].map(row => row.dataset.key);
    const index = keys.indexOf(key), other = index + direction;
    if (index < 0 || other < 0 || other >= keys.length) return;
    [keys[index], keys[other]] = [keys[other], keys[index]];
    const previous = P.order[parent];
    P.order[parent] = keys;
    await P.paint();
    const d = await K.api('/qobuz/favorites/order', { json: { parent, keys } });
    if (!d.ok) {
        if (previous === undefined) delete P.order[parent];
        else P.order[parent] = previous;
        K.toast(d.error || 'Could not save order', 'error');
        P.paint();
    }
};
P.listRow = (parent, key, content, extra) => h('div', { class: 'fav-tree-entry', dataset: { key } },
    h('div', { class: 'fav-tree-row' }, content, extra,
        h('div', { class: 'fav-tree-controls' },
            h('button', { type: 'button', class: 'btn',
                'aria-label': `Move ${key.slice(2)} up`, onclick: () => P.moveEntry(parent, key, -1) }, '↑'),
            h('button', { type: 'button', class: 'btn',
                'aria-label': `Move ${key.slice(2)} down`, onclick: () => P.moveEntry(parent, key, 1) }, '↓'))));
P.paintTree = async seq => {
    const render = async path => {
        const children = new Map();
        for (const folder of P.folders) {
            const parts = folder.path.split('/');
            const depth = path ? path.split('/').length : 0;
            if (parts.length > depth && parts.slice(0, depth).join('/') === path)
                children.set(parts[depth], (children.get(parts[depth]) || 0) + 1);
        }
        const entries = [...children].map(([name, count]) => ({
            key: 'f:' + (path ? path + '/' : '') + name, name, count,
        }));
        if (!path) entries.push({ key: 'f:Qobuz', name: 'Qobuz', count: P.albums.length });
        entries.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
        if (path === 'Qobuz') entries.push(...P.albums.map(album => ({ key: 'a:' + album.id, album })));
        else if (path) {
            for (const folder of P.folders.filter(f => f.path === path)) {
                const albums = await P.openPlaylist(folder);
                if (seq !== P.paintSeq) return null;
                entries.push(...albums.map(album => ({ key: 'a:' + album.id, album, folder })));
            }
        }
        const positions = new Map((P.order[path] || []).map((key, i) => [key, i]));
        entries.sort((a, b) => (positions.get(a.key) ?? Infinity) - (positions.get(b.key) ?? Infinity));
        const group = h('div', { class: 'fav-children', dataset: { parent: path } });
        for (let i = 0; i < entries.length; i++) {
            const entry = entries[i];
            if (entry.album) {
                const album = entry.album;
                const content = h('div', { class: 'fav-tree-album' },
                    h('button', { type: 'button', class: 'fav-tree-cover', title: `Details: ${album.title}`,
                        onclick: () => K.albumInfo(album.id, { off: album.streamable === false,
                            play: () => P.play(album, 'replace'), add: () => P.play(album, 'append') }) },
                    album.image ? h('img', { src: album.image, alt: '' }) : '♪'),
                    h('span', { class: 'fav-tree-name' }, h('strong', {}, album.title), h('small', {}, album.artist || '')));
                const actions = h('div', { class: 'fav-tree-actions' },
                    h('button', { type: 'button', class: 'btn', title: 'Play album', disabled: album.streamable === false,
                        onclick: () => P.play(album, 'replace') }, '▶'),
                    h('button', { type: 'button', class: 'btn', title: 'Add album to queue', disabled: album.streamable === false,
                        onclick: () => P.play(album, 'append') }, '+'),
                    h('button', { type: 'button', class: 'btn', title: 'Add to a folder', onclick: () => K.saveQobuzFavorite(album) }, '♡'),
                    h('button', { type: 'button', class: 'btn', title: entry.folder ? 'Remove from folder' : 'Remove Qobuz favorite',
                        onclick: () => P.remove(album, entry.folder) }, '−'));
                group.append(P.listRow(path, entry.key, content, actions));
            } else {
                const full = entry.key.slice(2), open = P.expanded.has(full);
                const content = h('button', { type: 'button', class: 'fav-tree-folder',
                    'aria-expanded': String(open), onclick: () => {
                        if (open) P.expanded.delete(full); else P.expanded.add(full);
                        P.paint();
                    } }, h('span', { class: 'fav-tree-chevron', 'aria-hidden': 'true' }, open ? '▾' : '▸'),
                    P.collage(full), h('span', { class: 'fav-tree-name' },
                        h('strong', {}, entry.name), h('small', {}, `${entry.count} ${full === 'Qobuz' ? 'album favorites' : 'playlists'}`)));
                const menu = full === 'Qobuz' ? null : h('button', { type: 'button', class: 'btn',
                    'aria-label': `Actions for ${entry.name}`, onclick: () => P.folderMenu(full) }, '⋯');
                const row = P.listRow(path, entry.key, content, menu);
                if (open) {
                    const nested = await render(full);
                    if (seq !== P.paintSeq) return null;
                    row.append(nested);
                }
                group.append(row);
            }
        }
        for (const row of group.children) {
            const controls = row.querySelector('.fav-tree-controls');
            controls.children[0].disabled = row === group.firstElementChild;
            controls.children[1].disabled = row === group.lastElementChild;
        }
        return group;
    };
    const root = await render('');
    if (seq === P.paintSeq) P.tree.replaceChildren(root);
};
P.paint = async () => {
    const path = P.pathName(), seq = P.paintSeq = (P.paintSeq || 0) + 1;
    P.loading = false;
    P.message.textContent = '';
    K.clear(P.head).append(...[
        P.layout === 'grid' && P.path.length ? h('button', { type: 'button', class: 'btn', onclick: P.back }, '‹ Back') : null,
        h('strong', {}, P.layout === 'list' ? 'Qobuz Library' : path || 'Qobuz Library'),
        h('button', { type: 'button', class: 'btn', 'aria-label': P.layout === 'list' ? 'Show tiles' : 'Show list',
            title: P.layout === 'list' ? 'Show tiles' : 'Show hierarchical list',
            onclick: () => P.setLayout(P.layout === 'list' ? 'grid' : 'list') }, P.layout === 'list' ? '▦' : '☷'),
        h('button', { type: 'button', class: 'btn', title: 'Refresh Qobuz Library', onclick: () => P.refresh() }, '↻'),
    ].filter(Boolean));
    P.grid.hidden = P.layout === 'list';
    P.tree.hidden = P.layout !== 'list';
    if (P.layout === 'list') { await P.paintTree(seq); return; }
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
    const parent = P.pathName();
    const start = [...P.grid.children].map(item => item.dataset.key);
    const next = tile.nextSibling;
    const rect = tile.getBoundingClientRect();
    const originalStyle = tile.style.cssText;
    const placeholder = h('div', { class: 'fav-drop-placeholder fav-item', 'aria-hidden': 'true' }, 'Drop here');
    placeholder.style.height = `${rect.height}px`;
    P.grid.insertBefore(placeholder, tile);
    P.grid.classList.add('is-dragging');
    Object.assign(tile.style, { position: 'fixed', left: `${rect.left}px`, top: `${rect.top}px`,
        width: `${rect.width}px`, height: `${rect.height}px`, zIndex: '100', pointerEvents: 'none' });
    tile.classList.add('dragging');
    const scrollBox = P.el.closest('.page-body') || P.el;
    let pointerX = e.clientX, pointerY = e.clientY, scrolling = true;
    const sourcePath = tile.dataset.key.startsWith('f:') ? tile.dataset.key.slice(2) : '';
    let hoverTarget = null, nestTarget = null, nestTimer = null;
    const clearHover = () => {
        clearTimeout(nestTimer);
        if (hoverTarget) hoverTarget.classList.remove('fav-drop-pending', 'fav-drop-into');
        hoverTarget = nestTarget = null;
    };
    const place = () => {
        const target = document.elementsFromPoint(pointerX, pointerY)
            .map(el => el.closest && el.closest('.fav-item'))
            .find(el => el && el !== tile && el !== placeholder && el.parentNode === P.grid);
        if (!target) { clearHover(); return; }
        const targetRect = target.getBoundingClientRect();
        const targetPath = target.dataset.key.startsWith('f:') ? target.dataset.key.slice(2) : '';
        const canNest = sourcePath && sourcePath !== 'Qobuz' && targetPath &&
            targetPath !== 'Qobuz' && targetPath !== sourcePath &&
            !targetPath.startsWith(sourcePath + '/') &&
            pointerX > targetRect.left + targetRect.width * .2 &&
            pointerX < targetRect.right - targetRect.width * .2 &&
            pointerY > targetRect.top + targetRect.height * .2 &&
            pointerY < targetRect.bottom - targetRect.height * .2;
        if (canNest) {
            if (hoverTarget !== target) {
                clearHover();
                hoverTarget = target;
                target.classList.add('fav-drop-pending');
                nestTimer = setTimeout(() => {
                    nestTarget = target;
                    target.classList.remove('fav-drop-pending');
                    target.classList.add('fav-drop-into');
                }, 550);
            }
            return;
        }
        clearHover();
        const before = pointerY < targetRect.top ? true
            : pointerY > targetRect.bottom ? false
            : pointerX < targetRect.left + targetRect.width / 2;
        P.grid.insertBefore(placeholder, before ? target : target.nextSibling);
    };
    const move = event => {
        if (event.pointerId !== e.pointerId) return;
        event.preventDefault(); event.stopPropagation();
        pointerX = event.clientX; pointerY = event.clientY;
        tile.style.left = `${pointerX - (e.clientX - rect.left)}px`;
        tile.style.top = `${pointerY - (e.clientY - rect.top)}px`;
        place();
    };
    const autoScroll = () => {
        if (!scrolling) return;
        const box = scrollBox.getBoundingClientRect();
        if (pointerX >= box.left && pointerX <= box.right &&
            pointerY >= box.top && pointerY <= box.bottom) {
            const edge = Math.min(96, box.height / 3);
            const up = Math.max(0, edge - (pointerY - box.top)) / edge;
            const down = Math.max(0, edge - (box.bottom - pointerY)) / edge;
            const step = Math.round(22 * (down * down - up * up));
            if (step) {
                const before = scrollBox.scrollTop;
                scrollBox.scrollTop += step;
                if (scrollBox.scrollTop !== before) place();
            }
        }
        requestAnimationFrame(autoScroll);
    };
    const end = async event => {
        if (event.pointerId !== e.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        window.removeEventListener('pointermove', move, true);
        window.removeEventListener('pointerup', end, true);
        window.removeEventListener('pointercancel', end, true);
        scrolling = false;
        const destination = nestTarget && nestTarget.dataset.key.slice(2);
        if (!destination && hoverTarget && event.type !== 'pointercancel') {
            const targetRect = hoverTarget.getBoundingClientRect();
            P.grid.insertBefore(placeholder, pointerX < targetRect.left + targetRect.width / 2
                ? hoverTarget : hoverTarget.nextSibling);
        }
        clearHover();
        P.grid.classList.remove('is-dragging');
        if (grip.hasPointerCapture && grip.hasPointerCapture(e.pointerId)) grip.releasePointerCapture(e.pointerId);
        tile.classList.remove('dragging');
        tile.style.cssText = originalStyle;
        if (event.type === 'pointercancel') {
            placeholder.remove();
            P.grid.insertBefore(tile, next);
            return;
        }
        if (destination) {
            placeholder.remove();
            const d = await K.api('/qobuz/favorites/folder', {
                json: { action: 'move', path: sourcePath, target: destination }, timeout: 60000 });
            const refreshed = await P.refresh(d.ok ? destination : undefined);
            if (!d.ok) K.toast(d.error || 'Could not move folder', 'error');
            else K.toast(refreshed ? `Moved into ${destination}` : 'Folder moved; refresh failed',
                         refreshed ? 'ok' : 'error');
            return;
        }
        placeholder.replaceWith(tile);
        const keys = [...P.grid.children].map(item => item.dataset.key);
        if (keys.join('\n') === start.join('\n')) return;
        P.order[parent] = keys;
        const d = await K.api('/qobuz/favorites/order', { json: { parent, keys } });
        if (!d.ok) {
            P.order[parent] = start;
            K.toast(d.error || 'Could not save tile order', 'error');
            if (P.pathName() === parent) P.paint();
        }
    };
    window.addEventListener('pointermove', move, true);
    window.addEventListener('pointerup', end, true);
    window.addEventListener('pointercancel', end, true);
    if (grip.setPointerCapture) grip.setPointerCapture(e.pointerId);
    requestAnimationFrame(autoScroll);
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
    const parts = P.visible && P.layout === 'grid' && P.pathName() !== 'Qobuz' ? [...P.path] : [];
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
K.library = P;
})();
