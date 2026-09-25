/* Page 4 — Source: what feeds the chain.  The renderer (qobuzconnect2mpd or
 * upmpdcli) and its activity, MPD's own state, and the CD/S-PDIF input. */
(() => {
'use strict';
const { h } = K;
const RENDERERS = ['qobuzconnect2mpd', 'upmpdcli'];

const P = { id: 'source', label: 'Source', title: 'Source', active: null, switching: false, error: null };

P.mount = el => {
    P.el = el;
    P.left = h('div', { class: 'col' });
    P.right = h('div', { class: 'col' });
    P.rendBody = h('div', {});
    P.mpdBody = h('div', {});
    P.cdBody = h('div', {});
    el.append(h('div', { class: 'two-col' }, P.left, P.right));
    P.left.append(K.card('Renderer', P.rendBody), K.card('MPD', P.mpdBody));
    if (K.state.features.cdin) P.right.append(K.card('CD input', P.cdBody));
    P.poll = new K.Poller(P.refresh, 4000);
};

P.show = () => P.poll.start();
P.hide = () => P.poll.stop();

P.refresh = async () => {
    const [svc, track, mpd, cd] = await Promise.all([
        K.api('/qconnect/services'), K.fetchTrack(), K.api('/mpd/info'),
        K.state.features.cdin ? K.api('/cdin/status') : Promise.resolve(null)]);
    if (svc && svc.ok && !P.switching) P.active = RENDERERS.find(r => svc[r]) || null;
    P.paintRenderer(track, svc);
    P.paintMpd(mpd);
    if (cd) P.paintCd(cd);
};

// ── renderer ─────────────────────────────────────────────────────────────────
P.switchTo = async target => {
    if (P.switching || target === P.active) return;
    P.switching = true; P.error = null;
    const b = K.busy(`Switching to ${target}…`);
    const d = await K.api('/qconnect/switch', { json: { target }, timeout: 90000 });
    b.done(); P.switching = false;
    if (d.ok) K.toast(`Switched to ${target}`);
    else { K.toast(d.error || 'switch failed', 'error'); P.error = { target, error: d.error || 'switch failed', detail: d.detail, log: d.log_path }; }
    P.refresh();
};

P.restart = async () => {
    if (!P.active) return;
    const b = K.busy(`Restarting ${P.active}…`);
    const d = await K.api('/renderer/restart', { json: { target: P.active }, timeout: 90000 });
    b.done();
    K.toast(d.ok ? `${P.active} restarted` : (d.error || 'restart failed'), d.ok ? 'ok' : 'error');
    P.refresh();
};

P.paintRenderer = (t, svc) => {
    const kids = [];
    kids.push(K.segmented(RENDERERS.map(r => ({ value: r, label: r })), P.active, v => P.switchTo(v)));
    if (P.error) kids.push(h('div', { class: 'errbox' },
        h('div', { class: 'errhead' }, h('strong', {}, `${P.error.target}: ${P.error.error}`),
            h('button', { class: 'btn', type: 'button', onclick: () => { P.error = null; P.refresh(); } }, '×')),
        P.error.detail ? h('pre', {}, P.error.detail) : null, P.error.log ? h('div', { class: 'muted small' }, P.error.log) : null));
    if (svc && svc.ok) kids.push(K.kv('Boot renderer', `${svc.boot || '—'}${svc.boot_is_default ? ' (default)' : ''}`));
    if (K.alerts && K.alerts.some(a => /oauth|auth/i.test(a.id || '') && a.severity !== 'ok'))
        kids.push(h('div', { class: 'errbox' }, h('strong', {}, 'Qobuz sign-in needed'),
            h('div', { class: 'muted small' }, 'The sign-in flow opens a browser page; do it from the full panel.'),
            h('button', { class: 'btn', type: 'button', onclick: () => K.frame('/', 'Full panel') }, 'Open full panel')));
    // now playing, as the renderer reports it
    if (t.ok) {
        kids.push(h('div', { class: 'np' }, h('strong', {}, t.title || '—'), h('div', { class: 'muted' }, [t.artist, t.album].filter(Boolean).join(' — ')),
            h('div', { class: 'small', style: { color: K.formatColor(t.format) } }, t.format)));
    }
    if (t.activity && t.activity.length) kids.push(h('ul', { class: 'activity' }, t.activity.slice(-6).map(e => h('li', {}, e))));
    kids.push(h('div', { class: 'btn-row' },
        h('button', { class: 'btn', type: 'button', disabled: !P.active, onclick: () => P.restart() }, 'Restart renderer'),
        h('button', { class: 'btn', type: 'button', disabled: !P.active, onclick: () => { K.logSource = P.active; K.goto('logs'); } }, 'Renderer log')));
    K.clear(P.rendBody).append(...kids);
};

// ── MPD ──────────────────────────────────────────────────────────────────────
P.paintMpd = m => {
    if (!m || !m.ok) { K.clear(P.mpdBody).append(h('p', { class: 'muted' }, m ? (m.error || 'unavailable') : 'reading…')); return; }
    const rows = [K.kv('State', m.running ? m.state : 'not running'), m.song ? K.kv('Song', m.song) : null,
        m.audio ? K.kv('Audio', m.audio) : null];
    if (m.alsa) rows.push(K.kv('ALSA', `${m.alsa.rate} Hz · ${m.alsa.format} · ${m.alsa.channels} ch · buffer ${m.alsa.buffer_size} / period ${m.alsa.period_size}`));
    if (m.rate_status && m.rate_status.text) rows.push(K.kv('Rate', m.rate_status.text, m.rate_status.kind === 'mismatch' ? 'bad' : ''));
    if (m.path_status && m.path_status.text) rows.push(K.kv('Path', m.path_status.text, m.path_status.kind === 'bad' ? 'bad' : ''));
    if (m.cpu !== undefined) rows.push(K.kv('CPU', `${m.cpu}%`));
    rows.push(h('div', { class: 'btn-row' }, h('button', { class: 'btn', type: 'button', onclick: async () => {
        const yes = await K.confirm({ title: 'Restart MPD?', message: 'Playback stops while MPD restarts.', ok: 'Restart', danger: true });
        if (!yes) return;
        const b = K.busy('Restarting MPD…');
        const d = await K.api('/mpd/restart', { method: 'POST', timeout: 60000 });
        b.done(); K.toast(d.ok ? 'MPD restarted' : (d.error || 'restart failed'), d.ok ? 'ok' : 'error'); P.refresh();
    } }, 'Restart MPD')));
    K.clear(P.mpdBody).append(...rows);
};

// ── CD input ─────────────────────────────────────────────────────────────────
P.paintCd = c => {
    if (!c.ok) { K.clear(P.cdBody).append(h('p', { class: 'muted' }, c.error || 'unavailable')); return; }
    const kids = [h('div', { class: 'status-line ' + ({ green: 'ok', red: 'bad' }[c.led] || 'off') }, h('i', { class: 'dot' }), h('div', {}, h('div', { class: 'big' }, c.summary || '—'), c.state ? h('div', { class: 'sub' }, `${c.state}${c.state_why ? ' — ' + c.state_why : ''}`) : null))];
    if (c.rate) kids.push(K.kv('Rate', K.fmtRate(c.rate)));
    if (c.last_error) kids.push(h('div', { class: 'errbox' }, `${(c.last_error.at || '').slice(11)} ${c.last_error.text || ''}`));
    (c.problems || []).forEach(p => kids.push(h('div', { class: 'errbox' }, p.text || String(p))));
    (c.metrics || []).forEach(m => kids.push(K.kv(m.label, m.value)));
    if (c.stats) kids.push(h('div', { class: 'muted small' }, c.stats));
    if (c.events && c.events.length) kids.push(h('ul', { class: 'activity' }, c.events.slice(-5).map(e => h('li', {}, `${(e.at || '').slice(11)} ${e.text || ''}`))));
    if (c.control && (c.running || c.control_start)) kids.push(h('div', { class: 'btn-row' }, h('button', { class: 'btn', type: 'button', onclick: async () => {
        const action = c.running ? 'stop' : 'start';
        const d = await K.api('/cdin/control', { json: { action }, timeout: 30000 });
        K.toast(d.ok ? `CD input ${action === 'stop' ? 'stopped' : 'started'}` : (d.error || 'failed'), d.ok ? 'ok' : 'error'); P.refresh();
    } }, c.running ? 'Stop bridge' : 'Start bridge')));
    K.clear(P.cdBody).append(...kids);
};

K.registerPage(P);
})();
