/* Page — Local database: the collection MPD indexes on this box, where it
 * lives, and the rescan that also calculates the missing DR14 reports.  The
 * Qobuz search shows these albums with a house mark (⌂) and their DR value. */
(() => {
'use strict';
const { h } = K;

const P = { id: 'local', label: 'Local', title: 'Local database' };

P.mount = el => {
    P.el = el;
    P.status = h('div', {});
    P.input = h('input', { type: 'text', class: 'local-path', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off',
        placeholder: '/path/to/music', 'aria-label': 'Music directory' });
    P.input.addEventListener('input', () => { P.dirty = true; });
    P.save = h('button', { type: 'button', class: 'btn primary', onclick: () => P.setPath(P.input.value) }, 'Save');
    P.useMpd = h('button', { type: 'button', class: 'btn', onclick: () => P.setPath('') }, 'Use MPD’s');
    P.scan = h('button', { type: 'button', class: 'btn primary', onclick: () => P.rescan() }, 'Rescan');
    P.pathHead = h('div', { class: 'lbl' }, 'Music directory');

    const about = K.card('What this is',
        h('p', {}, 'The local database is the music on this box that MPD has indexed. There is no second library: MPD’s own index is the database, and this page only tells omdrcctrl where the files are and when to refresh.'),
        h('p', {}, 'It is part of the Qobuz search. Albums in the collection that match what you type appear among the Qobuz results, marked ⌂, and play through the same MPD queue and player strip as a Qobuz album. The Source filter there can limit a search to the local collection only.'),
        h('p', {}, 'Next to ⌂ the album’s DR value is shown, for instance DR12. It is the album average from the dr14.txt report in its folder, read when you search. A folder with no report shows no DR yet.'),
        h('p', {}, 'The reports are calculated by Rescan, not by searching: it asks MPD to update its index, then in the background runs DR14 T.meter in every folder with audio that has no dr14.txt. A report is calculated once and kept, so a later rescan only does new albums. This needs dr14_tmeter installed on this box.'));

    const where = K.card('Where the music is',
        P.status,
        P.pathHead,
        h('div', { class: 'local-row' }, P.input, P.save, P.useMpd),
        h('p', { class: 'muted small' }, 'The path is kept per host, as the meter delay is per network: a library mounted at /srv/music on one box can be elsewhere on another. Without an entry for this host, the music_directory from the MPD configuration is used. The path is as this box sees it; it does not change MPD’s own setting.'));

    const rescan = K.card('Rescan',
        h('p', {}, 'Update MPD’s index with new, moved and removed files, and calculate the missing DR14 reports. It can run for a long time on a large library; the page shows how it is going.'),
        h('div', { class: 'btn-row' }, P.scan));
    el.append(h('div', { class: 'two-col' }, h('div', { class: 'col' }, where, rescan), h('div', { class: 'col' }, about)));
    P.poll = new K.Poller(P.refresh, 4000);
};
P.show = () => P.poll.start();
P.hide = () => P.poll.stop();

const when = t => new Date(t * 1000).toLocaleString();

P.refresh = async () => {
    const d = await K.api('/qobuz/local/status');
    if (!d.ok) { K.clear(P.status).append(h('p', { class: 'muted' }, d.error || 'unavailable')); return; }
    P.host = d.host;
    P.pathHead.textContent = `Music directory for ${d.host}`;
    if (!P.dirty && document.activeElement !== P.input) P.input.value = d.configured;
    P.input.placeholder = d.mpd_path || '/path/to/music';
    // say which directory that is, and offer it only when the configuration names one
    P.useMpd.textContent = d.mpd_path ? `Use MPD’s: ${d.mpd_path}` : 'Use MPD’s';
    P.useMpd.disabled = !d.mpd_path || d.source === 'mpd';
    const rows = [
        K.kv('Host', d.host),
        K.kv('In use', d.path || 'none', d.path && !d.exists ? 'bad' : ''),
        K.kv('From', d.source === 'configured' ? 'this page' : d.source === 'mpd' ? 'the MPD configuration' : '—'),
    ];
    if (d.path && !d.exists) rows.push(h('div', { class: 'errbox bad small' }, 'This directory does not exist on this host.'));
    if (!d.path) rows.push(h('div', { class: 'errbox warn small' }, 'No music directory: this host’s MPD configuration could not be read or names none. Set one below.'));
    const c = d.counts;
    if (c) {
        rows.push(K.kv('Album folders', c.busy ? 'counting…' : `${c.folders}${c.truncated ? '+' : ''}`));
        if (!c.busy) rows.push(K.kv('With a DR14 report', `${c.reports} of ${c.folders}`, c.reports < c.folders ? 'warn' : ''));
    }
    const s = d.scan;
    rows.push(K.kv('DR14 scan',
        s.state === 'running' ? `running since ${when(s.since)}`
        : s.state === 'done' ? `finished ${when(s.at)} — ${s.calculated} report${s.calculated === 1 ? '' : 's'} calculated`
        : s.state === 'interrupted' ? `interrupted (started ${when(s.since)})` : 'not run yet',
        s.state === 'running' ? 'warn' : ''));
    if (!d.dr14) rows.push(h('div', { class: 'errbox warn small' }, 'dr14_tmeter is not installed on this host: a rescan updates MPD but calculates no DR values.'));
    K.clear(P.status).append(...rows);
    P.scan.disabled = s.state === 'running';
};

P.setPath = async path => {
    P.save.disabled = P.useMpd.disabled = true;
    try {
        const d = await K.api('/qobuz/local/config', { json: { path } });
        if (!d.ok) { K.toast(d.error || 'Not saved', 'error'); return; }
        P.dirty = false;
        K.toast(d.message);
        P.refresh();
    } finally { P.save.disabled = false; P.refresh(); }
};

P.rescan = async () => {
    P.scan.disabled = true;
    const d = await K.api('/qobuz/local/refresh', { json: {}, timeout: 20000 });
    K.toast(d.ok ? (d.message || 'Rescan started') : `Rescan failed: ${d.error || 'request failed'}`, d.ok ? 'ok' : 'error');
    P.refresh();
};

K.registerPage(P);
})();
