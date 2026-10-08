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
    P.scan = h('button', { type: 'button', class: 'btn primary', onclick: () => P.rescan() }, 'Rescan');

    const about = K.card('What this is',
        h('p', {}, 'The local database is the music on this box that MPD has indexed. There is no second library: MPD’s own index is the database, and this page only tells omdrcctrl where the files are and when to refresh.'),
        h('p', {}, 'It is part of the Qobuz search. Albums in the collection that match what you type appear among the Qobuz results, marked ⌂, and play through the same MPD queue and player strip as a Qobuz album. The Source filter there can limit a search to the local collection only.'),
        h('p', {}, 'Next to ⌂ the album’s DR value is shown, for instance DR12. It is the album average from the dr14.txt report in its folder, read when you search. A folder with no report shows no DR yet.'),
        h('p', {}, 'The reports are calculated by Rescan, not by searching: it asks MPD to update its index, then in the background calculates the dynamic range of every folder with audio that has no dr14.txt. A report is calculated once and kept, so a later rescan only does new albums. The meter is built in (the TT Dynamic Range algorithm); nothing else needs installing.'));

    P.confNote = h('p', { class: 'muted small' }, 'Read from music_directory in the MPD configuration; change it there.');
    const where = K.card('Where the music is', P.dirRow = h('div', {}), P.confNote, P.status);

    const rescan = K.card('Rescan',
        h('p', {}, 'Update MPD’s index with new, moved and removed files, and calculate the missing DR14 reports. It can run for a long time on a large library; the page shows how it is going.'),
        h('div', { class: 'btn-row' }, P.scan));
    P.logEl = h('pre', { class: 'local-log', role: 'log', 'aria-label': 'DR scan log' });
    P.logCard = K.card('Scan log', P.logEl);
    P.logCard.hidden = true;
    el.append(h('div', { class: 'two-col' }, h('div', { class: 'col' }, where, rescan, P.logCard), h('div', { class: 'col' }, about)));
    P.poll = new K.Poller(P.refresh, 4000);
};
P.show = () => P.poll.start();
P.hide = () => P.poll.stop();

const when = t => new Date(t * 1000).toLocaleString();

P.refresh = async () => {
    const seq = P.seq = (P.seq || 0) + 1;
    const d = await K.api('/qobuz/local/status');
    // an answer asked for before Rescan was pressed would show the old scan
    if (seq !== P.seq || P.starting) return;
    if (!d.ok) { K.clear(P.status).append(h('p', { class: 'muted' }, d.error || 'unavailable')); return; }
    const rows = [
        K.kv('Host', d.host),
    ];
    if (d.path && !d.exists) rows.push(h('div', { class: 'errbox bad small' }, 'This directory does not exist on this host.'));
    if (!d.path) rows.push(h('div', { class: 'errbox warn small' }, 'No music directory: this host’s MPD configuration could not be read or names none.'));
    const c = d.counts;
    if (c) {
        rows.push(K.kv('Album folders', c.busy ? 'counting…' : `${c.folders}${c.truncated ? '+' : ''}`));
        if (!c.busy) rows.push(K.kv('With a DR14 report', `${c.reports} of ${c.folders}`, c.reports < c.folders ? 'warn' : ''));
    }
    const s = d.scan;
    rows.push(K.kv('DR14 scan',
        s.state === 'running' ? (s.total ? `${s.done} of ${s.total} folders` : `counting folders… (since ${when(s.since)})`)
        : s.state === 'done' ? `finished ${when(s.at)} — ${s.calculated} report${s.calculated === 1 ? '' : 's'} calculated${s.failed ? `, ${s.failed} failed (see the log)` : ''}`
        : s.state === 'interrupted' ? `interrupted (started ${when(s.since)})` : 'not run yet',
        s.state === 'running' || (s.state === 'done' && s.failed) ? 'warn' : ''));
    if (s.state === 'running' && s.total) {
        const pct = Math.round(100 * s.done / s.total);
        rows.push(h('div', { class: 'local-progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100',
            'aria-valuenow': String(pct), title: `${pct}%` }, h('span', { style: `width:${pct}%` })));
    }
    rows.push(K.kv('Meter', 'built in: TT Dynamic Range (drmeter.py), run with ffmpeg'));
    K.clear(P.dirRow).append(K.kv('Music directory', d.path || 'none', d.path && !d.exists ? 'bad' : ''));
    P.confNote.textContent = d.conf ? `Read from music_directory in ${d.conf}; change it there.`
        : 'Read from music_directory in the MPD configuration (none found); change it there.';
    K.clear(P.status).append(...rows);
    P.paintLog(d.log || []);
    P.paintButton(s.state === 'running');
};

P.paintButton = running => {
    P.scan.disabled = running || P.starting;
    P.scan.textContent = P.starting ? 'Starting…' : running ? 'Scanning…' : 'Rescan';
};

// The scan's log, newest line last; it follows the end unless scrolled up.
P.paintLog = lines => {
    P.logCard.hidden = !lines.length;
    const text = lines.join('\n');
    if (text === P.logText) return;
    P.logText = text;
    const el = P.logEl, atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    el.textContent = text;
    if (atEnd) el.scrollTop = el.scrollHeight;
};

P.rescan = async () => {
    P.starting = true;
    P.seq = (P.seq || 0) + 1;
    P.paintButton(true);
    K.toast('Updating MPD’s index…');
    const d = await K.api('/qobuz/local/refresh', { json: {}, timeout: 20000 });
    P.starting = false;
    K.toast(d.ok ? (d.message || 'Rescan started') : `Rescan failed: ${d.error || 'request failed'}`, d.ok ? 'ok' : 'error');
    P.paintButton(d.ok);
    P.refresh();
};

K.registerPage(P);
})();
