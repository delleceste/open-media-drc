/* Page 5 — Audio chain: who feeds whom, from the renderer down to the DAC,
 * drawn as a vertical flow.  Read from /audio/chain, only while showing. */
(() => {
'use strict';
const { h } = K;

const P = { id: 'chain', label: 'Chain', title: 'Audio chain', detail: false };

P.mount = el => {
    P.el = el;
    P.body = h('div', {});
    el.append(P.body);
    P.poll = new K.Poller(P.refresh, 4000);
};
P.show = () => P.poll.start();
P.hide = () => P.poll.stop();

P.refresh = async () => {
    const d = await K.api('/audio/chain');
    P.paint(d);
};

const stateClass = n => {
    if (n.kind === 'device') return n.colour === 'green' ? 'ok' : n.colour === 'red' ? 'bad' : n.present === false ? 'off' : 'warn';
    return n.running === false ? 'bad' : n.active ? 'ok' : 'off';
};

P.paint = d => {
    if (!d || !d.ok) { K.clear(P.body).append(K.card('Audio chain', h('p', { class: 'muted' }, d ? (d.error || 'unavailable') : 'reading…'))); return; }
    if (d.enabled === false) { K.clear(P.body).append(K.card('Audio chain', h('p', { class: 'muted' }, 'Disabled in commands.conf.'))); return; }
    const kids = [];
    const flowOk = d.flowing && d.holders_ok !== false;
    kids.push(h('div', { class: 'status-line ' + (flowOk ? 'ok' : 'warn') }, h('i', { class: 'dot' }),
        h('div', {}, h('div', { class: 'big' }, d.summary || '—'),
            h('div', { class: 'sub' }, `${d.os || ''} · ${d.flowing ? 'audio flowing' : 'nothing flowing'}${d.privileged ? '' : ' · holders unavailable (no privileges)'}`))));
    (d.problems || []).forEach(p => kids.push(h('div', { class: 'errbox ' + (p.severity || '') }, p.text)));

    // rows of nodes, joined by edge chips
    const nodes = (d.nodes || []).slice().sort((a, b) => (a.row - b.row));
    const edges = d.edges || [];
    const flow = h('div', { class: 'flow' });
    nodes.forEach((n, i) => {
        const holders = (n.holders || []).map(x => `${x.cmd}${x.mode ? ' (' + x.mode + ')' : ''}`).join(', ');
        flow.append(h('div', { class: 'node ' + stateClass(n) },
            h('i', { class: 'dot' }),
            h('div', { class: 'node-body' }, h('div', { class: 'node-title' }, n.title), h('div', { class: 'muted small' }, [n.sub, n.pid ? `pid ${n.pid}` : '', n.user].filter(Boolean).join(' · ')),
                holders ? h('div', { class: 'small' }, `held by ${holders}`) : null),
            n.kind === 'device' ? h('span', { class: 'chip ' + (n.role === 'dac' ? 'ok' : '') }, n.role || 'device') : null));
        const next = nodes[i + 1];
        if (next) {
            const e = edges.find(x => x.from === n.id && x.to === next.id) || edges.find(x => x.from === n.id);
            flow.append(h('div', { class: 'edge ' + (e ? (e.warn ? 'warn' : e.active ? 'ok' : 'off') : 'off') },
                h('i', { class: 'arrow' }, '↓'), h('span', {}, e ? e.label + (e.warn ? ` — ${e.warn}` : '') : '')));
        }
    });
    kids.push(K.card('Flow', flow));

    // every device, and who holds it
    const devs = d.devices || [];
    if (devs.length) {
        kids.push(K.card('Devices', h('div', { class: 'devs' }, devs.map(v => h('div', { class: 'dev' },
            h('div', { class: 'dev-head' }, h('strong', {}, v.label), h('span', { class: 'chip' }, v.role), h('span', { class: 'chip ' + (v.present ? 'ok' : 'bad') }, v.present ? 'present' : 'absent')),
            h('div', { class: 'muted small' }, [v.spec, v.path, v.target && '→ ' + v.target].filter(Boolean).join(' · ')),
            (v.holders || []).length ? h('div', { class: 'small' }, 'held by ' + v.holders.map(x => `${x.cmd} [${x.pid}] ${x.mode}`).join(', ')) : h('div', { class: 'muted small' }, 'free'))))));
    }
    K.clear(P.body).append(h('div', { class: 'two-col' }, h('div', { class: 'col' }, ...kids.slice(0, kids.length - (devs.length ? 1 : 0))), devs.length ? h('div', { class: 'col' }, kids[kids.length - 1]) : null));
};

K.registerPage(P);
})();
