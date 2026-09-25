/* The audio chain as a horizontal row of blocks (renderer → MPD → DAC), with the
 * analyzer FIFO and its listeners hanging under the MPD block.  Used on the Now
 * page when the level display is off, where it takes the meters' place. */
(() => {
'use strict';
const { h } = K;

// green = flowing / present, red = missing or stopped, amber = odd, grey = idle
K.chainState = n => {
    if (n.kind === 'device') return n.colour === 'green' ? 'ok' : n.colour === 'red' ? 'bad' : n.present === false ? 'off' : 'warn';
    return n.running === false ? 'bad' : n.active ? 'ok' : 'off';
};

K.chainFlow = {
    paint(host, d) {
        if (!d || !d.ok) { K.clear(host).append(h('div', { class: 'cf-msg' }, d ? (d.error || 'audio chain unavailable') : 'reading the audio chain…')); return; }
        if (d.enabled === false) { K.clear(host).append(h('div', { class: 'cf-msg' }, 'Audio chain disabled in commands.conf.')); return; }
        const nodes = d.nodes || [], edges = d.edges || [];
        const fifo = nodes.find(n => n.kind === 'fifo');
        const fifoSource = fifo && (edges.find(e => e.to === fifo.id) || {}).from;
        const consumers = nodes.filter(n => n.kind === 'fifo-consumer');
        const main = nodes.filter(n => n.kind !== 'fifo' && n.kind !== 'fifo-consumer').sort((a, b) => a.row - b.row);

        const row = h('div', { class: 'cf-row' });
        main.forEach((n, i) => {
            const holders = (n.holders || []).map(x => x.cmd).join(', ');
            const branch = fifo && n.id === fifoSource
                ? h('div', { class: 'cf-branch' + (fifo.active ? ' live' : '') },
                    h('span', { class: 'cf-fifo' }, `FIFO ${fifo.listeners ?? ''}`.trim()),
                    consumers.length
                        ? consumers.map(c => h('span', { class: 'cf-consumer', title: c.sub }, `${c.title} ${c.listeners ?? ''}`.trim()))
                        : h('span', { class: 'cf-consumer none' }, 'no listeners'))
                : null;
            row.append(h('div', { class: 'cf-node ' + K.chainState(n) },
                h('div', { class: 'cf-title' }, h('i', { class: 'dot' }), n.title),
                h('div', { class: 'cf-sub' }, [n.sub, n.pid ? `pid ${n.pid}` : ''].filter(Boolean).join(' · ')),
                holders ? h('div', { class: 'cf-sub' }, `held by ${holders}`) : null,
                branch));
            const next = main[i + 1];
            if (next) {
                const e = edges.find(x => x.from === n.id && x.to === next.id) || edges.find(x => x.from === n.id && !/^spectrum:/.test(x.to));
                row.append(h('div', { class: 'cf-edge ' + (e ? (e.warn ? 'warn' : e.active ? 'ok' : 'off') : 'off') },
                    h('span', { class: 'cf-label' }, e ? e.label + (e.warn ? ` — ${e.warn}` : '') : ''), h('i', { class: 'cf-arrow' }, '→')));
            }
        });
        K.clear(host).append(row, ...(d.problems || []).slice(0, 2).map(p => h('div', { class: 'cf-problem ' + (p.severity || '') }, p.text)));
    },
};
})();
