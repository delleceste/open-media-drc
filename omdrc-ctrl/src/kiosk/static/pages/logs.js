/* Page 7 — Logs: the alerts the panel has recognised, and a tail of any of the
 * configured logs (matching lines are marked).  Tails refresh only while shown. */
(() => {
'use strict';
const { h } = K;
const TAIL_BYTES = 16384;

const P = { id: 'logs', label: 'Logs', title: 'Logs', sources: [], current: null };

P.mount = el => {
    P.el = el;
    P.alerts = h('div', {});
    P.picker = h('div', {});
    P.pre = h('pre', { class: 'logtail' });
    P.meta = h('div', { class: 'muted small' });
    el.append(h('div', { class: 'two-col logs' },
        h('div', { class: 'col' }, K.card('Alerts', P.alerts)),
        h('div', { class: 'col grow' }, K.card('Log', P.picker, P.meta, P.pre))));
    P.poll = new K.Poller(P.refresh, 5000);
    K.onAlerts(P.paintAlerts);
};

P.show = () => {
    if (K.logSource) { P.current = K.logSource; K.logSource = null; }
    P.poll.start();
    P.paintAlerts();
};
P.hide = () => P.poll.stop();

P.refresh = async () => {
    if (!P.sources.length) {
        const s = await K.api('/logs/sources');
        if (s.ok) {
            P.sources = s.sources;
            if (!P.current || !P.sources.some(x => x.id === P.current)) P.current = (P.sources.find(x => x.exists) || P.sources[0] || {}).id;
        }
    }
    P.paintPicker();
    if (!P.current) return;
    const d = await K.api(`/logs/tail?source=${encodeURIComponent(P.current)}&bytes=${TAIL_BYTES}`);
    if (!d.ok) { P.pre.textContent = d.error || 'unavailable'; return; }
    const lines = d.content.split('\n');
    const marked = new Set(d.matches || []);
    const atBottom = P.pre.scrollHeight - P.pre.scrollTop - P.pre.clientHeight < 40;
    K.clear(P.pre).append(...lines.map((l, i) => h('span', { class: 'ln' + (marked.has(i) ? ' hit' : '') }, l + '\n')));
    P.meta.textContent = `${d.path} · ${K.fmtBytes(d.size)}${d.truncated ? ' · showing the end' : ''}${d.exists === false ? ' · file not found' : ''}`;
    if (atBottom) P.pre.scrollTop = P.pre.scrollHeight;
};

P.paintPicker = () => {
    K.clear(P.picker).append(K.segmented(P.sources.map(s => ({ value: s.id, label: s.label, disabled: !s.exists })), P.current, v => { P.current = v; P.refresh(); }, 'wrap small'));
};

P.paintAlerts = () => {
    if (!P.alerts) return;
    const list = (K.alerts || []).filter(a => a.severity !== 'ok');
    K.clear(P.alerts);
    if (!list.length) { P.alerts.append(h('p', { class: 'muted' }, 'No alerts.')); return; }
    P.alerts.append(...list.map(a => h('div', { class: 'errbox ' + a.severity },
        h('strong', {}, a.message), h('div', { class: 'muted small' }, a.hint || ''),
        h('div', { class: 'small mono' }, `${new Date(a.at * 1000).toLocaleTimeString()}${a.count > 1 ? ` · ×${a.count}` : ''} · ${a.source_label || a.source}`))));
};

K.registerPage(P);
})();
