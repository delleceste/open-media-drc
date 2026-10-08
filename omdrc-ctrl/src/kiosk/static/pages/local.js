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
    P.scan = h('button', { type: 'button', class: 'btn primary', onclick: () => P.action() }, 'Rescan');
    P.splitCue = h('input', { type: 'checkbox', disabled: true });
    P.splitCueLabel = h('label', { class: 'small' }, P.splitCue, ' Split single-FLAC CUE albums for per-track DR');
    P.toolsEl = h('div', {});

    const about = K.card('What this is',
        h('p', {}, 'The local database is the music on this box that MPD has indexed. There is no second library: MPD’s own index is the database, and this page only tells omdrcctrl where the files are and when to refresh.'),
        h('p', {}, 'It is part of the Qobuz search. Albums in the collection that match what you type appear among the Qobuz results, marked ⌂, and play through the same MPD queue and player strip as a Qobuz album. The Source filter there can limit a search to the local collection only.'),
        h('p', {}, 'Next to ⌂ the album’s DR value is shown, for instance DR12. It is the album average from the dr14.txt report in its folder, read when you search. A folder with no report shows no DR yet.'),
        h('p', {}, 'Rescan asks MPD to update its index, then calculates missing dr14.txt reports. The meter is built in. When the CUE option is checked, albums with one FLAC and one CUE are split into tagged track FLACs, verified against the original audio, and measured per track. The original FLAC is removed only after verification.'));

    P.confNote = h('p', { class: 'muted small' }, 'Read from music_directory in the MPD configuration; change it there.');
    const where = K.card('Where the music is', P.dirRow = h('div', {}), P.confNote, P.status);

    const rescan = K.card('Rescan',
        h('p', {}, 'Update MPD’s index with new, moved and removed files, and calculate the missing DR14 reports. It can run for a long time on a large library; the page shows how it is going.'),
        h('div', { class: 'btn-row' }, P.scan, P.splitCueLabel),
        P.splitCueHint = h('p', { class: 'muted small' }, 'Checking for CUE tools…'));
    const tools = K.card('DR14 tools', P.toolsEl);
    P.logEl = h('div', { class: 'local-log', role: 'log', 'aria-label': 'DR scan log' });
    P.logCard = K.card('Scan log', P.logEl);
    P.logCard.hidden = true;
    P.activity = h('div', { class: 'local-activity' });
    P.activityCard = K.card('Scan activity',
        h('p', { class: 'muted small' }, 'Scanner manages folders; CUE splitter creates track FLACs; Audio verifier compares their PCM hash; DR meter calculates each track.'),
        P.activity);
    P.activityCard.hidden = true;
    el.append(h('div', { class: 'two-col' }, h('div', { class: 'col' }, where, rescan, tools, P.logCard, P.activityCard), h('div', { class: 'col' }, about)));
    P.poll = new K.Poller(P.refresh, 4000);
};
P.show = () => P.poll.start();
P.hide = () => P.poll.stop();

const when = t => new Date(t * 1000).toLocaleString();

P.refresh = async () => {
    const seq = P.seq = (P.seq || 0) + 1;
    const d = await K.api('/qobuz/local/status');
    // an answer asked for before Rescan was pressed would show the old scan
    if (seq !== P.seq || P.starting || P.stopping) return;
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
    const tools = d.dr14_tools || { cue_split_available: false, tools: [], meter: {} };
    const ready = !!tools.cue_split_available;
    if (P.cueReady === undefined || (!P.cueReady && ready)) P.splitCue.checked = ready;
    if (!ready) P.splitCue.checked = false;
    P.cueReady = ready;
    P.splitCueHint.textContent = ready
        ? 'Checked by default. The source FLAC is removed only after a lossless audio hash check and per-track DR report.'
        : 'Install cuetools, shntool and FLAC to enable verified CUE splitting.';
    const toolKey = JSON.stringify(tools);
    if (toolKey !== P.toolKey) {
        P.toolKey = toolKey;
        K.clear(P.toolsEl).append(
            K.kv('Custom DR meter', tools.meter?.available ? tools.meter.path : 'missing', tools.meter?.available ? '' : 'warn'),
            h('p', { class: 'muted small' }, tools.meter?.purpose || 'Measures audio tracks and writes dr14.txt.'),
            ...(tools.tools || []).map(tool => h('div', {},
                K.kv(tool.name, tool.path ? tool.version || tool.path : 'not installed', tool.path ? '' : 'warn'),
                h('p', { class: 'muted small' }, tool.purpose))));
    }
    rows.push(K.kv('DR14 scan',
        s.state === 'running' ? (s.phase === 'cue' ? `checking CUE albums: ${s.done} of ${s.total}`
            : s.total ? `${s.done} of ${s.total} folders` : `looking for folders to measure… (since ${when(s.since)})`)
        : s.state === 'done' ? `finished ${when(s.at)} — ${s.calculated} report${s.calculated === 1 ? '' : 's'} calculated${s.failed ? `, ${s.failed} failed (see the log)` : ''}`
        : s.state === 'stopped' ? `stopped after ${s.done} of ${s.total} ${s.phase === 'cue' ? 'CUE folders' : 'folders'}`
        : s.state === 'interrupted' ? `interrupted (started ${when(s.since)})` : 'not run yet',
        s.state === 'running' || (s.state === 'done' && s.failed) ? 'warn' : ''));
    if (s.state === 'running' && s.total) {
        const pct = Math.round(100 * s.done / s.total);
        rows.push(h('div', { class: 'local-progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100',
            'aria-valuenow': String(pct), title: `${pct}%` }, h('span', { style: `width:${pct}%` })));
    } else if (s.state === 'running') {
        rows.push(P.busyBar());                   // the folder count is not known yet: moving, not filling
    }
    rows.push(K.kv('Meter', 'built in: TT Dynamic Range (drmeter.py), run with ffmpeg'));
    K.clear(P.dirRow).append(K.kv('Music directory', d.path || 'none', d.path && !d.exists ? 'bad' : ''));
    P.confNote.textContent = d.conf ? `Read from music_directory in ${d.conf}; change it there.`
        : 'Read from music_directory in the MPD configuration (none found); change it there.';
    K.clear(P.status).append(...rows);
    P.paintLog(d.log || [], d.log_levels || []);
    P.paintActivity(s.state === 'running', d.activity || []);
    P.paintButton(s.state === 'running');
};

P.paintActivity = (running, processes) => {
    P.activityCard.hidden = !running;
    if (!running) return;
    K.clear(P.activity);
    if (!processes.length) {
        P.activity.append(h('p', { class: 'muted small' }, 'No scan process found. The status may be stale.'));
        return;
    }
    P.activity.append(...processes.map(p => h('div', { class: 'local-process' },
        h('div', { class: 'local-process-summary' }, `${p.kind} · PID ${p.pid} · CPU ${p.cpu}% · elapsed ${p.elapsed}`),
        h('pre', { class: 'local-process-command' }, p.command))));
};

P.busyBar = () => h('div', { class: 'local-progress busy', role: 'progressbar', 'aria-label': 'Starting' }, h('span', {}));

P.paintButton = running => {
    P.running = running;
    P.scan.disabled = !!(P.starting || P.stopping);
    P.splitCue.disabled = running || P.starting || !P.cueReady;
    P.scan.className = running ? 'btn danger' : 'btn primary';
    P.scan.textContent = P.starting ? 'Starting…' : P.stopping ? 'Stopping…'
        : running ? 'Scanning… Stop' : 'Rescan';
};

P.action = () => P.running ? P.stop() : P.rescan();

P.stop = async () => {
    P.stopping = true;
    P.seq = (P.seq || 0) + 1;
    P.paintButton(true);
    const d = await K.api('/qobuz/local/stop', { json: {}, timeout: 10000 });
    P.stopping = false;
    K.toast(d.ok ? (d.message || 'Scan stopped') : `Could not stop scan: ${d.error || 'request failed'}`,
        d.ok ? 'ok' : 'error');
    P.refresh();
};

// The scan's log, newest line last; it follows the end unless scrolled up.
P.paintLog = (lines, levels = []) => {
    P.logCard.hidden = !lines.length;
    const text = JSON.stringify([lines, levels]);
    if (text === P.logText) return;
    P.logText = text;
    const el = P.logEl, atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    const fragment = document.createDocumentFragment();
    lines.forEach((line, index) => {
        const level = levels[index];
        const row = h('div', { class: 'local-log-line' });
        if (['ok', 'warn', 'bad'].includes(level)) {
            const label = level === 'ok' ? 'Already split into tracks'
                : level === 'warn' ? 'Not eligible for automatic splitting' : 'CUE processing error';
            row.append(h('span', { class: `local-log-led ${level}`, role: 'img', 'aria-label': label, title: label }));
        }
        row.append(document.createTextNode(line));
        fragment.append(row);
    });
    K.clear(el).append(fragment);
    if (atEnd) el.scrollTop = el.scrollHeight;
};

P.rescan = async () => {
    P.starting = true;
    P.seq = (P.seq || 0) + 1;
    P.paintButton(true);
    K.toast('Updating MPD’s index…');
    // the server's own log replaces these lines at the next look
    P.paintLog(['Rescan started', 'Updating MPD’s index…']);
    P.status.append(P.busyBar());
    const d = await K.api('/qobuz/local/refresh', { json: { split_cue: P.splitCue.checked }, timeout: 20000 });
    P.starting = false;
    K.toast(d.ok ? (d.message || 'Rescan started') : `Rescan failed: ${d.error || 'request failed'}`, d.ok ? 'ok' : 'error');
    P.paintButton(d.ok);
    P.refresh();
};

K.registerPage(P);
})();
