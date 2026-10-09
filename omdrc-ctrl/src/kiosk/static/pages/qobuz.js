/* Qobuz (optional: [qobuz_search] enabled): album search with label
 * and release-date filters, results newest first, and a player strip.  ▶ and +
 * queue an album on upmpdcli (/qobuz/play), so the box streams it itself; the
 * phone only drives.  The whole page is greyed while upmpdcli is not the running
 * renderer: the other renderer could not play what is found here.
 *
 * The search field completes from a classical word list (composers, works,
 * forms, instruments, performers: /qobuz/words) and from what was played
 * after a search (the search text, the album's artist and composer).  The
 * player strip opens into a full-screen player with the cover and the queue.
 *
 * − on a result lowers it: the album (and, from the bar that follows, its label
 * or artist) moves to the end of every result list, folded away; the Lowered
 * list, by the filters, restores entries or clears them (/qobuz/lowered). */
(() => {
'use strict';
const { h } = K;

const P = {
    id: 'qobuz', label: 'Qobuz', title: 'Qobuz search',
    noPullReload: true,        // main.js: a pull down here only scrolls up to the box
    optional: () => !!K.state.features.qobuz_search,
    status: null,              // /qobuz/status
    favourites: [],            // configured label groups' names
    selected: new Set(K.pref('qobuz.labels', [])),
    last: null,                // the last search's answer, and the request it answered
    request: null,
    open: new Set(),           // album ids whose track list is unfolded
    albums: new Map(),         // album id -> /qobuz/album answer (or a pending promise)
    searching: 0,              // a sequence number: a slow answer never paints over a newer one
};

// Date filter: 'any', 'last' (N rolling years) or 'span' (calendar years).
const pref = (k, d) => K.pref('qobuz.' + k, d);
const setPref = (k, v) => K.setPref('qobuz.' + k, v);
const thisYear = new Date().getFullYear();

P.makeInput = enabled => {
    const input = h(enabled ? 'textarea' : 'input', {
        ...(enabled ? { rows: 4 } : { type: 'search' }), class: 'qz-input',
        placeholder: enabled ? 'Describe the recordings you want…' : 'Composer, work, performer…',
        enterkeyhint: enabled ? 'enter' : 'search',
        autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
        onkeydown: e => P.suggestKey(e),
        oninput: () => { P.dismissEditedPreview(); P.suggestSoon(); P.syncLabels(); P.paintStale(); },
        onfocus: () => P.suggestSoon(),
        onblur: () => setTimeout(() => P.showSuggestions([]), 150),
    });
    input.addEventListener('focus', () => P.revealInput());
    return input;
};

P.clearInput = () => {
    P.input.value = '';
    setPref('q', '');
    P.input.dispatchEvent(new Event('input', { bubbles: true }));
};

P.mount = el => {
    P.el = el;
    P.banner = h('div', {});
    P.input = P.makeInput(pref('aiMode', false));
    P.suggestBox = h('div', { class: 'qz-suggest', role: 'listbox' });
    P.input.value = pref('q', '');
    P.labelsScroll = h('div', { class: 'qz-chips qz-label-scroll', tabindex: '0',
        'aria-label': 'Search labels' });
    P.labelsBox = h('div', { class: 'qz-label-picker' }, P.labelsScroll);
    P.seenBox = h('div', {});
    P.hintBox = h('div', { class: 'qz-hints' });      // the labels of the artist typed (see P.hintsFor)
    P.labelsBody = h('div', { class: 'qz-label-body' }, P.hintBox, P.labelsBox, P.seenBox);
    P.labelsToggle = h('button', { type: 'button', class: 'qz-label-toggle',
        onclick: () => P.setLabelsOpen(!P.labelsOpen) });
    P.dateBox = h('div', {});
    P.sortBox = h('div', {});
    P.awardedFilterBox = h('div', { class: 'qz-chips' });
    P.qualityBox = h('div', { class: 'qz-chips' });
    P.sourceBox = h('div', { class: 'qz-chips' });
    // Full width; the filters sit side by side where there is room, and fold
    // into one summary line once results arrive (a tap opens them again).
    P.fsum = h('div', { class: 'qz-fsum' });
    P.inputBox = h('div', { class: 'qz-inputbox' }, P.input);
    P.filters = h('div', { class: 'qz-filters' },
        h('div', { class: 'qz-fgroup' }, P.labelsToggle, P.labelsBody),
        h('div', { class: 'qz-fgroup' }, h('div', { class: 'lbl' }, 'Released'), P.dateBox,
            h('div', { class: 'lbl' }, 'Order'), P.sortBox,
            h('div', { class: 'lbl' }, 'Awarded'), P.awardedFilterBox,
            h('div', { class: 'lbl' }, 'Audio quality'), P.qualityBox,
            h('div', { class: 'lbl' }, 'Source'), P.sourceBox,
            P.lowLink = h('button', { type: 'button', class: 'btn link qz-lowlink', onclick: () => P.openLowered() }, 'Lowered list ›')));
    P.form = h('div', { class: 'qz-form' },
        K.card(null,
            P.searchWrap = h('div', { class: 'qz-searchwrap' },
                // a real form: the keyboard's Search key submits it (Android's IME action
                // does not always come through as an Enter keydown)
                h('form', { class: 'qz-searchrow', action: '', onsubmit: e => { e.preventDefault(); P.go(); } },
                    h('div', { class: 'qz-modeactions' },
                        P.aiToggle = h('button', { type: 'button', class: 'btn qz-ai-toggle' + (pref('aiMode', false) ? ' active' : ''),
                            'aria-label': 'AI search mode', 'aria-pressed': String(pref('aiMode', false)),
                            onclick: () => P.toggleAI() }, 'AI'),
                        P.textClear = h('button', { type: 'button', class: 'chip qz-text-clear',
                            'aria-label': 'Clear search text', onclick: () => P.clearInput() }, 'Clear')),
                    P.inputBox,
                    P.searchActions = h('div', { class: 'qz-searchactions' },
                        P.aiButton = h('button', { type: 'submit', class: 'btn primary qz-go', title: 'Search', 'aria-label': 'Search' }, K.tIcon('search')),
                        // down on Now, once there are results: over to them, in Search's place
                        // until the text or a filter changes (kiosk.css, paintStale)
                        h('button', { type: 'button', class: 'btn qz-toresults', title: 'Open the results', 'aria-label': 'Open the results', onclick: () => P.openResults() }, '›'))),
                P.suggestBox),
            P.fsum, P.filters));
    if (pref('aiMode', false)) P.searchActions.append(P.fsum);
    P.results = h('div', { class: 'qz-results' });
    P.recentBox = h('div', { class: 'qz-recent' });
    P.awardedBox = h('div', { class: 'qz-awarded' });
    P.genres = [{ id: '', name: 'All genres' }];
    P.genreBtn = h('button', { type: 'button', class: 'chip qz-chip', 'aria-haspopup': 'menu', 'aria-label': 'Discover genre',
        onclick: () => P.openGenreMenu() });
    P.paintGenre();
    P.discoverFilterBtn = h('button', { type: 'button', class: 'chip qz-chip',
        'aria-label': 'Discover filters', 'aria-expanded': 'false',
        onclick: () => P.openDiscoverFilters(!P.discoverFiltersOpen) });
    P.discoverFilterSummary = h('span', { class: 'small muted qz-discover-summary' });
    P.discoverLabelsScroll = h('div', { class: 'qz-chips qz-label-scroll', tabindex: '0',
        'aria-label': 'Discover labels' });
    P.discoverLabels = h('div', { class: 'qz-label-picker' }, P.discoverLabelsScroll);
    P.discoverAwarded = h('div', { class: 'qz-chips' });
    P.discoverQuality = h('div', { class: 'qz-chips' });
    P.discoverFilters = h('div', { class: 'qz-discover-filters', hidden: true },
        h('div', { class: 'lbl' }, 'Labels'), P.discoverLabels,
        h('div', { class: 'lbl' }, 'Awarded'), P.discoverAwarded,
        h('div', { class: 'lbl' }, 'Audio quality'), P.discoverQuality);
    P.discoverList = h('div', {});
    P.discoverBox = h('div', { class: 'qz-discover' },
        h('div', { class: 'qz-discover-controls' }, P.genreBtn, P.discoverFilterBtn),
        P.discoverFilters, P.discoverList);
    P.paintDiscoverFilters();
    P.loadGenres();
    // Under the box: which list is shown, remembered (the albums played from here, or
    // the last search's results), and, while the box is scrolled out of sight, a hint
    // that it is up there.
    P.viewChips = h('div', { class: 'qz-viewchips' },
        // Results in words; the others as icons (a clock, Qobuz's Discover compass, the award cup)
        [['results', 'Results'], ['recent', 'Recent', 'clock'], ['discover', 'Discover', 'compass'], ['awarded', 'Awarded', 'trophy']].map(([x, name, icon]) =>
            h('button', { type: 'button', class: 'chip tog qz-chip' + (icon ? ' qz-viewicon' : ''), dataset: { view: x },
                title: name, 'aria-label': name, onclick: () => P.setView(x, true) }, icon ? K.tIcon(icon) : name)),
        h('button', { type: 'button', class: 'chip tog qz-chip qz-library-shortcut',
            dataset: { view: 'library' }, title: 'Favorites', 'aria-label': 'Favorites',
            onclick: () => P.setView('library', true) }, '♡'));
    P.searchHint = h('button', { type: 'button', class: 'btn link qz-searchhint', hidden: true, title: 'Back to search', 'aria-label': 'Back to search',
        onclick: () => P.el.scrollTo({ top: 0, behavior: 'smooth' }) }, K.tIcon('search'), h('span', { 'aria-hidden': 'true' }, '⌃'));
    // list or grid, for all four lists alike (remembered)
    P.layoutBtn = h('button', { type: 'button', class: 'chip qz-chip qz-layout', onclick: () => P.setLayout(P.grid() ? 'list' : 'grid') });
    P.paintLayout();
    P.viewRow = h('div', { class: 'qz-viewrow' }, P.viewChips, P.searchHint, P.layoutBtn);
    P.preview = h('div', { class: 'qz-preview', hidden: true, role: 'button', tabindex: '-1',
        'aria-label': 'Open the results', 'aria-disabled': 'true',
        onclick: () => { if (P.previewReady()) P.openResults(); },
        onkeydown: e => {
            if (P.previewReady() && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                P.openResults();
            }
        } });   // on Now only, above the box
    P.fetchProgress = h('div', { class: 'qz-fetch-progress', hidden: true,
        role: 'progressbar', 'aria-label': 'Fetching search results' },
        h('span', { class: 'qz-fetch-progress-fill' }));
    P.player = h('div', { class: 'qz-player' });
    // the box's place on this page (it may be on Now instead: see "where the search box is"),
    // and the head shown in its place over results searched from Now
    P.formHome = h('div', { class: 'qz-home' }, P.form);
    P.resHead = h('div', { class: 'qz-reshead', hidden: true },
        h('button', { type: 'button', class: 'btn qz-back', title: 'Back to Now playing', 'aria-label': 'Back to Now playing', onclick: () => K.showPage('now') }, '‹'),
        h('span', { class: 'qz-reshead-title' }, 'Search results'));
    P.libraryBox = h('div', { class: 'qz-library', hidden: true });
    K.library.mount(P.libraryBox);
    P.main = h('div', { class: 'qz-main' }, P.resHead, P.formHome, P.viewRow,
        P.recentBox, P.results, P.discoverBox, P.awardedBox, P.libraryBox);
    el.append(h('div', { class: 'qz' }, P.banner, P.main, P.player));
    P.refreshAIIcon();
    P.buildPlayer();
    P.paintDate(); P.paintSort(); P.paintAwarded(); P.paintQuality(); P.paintSource(); P.paintLabels();
    P.setLabelsOpen(true);
    P.openFilters(false);                          // the summary line opens them
    P.poll = new K.Poller(P.refreshStatus, 10000);
    P.playerPoll = new K.Poller(P.refreshPlayer, 2000);
    P.recent();
    // the last results, as they were: the app may have been left, or the page reloaded
    const kept = pref('lastResults', null);
    if (kept && kept.d && Array.isArray(kept.d.results)) {
        // A previous artwork request may have cached a temporary 404. Give
        // restored local results a fresh URL so this page load retries it.
        const artRetry = Date.now();
        for (const card of kept.d.results) {
            if (card.source !== 'local' || !card.image) continue;
            const url = new URL(card.image, location.href);
            url.searchParams.set('_retry', String(artRetry));
            card.image = url.pathname + url.search;
        }
        P.last = kept.d; P.searchedKey = kept.key || null;
        P.paintResults(); P.paintSeen();
    }
    P.paintView();
    if ('IntersectionObserver' in window) new IntersectionObserver(es => {
        P.searchHint.hidden = es[es.length - 1].isIntersecting;
    }, { root: P.el, rootMargin: '-24px 0px 0px 0px' }).observe(P.formHome);   // a sliver at the top edge is not "in view"
    P.loadWords();
    K.api('/qobuz/lowered').then(d => { if (d.ok) { P.lowCountCache = d.entries.length; P.paintLowLink(); } });
};

// Prepare before the pager moves: bringing the form back after the swipe settles
// would insert it into an already visible page. Keep it above the viewport.
P.prepareEnter = from => {
    if (P.wantSearch) {
        // the top bar's Search: this page with its box in view, from wherever
        P.wantSearch = false;
        P.ensureMounted();
        P.dismissPreview();
        P.fromNow = false;
        P.undock();
        P.el.scrollTo({ top: 0, behavior: 'instant' });
        return;
    }
    if (from !== 'now') return;
    P.ensureMounted();
    P.dismissPreview();
    if (P.fromNow) return;
    if (!P.dockedOnNow()) return;
    P.undock();
    P.el.scrollTo({ top: P.el.scrollTop + P.formHome.getBoundingClientRect().bottom
        - P.el.getBoundingClientRect().top, behavior: 'instant' });
};

P.show = () => {
    P.visible = true;
    P.dismissPreview();
    // searched from Now: the results under their head, the box stays down there;
    // otherwise the box is up here
    P.fromNowShown = P.fromNow;
    P.fromNow = false;
    P.resHead.hidden = !P.fromNowShown;
    P.viewRow.hidden = P.fromNowShown;
    if (P.fromNowShown) P.setView('results');
    else if (P.view() === 'recent') P.recent();
    else if (P.view() === 'discover') P.discover();
    else if (P.view() === 'awarded') P.awardedList();
    else if (P.view() === 'library') K.library.show();
    if (!P.fromNowShown) P.undock();
    P.poll.start();
    P.playerPoll.start();
    P.clock = setInterval(P.paintTime, 500);
};
P.hide = () => {
    P.visible = false;
    K.library.hide();
    P.closeTileMenu();
    if (P.discoverObserver) P.discoverObserver.disconnect();
    P.poll.stop();
    P.playerPoll.stop();
    clearInterval(P.clock);
    // results searched from Now are only for the moment: left (‹, a play, a swipe),
    // they go, from here and from the preview on Now
    // ... and so is the preview on Now, once its results have been looked at up here
    const seen = P.fromNowShown || (P.view() === 'results' && P.form.classList.contains('qz-has-res'));
    if (P.fromNowShown) { P.fromNowShown = false; P.forget(); }
    else if (seen) P.forget();
};

// Back on Now as before the search: no preview, Search beside the field.  The results
// stay, for this page's Results view.  A search still running is dropped.
P.forget = () => {
    if (P.request) {
        ++P.searching;
        if (P.streamEnd) P.streamEnd({ ok: false, error: 'dropped' });
        P.request = null;
        if (P.last) P.paintResults(); else K.clear(P.results);
        P.paintView();
    }
    P.preview.hidden = true;
    P.fetchProgress.hidden = true;
    P.form.classList.remove('qz-has-res');
    P.updatePreviewAction();
};

// ── which list: played recently, or the results ──────────────────────────────
P.view = () => ['results', 'recent', 'discover', 'awarded', 'library'].includes(pref('view', 'recent')) ? pref('view', 'recent') : 'recent';
P.setView = (v, scroll = false) => {
    P.closeGenreMenu(); P.closeTileMenu();
    if (scroll && document.activeElement === P.input) P.input.blur();
    const wasLibrary = P.view() === 'library';
    setPref('view', v); P.paintView();
    if (wasLibrary && v !== 'library') K.library.hide();
    if (v === 'library') { if (P.visible && !K.library.visible) K.library.show(); }
    else if (v === 'recent') P.recent();
    else if (v === 'awarded') P.awardedList();
    else if (v === 'discover') P.discover();
    if (scroll && P.visible && !P.viewRow.hidden) requestAnimationFrame(() => {
        if (P.viewRow.hidden) return;
        P.el.scrollTo({ top: P.el.scrollTop + P.viewRow.getBoundingClientRect().top
            - P.el.getBoundingClientRect().top, behavior: 'smooth' });
    });
};
P.paintView = () => {
    const v = P.view();
    [...P.viewChips.children].forEach(b => b.classList.toggle('on', b.dataset.view === v));
    P.recentBox.hidden = v !== 'recent';
    P.results.hidden = v !== 'results';
    P.awardedBox.hidden = v !== 'awarded';
    P.discoverBox.hidden = v !== 'discover';
    P.libraryBox.hidden = v !== 'library';
    P.layoutBtn.hidden = v === 'library';
    if (v === 'results' && !P.results.firstChild)
        P.results.append(h('p', { class: 'muted' }, 'No search yet: the search is at the top.'));
};
P.prefetch = () => P.refreshStatus();     // which also loads the labels

// ── list or grid ─────────────────────────────────────────────────────────────
// The grid is Qobuz's Discover: covers in columns, the title and artist small under
// each.  Switching rebuilds the albums already on screen from their cards, so a
// Discover read several pages deep stays as it is.
P.grid = () => pref('layout', 'list') === 'grid';
P.paintLayout = () => {
    const grid = P.grid();
    K.clear(P.layoutBtn).append(K.tIcon(grid ? 'list' : 'grid'));
    P.layoutBtn.title = grid ? 'Show as a list' : 'Show as a grid';
    P.layoutBtn.setAttribute('aria-label', P.layoutBtn.title);
};
P.setLayout = v => {
    P.closeTileMenu();
    setPref('layout', v);
    P.paintLayout();
    P.main.querySelectorAll('.qz-list').forEach(list => {
        list.classList.toggle('qz-grid', P.grid());
        [...list.children].forEach(el => { if (el.__card) el.replaceWith(P.item(el.__card, el.__where)); });
    });
};
P.list = (cards, where = '') => h('div', { class: 'qz-list' + (P.grid() ? ' qz-grid' : '') }, cards.map(c => P.item(c, where)));
P.item = (c, where = '') => P.grid() ? P.tile(c, where) : P.row(c, where);

// ── where the search box is ──────────────────────────────────────────────────
// One search box (field, completions, filters), in one place at a time: at the
// bottom of Now while that page is upright (pages/now.js gives it a slot), else at
// the top of this page.  A search made down there opens this page on its results,
// under a "‹ Search results" head whose ‹ goes back to Now.
P.ensureMounted = () => {
    if (!P.mounted) { P.mounted = true; P.mount(P.body); P.refreshStatus(); }
};
P.dockedOnNow = () => !!P.form && P.form.parentElement !== P.formHome;
// The top bar's Search, from any page.  Reached otherwise (a swipe, a tab), this
// page keeps the box scrolled out of sight above the lists.
K.qobuzSearch = () => {
    P.wantSearch = true;
    K.showPage(P.id);
};
K.qobuzDock = slot => {
    P.ensureMounted();
    if (P.form.parentElement === slot) return;
    P.openFilters(false);                          // folded down there until asked for
    slot.append(P.preview, P.fetchProgress, P.form);
    P.form.classList.add('qz-docked');
    P.formHome.hidden = true;
};
P.undock = () => {
    if (!P.dockedOnNow()) return;
    P.form.classList.remove('qz-docked');
    P.preview.remove();
    P.fetchProgress.remove();
    P.formHome.append(P.form);
    P.formHome.hidden = false;
};

// An explicit search (the button, Enter).  From Now it stays there: the results show
// as a preview above the box, and › opens them here.
P.go = () => {
    if (P.request || P.aiStarting) return P.stopSearch();
    if (pref('aiMode', false)) return P.askAI();
    P.input.blur();
    P.showSuggestions([]);
    P.search();
};
P.paintSearchControl = () => {
    const busy = !!(P.request || P.aiStarting);
    P.aiButton.disabled = false;
    K.clear(P.aiButton).append(K.tIcon(busy ? 'close' : 'search'));
    P.aiButton.title = busy ? 'Stop search' : 'Search';
    P.aiButton.setAttribute('aria-label', P.aiButton.title);
    P.aiToggle.disabled = busy;
    P.form.classList.toggle('qz-searching', busy);
};
P.stopSearch = () => {
    clearTimeout(soon);
    ++P.searching;
    if (P.searchController) P.searchController.abort();
    P.searchController = null;
    if (P.streamEnd) P.streamEnd({ ok: false, error: 'Search stopped' });
    P.request = null; P.aiStarting = false; P.aiRunning = false;
    P.autoMore = false;
    if (P.moreObserver) P.moreObserver.disconnect();
    P.searchedKey = null;
    P.fetchProgress.hidden = true;
    P.paintSearchControl(); P.paintStale();
    if (P.last) { P.paintResults(); P.paintPreview(); }
    else { P.paintError('Search stopped'); P.paintPreview('Search stopped'); }
};
// No smooth scroll: the keyboard closing meanwhile resizes the page, and a resize
// puts the pager back on the page it is on (main.js).
P.openResults = () => {
    P.dismissPreview();
    P.input.blur();
    P.setView('results');
    P.fromNow = true;
    K.showPage(P.id, false);
    P.el.scrollTop = 0;
};
P.dismissPreview = () => {
    P.previewDismissed = true;
    P.preview.hidden = true;
    P.fetchProgress.hidden = true;
    P.form.classList.remove('qz-has-res');
    P.updatePreviewAction();
};
P.dismissEditedPreview = () => {
    const length = P.input.value.trim().length;
    const searchedLength = new URLSearchParams(P.searchedKey || '').get('q')?.length || 0;
    if (!length || (searchedLength && length <= searchedLength * .75)) {
        P.dismissPreview();
    }
};
P.previewReady = () => P.dockedOnNow() && P.form.classList.contains('qz-has-res')
    && !P.form.classList.contains('qz-stale');
P.updatePreviewAction = () => {
    const ready = P.previewReady();
    P.preview.classList.toggle('ready', ready);
    P.preview.tabIndex = ready ? 0 : -1;
    P.preview.setAttribute('aria-disabled', String(!ready));
};

// The app shrinks the page by the keyboard: keep the field (and at the bottom of Now,
// the completions above it) in sight once it has.
P.revealInput = () => {
    [120, 450].forEach(ms => setTimeout(() => {
        if (document.activeElement === P.input) P.input.scrollIntoView({ block: 'nearest' });
    }, ms));
};
addEventListener('resize', () => { if (P.input && document.activeElement === P.input) P.revealInput(); });

// The field keeps the focus only while it is being used.  A focused field is what
// Android brings the keyboard back for, on the next touch anywhere in the page (the
// cover's seek ring cancels the touch's default, so the browser does not blur it
// then); the keyboard then shrinks the page and the field is scrolled into view.
// So: a touch outside the search box, or the keyboard closing (the Android back
// gesture, which blurs nothing), ends the typing.
document.addEventListener('pointerdown', e => {
    if (P.input && document.activeElement === P.input && !(e.target.closest && e.target.closest('.qz-searchwrap')))
        P.input.blur();
}, { capture: true, passive: true });
// The keyboard has gone when the page, having shrunk for it, is back to its height from
// before the field took the focus.  Not merely "grew again": in the app the page may
// shrink twice as the keyboard comes up (the WebView's own step, then the app's padding),
// and the step back between the two is not the keyboard going.
if (window.visualViewport) {
    let base = visualViewport.height, low = base;    // before the focus; the lowest since
    document.addEventListener('focusin', e => {
        if (e.target === P.input) { base = visualViewport.height; low = base; }
    });
    visualViewport.addEventListener('resize', () => {
        if (!P.input || document.activeElement !== P.input) return;
        const hgt = visualViewport.height;
        low = Math.min(low, hgt);
        if (low < base - 120 && hgt > base - 60) P.input.blur();
    });
}

// ── availability ─────────────────────────────────────────────────────────────
P.refreshStatus = async () => {
    const s = await K.api('/qobuz/status', { timeout: 8000 });
    P.status = s;
    if (!P.favouritesLoaded && s.ok && s.enabled) P.loadLabels();
    P.paintBanner();
};

K.qobuzBusy = () => !!P.request;     // a search running: main.js waits with a reload
P.usable = () => !!(P.status && P.status.ok && P.status.enabled && P.status.renderer);

P.paintBanner = () => {
    const s = P.status;
    let box = null;
    if (!s) box = null;
    else if (!s.ok) box = h('div', { class: 'errbox' }, s.error || 'The Qobuz search cannot be reached.');
    else if (!s.renderer) box = h('div', { class: 'errbox warn qz-banner' },
        h('div', {}, h('strong', {}, 'upmpdcli is not running.'),
            h('div', { class: 'small' }, 'Albums found here play through upmpdcli on the box. Switch the renderer to upmpdcli to use this page.')),
        h('button', { type: 'button', class: 'btn', onclick: () => K.goto('source') }, 'Source page'));
    else if (!s.token) box = h('div', { class: 'errbox warn qz-banner' },
        h('div', {}, h('strong', {}, 'Not signed in to Qobuz.'),
            h('div', { class: 'small' }, 'upmpdcli’s Qobuz plugin has no sign-in yet. Sign in from the full panel.')),
        h('button', { type: 'button', class: 'btn', onclick: () => K.frame('/', 'Full panel') }, 'Full panel'));
    K.clear(P.banner);
    if (box) P.banner.append(box);
    P.main.classList.toggle('qz-disabled', !P.usable());
    P.player.classList.toggle('qz-disabled', !P.usable());
    P.form.classList.toggle('qz-disabled', !P.usable() && P.dockedOnNow());   // on Now, outside P.main
    if (!P.usable()) P.closeFull();
};

// ── labels ───────────────────────────────────────────────────────────────────
P.loadLabels = async () => {
    const d = await K.api('/qobuz/labels');
    if (!d.ok) return;
    P.favouritesLoaded = true;
    P.favourites = d.labels.map(l => l.name);
    P.paintLabels();
    P.paintDiscoverFilters();
};

P.setLabelsOpen = open => {
    P.labelsOpen = open;
    P.labelsBody.hidden = !open;
    P.filters.classList.toggle('qz-labels-closed', !open);
    P.labelsToggle.setAttribute('aria-expanded', String(open));
    P.labelsToggle.textContent = `${open ? '⌄' : '›'} LABELS${P.selected.size ? ` (${P.selected.size} selected)` : ''}`;
};

// Recognize complete terms anywhere, or a sufficiently long final word prefix
// while typing. Short fragments would classify too many ordinary searches.
P.syncLabels = () => {
    const query = P.plain(P.input.value);
    const hinted = P.paintHints(query);
    if (!query) { P.setLabelsOpen(true); return; }
    if (hinted) { P.setLabelsOpen(true); return; }
    if (query.length < 4 && query !== 'duo') return;
    const padded = ` ${query} `;
    const last = query.split(' ').pop();
    P.setLabelsOpen(P.words.some(w => w.term && (padded.includes(` ${w.term} `)
        || (query === last && last.length >= 4 && !w.term.includes(' ') && w.term.startsWith(last))))
        || query === 'quarted');
};

// The labels of the artist being searched, offered as chips: Qobuz cannot search
// by label, so the way to a label is through the artist.  An artist is recognised
// at the start or the end of the text; a short single word ("Low", "Free", "Can")
// only when it is all that was typed, or every search with the word would offer it.
P.hintsFor = query => {
    if (!query) return [];
    const found = [];
    for (const a of P.artists) {
        const t = a.term;
        if (!t || t.length < 3) continue;
        const short = !t.includes(' ') && t.length <= 4;
        if (query === t || (!short && (query.startsWith(t + ' ') || query.endsWith(' ' + t)))) {
            found.push({ artist: a.e.text, labels: [...a.labels], term: t });
        }
    }
    for (const [artist, label] of P.artistPairs) {
        const t = P.plain(artist);
        if (t && t.length >= 3 && (query === t || query.startsWith(t + ' ') || query.endsWith(' ' + t))) {
            let f = found.find(x => x.term === t);
            if (!f) found.push(f = { artist, labels: [], term: t });
            if (!f.labels.some(l => P.plain(l) === P.plain(label))) f.labels.push(label);
        }
    }
    return found.sort((a, b) => b.term.length - a.term.length).slice(0, 2);
};

P.paintHints = query => {
    const found = P.hintsFor(query);
    K.clear(P.hintBox);
    for (const f of found) {
        P.hintBox.append(h('div', { class: 'qz-hint' },
            h('div', { class: 'small muted' }, `Labels of ${f.artist}`),
            h('div', { class: 'qz-chips' }, f.labels.map(name => h('button', {
                type: 'button', class: 'chip tog qz-chip' + (P.selected.has(name) ? ' on' : ''),
                onclick: () => P.toggleLabel(name),
            }, name)))));
    }
    return found.length > 0;
};

// User-entered labels are available in both filter lists, with independent ticks.
P.labelNames = selected => {
    const names = [];
    for (const name of [...P.favourites, ...pref('customLabels', []), ...selected]) {
        if (!names.some(n => n.toLowerCase() === name.toLowerCase())) names.push(name);
    }
    return names;
};
P.addLabel = view => {
    const input = h('input', { type: 'text', class: 'qz-input', placeholder: 'Label name',
        'aria-label': 'Label name', autocomplete: 'off' });
    const error = h('p', { class: 'small', hidden: true });
    const close = () => scrim.remove();
    const save = e => {
        e.preventDefault();
        const typed = input.value.trim().replace(/\s+/g, ' ');
        if (!typed) { error.hidden = false; error.textContent = 'Enter a label name.'; input.focus(); return; }
        const name = P.labelNames([]).find(n => n.toLowerCase() === typed.toLowerCase()) || typed;
        if (!P.labelNames([]).some(n => n.toLowerCase() === typed.toLowerCase()))
            setPref('customLabels', [...pref('customLabels', []), name]);
        if (view === 'discover') {
            const selected = P.discoverSelected();
            selected.add(name); setPref('discoverLabels', [...selected]);
            P.paintDiscoverFilters(); P.paintLabels(); P.discover();
            P.discoverLabelsScroll.scrollTop = P.discoverLabelsScroll.scrollHeight;
        } else {
            P.selected.add(name); setPref('labels', [...P.selected]);
            P.paintLabels(); P.paintDiscoverFilters(); P.searchSoon();
            P.labelsScroll.scrollTop = P.labelsScroll.scrollHeight;
        }
        close();
    };
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('form', { class: 'sheet qz-add-label', role: 'dialog', 'aria-modal': 'true',
            'aria-label': 'Add label', onsubmit: save },
            h('h2', {}, 'Add label'), input, error,
            h('div', { class: 'sheet-actions' },
                h('button', { type: 'button', class: 'btn', onclick: close }, 'Cancel'),
                h('button', { type: 'submit', class: 'btn primary' }, 'Add'))));
    document.getElementById('overlay-root').append(scrim);
    input.focus();
};

// A ticked name that is not a favourite is sent as its own label keyword.
P.paintLabels = () => {
    const names = P.labelNames(P.selected);
    const top = P.labelsScroll.scrollTop;
    P.paintSummary();
    P.paintHints(P.plain(P.input.value));            // their ticks follow
    const chip = name => h('button', {
        type: 'button', class: 'chip tog qz-chip' + (P.selected.has(name) ? ' on' : ''),
        onclick: () => P.toggleLabel(name),
    }, name);
    K.clear(P.labelsScroll).append(...names.map(chip));
    P.labelsScroll.scrollTop = top;
    K.clear(P.labelsBox).append(P.labelsScroll,
        h('button', { type: 'button', class: 'chip qz-chip qz-add-label-button',
            'aria-label': 'Add search label', title: 'Add label', onclick: () => P.addLabel('search') }, '+'));
    P.paintSeen();
    if (P.labelsToggle && P.labelsOpen !== undefined) P.setLabelsOpen(P.labelsOpen);
};

// The labels met in the last results, to tick one that is not a favourite.
P.paintSeen = () => {
    K.clear(P.seenBox);
    const seen = (P.last && P.last.labels_seen || [])
        .filter(l => !l.groups.length && !P.selected.has(l.name)).slice(0, 24);
    if (!seen.length) return;
    P.seenBox.append(h('details', { class: 'qz-seen' },
        h('summary', { class: 'small muted' }, `Other labels in these results (${seen.length})`),
        h('div', { class: 'qz-chips' }, seen.map(l => h('button', {
            type: 'button', class: 'chip qz-chip', onclick: () => P.toggleLabel(l.name),
        }, `${l.name} · ${l.count}`)))));
};

P.toggleLabel = name => {
    if (P.selected.has(name)) P.selected.delete(name);
    else P.selected.add(name);
    setPref('labels', [...P.selected]);
    P.paintLabels();
    P.searchSoon();
};

// ── release date and order ───────────────────────────────────────────────────
P.paintDate = () => {
    const mode = pref('date', 'any');
    const kids = [K.segmented([
        { value: 'any', label: 'Any time' }, { value: 'last', label: 'Last years' }, { value: 'span', label: 'From – to' },
    ], mode, v => { setPref('date', v); P.paintDate(); P.searchSoon(); }, 'small')];
    if (mode === 'last') {
        const n = pref('lastN', 2);
        const out = h('output', {}, n === 1 ? 'the last year' : `the last ${n} years`);
        kids.push(h('div', { class: 'win-row' },
            h('input', {
                type: 'range', min: 1, max: 30, step: 1, value: n,
                oninput: e => { const v = +e.target.value; out.textContent = v === 1 ? 'the last year' : `the last ${v} years`; },
                onchange: e => { setPref('lastN', +e.target.value); P.searchSoon(); },
            }), out));
    } else if (mode === 'span') {
        kids.push(h('div', { class: 'win-row qz-span' },
            P.yearSpinner('from'), h('span', { class: 'muted' }, 'to'), P.yearSpinner('to')));
    }
    K.clear(P.dateBox).append(...kids);
    P.paintSummary();
};

// ── year spinners (the from – to span) ───────────────────────────────────────
// A year is changed by dragging it up or down, faster the further the finger
// goes (a quarter of a screen is decades); a tap opens a picker instead.
// "to" can also be "today" (no upper bound), one step above this year.
const YEAR_MIN = 1900;
const TODAY = thisYear + 1;                  // "to" only: the open end
const yearOf = key => {
    const v = parseInt(pref(key, key === 'from' ? thisYear - 5 : ''), 10);
    return Number.isFinite(v) ? K.clamp(v, YEAR_MIN, thisYear) : key === 'from' ? thisYear - 5 : TODAY;
};
const yearText = y => y === TODAY ? 'today' : String(y);

// Store one end, and move the other when the span would turn inside out.
P.setYear = (key, y) => {
    setPref(key, y === TODAY ? '' : y);
    const from = yearOf('from'), to = yearOf('to');
    if (to !== TODAY && from > to) setPref(key === 'from' ? 'to' : 'from', key === 'from' ? from : to);
    P.paintDate();
    P.searchSoon();
};

P.yearSpinner = key => {
    const top = key === 'to' ? TODAY : thisYear;
    const value = h('span', { class: 'qz-yval' }, yearText(yearOf(key)));
    const box = h('div', {
        class: 'qz-yspin tap', role: 'spinbutton', tabindex: 0,
        'aria-label': key === 'from' ? 'From year' : 'To year', 'aria-valuenow': yearOf(key),
        title: 'Drag up or down, or tap to pick',
    }, h('span', { class: 'qz-yarrow' }, '▲'), value, h('span', { class: 'qz-yarrow' }, '▼'));
    let drag = null;
    const show = y => { value.textContent = yearText(y); box.setAttribute('aria-valuenow', y); };
    box.addEventListener('pointerdown', e => {
        e.stopPropagation();          // a diagonal drag must not turn the page
        drag = { id: e.pointerId, y: e.clientY, start: yearOf(key), now: yearOf(key), moved: false };
        try { box.setPointerCapture(e.pointerId); } catch {}
        box.classList.add('on');
    });
    box.addEventListener('pointermove', e => {
        if (!drag || e.pointerId !== drag.id) return;
        const dy = drag.y - e.clientY;                   // up = later
        if (Math.abs(dy) > 6) drag.moved = true;
        // 1 year per 12 px at first, then faster: 50 px ≈ 5 years, 150 px ≈ 25
        const steps = Math.sign(dy) * Math.floor(Math.pow(Math.abs(dy) / 12, 1.35));
        drag.now = K.clamp(drag.start + steps, YEAR_MIN, top);
        show(drag.now);
    });
    const end = e => {
        if (!drag || e.pointerId !== drag.id) return;
        const d = drag; drag = null;
        box.classList.remove('on');
        if (!d.moved) P.pickYear(key);
        else if (d.now !== d.start) P.setYear(key, d.now);
    };
    box.addEventListener('pointerup', end);
    box.addEventListener('pointercancel', e => { if (drag && e.pointerId === drag.id) { drag = null; box.classList.remove('on'); show(yearOf(key)); } });
    box.addEventListener('wheel', e => {                  // a mouse on the desktop kiosk
        e.preventDefault();
        P.setYear(key, K.clamp(yearOf(key) + (e.deltaY < 0 ? 1 : -1), YEAR_MIN, top));
    }, { passive: false });
    box.addEventListener('keydown', e => {
        const step = { ArrowUp: 1, ArrowDown: -1, PageUp: 10, PageDown: -10 }[e.key];
        if (step) { e.preventDefault(); P.setYear(key, K.clamp(yearOf(key) + step, YEAR_MIN, top)); }
        else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); P.pickYear(key); }
    });
    return box;
};

// The picker: the year large with ±1 and ±10, a decade of years to tap, and
// for "to" a Today button.  Typing a year works too.
P.pickYear = key => {
    const top = key === 'to' ? TODAY : thisYear;
    let year = yearOf(key);
    let decade = Math.floor(Math.min(year, thisYear) / 10) * 10;
    const field = h('input', {
        type: 'text', class: 'qz-yfield', inputmode: 'numeric', maxlength: 4, 'aria-label': 'Year',
        oninput: e => {
            const v = parseInt(e.target.value, 10);
            if (e.target.value.length === 4 && v >= YEAR_MIN && v <= thisYear) { year = v; decade = Math.floor(v / 10) * 10; paint(false); }
        },
        onkeydown: e => { if (e.key === 'Enter') { e.preventDefault(); done(true); } },
    });
    const grid = h('div', { class: 'qz-ygrid' });
    const decadeLabel = h('span', { class: 'qz-ydecade' });
    const step = n => { year = K.clamp(year + n, YEAR_MIN, top); if (year !== TODAY) decade = Math.floor(year / 10) * 10; paint(); };
    const btn = (label, fn, cls = '') => h('button', { type: 'button', class: 'btn ' + cls, onclick: fn }, label);
    function paint(setField = true) {
        if (setField) field.value = year === TODAY ? '' : String(year);
        field.placeholder = year === TODAY ? 'today' : '';
        decadeLabel.textContent = `${decade}s`;
        K.clear(grid).append(...Array.from({ length: 10 }, (_, i) => decade + i).map(y =>
            h('button', { type: 'button', class: 'btn qz-ycell' + (y === year ? ' active' : ''), disabled: y < YEAR_MIN || y > thisYear,
                onclick: () => { year = y; done(true); } }, y)));
    }
    const close = () => scrim.remove();
    function done(apply) { close(); if (apply) P.setYear(key, year); }
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet qz-ysheet' },
            h('h2', {}, key === 'from' ? 'Released from' : 'Released until'),
            h('div', { class: 'qz-yrow' }, btn('−10', () => step(-10), 'step'), btn('−1', () => step(-1), 'step'),
                field, btn('+1', () => step(1), 'step'), btn('+10', () => step(10), 'step')),
            h('div', { class: 'qz-yrow' },
                btn('‹', () => { decade = Math.max(Math.floor(YEAR_MIN / 10) * 10, decade - 10); paint(false); }, 'step'),
                decadeLabel,
                btn('›', () => { decade = Math.min(Math.floor(thisYear / 10) * 10, decade + 10); paint(false); }, 'step')),
            grid,
            h('div', { class: 'sheet-actions' },
                key === 'to' ? btn('Today', () => { year = TODAY; done(true); }) : null,
                h('span', { class: 'spacer' }),
                btn('Cancel', () => done(false)),
                btn('Set', () => done(true), 'primary'))));
    paint();
    document.getElementById('overlay-root').append(scrim);
};

// ── filter summary ───────────────────────────────────────────────────────────
P.filterSummary = () => {
    const mode = pref('date', 'any');
    const to = yearOf('to');
    const when = mode === 'last' ? (pref('lastN', 2) === 1 ? 'last year' : `last ${pref('lastN', 2)} years`)
        : mode === 'span' ? `${yearOf('from')}–${to === TODAY ? 'today' : to}` : '';
    return [P.selected.size ? [...P.selected].join(', ') : '', when,
        pref('order', 'relevance') === 'date' ? 'newest first' : '',
        ...(pref('awarded', false) ? ['awarded only'] : []),
        ...(pref('hires', false) ? ['Hi-Res: exclude 16/44.1'] : []),
        ...(pref('localOnly', false) ? ['Local only'] : [])].filter(Boolean).join(' · ');
};

P.filtersActive = () => P.selected.size > 0 || pref('date', 'any') !== 'any'
    || pref('order', 'relevance') !== 'relevance' || pref('awarded', false) || pref('hires', false)
    || pref('localOnly', false);
P.resetFilters = () => {
    clearTimeout(soon);
    P.filtersDirty = false;
    P.selected.clear();
    for (const [key, value] of Object.entries({ labels: [], date: 'any', from: thisYear - 5, to: '', lastN: 2,
        order: 'relevance', awarded: false, hires: false, localOnly: false })) setPref(key, value);
    P.paintLabels(); P.paintDate(); P.paintSort(); P.paintAwarded(); P.paintQuality(); P.paintSource();
    P.openFilters(false);
};
P.paintSummary = () => {
    if (!P.fsum) return;
    const active = !!P.filtersActive();
    P.fsum.classList.toggle('qz-filtered', active);
    K.clear(P.fsum).append(
        h('button', { type: 'button', class: 'qz-fsum-toggle',
            'aria-label': 'Filters', 'aria-expanded': String(!!P.filtersOpen),
            title: P.filterSummary() || 'Filters', onclick: () => P.openFilters(!P.filtersOpen) },
            h('span', { class: 'qz-fsum-text' }, P.filterSummary()),
            h('span', { class: 'qz-fsum-mark' }, 'Filters' + (P.filtersOpen ? ' ▴' : ' ▾'))));
    if (P.filtersDirty) P.fsum.append(h('button', { type: 'button', class: 'btn qz-filter-apply',
        title: 'Search with these filters', onclick: () => P.go() }, 'Apply Filters'));
    if (active) P.fsum.append(h('button', { type: 'button', class: 'btn qz-filter-clear',
        title: 'Clear filters', 'aria-label': 'Clear filters',
        onclick: () => { P.resetFilters(); P.searchSoon(); } }, K.tIcon('clear')));
};

P.openFilters = open => {
    P.filtersOpen = open;
    P.filters.hidden = !open;
    P.paintSummary();
    if (open && P.dockedOnNow()) setTimeout(() => P.form.scrollIntoView({ block: 'end', behavior: 'smooth' }), 30);
};

P.paintSort = () => {
    P.paintSummary();
    K.clear(P.sortBox).append(K.segmented([
        { value: 'relevance', label: 'Qobuz order' }, { value: 'date', label: 'Newest first' },
    ], pref('order', 'relevance'), v => { setPref('order', v); P.paintSort(); P.searchSoon(); }, 'small'));
};

P.paintAwarded = () => {
    const only = pref('awarded', false);
    K.clear(P.awardedFilterBox).append(h('button', {
        type: 'button', class: 'chip tog qz-chip' + (only ? ' on' : ''),
        'aria-pressed': String(only),
        title: 'Albums with an award or your rating already found by this app',
        onclick: () => { setPref('awarded', !only); P.paintAwarded(); P.searchSoon(); },
    }, 'Awarded only'));
    P.paintSummary();
};

P.paintQuality = () => {
    const on = pref('hires', false);
    K.clear(P.qualityBox).append(h('button', {
        type: 'button', class: 'chip tog qz-chip' + (on ? ' on' : ''),
        'aria-pressed': String(on), title: 'Exclude 16-bit/44.1 kHz releases',
        onclick: () => { setPref('hires', !on); P.paintQuality(); P.searchSoon(); },
    }, 'Hi-Res'));
    P.paintSummary();
};

P.paintSource = () => {
    const on = pref('localOnly', false);
    K.clear(P.sourceBox).append(h('button', {
        type: 'button', class: 'chip tog qz-chip' + (on ? ' on' : ''),
        'aria-pressed': String(on), title: 'Show only albums in the local collection',
        onclick: () => { setPref('localOnly', !on); P.paintSource(); P.searchSoon(); },
    }, 'Local'));
    P.paintSummary();
};

P.setAIMode = enabled => {
    setPref('aiMode', enabled);
    P.aiToggle.classList.toggle('active', enabled);
    P.aiToggle.setAttribute('aria-pressed', String(enabled));
    const oldInput = P.input;
    const focused = document.activeElement === oldInput;
    P.input = P.makeInput(enabled);
    P.input.value = oldInput.value;
    oldInput.replaceWith(P.input);
    if (enabled) P.searchActions.append(P.fsum);
    else P.searchWrap.after(P.fsum);
    P.showSuggestions([]);
    if (focused) P.input.focus();
    if (enabled) P.resetFilters();
    P.paintSummary();
    P.searchedKey = null; P.paintStale();
};
P.toggleAI = async () => {
    if (P.aiToggle.disabled) return;
    if (pref('aiMode', false)) { P.setAIMode(false); return; }
    P.aiToggle.disabled = true;
    try {
        const settings = await K.api('/qobuz/ai/settings');
        P.paintAIIcon(settings);
        if (!settings.ok) { K.toast(settings.error || 'Could not read AI settings', 'error'); return; }
        if (!settings.configured) { await P.aiSettings(true); return; }
        P.setAIMode(true);
    } finally { P.aiToggle.disabled = !!(P.request || P.aiStarting); }
};
P.refreshAIIcon = async () => {
    const settings = await K.api('/qobuz/ai/settings');
    P.paintAIIcon(settings);
};
P.paintAIIcon = settings => {
    if (!P.aiToggle) return;
    const provider = settings.ok && settings.configured ? settings.provider : '';
    const name = provider.startsWith('claude') ? 'Claude' : provider === 'openai' ? 'ChatGPT' : 'AI';
    K.clear(P.aiToggle).append(name === 'AI' ? 'AI' : h('img', {
        src: '/k/static/img/' + (name === 'Claude' ? 'claude' : 'openai') + '.svg',
        alt: '', width: '18', height: '18', class: 'qz-provider-icon' }));
    P.aiToggle.title = name + ' search mode';
    P.aiToggle.setAttribute('aria-label', name + ' search mode');
};
P.aiPost = async (url, json, ctl = new AbortController()) => {
    const timer = setTimeout(() => ctl.abort(), 420000);
    try {
        return await (await fetch(url, { method: 'POST', signal: ctl.signal,
            headers: { 'Content-Type': 'application/json', 'X-Qobuz-AI': '1' },
            body: JSON.stringify(json) })).json();
    } catch (e) {
        return { ok: false, error: e.name === 'AbortError' ? 'AI research timed out. Try again.' : 'Could not reach AI recommendations.' };
    } finally { clearTimeout(timer); }
};

P.accountUsageText = status => {
    if (!status?.windows) return '';
    const names = { five_hour: '5-hour', seven_day: '7-day' };
    return Object.entries(names).map(([key, label]) => {
        const window = status.windows[key];
        if (!window) return '';
        const reset = window.resets_at ? ` · resets ${new Date(window.resets_at * 1000).toLocaleString()}` : '';
        return `${label}: ${window.used_percent}% used${reset}`;
    }).filter(Boolean).join(' · ');
};

P.aiSettings = K.openAISettings = async (required = false) => {
    const d = await K.api('/qobuz/ai/settings');
    if (!d.ok) { K.toast(d.error, 'error'); return; }
    const provider = h('select', { 'aria-label': 'AI provider' },
        h('option', { value: 'claude_account' }, 'Claude account (server login)'),
        h('option', { value: 'claude' }, 'Claude API'), h('option', { value: 'openai' }, 'OpenAI API'));
    provider.value = d.provider;
    // Account aliases follow the newest model of their tier, so the list never needs refreshing.
    const tiers = { sonnet: 'Sonnet (latest) · balanced, recommended', opus: 'Opus (latest) · most thorough, slower, uses limits faster',
        fable: 'Fable (latest) · top tier', haiku: 'Haiku (latest) · fastest, lightest' };
    const model = h('select', { 'aria-label': 'Model' });
    const custom = h('input', { type: 'text', 'aria-label': 'Exact model name', autocomplete: 'off', placeholder: 'Exact model name' });
    const modelHint = h('span', { class: 'small muted' });
    const paintModels = value => {
        const list = d.choices?.[provider.value] || [];
        K.clear(model).append(...list.map(m => h('option', { value: m }, tiers[m] || m)), h('option', { value: '' }, 'Other…'));
        model.value = list.includes(value) ? value : '';
        custom.value = list.includes(value) ? '' : value;
        custom.hidden = model.value !== '';
        modelHint.textContent = provider.value === 'claude_account'
            ? '“Latest” moves to each new model of that tier automatically; the next AI research tells you when it does.' : '';
    };
    model.onchange = () => { custom.hidden = model.value !== ''; if (!custom.hidden) custom.focus(); };
    const key = h('input', { type: 'password', 'aria-label': 'API key', autocomplete: 'off',
        placeholder: d.configured ? 'Key configured; leave blank to keep it' : 'Paste your API key' });
    const keyLabel = h('label', {}, 'API key', key);
    const note = h('p', { class: 'small muted' });
    const usage = h('p', { class: 'small' });
    const paintAccount = () => {
        const account = provider.value === 'claude_account';
        keyLabel.hidden = account;
        note.textContent = account
            ? (d.account_ready ? 'Claude is signed in on this server. Requests use that account and its applicable usage limits or credits.' : 'Sign in to Claude Code on this server as the web service user. No API key is needed for account mode.')
            : 'API requests use separately billed provider credits. The key is saved on this server and is never returned to the browser. A blank key keeps the saved key.';
        const link = account ? 'https://claude.ai/settings/usage' : provider.value === 'openai'
            ? 'https://platform.openai.com/usage' : 'https://console.anthropic.com/settings/usage';
        K.clear(usage).append(h('a', { href: link, target: '_blank', rel: 'noopener noreferrer' },
            account ? 'View Claude account usage and remaining limits' : 'View API usage and billing'),
            h('span', { class: 'muted' }, ' · Balance is managed by the provider.'));
        if (account) {
            const reported = P.accountUsageText(d.account_usage);
            usage.append(h('div', { class: 'small muted' }, reported
                ? `Claude Code last reported: ${reported}`
                : 'Claude Code usage appears here after an AI request.'));
        }
    };
    provider.onchange = () => { paintModels(d.defaults[provider.value]); key.value = ''; key.placeholder = 'API key (blank keeps any saved key)'; paintAccount(); };
    paintModels(d.model);
    paintAccount();
    const error = h('div', { class: 'errbox', hidden: true });
    const save = h('button', { type: 'button', class: 'btn primary', onclick: async () => {
        save.disabled = true;
        const r = await P.aiPost('/qobuz/ai/settings', { provider: provider.value, model: (model.value || custom.value).trim(), key: key.value.trim() });
        save.disabled = false;
        if (!r.ok) { error.hidden = false; error.textContent = r.error; return; }
        P.paintAIIcon(r);
        key.value = ''; scrim.remove(); K.toast(r.configured ? 'AI settings saved' : r.provider === 'claude_account' ? 'Settings saved; sign in to Claude Code on this server' : 'Settings saved; add an API key to use Ask AI');
    } }, 'Save');
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) scrim.remove(); } },
        h('div', { class: 'sheet qz-ai-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'AI settings' }, h('h2', {}, 'AI settings'),
            required ? h('p', {}, 'Configure an AI provider to use Ask AI: use the server’s Claude account login, or an API provider with a key. You can also change these settings in Configuration.') : null,
            h('p', { class: 'small muted' }, 'Ask AI researches reviews and finds playable Qobuz releases. Your request and album candidates are sent to the selected provider.'),
            h('label', {}, 'Provider', provider), h('label', {}, 'Model', model, custom, modelHint), keyLabel, note, usage,
            h('details', { class: 'small' },
                h('summary', {}, 'How AI search works'),
                h('p', {}, 'Web research + Qobuz catalog + AI reasoning. The selected model researches reviews using web search, then proposes Qobuz searches. It can also draw on its learned knowledge; answers are not based exclusively on web pages.'),
                h('p', {}, 'This server searches Qobuz and sends matching album metadata to the model. The model selects recordings from those candidates and explains its choices. The server checks each selected album again for playback availability and your audio-quality filter.'),
                h('p', {}, 'The AI does not listen to the recordings or analyse their audio. Sound-quality recommendations come from review evidence and reasoning; Hi-Res alone is not proof of better engineering. Results include review links when available and flag missing sources or uncertain editions.'),
                h('p', {}, 'Your query determines the number of recommendations, or the AI chooses a suitable number, up to 20. Filters you set after enabling AI still apply. Each search makes multiple AI requests; changing filters alone does not start another AI search.')),

            error, h('div', { class: 'sheet-actions' }, h('button', { type: 'button', class: 'btn', onclick: () => scrim.remove() }, 'Cancel'), save)));
    document.getElementById('overlay-root').append(scrim);
};

P.askAI = async () => {
    clearTimeout(soon);
    const prompt = P.input.value.trim();
    if (P.request || P.aiStarting) { K.toast('Wait for the current search to finish', 'error'); return; }
    const seq = ++P.searching;
    P.aiStarting = true; P.paintSearchControl();
    const settings = await K.api('/qobuz/ai/settings');
    if (seq !== P.searching) return;
    P.aiStarting = false; P.paintSearchControl(); P.paintAIIcon(settings);
    if (!settings.ok) { P.aiButton.disabled = false; K.toast(settings.error, 'error'); return; }
    if (!settings.configured) { P.aiButton.disabled = false; P.aiSettings(true); return; }
    P.aiAccountUsage = settings.provider === 'claude_account' ? settings.account_usage : null;
    if (!prompt) { P.aiButton.disabled = false; K.toast('Type a recommendation request first', 'error'); return; }
    P.input.blur(); P.showSuggestions([]);
    const params = P.params(0);
    P.previewDismissed = false;
    P.searchedKey = params.toString(); P.request = params.toString();
    setPref('q', prompt);
    P.aiRunning = true;
    P.aiTrace = [];
    P.aiUserPrompt = prompt;
    const ctl = P.searchController = new AbortController();
    P.paintSearchControl();
    P.paintWorking('Preparing AI request…');
    P.paintPreview('Preparing AI request…'); P.revealPreview();
    if (!P.dockedOnNow()) P.setView('results');
    const prepared = await P.aiPost('/qobuz/ai/recommend/prompt', { prompt }, ctl);
    if (seq !== P.searching) return;
    if (!prepared.ok) {
        P.searchController = null; P.aiRunning = false; P.request = null;
        P.fetchProgress.hidden = true; P.paintSearchControl();
        P.paintError(prepared.error); P.paintPreview(prepared.error); return;
    }
    P.aiTrace = [{ stage: 'Web research', prompt: prepared.prompt,
        prompt_source: prepared.prompt_source, system_prompt: prepared.system_prompt,
        system_prompt_source: prepared.system_prompt_source, reply: '' }];
    P.paintWorking('Waiting for AI reply…');
    P.paintPreview('Prompt sent to AI; waiting for reply…');
    const d = await P.aiPost('/qobuz/ai/recommend?' + params, { prompt }, ctl);
    if (seq !== P.searching) return;
    P.searchController = null; P.aiRunning = false;
    P.request = null; P.fetchProgress.hidden = true; P.paintSearchControl();
    if (d.trace) P.aiTrace = d.trace;
    if (d.ai?.trace) P.aiTrace = d.ai.trace;
    if (!d.ok) { P.paintError(d.error); P.paintPreview(d.error); return; }
    P.last = d; P.autoMore = false;
    P.paintSeen(); P.paintResults(); P.paintPreview(); P.paintStale(); P.openFilters(false);
};

// ── search ───────────────────────────────────────────────────────────────────
P.params = scan => {
    const q = new URLSearchParams();
    const text = P.input.value.trim();
    if (text) q.set('q', text);
    for (const name of P.selected) q.append('label', name);
    const mode = pref('date', 'any');
    if (mode === 'last') q.set('last', pref('lastN', 2));
    if (mode === 'span') {
        if (pref('from', thisYear - 5)) q.set('from', pref('from', thisYear - 5));
        if (pref('to', '')) q.set('to', pref('to', ''));
    }
    q.set('sort', pref('order', 'relevance'));
    if (pref('awarded', false)) q.set('awarded', '1');
    if (pref('hires', false)) q.set('hires', '1');
    if (pref('localOnly', false) && !pref('aiMode', false)) q.set('local', '1');
    if (scan) q.set('scan', scan);
    return q;
};

// A filter changed: search again, but only once a search has been made (the
// first one is always asked for), and not on every step of a quick change.
let soon = null;
P.searchSoon = () => {
    clearTimeout(soon);
    P.filtersDirty = true;                        // "Apply" shows until a search uses them
    P.paintStale(); P.paintSummary();
    if (P.dockedOnNow()) return;                  // down on Now: the next Search uses them
    // Changing a filter never silently starts another paid AI request.
    if (pref('aiMode', false) || P.aiRunning || P.aiStarting || (P.last && P.last.ai)) return;
    if (P.last || P.request) soon = setTimeout(() => P.search(), 450);
};

P.search = async (scan = 0, { quiet = false } = {}) => {
    clearTimeout(soon);
    P.aiTrace = [];
    P.aiUserPrompt = '';
    if (P.request || P.aiStarting) P.stopSearch();
    const params = P.params(scan);
    if (!params.has('q') && !params.has('label')) {
        K.toast('Type something or tick a label', 'error');
        return;
    }
    setPref('q', P.input.value.trim());
    if (!scan) { P.filtersDirty = false; P.paintSummary(); }
    if (!scan) { P.previewDismissed = false; P.searchedKey = params.toString(); }
    const seq = ++P.searching;
    P.request = params.toString();
    const ctl = P.searchController = new AbortController();
    P.paintSearchControl();
    P.fetchProgress.hidden = P.preview.hidden;
    if (!quiet) P.paintWorking(scan ? 'Reading further…' : 'Searching…', !!scan);
    if (!scan && !quiet) { P.paintPreview('Searching…'); P.revealPreview(); }
    if (!scan && !quiet && !P.dockedOnNow()) P.setView('results');   // searched up here: its results
    // a new search streams its partial results into the preview; "Load more" just asks
    const d = scan ? await P.fetchMore(params, ctl)
        : await P.streamSearch(params, seq);
    if (seq !== P.searching) return;
    P.request = null; P.searchController = null;
    P.paintSearchControl();
    P.fetchProgress.hidden = true;
    if (!d.ok) {
        if (d.renderer === false) P.refreshStatus();
        P.paintError(d.error || 'search failed');
        P.paintPreview(d.error || 'search failed');
        return;
    }
    // A new search may read on by itself at the end of its list; a further page
    // only if the previous one brought albums (see watchMore).
    P.autoMore = !scan || (!!P.last && d.count > P.last.count);
    P.last = d;
    P.paintSeen();
    P.paintResults();
    P.paintPreview();
    P.paintStale();
    // Results fold the filters away: the list gets the screen.
    if (!quiet) P.openFilters(false);
};

P.paintWorking = (text, more_) => {
    // A "Load more" keeps the list on screen and only turns its button into a spinner.
    const more = more_ && P.results.querySelector('.qz-more');
    if (more) { K.clear(more).append(h('div', { class: 'spinner small' }), h('span', {}, text)); return; }
    K.clear(P.results).append(h('div', { class: 'qz-working' }, h('div', { class: 'spinner' }), h('div', { class: 'muted' }, text)),
        ...(P.aiTrace?.length ? [P.aiTraceBlock()] : []));
};

P.paintError = error => {
    K.clear(P.results).append(h('div', { class: 'errbox' }, error),
        ...(P.aiTrace?.length ? [P.aiTraceBlock()] : []));
};

P.aiTraceBlock = () => {
    if (!P.aiTrace?.length) return null;
    const userPrompt = P.aiUserPrompt || P.last?.query || P.input?.value?.trim() || '';
    const reported = [...P.aiTrace].reverse().find(item => item.account_usage)?.account_usage || P.aiAccountUsage;
    const percentages = ['five_hour', 'seven_day'].map(key => reported?.windows?.[key]?.used_percent)
        .filter(value => Number.isFinite(value) && value >= 0 && value <= 100);
    const percentage = percentages.length ? Math.max(...percentages) : null;
    const level = percentage === null ? '' : percentage < 50 ? 'low' : percentage < 80 ? 'medium' : 'high';
    return h('div', { class: 'qz-ai-trace' },
        userPrompt ? h('div', { class: 'qz-ai-user-prompt' },
            h('strong', {}, 'Your request'), h('pre', {}, userPrompt)) : null,
        h('details', {}, h('summary', {}, h('span', {}, 'AI prompt and replies'),
            P.aiRunning ? h('span', { class: 'qz-ai-estimate', role: 'progressbar',
                'aria-label': 'Estimated AI search progress',
                'aria-valuetext': 'Estimated progress; repeats until the AI replies',
                title: 'Estimated progress; restarts while research continues' },
                h('span', { class: 'qz-ai-estimate-fill' })) : null,
            percentage === null ? null : h('span', { class: 'qz-ai-usage ' + level,
                title: 'Claude account usage: ' + P.accountUsageText(reported) + '. Higher limit used.',
                'aria-label': `Claude account usage ${percentage}%` }, `${percentage}%`)),
        ...P.aiTrace.flatMap(item => [
            h('h4', {}, item.stage),
            item.system_prompt ? h('details', {}, h('summary', {}, 'System prompt'),
                item.system_prompt_source ? h('div', { class: 'small muted' }, item.system_prompt_source) : null,
                h('pre', {}, item.system_prompt)) : null,
            h('div', {}, h('strong', {}, 'Sent to AI'),
                item.prompt_source ? h('div', { class: 'small muted' }, item.prompt_source) : null,
                h('pre', {}, item.prompt)),
            h('div', {}, h('strong', {}, 'AI reply'), h('pre', {}, item.reply || 'No reply received yet.')),
            item.activity ? h('div', {}, h('strong', {}, 'Claude tool activity'), h('pre', {}, item.activity)) : null,
            item.usage && Object.keys(item.usage).length ? h('p', { class: 'small muted' },
                `Request tokens: ${item.usage.input_tokens ?? '?'} input, ${item.usage.output_tokens ?? '?'} output`) : null,
            item.account_usage ? h('p', { class: 'small muted' },
                `Claude account usage: ${P.accountUsageText(item.account_usage)}`) : null
        ].filter(Boolean))));
};

P.paintResults = () => {
    const d = P.last;
    if (d.ai?.trace) P.aiTrace = d.ai.trace;
    const failed = d.queries.filter(q => q.error);
    const kids = [h('div', { class: 'qz-summary small muted' },
        `${d.count} album${d.count === 1 ? '' : 's'}`,
        d.window.from || d.window.to ? ` · ${d.window.from ? d.window.from.slice(0, 4) : '…'}–${d.window.to ? d.window.to.slice(0, 4) : 'today'}` : '',
        ` · ${d.considered} looked at`,
        d.sort === 'ai' ? ' · AI recommendations' : d.sort === 'date' ? ' · newest first' : '',
        d.unstreamable ? ` · ${d.unstreamable} not available` : '',
        P.lowCount(d) ? ` · ${P.lowCount(d)} lowered` : '')];
    if (d.local_error) kids.push(h('div', { class: 'errbox warn small' }, `Local collection unavailable: ${d.local_error}`));
    if (d.qobuz_error) kids.push(h('div', { class: 'errbox warn small' }, `Qobuz results unavailable: ${d.qobuz_error}`));
    if (d.ai) {
        if (d.ai.model_notice) kids.push(h('p', { class: 'small qz-ai-model-notice' }, d.ai.model_notice));
        if (d.ai.summary) kids.push(h('p', { class: 'small qz-ai-summary' }, d.ai.summary));
        if (d.count < d.ai.requested) kids.push(h('p', { class: 'small muted' }, `Found ${d.count} verified releases of ${d.ai.requested} requested.`));
    }
    failed.forEach(q => kids.push(h('div', { class: 'errbox warn small' }, `“${q.query}”: stopped after ${q.fetched} albums: ${q.error}`)));
    if (!d.results.length) kids.push(h('p', { class: 'muted' }, d.more
        ? 'Nothing matches yet among the albums read so far.'
        : 'Nothing matches.'));
    const shown = d.results.filter(c => !c.lowered), low = d.results.filter(c => c.lowered);
    kids.push(P.list(shown));
    if (low.length) kids.push(h('details', { class: 'qz-lowgroup', open: P.lowOpen || null,
        ontoggle: e => { P.lowOpen = e.target.open; } },
        h('summary', { class: 'small muted' }, `${low.length} lowered result${low.length === 1 ? '' : 's'}`),
        P.list(low)));
    if (d.more) {
        const automatic = P.autoMore && d.results.length && 'IntersectionObserver' in window;
        const more = automatic
            ? h('div', { class: 'qz-more qz-page-end', 'aria-live': 'polite' })
            : h('button', { type: 'button', class: 'btn qz-more', onclick: () => P.search(d.next_scan) }, 'Load more');
        kids.push(more);
        P.watchMore(more, d);
    }
    if (d.ai && P.aiTrace?.length) kids.push(P.aiTraceBlock());
    K.clear(P.results).append(...kids);
    setPref('lastResults', { d, key: P.searchedKey });   // kept on this device (see mount)
};

// The search as server-sent events: partial results (already in their final order)
// repaint the preview while Qobuz is being read; the promise gets the whole answer.
// A newer search ends the older one's stream.
P.fetchMore = async (params, ctl) => {
    const timer = setTimeout(() => ctl.abort(), 120000);
    try { return await (await fetch('/qobuz/search?' + params, { signal: ctl.signal })).json(); }
    catch (e) { return { ok: false, error: e.name === 'AbortError' ? 'Search stopped or timed out' : 'Could not reach search' }; }
    finally { clearTimeout(timer); }
};
P.streamSearch = (params, seq) => new Promise(resolve => {
    if (P.streamEnd) P.streamEnd({ ok: false, error: 'replaced' });
    const es = new EventSource('/qobuz/search/stream?' + params);
    const end = d => {
        if (P.streamEnd !== end) return;
        P.streamEnd = null;
        clearTimeout(timer);
        es.close();
        resolve(d);
    };
    P.streamEnd = end;
    const timer = setTimeout(() => end({ ok: false, error: 'the search took too long' }), 120000);
    es.onmessage = ev => {
        let d;
        try { d = JSON.parse(ev.data); } catch { return; }
        if (d.ok && d.partial) { if (seq === P.searching) P.paintPreview(null, d); return; }
        end(d);
    };
    es.onerror = () => end({ ok: false, error: 'the search was cut off' });
});

// The preview on Now: one line of text per album, the lowered ones last and dimmed;
// or, with text given, just that (searching, an error).  `d` is a partial answer
// while the search runs (see streamSearch), else the last one.
P.paintPreview = (text, d = P.last) => {
    if (P.previewDismissed) return;
    const partial = !!(d && d.partial);
    P.form.classList.toggle('qz-has-res', !text && !partial && !!d);
    P.updatePreviewAction();
    P.preview.hidden = false;
    P.fetchProgress.hidden = !P.request;
    if (text) {
        K.clear(P.preview).append(h('div', { class: 'qz-pv-line muted' }, text),
            ...(P.aiTrace?.length ? [P.aiTraceBlock()] : []));
        return;
    }
    if (!d) { P.preview.hidden = true; P.fetchProgress.hidden = true; return; }
    // the artist only if the search was not for them ("rolling stones" leaves out
    // "The Rolling Stones"); then the year and the label
    const asked = P.plain(P.input.value);
    const named = a => { const x = P.plain(a); return !!x && !!asked && (asked.includes(x) || (asked.length > 2 && x.includes(asked))); };
    const line = c => h('div', { class: 'qz-pv-line' + (c.lowered ? ' low' : '') },
        [((c.awards && c.awards.length) || c.rating ? '🏆 ' : '') + c.title + (c.version ? ` (${c.version})` : ''), named(c.artist) ? '' : c.artist, c.year, c.label]
            .filter(Boolean).join(' · '));
    const n = `${d.count} album${d.count === 1 ? '' : 's'}`;
    const head = partial ? `Searching… ${n} so far (${d.considered} looked at)` : n + (d.more ? ' so far' : '');
    const top = P.preview.scrollTop;
    K.clear(P.preview).append(h('div', { class: 'qz-pv-line muted' }, head), ...d.results.map(line));
    P.preview.scrollTop = partial ? top : 0;     // the live list stays where it is being read
};

// Down on Now the preview has a height of its own (kiosk.css); the page scrolls so it
// and the box are in view.  Again once the keyboard has gone: that resizes the page.
P.revealPreview = () => {
    if (!P.dockedOnNow()) return;
    [0, 450].forEach(ms => setTimeout(() => P.form.scrollIntoView({ block: 'end', inline: 'nearest' }), ms));
};

// Down on Now, once there are results, › takes Search's place; Search comes back when
// the text or a filter no longer matches the search those results answer.
P.paintStale = () => {
    if (P.form) P.form.classList.toggle('qz-stale', !P.last || P.params(0).toString() !== P.searchedKey);
    if (P.preview) P.updatePreviewAction();
};

// A name or a search text, compared loosely: no case, accents, punctuation or leading "the".
P.plain = t => fold(String(t || '')).replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/^the /, '');

// Reaching the end of the list reads on by itself, as scrolling does in Qobuz's
// app -- but only while that keeps bringing albums: a filter that matches
// nothing further down waits for a tap instead of reading thousands.
P.watchMore = (button, d) => {
    if (P.moreObserver) P.moreObserver.disconnect();
    if (!P.autoMore || !d.results.length || !('IntersectionObserver' in window)) return;
    P.moreObserver = new IntersectionObserver(entries => {
        if (!entries.some(e => e.isIntersecting) || P.request || P.last !== d || !P.visible || P.view() !== 'results') return;
        P.moreObserver.disconnect();
        P.search(d.next_scan);
    }, { root: P.el, rootMargin: '0px 0px 200px 0px' });
    P.moreObserver.observe(button);
};

// The albums played from here, newest first (the "Played recently" view).
P.recent = async () => {
    const d = await K.api('/qobuz/played?limit=30');
    if (!d.ok) return;
    K.clear(P.recentBox).append(d.albums.length ? P.list(d.albums, 'recent')
        : h('p', { class: 'muted' }, 'Nothing played from here yet.'));
};

P.loadGenres = async () => {
    const d = await K.api('/qobuz/genres');
    if (!d.ok) { K.toast(d.error || 'Could not load genres', 'error'); return; }
    P.genres = [{ id: '', name: 'All genres' }, ...d.genres.map(g => ({ id: String(g.id), name: g.name }))];
    P.paintGenre();
};

P.paintGenre = () => {
    const g = P.genres.find(x => x.id === String(pref('discoverGenre', '')));
    P.genreBtn.textContent = `${g ? g.name : 'All genres'} ▾`;
};

// The genre menu: the same look as the Now page's View menu (.menu-pop), one genre marked.
P.closeGenreMenu = () => {
    if (!P.genreMenu) return;
    document.removeEventListener('pointerdown', P.genreOutside, true);
    P.genreMenu.remove();
    P.genreMenu = null;
};
P.openGenreMenu = () => {
    if (P.genreMenu) { P.closeGenreMenu(); return; }
    const cur = String(pref('discoverGenre', ''));
    const menu = P.genreMenu = h('div', { class: 'menu-pop menu-scroll', role: 'menu' },
        ...P.genres.map(g => h('button', {
            type: 'button', class: 'menu-item', role: 'menuitemradio', 'aria-checked': String(g.id === cur),
            onclick: () => { P.closeGenreMenu(); setPref('discoverGenre', g.id); P.paintGenre(); P.discover(); },
        }, h('span', { class: 'mk' }, g.id === cur ? '●' : ''), g.name)));
    document.body.append(menu);
    const r = P.genreBtn.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(r.left, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${r.bottom + 4}px`;
    menu.style.maxHeight = `${Math.max(120, innerHeight - r.bottom - 16)}px`;
    P.genreOutside = e => { if (!menu.contains(e.target) && e.target !== P.genreBtn) P.closeGenreMenu(); };
    document.addEventListener('pointerdown', P.genreOutside, true);
};

P.discoverSelected = () => new Set(pref('discoverLabels', []));
P.discoverSummary = () => [
    ...P.discoverSelected(),
    ...(pref('discoverAwarded', false) ? ['awarded'] : []),
    ...(pref('discoverHires', false) ? ['Hi-Res'] : []),
].join(' · ');
P.openDiscoverFilters = open => {
    P.discoverFiltersOpen = open;
    P.discoverFilters.hidden = !open;
    P.discoverFilterBtn.setAttribute('aria-expanded', String(open));
    P.paintDiscoverFilters();
};
P.paintDiscoverFilters = () => {
    const selected = P.discoverSelected();
    const names = P.labelNames(selected);
    const top = P.discoverLabelsScroll.scrollTop;
    K.clear(P.discoverLabelsScroll).append(...names.map(name => h('button', {
        type: 'button', class: 'chip tog qz-chip' + (selected.has(name) ? ' on' : ''),
        'aria-pressed': String(selected.has(name)),
        onclick: () => {
            selected.has(name) ? selected.delete(name) : selected.add(name);
            setPref('discoverLabels', [...selected]); P.paintDiscoverFilters(); P.discover();
        },
    }, name)));
    P.discoverLabelsScroll.scrollTop = top;
    K.clear(P.discoverLabels).append(P.discoverLabelsScroll,
        h('button', { type: 'button', class: 'chip qz-chip qz-add-label-button',
        'aria-label': 'Add Discover label', title: 'Add label', onclick: () => P.addLabel('discover') }, '+'));
    for (const [box, key, label, title] of [
        [P.discoverAwarded, 'discoverAwarded', 'Awarded only', 'Albums with an award or your rating already found by this app'],
        [P.discoverQuality, 'discoverHires', 'Hi-Res', 'Exclude 16-bit/44.1 kHz releases'],
    ]) {
        const on = pref(key, false);
        K.clear(box).append(h('button', { type: 'button', class: 'chip tog qz-chip' + (on ? ' on' : ''),
            'aria-pressed': String(on), title,
            onclick: () => { setPref(key, !on); P.paintDiscoverFilters(); P.discover(); },
        }, label));
    }
    const summary = P.discoverSummary();
    P.discoverFilterBtn.textContent = 'Filters ' + (P.discoverFiltersOpen ? '▴' : '▾');
    P.discoverFilterBtn.classList.toggle('on', !!summary);
    P.discoverFilterSummary.textContent = summary;
    P.discoverFilterSummary.title = summary;
};

P.discover = async (offset = 0) => {
    if (P.discoverObserver) P.discoverObserver.disconnect();
    const seq = P.discoverSeq = (P.discoverSeq || 0) + 1;
    const genre = pref('discoverGenre', '');
    if (!offset) K.clear(P.discoverList).append(h('p', { class: 'muted' }, 'Loading new releases…'));
    const params = new URLSearchParams({ genre, offset });
    for (const label of P.discoverSelected()) params.append('label', label);
    if (pref('discoverAwarded', false)) params.set('awarded', '1');
    if (pref('discoverHires', false)) params.set('hires', '1');
    const d = await K.api('/qobuz/discover?' + params);
    if (seq !== P.discoverSeq) return;
    if (!d.ok) {
        K.clear(P.discoverList).append(h('div', { class: 'errbox' }, d.error || 'Could not load new releases',
            h('button', { type: 'button', class: 'btn', onclick: () => P.discover() }, 'Retry')));
        return;
    }
    if (!offset) {
        P.discoverRows = P.list([]);
        K.clear(P.discoverList).append(h('div', { class: 'qz-discover-heading' },
            h('span', { class: 'small muted' }, 'New releases'), P.discoverFilterSummary), P.discoverRows);
    }
    if (P.discoverMore) P.discoverMore.remove();
    P.discoverRows.append(...d.albums.map(c => P.item(c)));
    if (!P.discoverRows.firstChild && !d.more) P.discoverRows.append(h('p', { class: 'muted' }, 'No matching new releases.'));
    if (d.more) {
        let loading = false;
        const loadMore = () => {
            if (seq !== P.discoverSeq || loading) return;
            loading = true;
            P.discoverMore.textContent = 'Loading new releases…';
            P.discover(d.next_offset);
        };
        const automatic = d.albums.length && 'IntersectionObserver' in window;
        P.discoverMore = automatic
            ? h('div', { class: 'qz-page-end', 'aria-live': 'polite' })
            : h('button', { type: 'button', class: 'btn', onclick: loadMore }, 'More releases');
        P.discoverList.append(P.discoverMore);
        // As in Results, reaching the end while swiping up reads the next page.
        // An empty page stops automatic reading; the button can still continue.
        if (d.albums.length && P.visible && P.view() === 'discover' && 'IntersectionObserver' in window) {
            P.discoverObserver = new IntersectionObserver(entries => {
                if (P.visible && P.view() === 'discover' && entries.some(e => e.isIntersecting)) loadMore();
            }, { root: P.el, rootMargin: '0px 0px 200px 0px' });
            P.discoverObserver.observe(P.discoverMore);
        }
    }
};

// Every album met with an award or marked awarded, most recent first: to look at
// together (the "Awarded" view).
P.awardedList = async () => {
    const d = await K.api('/qobuz/awarded');
    if (!d.ok) { K.clear(P.awardedBox).append(h('div', { class: 'errbox' }, d.error || 'the list cannot be read')); return; }
    K.clear(P.awardedBox).append(d.albums.length
        ? h('div', {}, h('div', { class: 'qz-summary small muted' }, `${d.albums.length} album${d.albums.length === 1 ? '' : 's'} with an award, met in searches or marked by you`),
            P.list(d.albums))
        : h('p', { class: 'muted' }, 'None yet: albums with an award are listed here as searches meet them, and so are the ones you mark awarded (Album details).'));
};

// Out of "Played recently" only: still counted as played, not lowered in searches.
P.hideRecent = async (c, row) => {
    row.classList.add('going');
    const d = await K.api('/qobuz/played', { json: { action: 'hide', album_id: c.id } });
    if (!d.ok) { row.classList.remove('going'); K.toast(d.error || 'could not hide it', 'error'); return; }
    row.remove();
    P.snack(`Hidden from Recent: “${c.title}”`, [{ label: 'Undo', run: async () => {
        const u = await K.api('/qobuz/played', { json: { action: 'show', album_id: c.id } });
        if (!u.ok) K.toast(u.error || 'could not put it back', 'error');
        P.recent();
    } }]);
};

// ── completion ───────────────────────────────────────────────────────────────
// The whole list comes once (a few tens of KB) and is matched here as the user
// types: the last few words typed are compared with the start of each entry and
// of each of its words, ignoring case and accents.  Learned entries (played
// after a search) come first, most played first.
const FOLD1 = { 'ø': 'o', 'ł': 'l', 'đ': 'd', 'ı': 'i', 'ð': 'd', 'þ': 't' };
// Strips accents but keeps the length, so a match can be shown in the original.
const fold = t => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[øłđıðþ]/g, c => FOLD1[c]);
const entry = (text, learned = 0) => {
    const nfc = text.normalize('NFC');
    const f = fold(nfc);
    const starts = [0];
    for (let i = 1; i < f.length; i++) if (/[\s\-'’.(/]/.test(f[i - 1]) && !/[\s\-'’.(/]/.test(f[i])) starts.push(i);
    return { text: nfc, f, starts, learned };
};
P.words = [];
P.learned = [];
P.artists = [];          // [{ e: entry, term, labels }]: the shipped artists and their labels
P.artistPairs = [];      // [[artist, label]]: learned from plays, a ring the server keeps

P.loadWords = async () => {
    const d = await K.api('/qobuz/words', { timeout: 30000 });
    if (!d.ok) return;
    P.words = d.words.map(w => ({ ...entry(w), term: P.plain(w) }));
    P.learned = d.learned.map(w => entry(w.text, w.count));
    P.artists = (d.artists || []).map(([a, labels]) => ({ ...entry(a), term: P.plain(a), labels }));
    P.artistPairs = d.artist_pairs || [];
    P.syncLabels();
};

// The server's ring, kept alike here: newest first, the oldest forgotten.
P.learnPair = (artist, label) => {
    P.artistPairs = [[artist, label], ...P.artistPairs.filter(([a, l]) => !(P.plain(a) === P.plain(artist) && P.plain(l) === P.plain(label)))]
        .slice(0, 200);
    P.syncLabels();
};

P.learn = texts => {
    for (const t of texts.map(x => String(x || '').trim()).filter(x => x.length > 1).reverse()) {
        const e = entry(t);
        const old = P.learned.find(x => x.f === e.f);
        e.learned = (old ? old.learned : 0) + 1;
        P.learned = [e, ...P.learned.filter(x => x !== old)];
    }
};

// [{entry, from, to, at}]: `entry` would replace input[from:to]; the match is
// at entry.f[at .. at + to - from].
P.suggestions = (value, caret) => {
    const before = value.slice(0, caret);
    if (!before.trim()) return P.learned.slice(0, 8).map(e => ({ e, from: 0, to: caret, at: -1 }));
    if (/\s$/.test(before)) return [];
    const tokens = [...before.matchAll(/\S+/g)];
    const best = new Map();
    for (let k = Math.min(4, tokens.length); k >= 1; k--) {
        const from = tokens[tokens.length - k].index;
        const frag = fold(before.slice(from)).replace(/\s+/g, ' ');
        if (frag.length < 2) continue;
        const consider = (e, source) => {
            if (best.has(e.f) || e.f === frag) return;
            const at = e.starts.find(i => e.f.startsWith(frag, i));
            if (at === undefined) return;
            best.set(e.f, { e, from, to: caret, at, k, source });
        };
        P.learned.forEach(e => consider(e, 0));
        P.words.forEach(e => consider(e, 1));
        P.artists.forEach(a => consider(a, 1));
    }
    return [...best.values()].sort((a, b) =>
        a.source - b.source || (b.e.learned - a.e.learned) || (a.at > 0) - (b.at > 0)
        || b.k - a.k || a.e.text.length - b.e.text.length).slice(0, 8);
};

let suggestTimer = null;
P.suggestSoon = () => {
    if (pref('aiMode', false)) { P.showSuggestions([]); return; }
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => {
        if (document.activeElement !== P.input) return;
        P.showSuggestions(P.suggestions(P.input.value, P.input.selectionStart ?? P.input.value.length));
    }, 60);
};

P.showSuggestions = list => {
    P.shown = list;
    P.active = -1;
    K.clear(P.suggestBox);
    P.suggestBox.classList.toggle('on', list.length > 0);
    list.forEach((s, n) => {
        const t = s.e.text, len = s.to - s.from;
        const label = s.at < 0 ? [t]
            : [t.slice(0, s.at), h('b', {}, t.slice(s.at, s.at + len)), t.slice(s.at + len)];
        P.suggestBox.append(h('button', {
            type: 'button', class: 'qz-sug' + (s.e.learned ? ' learned' : ''), role: 'option',
            // pointerdown, not click: the field must keep the focus (and the keyboard)
            onpointerdown: e => { e.preventDefault(); P.pick(n); },
        }, h('span', { class: 'qz-sug-text' }, label), s.e.learned ? h('span', { class: 'qz-sug-mark', title: 'played before' }, '↺') : null));
    });
};

P.pick = n => {
    const s = P.shown && P.shown[n];
    if (!s) return;
    const v = P.input.value;
    const head = v.slice(0, s.from) + s.e.text + ' ';
    P.input.value = head + v.slice(s.to).replace(/^\s+/, '');
    P.syncLabels();
    P.paintStale();
    P.input.setSelectionRange(head.length, head.length);
    P.input.focus();
    P.showSuggestions([]);
};

P.suggestKey = e => {
    if (pref('aiMode', false)) return;
    const n = (P.shown || []).length;
    if (n && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        const down = e.key === 'ArrowDown';
        P.active = P.active < 0 ? (down ? 0 : n - 1) : (P.active + (down ? 1 : n - 1)) % n;
        [...P.suggestBox.children].forEach((b, i) => b.classList.toggle('on', i === P.active));
    } else if (e.key === 'Tab' && n) {
        e.preventDefault();
        P.pick(Math.max(0, P.active));
    } else if (e.key === 'Escape') {
        P.showSuggestions([]);
    } else if (e.key === 'Enter' && n && P.active >= 0) {
        e.preventDefault();                       // a completion, not a search
        P.pick(P.active);
    }                                             // else Enter submits the form: P.go()
};

// ── lowering ─────────────────────────────────────────────────────────────────
P.lowCount = d => d.results.filter(c => c.lowered).length;

P.syncAILowered = entries => {
    if (!P.last || !P.last.ai) return;
    const normal = text => fold(String(text || '')).trim().replace(/\s+/g, ' ');
    for (const c of P.last.results) {
        const people = [c.artist, ...(c.performers || []).map(p => p.name)].map(normal);
        const reason = entries.find(e => e.kind === 'album' ? e.key === c.id
            : e.kind === 'artist' ? people.includes(normal(e.key))
            : new RegExp('(^|[^\\p{L}\\p{N}_])' + normal(e.key).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '($|[^\\p{L}\\p{N}_])', 'u').test(normal(c.label)));
        if (reason) c.lowered = reason; else delete c.lowered;
    }
    P.last.results.sort((a, b) => Number(!!a.lowered) - Number(!!b.lowered) || a.ai.rank - b.ai.rank);
};
P.lowerApi = async body => {
    const d = await K.api('/qobuz/lowered', { json: body });
    if (d.ok) P.syncAILowered(d.entries);
    return d;
};

// − : the album at once (the row goes, and joins the folded group at the end),
// then a bar to undo it or to lower its whole label or artist instead.
P.lower = async (c, row) => {
    row.classList.add('going');
    const d = await P.lowerApi({ action: 'add', kind: 'album', key: c.id, name: c.title });
    if (!d.ok) { row.classList.remove('going'); K.toast(d.error || 'could not lower it', 'error'); return; }
    c.lowered = { kind: 'album', key: c.id, name: c.title };
    P.lowCountCache = d.entries.length;
    const inResults = P.last && P.last.results.includes(c);
    if (inResults) {
        // to the end, in the order the server would give it
        P.last.results = [...P.last.results.filter(x => x !== c && !x.lowered), c, ...P.last.results.filter(x => x !== c && x.lowered)];
        P.paintResults();
    } else row.remove();
    P.paintLowLink();
    P.snack(`Lowered “${c.title}”`, [
        { label: 'Undo', run: () => P.restore(c.lowered) },
        c.label ? { label: `All of ${c.label}`, run: () => P.lowerMore('label', c.label, c) } : null,
        c.artist ? { label: `All by ${c.artist}`, run: () => P.lowerMore('artist', c.artist, c) } : null,
    ]);
};

// A whole label or artist: the album entry is replaced by the wider one.
P.lowerMore = async (kind, name, c) => {
    await P.lowerApi({ action: 'remove', kind: 'album', key: c.id });
    const d = await P.lowerApi({ action: 'add', kind, key: name, name });
    if (!d.ok) { K.toast(d.error || 'could not lower it', 'error'); return; }
    K.toast(`Lowered all ${kind === 'label' ? 'of' : 'by'} ${name}`);
    P.lowCountCache = d.entries.length;
    P.paintLowLink();
    P.refreshResults();
};

P.restore = async entry => {
    const d = await P.lowerApi({ action: 'remove', kind: entry.kind, key: entry.key });
    if (!d.ok) { K.toast(d.error || 'could not restore it', 'error'); return; }
    P.lowCountCache = d.entries.length;
    P.paintLowLink();
    P.refreshResults();
};

// Ask again, as deep as the list on screen: the pages come from the server's cache.
P.refreshResults = async () => {
    if (!P.last || P.request) return;
    if (P.last.ai) {
        const last = P.last, d = await K.api('/qobuz/lowered');
        if (P.last !== last) return;
        if (d.ok) P.syncAILowered(d.entries);
        P.paintResults(); return;
    }
    P.search(P.last.scan > 50 ? P.last.scan : 0, { quiet: true });
};

// A bar above the player strip: a message and a few actions, gone after 10 s.
P.snack = (text, actions) => {
    clearTimeout(P.snackTimer);
    if (P.snackEl) P.snackEl.remove();
    const close = () => { clearTimeout(P.snackTimer); if (P.snackEl === bar) P.snackEl = null; bar.remove(); };
    const bar = h('div', { class: 'qz-snack' },
        h('div', { class: 'qz-snack-text' }, text),
        h('div', { class: 'qz-snack-acts' }, actions.filter(Boolean).map(a =>
            h('button', { type: 'button', class: 'btn', onclick: () => { close(); a.run(); } }, a.label)),
            h('button', { type: 'button', class: 'btn qz-snack-x', title: 'Close', onclick: close }, '✕')));
    P.snackEl = bar;
    P.player.before(bar);
    P.snackTimer = setTimeout(close, 10000);
};

// The Lowered list: every entry with its kind, restore one, or clear them all.
P.paintLowLink = () => {
    if (!P.lowLink) return;
    const n = P.lowCountCache;
    P.lowLink.textContent = n ? `Lowered list (${n}) ›` : 'Lowered list ›';
};

P.openLowered = async () => {
    const d = await K.api('/qobuz/lowered');
    if (!d.ok) { K.toast(d.error || 'could not read the lowered list', 'error'); return; }
    const KIND = { album: 'Album', label: 'Label', artist: 'Artist' };
    const list = h('div', { class: 'qz-lowlist' });
    const close = () => scrim.remove();
    const paint = entries => {
        P.lowCountCache = entries.length;
        P.paintLowLink();
        K.clear(list).append(...(entries.length ? entries.map(e => h('div', { class: 'qz-lowrow' },
            h('span', { class: 'chip dim' }, KIND[e.kind] || e.kind),
            h('span', { class: 'qz-lowname' }, e.name),
            h('button', { type: 'button', class: 'btn', onclick: async () => {
                const r = await P.lowerApi({ action: 'remove', kind: e.kind, key: e.key });
                if (r.ok) { paint(r.entries); P.changedLow = true; }
            } }, '↺ Restore'))) : [h('p', { class: 'muted' }, 'Nothing is lowered.')]));
    };
    P.changedLow = false;
    const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) done(); } },
        h('div', { class: 'sheet qz-lowsheet' },
            h('h2', {}, 'Lowered'),
            h('p', {}, 'These come last in every result list, folded away. Restore one to see it in its place again.'),
            list,
            h('div', { class: 'sheet-actions' },
                h('button', { type: 'button', class: 'btn danger-soft', onclick: async () => {
                    if (!P.lowCountCache) return;
                    const yes = await K.confirm({ title: 'Clear the lowered list?', message: 'Every album, label and artist in it gets its place back.', ok: 'Clear', danger: true });
                    if (!yes) return;
                    const r = await P.lowerApi({ action: 'clear' });
                    if (r.ok) { paint(r.entries); P.changedLow = true; }
                } }, 'Clear all'),
                h('span', { class: 'spacer' }),
                h('button', { type: 'button', class: 'btn primary', onclick: () => done() }, 'Done'))));
    function done() { close(); if (P.changedLow) P.refreshResults(); }
    paint(d.entries);
    document.getElementById('overlay-root').append(scrim);
};

// ── one result ───────────────────────────────────────────────────────────────
const quality = c => c.bits && c.bits > 16 ? `${c.bits}/${+(+c.rate).toFixed(1)}` : '';
const who = p => p.roles && p.roles.length ? `${p.name} (${p.roles[0]})` : p.name;

P.row = (c, where = '') => {
    const tracks = h('div', { class: 'qz-tracks' });
    const body = h('div', { class: 'qz-body tap', onclick: () => P.toggleTracks(c, row, tracks) },
        h('div', { class: 'qz-title' }, c.title, c.version ? h('span', { class: 'muted' }, ` (${c.version})`) : null),
        h('div', { class: 'qz-artist' }, c.artist),
        // one badge: awards and my rating, a tap shows them all (widgets/albuminfo.js)
        K.awardsBox(c, c.awards ? { awards: c.awards, rating: c.rating || 0 } : null, { compact: true }),
        c.performers && c.performers.length
            ? h('div', { class: 'qz-perf small muted' }, c.performers.slice(0, 4).map(who).join(', ')) : null,
        h('div', { class: 'qz-meta small' },
            h('span', {}, c.label || '—'), h('span', { class: 'muted' }, c.year || ''),
            quality(c) ? h('span', { class: 'chip ok' }, quality(c)) : null,
            // the DR log's figure: exact, or ≈ from the tracks heard (widgets/dr.js)
            K.drLogBadge ? K.drLogBadge(c.dr_log) : null,
            c.played ? h('span', { class: 'chip dim' }, `played ${c.played}×`) : null));
    const off = c.streamable === false;
    const local = c.source === 'local';
    if (c.ai) body.append(h('div', { class: 'qz-ai-reason small', onclick: e => e.stopPropagation() },
        h('p', {}, c.ai.reason),
        c.ai.uncertain ? h('p', { class: 'muted' }, 'Limited evidence: no supporting review linked.') : null,
        h('div', { class: 'qz-ai-sources' }, c.ai.sources.map(s => h('a', {
            href: s.url, target: '_blank', rel: 'noopener noreferrer',
            onclick: e => { e.preventDefault(); K.openExternal(s.url); },
        }, s.title)))));
    // play stays on the row; add, save and remove live in the ⋯ menu (P.tileMenu)
    const moreBtn = h('button', { type: 'button', class: 'btn qz-more-menu', title: 'More: add to the queue, save, remove',
        'aria-label': 'More actions', 'aria-haspopup': 'menu',
        onclick: () => P.tileMenuEl && P.tileMenuTile === row ? P.closeTileMenu() : P.tileMenu(c, where, row, moreBtn) }, '⋯');
    const row = h('div', { class: 'qz-row' + (off ? ' off' : '') + (c.lowered ? ' lowered' : '') },
        h('div', { class: 'qz-cover-wrap' }, c.image ? h('img', { class: 'qz-cover', src: c.image, alt: '', loading: 'lazy' }) : h('div', { class: 'qz-cover' }), local ? h('span', { class: 'qz-source', title: 'Local collection', 'aria-label': 'Local collection' }, '⌂') : null),
        body,
        h('div', { class: 'qz-act' },
            h('button', { type: 'button', class: 'btn primary qz-play', disabled: off, title: 'Replace the queue and play', onclick: () => P.play(c, 'replace') }, K.tIcon('play')),
            moreBtn),
        tracks);
    if (P.open.has(c.id)) P.toggleTracks(c, row, tracks, true);
    row.__card = c; row.__where = where;
    return row;
};

// One album in the grid.  A tap opens its details, with ▶ and + there; a long press
// drops a menu under the cover with the list row's three buttons (P.tileMenu).
const LONG_PRESS_MS = 550;
P.tile = (c, where = '') => {
    const off = c.streamable === false;
    const sub = [c.year, c.label].filter(Boolean).join(' · ');
    const tile = h('div', {
        class: 'qz-tile' + (off ? ' off' : '') + (c.lowered ? ' lowered' : ''), role: 'button', tabindex: 0,
        'aria-haspopup': 'menu', title: [c.title, c.artist].filter(Boolean).join(' — ') + ' (long press: play, add, remove)',
        onclick: () => { if (!held) P.details(c); },
        onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); P.details(c); } },
    },
        h('div', { class: 'qz-tcover' },
            c.image ? h('img', { src: c.image_large || c.image, alt: '', loading: 'lazy', draggable: 'false',
                ...(c.image_large ? { srcset: `${c.image} 230w, ${c.image_large} 600w`, sizes: '9rem' } : {}) }) : null,
            (c.awards && c.awards.length) || c.rating ? h('span', { class: 'qz-taward', title: 'Awarded' }, '🏆') : null,
            c.source === 'local' ? h('span', { class: 'qz-taward qz-local-source', title: 'Local collection', 'aria-label': 'Local collection' }, '⌂') : null,
            quality(c) ? h('span', { class: 'qz-tq' }, quality(c)) : null,
            K.drLogBadge ? K.drLogBadge(c.dr_log, 'qz-tdr') : null),
        h('div', { class: 'qz-ttl' }, c.title, c.version ? h('span', { class: 'muted' }, ` (${c.version})`) : null),
        h('div', { class: 'qz-tsub muted' }, c.artist || ''),
        sub ? h('div', { class: 'qz-tsub muted' }, sub) : null);
    // the press: held still for LONG_PRESS_MS; a move (a scroll, a page swipe) ends it
    let timer = null, start = null, held = false;
    const cancel = () => { clearTimeout(timer); timer = null; tile.classList.remove('pressing'); };
    const fire = () => {
        cancel();
        if (held) return;
        held = true;
        try { navigator.vibrate && navigator.vibrate(25); } catch {}
        P.tileMenu(c, where, tile);
    };
    tile.addEventListener('pointerdown', e => {
        if (e.button) return;
        held = false;
        start = { x: e.clientX, y: e.clientY };
        tile.classList.add('pressing');
        timer = setTimeout(fire, LONG_PRESS_MS);
    });
    tile.addEventListener('pointermove', e => {
        if (timer && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) cancel();
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(t => tile.addEventListener(t, cancel));
    // Android's own long press (and a right click) asks for a context menu: it is ours
    tile.addEventListener('contextmenu', e => { e.preventDefault(); if (timer || e.pointerType === 'mouse' || !e.pointerType) fire(); });
    tile.__card = c; tile.__where = where;
    return tile;
};

// The long press's menu, dropped under the cover (over it when there is no room
// below): ▶ replaces the queue, + adds to it, and − is the list's −: out of Recent,
// else lowered (the bar after it can undo).  A lowered album gets ↺ there instead.
P.tileMenu = (c, where, tile, anchor = null) => {
    P.closeTileMenu();
    const off = c.streamable === false;
    const item = (icon, label, run, disabled = false) => h('button', {
        type: 'button', class: 'menu-item', role: 'menuitem', disabled,
        onclick: () => { P.closeTileMenu(); run(); },
    }, h('span', { class: 'mk' }, icon), label);
    const menu = P.tileMenuEl = h('div', { class: 'menu-pop qz-tilemenu', role: 'menu', 'aria-label': c.title },
        h('div', { class: 'qz-tilemenu-head' }, c.title),
        item(K.tIcon('play'), off ? 'Not available on Qobuz' : 'Play (replace the queue)', () => P.play(c, 'replace'), off),
        item('+', 'Add to the queue', () => P.play(c, 'append'), off),
        c.source === 'local' ? null : item('♡', 'Save in Qobuz Library', () => K.saveQobuzFavorite(c)),
        c.source === 'local' ? null : where === 'recent' ? item('−', 'Remove from Recent', () => P.hideRecent(c, tile))
        : c.lowered ? item('↺', `Restore (lowered ${c.lowered.kind}: ${c.lowered.name})`, () => P.restore(c.lowered))
        : item('−', 'Remove (lower it)', () => P.lower(c, tile)));
    document.body.append(menu);
    tile.classList.add('menu-open');
    P.tileMenuTile = tile;
    const r = (anchor || tile.querySelector('.qz-tcover')).getBoundingClientRect();
    const w = menu.offsetWidth, hgt = menu.offsetHeight;
    // from a list row's ⋯ the menu hangs from the button's right edge, above it when low
    const x = anchor ? r.right - w : r.left;
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - w - 8))}px`;
    menu.style.top = `${r.bottom + 4 + hgt <= innerHeight - 8 ? r.bottom + 4
        : anchor ? Math.max(8, r.top - 4 - hgt) : Math.max(8, r.top + 8)}px`;
    // the finger that held lifts after this: only a new touch elsewhere closes it
    P.tileMenuOutside = e => { if (!menu.contains(e.target) && !(anchor && anchor.contains(e.target))) P.closeTileMenu(); };
    P.tileMenuKey = e => { if (e.key === 'Escape') P.closeTileMenu(); };
    document.addEventListener('pointerdown', P.tileMenuOutside, true);
    document.addEventListener('keydown', P.tileMenuKey);
    P.el.addEventListener('scroll', P.closeTileMenu, { passive: true, once: true });
};
P.closeTileMenu = () => {
    if (!P.tileMenuEl) return;
    document.removeEventListener('pointerdown', P.tileMenuOutside, true);
    document.removeEventListener('keydown', P.tileMenuKey);
    P.el.removeEventListener('scroll', P.closeTileMenu);
    P.tileMenuEl.remove();
    P.tileMenuEl = null;
    if (P.tileMenuTile) P.tileMenuTile.classList.remove('menu-open');
    P.tileMenuTile = null;
};

// The details page from the grid: with ▶ and + for this album.
P.details = c => {
    if (c.source === 'local') {
        const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) scrim.remove(); } },
            h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': `${c.title} — Local collection` },
                h('h2', {}, c.title), h('p', { class: 'muted' }, [c.artist, 'Local collection'].filter(Boolean).join(' · ')),
                h('div', { class: 'qz-local-tracks' }, (c.tracks || []).map((t, i) => h('div', { class: 'qz-track' },
                    h('span', { class: 'qz-num muted' }, i + 1), h('span', { class: 'qz-ttitle' }, t.title || t.file),
                    h('span', { class: 'qz-dur muted' }, K.fmtClock(t.duration))))),
                h('div', { class: 'sheet-actions' },
                    h('button', { type: 'button', class: 'btn primary', onclick: () => { scrim.remove(); P.play(c, 'replace'); } }, '▶ Play'),
                    h('button', { type: 'button', class: 'btn', onclick: () => { scrim.remove(); P.play(c, 'append'); } }, '+ Add'),
                    h('button', { type: 'button', class: 'btn', onclick: () => scrim.remove() }, 'Close'))));
        document.getElementById('overlay-root').append(scrim);
        return;
    }
    return K.albumInfo(c.id, {
    off: c.streamable === false,
    play: () => P.play(c, 'replace'),
    add: () => P.play(c, 'append'),
    });
};

P.album = id => {
    if (!P.albums.has(id)) {
        const pending = K.api('/qobuz/album/' + encodeURIComponent(id), { timeout: 30000 })
            .then(d => { if (d.ok) P.albums.set(id, d); else P.albums.delete(id); return d; });
        P.albums.set(id, pending);
    }
    return Promise.resolve(P.albums.get(id));
};

// Tap on an album: its tracks, each one playable from there.
P.toggleTracks = async (c, row, box, keep = false) => {
    if (!keep && P.open.has(c.id)) { P.open.delete(c.id); row.classList.remove('open'); K.clear(box); return; }
    P.open.add(c.id);
    row.classList.add('open');
    if (c.source === 'local') {
        K.clear(box).append(...(c.tracks || []).map((t, i) => h('div', { class: 'qz-track' },
            h('span', { class: 'qz-num muted' }, i + 1), h('span', { class: 'qz-ttitle' }, t.title || t.file),
            h('span', { class: 'qz-dur muted' }, K.fmtClock(t.duration)))))
        return;
    }
    K.clear(box).append(h('div', { class: 'spinner small' }));
    const d = await P.album(c.id);
    if (!P.open.has(c.id)) return;
    if (!d.ok) { K.clear(box).append(h('div', { class: 'errbox' }, d.error || 'could not read the album')); return; }
    const a = d.album;
    const multiDisc = new Set(a.track_list.map(t => t.disc)).size > 1;
    let work = '';
    const lines = [];
    for (const t of a.track_list) {
        if (t.work && t.work !== work) { work = t.work; lines.push(h('div', { class: 'qz-work small muted' }, t.work)); }
        lines.push(h('div', { class: 'qz-track' + (t.streamable ? '' : ' off') },
            h('span', { class: 'qz-num muted' }, multiDisc ? `${t.disc}.${t.number}` : t.number),
            h('span', { class: 'qz-ttitle' }, t.title, t.version ? h('span', { class: 'muted' }, ` (${t.version})`) : null),
            h('span', { class: 'qz-dur muted' }, K.fmtClock(t.duration)),
            h('button', { type: 'button', class: 'btn qz-tplay', disabled: !t.streamable, title: 'Play the album from here',
                onclick: () => P.play(c, 'replace', t) }, K.tIcon('play'))));
    }
    K.clear(box).append(...lines,
        // everything about it, and marking it awarded (widgets/albuminfo.js)
        h('button', { type: 'button', class: 'btn qz-details', onclick: () => K.albumInfo(c.id) }, 'Album details ›'));
};

// ── play ─────────────────────────────────────────────────────────────────────
P.play = async (c, mode, track = null) => {
    if (c.source === 'local') {
        const busy = K.busy(mode === 'append' ? `Adding “${c.title}”…` : `Queueing “${c.title}”…`);
        const d = await K.api('/qobuz/local/play', { json: { album_id: c.id, mode, tracks: c.tracks }, timeout: 90000 });
        busy.done();
        if (!d.ok) { K.toast(d.error || 'could not queue the album', 'error'); return; }
        K.toast(mode === 'append' ? `Added ${d.queued} tracks to the queue` : `Playing — ${d.queued} tracks queued`, 'ok', mode !== 'append');
        P.playerPoll.now(); return;
    }
    if (!P.usable()) { K.toast('Switch the renderer to upmpdcli first', 'error'); return; }
    const busy = K.busy(mode === 'append' ? `Adding “${c.title}”…` : `Queueing “${c.title}”…`);
    const body = { album_id: c.id, mode };
    if (track) body.start = track.id;
    // Played from a search's results: the server learns the search text and the
    // album's artist and composer as completions.  Not from "Played recently".
    const fromSearch = !!(P.last && P.last.results.includes(c));
    if (fromSearch) body.query = P.last.query || '';
    const d = await K.api('/qobuz/play', { json: body, timeout: 90000 });
    busy.done();
    if (!d.ok) {
        K.toast(d.error || 'could not queue the album', 'error');
        if (d.renderer === false) P.refreshStatus();
        return;
    }
    const n = `${d.queued} track${d.queued === 1 ? '' : 's'}`;
    K.toast(mode === 'append' ? `Added ${n} to the queue` : `Playing — ${n} queued`, 'ok', mode !== 'append');
    if (d.remembered) c.played = (c.played || 0) + 1;
    if (fromSearch) P.learn([P.last.query, c.artist, c.composer]);
    if (d.label_learned) P.learnPair(c.artist, c.label);
    P.playerPoll.now();
    // results searched from Now: back there, to what is now playing
    if (P.visible && P.fromNowShown) K.showPage('now');
};

// ── player ───────────────────────────────────────────────────────────────────
// MPD's own state (/k/api/player), which is also upmpdcli's: the playlist
// upmpdcli shows is MPD's queue.  Between polls the time runs on locally.  The
// strip and the full-screen player are two views of that one state; the cover
// comes from Qobuz for a track the plugin streams (/qobuz/track/<id>).
P.views = [];
P.tracks = new Map();         // Qobuz track id -> /qobuz/track's track, null, or a pending promise
const PLUGIN_TRACK = /\/qobuz\/track\/version\/\d+\/trackId\/(\d+)/;

P.makeView = full => {
    const btn = (label, title, fn, cls = '') => h('button', { type: 'button', class: 'btn qz-pbtn ' + cls, title, onclick: fn }, label);
    const v = { full };
    v.cover = h('div', { class: 'qz-pcover' });
    v.title = h('div', { class: 'qz-ptitle' }, '—');
    v.sub = h('div', { class: 'qz-psub muted' }, '');
    v.toggle = btn(K.tIcon('play'), 'Play / pause', () => P.transport(P.base && P.base.playing ? 'pause' : 'play'), 'primary qz-toggle');
    v.elapsed = h('span', { class: 'qz-ptime' }, '–:––');
    v.total = h('span', { class: 'qz-ptime' }, '–:––');
    v.seek = h('input', {
        type: 'range', class: 'qz-seek', min: 0, max: 1, step: 1, value: 0,
        oninput: () => { P.seeking = true; v.elapsed.textContent = K.fmtClock(+v.seek.value); },
        onchange: () => P.seekTo(+v.seek.value),
    });
    if (full) {   // the full player only: the strip keeps just play/pause
        v.favoriteDivider = h('span', { class: 'qz-pdivider', hidden: true, 'aria-hidden': 'true' });
        v.favorite = btn('♡', 'Save playing album in Library', () => {
            const album = P.trackInfo()?.album;
            if (album && album.id) K.saveQobuzFavorite(album);
        }, 'qz-favorite');
        v.favorite.hidden = true;
    }
    v.buttons = h('div', { class: 'qz-pbtns' },
        full ? btn('<<', 'Seek backward within this track', () => P.stepSeek(-1), 'qz-step') : null,
        btn(K.tIcon('prev'), 'Previous track', () => P.transport('prev')),
        v.toggle,
        btn(K.tIcon('stop'), 'Stop', () => P.transport('stop')),
        btn(K.tIcon('next'), 'Next track', () => P.transport('next')),
        full ? btn('>>', 'Seek forward within this track', () => P.stepSeek(1), 'qz-step') : null,
        v.favoriteDivider, v.favorite);
    v.seekRow = h('div', { class: 'qz-pseek' }, v.elapsed, v.seek, v.total);
    return v;
};

P.buildPlayer = () => {
    const v = P.makeView(false);
    v.prog = h('div', { class: 'qz-pprog' }, h('i'));    // upright: a thin line instead of the slider
    P.views.push(v);
    K.clear(P.player).append(v.prog,
        h('div', { class: 'qz-pinfo', role: 'button', tabindex: '0', 'aria-label': 'Open Now playing',
            onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); P.openFull(); } },
            title: 'Tap or swipe up to open the player' },
            v.cover, h('div', { class: 'qz-ptext' }, v.title, v.sub)),
        v.buttons, v.seekRow);
    P.wirePlayerDrawer();
};

// Where the strip's top edge is: the sheet rises from there, as the strip itself lifting, not from the screen bottom.
P.stripTop = () => {
    const r = P.player.getBoundingClientRect();
    return r.height ? Math.max(0, Math.round(r.top)) : window.innerHeight;
};

P.wirePlayerDrawer = () => {
    let drag = null, suppressClick = false;
    const control = target => target.closest('button, input, select, a');
    P.player.addEventListener('click', e => {
        if (control(e.target)) return;
        if (suppressClick) { suppressClick = false; return; }
        P.openFull();
    });
    P.player.addEventListener('pointerdown', e => {
        if (control(e.target) || e.button !== 0 || P.fullEl) return;
        suppressClick = false;
        drag = { id: e.pointerId, y: e.clientY, distance: 0, height: P.stripTop() };
        P.player.setPointerCapture(e.pointerId);
        e.stopPropagation();
    });
    P.player.addEventListener('pointermove', e => {
        if (!drag || e.pointerId !== drag.id) return;
        drag.distance = Math.max(0, drag.y - e.clientY);
        if (Math.abs(drag.y - e.clientY) > 8) suppressClick = true;
        if (drag.distance > 8 && !P.fullEl) P.openFull({ offset: drag.height - drag.distance });
        if (P.fullEl) {
            const at = Math.max(0, drag.height - drag.distance);
            P.fullEl.style.transform = `translateY(${at}px)`;
            K.riseVeil(P.fullEl, at, drag.height);
        }
    });
    const end = e => {
        if (!drag || e.pointerId !== drag.id) return;
        const finish = e.type !== 'pointercancel' && drag.distance > Math.min(100, drag.height * .2);
        drag = null;
        if (!P.fullEl) return;
        const panel = P.fullEl;
        panel.style.pointerEvents = '';
        panel.style.transition = 'transform 180ms ease-out, background-color 180ms';
        if (finish) panel.style.backgroundColor = ''; else K.riseVeil(panel, P.stripTop(), P.stripTop());
        panel.style.transform = finish ? 'translateY(0)' : `translateY(${P.stripTop()}px)`;
        if (!finish) setTimeout(() => { if (P.fullEl === panel) P.closeFull(); }, 180);
    };
    P.player.addEventListener('pointerup', end);
    P.player.addEventListener('pointercancel', end);
};

// Full screen, like Qobuz's own player: the cover as large as the screen
// allows, the track, the transport, and the queue (a tap plays from there).
P.openFull = ({ offset = 0 } = {}) => {
    if (P.fullEl || !P.usable()) return;
    const v = P.makeView(true);
    v.work = h('div', { class: 'qz-fwork muted' });
    v.detail = h('div', { class: 'qz-fdetail muted' });
    P.queueHead = h('button', { type: 'button', class: 'btn lbl', 'aria-haspopup': 'menu', 'aria-expanded': 'false', onclick: () => P.openQueueActions() }, 'Queue · 0 ▾');
    P.queueBox = h('div', { class: 'qz-queue' });
    P.queueTools = h('div', { class: 'qz-queue-tools' }, P.queueHead);
    // The cover's own maximize button, moved up into this row (to the right of the
    // "Now playing" label) rather than floating over the art itself.
    v.maxBtn = K.coverMaxButton(() => v.cover.dataset.src || '',
        { usable: P.seekable, elapsed: () => P.elapsedNow(), duration: () => P.base.duration, seek: s => P.seekTo(s) },
        () => {
            const t = P.trackInfo(), album = t ? t.album : null, d = P.now;
            return {
                title: (d && d.album) || (album && album.title) || '',
                facts: [(album && album.label) || (d && d.label), (album && album.year) || (d && d.date || '').slice(0, 4)].filter(Boolean).join(' · '),
                transport: { playing: () => !!(P.base && P.base.playing), action: a => P.transport(a) },
            };
        });
    P.fullEl = h('div', { class: 'scrim qz-full' },
        v.grip = h('div', { class: 'qz-grip', role: 'button', tabindex: '0', title: 'Swipe down or tap to close',
            'aria-label': 'Close the player' }, h('i')),
        h('div', { class: 'qz-full-top' },
            h('span', { class: 'qz-full-label' }, 'Now playing'), v.maxBtn,
            v.aiBtn = h('button', { type: 'button', class: 'btn qz-listening', title: 'Research this music',
                'aria-label': 'Research this music', onclick: e => K.listening.open(P.listeningTrack(), e.currentTarget) }, K.listening.icon())),
        h('div', { class: 'qz-full-body' },
            v.cover,
            h('div', { class: 'qz-full-side' },
                h('div', { class: 'qz-full-info' }, v.title, v.work, v.sub, v.awards = h('div', {}), v.detail),
                v.seekRow, v.buttons, P.queueTools, P.queueBox)));
    // Keep the playing album's details beside the queue actions.
    v.infoBtn = h('button', { type: 'button', class: 'btn qz-finfo', hidden: true, onclick: () => {
        const t = P.trackInfo();
        if (t && t.album && t.album.id) K.albumInfo(t.album.id);
    } }, 'Album details ›');
    P.queueTools.append(v.infoBtn);
    // Tap the cover to seek; a downward swipe minimizes the player.
    v.cover.classList.add('seek-zone');
    v.ring = new K.SeekRing(v.cover, { usable: P.seekable, elapsed: () => P.elapsedNow(),
        duration: () => P.base.duration, seek: s => P.seekTo(s),
        onMinimize: () => P.closeFull(),
        onPull: distance => { if (P.fullEl) P.fullEl.style.transform = `translateY(${distance}px)`; } });
    P.seekTaps = null;
    P.wireGrip(v.grip);
    const top = P.stripTop();
    P.player.classList.add('lifted');   // the sheet takes the strip's place and rises from it
    if (offset) {
        P.fullEl.style.transform = `translateY(${Math.max(0, offset)}px)`;
        P.fullEl.style.pointerEvents = 'none';
    } else if (top < window.innerHeight) {
        // a tap: rise from the strip's position too
        const sheet = P.fullEl;
        sheet.style.transform = `translateY(${top}px)`;
        K.riseVeil(sheet, top, top);
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (P.fullEl !== sheet) return;
            sheet.style.transition = 'transform 220ms cubic-bezier(.2,.8,.2,1), background-color 220ms';
            sheet.style.backgroundColor = '';
            sheet.style.transform = 'translateY(0)';
            setTimeout(() => { if (P.fullEl === sheet) sheet.style.transition = ''; }, 240);
        }));
    }
    document.getElementById('overlay-root').append(P.fullEl);
    P.fullView = v;
    P.paintListeningIcon = () => { if (v.aiBtn.isConnected) K.clear(v.aiBtn).append(K.listening.icon(K.listening.busy ? 'busy' : '')); };
    P.listeningUnsub = K.listening.onChange(P.paintListeningIcon);
    P.views.push(v);
    P.queueVersion = null;
    P.queueCount = 0;
    P.markedId = null;
    P.queueAuto = true;
    P.wireQueueScroll();
    P.queueSizer = new ResizeObserver(() => requestAnimationFrame(P.sizeQueue));
    [P.fullEl, v.cover, P.fullEl.querySelector('.qz-full-info'), v.seekRow, v.buttons, P.queueTools]
        .forEach(el => P.queueSizer.observe(el));
    requestAnimationFrame(P.sizeQueue);
    document.addEventListener('keydown', P.fullKey);
    P.paintViews();
    P.playerPoll.now();
};

// The handle at the top edge: a tap or Enter closes, a downward drag pulls the sheet and closes past 70 px.
P.wireGrip = grip => {
    let y0 = null, dy = 0;
    grip.addEventListener('pointerdown', e => { y0 = e.clientY; dy = 0; grip.setPointerCapture(e.pointerId); });
    grip.addEventListener('pointermove', e => {
        if (y0 === null) return;
        dy = Math.max(0, e.clientY - y0);
        if (P.fullEl) P.fullEl.style.transform = dy ? `translateY(${dy}px)` : '';
    });
    const end = e => {
        if (y0 === null) return;
        y0 = null;
        if (e.type === 'pointerup' && (dy > 70 || dy < 6)) P.closeFull();
        else if (P.fullEl) P.fullEl.style.transform = '';
    };
    grip.addEventListener('pointerup', end);
    grip.addEventListener('pointercancel', end);
    grip.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); P.closeFull(); } });
};
P.closeFull = () => {
    if (!P.fullEl) return;
    P.closeQueueActions();
    P.listeningUnsub?.();
    P.queueSizer.disconnect();
    if (P.fullView) P.fullView.ring.destroy();
    P.seekTaps = null;
    P.fullEl.remove();
    P.fullEl = null;
    P.player.classList.remove('lifted');
    P.views = P.views.filter(v => v !== P.fullView);
    P.fullView = null;
    document.removeEventListener('keydown', P.fullKey);
};
K.closeQobuzPlayer = () => {
    if (!P.fullEl) return false;
    P.closeFull();
    return true;
};
P.fullKey = e => { if (e.key === 'Escape') { if (P.queueActions) { P.closeQueueActions(); P.queueHead.focus(); } else P.closeFull(); } };

// In portrait the outer sheet travels until the transport reaches its top edge.
// The queue then fills the rest of the sheet and takes subsequent scrolling.
P.sizeQueue = () => {
    if (!P.fullEl) return;
    if (!matchMedia('(orientation: portrait)').matches) { P.queueBox.style.height = ''; return; }
    const bottom = parseFloat(getComputedStyle(P.fullEl).paddingBottom) || 0;
    const controls = P.fullView.buttons.getBoundingClientRect();
    const queue = P.queueBox.getBoundingClientRect();
    P.queueBox.style.height = `${Math.max(64, P.fullEl.clientHeight - (queue.top - controls.top) - bottom)}px`;
};

P.wireQueueScroll = () => {
    // Let the browser own wheel and touch scrolling so momentum stays native.
    // User movement turns off track following until the full player is reopened.
    const box = P.queueBox;
    const stopFollowing = e => {
        if (e.target.closest('.qz-qrow')) P.queueAuto = false;
    };
    box.addEventListener('wheel', stopFollowing, { passive: true });
    box.addEventListener('touchstart', stopFollowing, { passive: true });
    box.addEventListener('keydown', stopFollowing);
};

P.openQueueActions = () => {
    if (P.queueActions) { P.closeQueueActions(); return; }
    const clear = h('button', { type: 'button', class: 'menu-item', role: 'menuitem', disabled: !P.queueCount, onclick: () => {
        P.closeQueueActions();
        P.queueHead.focus();
        P.editQueue('clear');
    } }, 'Clear queue');
    P.queueActions = h('div', { class: 'menu-pop qz-queue-actions', role: 'menu' }, clear);
    P.queueTools.append(P.queueActions);
    P.queueHead.setAttribute('aria-expanded', 'true');
    P.queueActionsOutside = e => {
        if (!P.queueActions.contains(e.target) && !P.queueHead.contains(e.target)) P.closeQueueActions();
    };
    document.addEventListener('pointerdown', P.queueActionsOutside, true);
    clear.focus();
};
P.closeQueueActions = () => {
    if (!P.queueActions) return;
    document.removeEventListener('pointerdown', P.queueActionsOutside, true);
    P.queueActions.remove();
    P.queueActions = null;
    P.queueHead.setAttribute('aria-expanded', 'false');
};

P.transport = async (action, extra = {}) => {
    const d = await K.api('/k/api/transport', { json: { action, ...extra } });
    if (!d.ok) K.toast(d.error || `${action} failed`, 'error');
    P.playerPoll.now();
};

// First tap exposes the ring. Repeat taps seek; faster repeats increase the step.
P.stepSeek = direction => {
    if (!P.fullView || !P.seekable()) return;
    const ring = P.fullView.ring;
    const songid = P.now && P.now.songid;
    const seconds = ring.step(direction, songid);
    P.seekTaps = ring.taps;
    if (seconds === null) return;
    P.base = { ...P.base, elapsed: seconds, at: performance.now() };
    ring.paint(seconds / P.base.duration);
    // Serialize requests and keep the newest target during rapid tapping.
    P.stepSeekPending = { seconds, songid };
    P.flushStepSeek();
};
P.flushStepSeek = async () => {
    if (P.stepSeekSending) return;
    P.stepSeekSending = true;
    try {
        while (P.stepSeekPending) {
            const target = P.stepSeekPending;
            P.stepSeekPending = null;
            if (!P.now || P.now.songid !== target.songid) continue;
            const d = await K.api('/k/api/transport', { json: { action: 'seek', seconds: target.seconds } });
            if (!d.ok) { P.stepSeekPending = null; K.toast(d.error || 'Seek failed', 'error'); }
        }
    } finally { P.stepSeekSending = false; P.playerPoll.now(); }
};

P.seekTo = async seconds => {
    await P.transport('seek', { seconds });
    P.seeking = false;
};

// The Qobuz track playing, once known: its album's cover, title, label, year.
P.trackInfo = () => {
    const t = P.tracks.get(P.trackId);
    return t && typeof t.then !== 'function' ? t : null;
};

P.listeningTrack = () => {
    const d = P.now, t = P.trackInfo(), a = t?.album;
    return d && { ok: true, state: d.state, title: d.title || t?.title || '',
        artist: d.artist || t?.performer || a?.artist || '', album: d.album || a?.title || '',
        qobuz_album: a?.id || '', track_id: P.trackId };
};

P.refreshPlayer = async () => {
    const d = await K.api('/k/api/player', { timeout: 6000 });
    P.now = d.ok ? d : null;
    P.playerError = d.ok ? '' : (d.error || 'MPD unavailable');
    if (!P.stepSeekSending || !d.ok || !P.seekTaps || d.songid !== P.seekTaps.songid)
        P.base = d.ok ? { elapsed: d.elapsed, duration: d.duration, at: performance.now(), playing: d.state === 'play' } : null;
    const m = d.ok ? d.file.match(PLUGIN_TRACK) : null;
    P.trackId = m ? m[1] : null;
    if (P.trackId && !P.tracks.has(P.trackId)) {
        const id = P.trackId;
        // Looked up once per track; a failure is remembered too, not retried every poll.
        P.tracks.set(id, K.api('/qobuz/track/' + id, { timeout: 15000 })
            .then(r => { P.tracks.set(id, r.ok ? r.track : null); P.paintViews(); }));
    }
    P.paintViews();
    if (P.fullEl && d.ok && d.version !== P.queueVersion) P.loadQueue();
    else if (P.fullEl) P.markQueue();
};

const setCover = (box, src) => {
    if (box.dataset.src === src) return;
    box.dataset.src = src;
    [...box.children].forEach(c => { if (!c.classList.contains('seek-ring')) c.remove(); });   // the ring stays
    box.prepend(src ? h('img', { src, alt: '' }) : h('span', { class: 'qz-nocover' }, '♪'));
};

P.paintViews = () => {
    const d = P.now, t = P.trackInfo(), album = t ? t.album : null;
    const playing = !!(P.base && P.base.playing);
    if (P.fullView) {
        P.fullView.aiBtn.hidden = !d || !(d.title || t?.title);
        K.listening.observe(P.listeningTrack());
    }
    const title = !d ? '—' : d.state === 'stop' && !d.title ? 'Stopped' : (d.title || (t && t.title) || '—');
    const pos = d && d.pos && d.length ? `${d.pos} / ${d.length}` : '';
    for (const v of P.views) {
        v.title.textContent = title;
        if (v.favorite) {
            v.favorite.hidden = !(album && album.id);
            v.favoriteDivider.hidden = v.favorite.hidden;
        }
        if (v.toggle.dataset.icon !== (playing ? 'pause' : 'play')) {
            v.toggle.dataset.icon = playing ? 'pause' : 'play';
            K.clear(v.toggle).append(K.tIcon(v.toggle.dataset.icon));
        }
        setCover(v.cover, album ? (v.full ? album.image_large || album.image : album.image || album.image_large) : '');
        if (!v.full) K.cover.tintStrips(v.cover.dataset.src);
        if (v.full) {
            v.maxBtn.hidden = !v.cover.dataset.src;
            v.infoBtn.hidden = !(album && album.id);
            const aid = album && album.id || '';       // the album's awards, above its label (v.detail)
            if (v.awardsFor !== aid) { v.awardsFor = aid; K.clear(v.awards); if (aid) v.awards.append(K.awardsBox(album)); }
            v.work.textContent = t && t.work ? t.work : '';
            v.sub.textContent = d ? d.artist || (t && t.performer) || '' : P.playerError;
            v.detail.textContent = [
                (d && d.album) || (album && album.title), (album && album.label) || (d && d.label),
                (album && album.year) || (d && d.date || '').slice(0, 4), pos ? `track ${pos}` : '',
            ].filter(Boolean).join(' · ');
        } else {
            v.sub.textContent = d ? [d.artist, d.album, pos].filter(Boolean).join(' · ') : P.playerError;
        }
    }
    P.paintTime();
};

P.seekable = () => !!(P.base && Number.isFinite(P.base.duration) && P.base.duration > 0 && Number.isFinite(P.base.elapsed));
P.elapsedNow = () => {
    const b = P.base;
    return K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration);
};

P.paintTime = () => {
    if (P.fullView && P.seekable() && P.fullView.ring.shown && !P.fullView.ring.el.classList.contains('active') && !P.seeking) P.fullView.ring.paint(P.elapsedNow() / P.base.duration);
    const b = P.base;
    const known = b && Number.isFinite(b.duration) && b.duration > 0 && Number.isFinite(b.elapsed);
    const e = known ? K.clamp(b.elapsed + (b.playing ? (performance.now() - b.at) / 1000 : 0), 0, b.duration) : 0;
    for (const v of P.views) {
        v.seek.disabled = !known;
        v.total.textContent = b && Number.isFinite(b.duration) ? K.fmtClock(b.duration) : '–:––';
        if (v.prog) v.prog.firstChild.style.width = known ? `${(e / b.duration * 100).toFixed(1)}%` : '0';
        if (P.seeking) continue;          // a finger is on a slider
        if (!known) { v.elapsed.textContent = '–:––'; v.seek.value = 0; continue; }
        v.seek.max = Math.floor(b.duration);
        v.seek.value = Math.floor(e);
        v.elapsed.textContent = K.fmtClock(e);
    }
};

// The queue is re-read only when MPD says it changed (status "playlist").
P.loadQueue = async () => {
    const q = await K.api('/k/api/queue', { timeout: 8000 });
    if (!P.fullEl) return;
    if (!q.ok) { K.clear(P.queueBox).append(h('div', { class: 'muted small' }, q.error || 'queue unavailable')); return; }
    P.queueVersion = q.version;
    P.queueCount = q.length;
    P.queueHead.textContent = `Queue · ${q.length} ▾`;
    if (P.queueActions) P.queueActions.querySelector('button').disabled = !q.length;
    const manualTop = P.queueAuto ? null : P.queueBox.scrollTop;
    K.clear(P.queueBox).append(...q.songs.map(P.queueRow));
    if (q.length > q.songs.length) P.queueBox.append(h('div', { class: 'muted small' }, `… and ${q.length - q.songs.length} more`));
    P.sizeQueue();
    if (manualTop !== null) P.queueBox.scrollTop = manualTop;
    if (P.queueAuto) P.markedId = null;
    P.markQueue();
};

P.editQueue = async (action, song = null) => {
    if (P.queueEditing) return;
    P.queueEditing = true;
    try {
        const d = await K.api('/k/api/queue', { json: { action, ...(song ? { id: song.id } : {}) } });
        if (!d.ok) K.toast(d.error || 'Could not change the queue', 'error');
        else K.toast(action === 'clear' ? 'Queue cleared' : action === 'remove_album' ? 'Album removed' : 'Track removed', 'ok');
        P.queueVersion = null;
        P.playerPoll.now();
    } finally { P.queueEditing = false; }
};

P.queueMenu = song => {
    const close = () => scrim.remove();
    const remove = action => { close(); P.editQueue(action, song); };
    const scrim = h('div', { class: 'scrim qz-queue-menu', onclick: e => { if (e.target === scrim) close(); } },
        h('div', { class: 'sheet' }, h('h2', {}, song.title),
            song.album ? h('p', {}, song.album) : null,
            h('div', { class: 'sheet-actions' },
                h('button', { type: 'button', class: 'btn', onclick: close }, 'Cancel'),
                h('button', { type: 'button', class: 'btn danger', onclick: () => remove('remove') }, 'Remove track'),
                song.album ? h('button', { type: 'button', class: 'btn danger', onclick: () => remove('remove_album') }, 'Remove album') : null)));
    document.getElementById('overlay-root').append(scrim);
};

P.queueRow = song => {
    let gesture = null, timer = null, suppressClick = false;
    const cancel = () => { clearTimeout(timer); timer = null; };
    const row = h('button', {
        type: 'button', class: 'qz-qrow', dataset: { id: song.id },
        title: 'Tap to play; hold for removal options; swipe left to remove',
        onclick: () => {
            if (suppressClick) { suppressClick = false; return; }
            P.transport('jump', { pos: song.pos });
        },
        oncontextmenu: e => { e.preventDefault(); cancel(); if (!suppressClick) P.queueMenu(song); suppressClick = true; },
    }, h('span', { class: 'qz-num muted' }, song.pos),
        h('span', { class: 'qz-qtext' }, h('span', { class: 'qz-qtitle' }, song.title),
            song.artist ? h('span', { class: 'qz-qartist muted' }, song.artist) : null),
        h('span', { class: 'qz-dur muted' }, K.fmtClock(song.duration)));
    row.addEventListener('pointerdown', e => {
        if (e.button !== 0) return;
        cancel(); suppressClick = false;
        gesture = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: 0, horizontal: false };
        timer = setTimeout(() => { suppressClick = true; gesture = null; P.queueMenu(song); }, 550);
    });
    row.addEventListener('pointermove', e => {
        if (!gesture || gesture.id !== e.pointerId) return;
        const dx = e.clientX - gesture.x, dy = e.clientY - gesture.y;
        if (Math.hypot(dx, dy) > 10) { cancel(); suppressClick = true; }
        if (!gesture.horizontal && Math.abs(dy) > 10 && Math.abs(dy) >= Math.abs(dx)) { gesture = null; return; }
        if (dx < -10 && Math.abs(dx) > 1.4 * Math.abs(dy)) {
            gesture.horizontal = true;
            row.setPointerCapture(e.pointerId);
        }
        if (gesture.horizontal) {
            gesture.dx = Math.min(0, dx);
            row.style.transform = `translateX(${Math.max(-100, gesture.dx)}px)`;
            row.classList.toggle('qz-removing', gesture.dx < -65);
        }
    });
    const end = e => {
        cancel();
        if (!gesture || gesture.id !== e.pointerId) return;
        const remove = e.type === 'pointerup' && gesture.horizontal && gesture.dx < -65;
        gesture = null;
        row.style.transform = ''; row.classList.remove('qz-removing');
        if (remove) P.editQueue('remove', song);
    };
    row.addEventListener('pointerup', end);
    row.addEventListener('pointercancel', end);
    row.addEventListener('pointerleave', () => { if (gesture && !gesture.horizontal) { cancel(); gesture = null; } });
    return row;
};

P.markQueue = () => {
    const id = P.now ? P.now.songid : '';
    let current = null;
    for (const row of P.queueBox.children) {
        const on = !!id && row.dataset.id === id;
        row.classList.toggle('on', on);
        if (on) current = row;
    }
    // Leave enough room after the last row to align even the final track at the top.
    const box = P.queueBox;
    const last = box.querySelector('.qz-qrow:last-of-type');
    box.style.setProperty('--queue-tail', `${Math.max(0, box.clientHeight - (last ? last.offsetHeight : 0) - 2)}px`);
    if (P.queueAuto && current && id !== P.markedId) {
        box.scrollTop += current.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientTop;
    }
    P.markedId = id;
};

K.registerPage(P);
})();
