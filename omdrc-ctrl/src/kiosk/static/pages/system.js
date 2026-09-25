/* Page 6 — System: host load, memory, sound devices, and the panel's own
 * system/application buttons (reboot, power off, Kodi ...).  Refreshed only
 * while this page is showing. */
(() => {
'use strict';
const { h } = K;

const P = { id: 'system', label: 'System', title: 'System' };

P.mount = el => {
    P.el = el;
    P.left = h('div', { class: 'col' });
    P.right = h('div', { class: 'col' });
    P.bf = h('div', {}); P.ram = h('div', {}); P.top = h('div', {}); P.dev = h('div', {}); P.adv = h('div', {});
    P.left.append(K.card('BruteFIR CPU', P.bf), K.card('Memory', P.ram), K.card('Busiest processes', P.top));
    P.advCard = K.card('Audio devices', P.dev, P.adv);
    P.right.append(P.advCard, P.actions());
    el.append(h('div', { class: 'two-col' }, P.left, P.right));
    P.poll = new K.Poller(P.refresh, 5000);
};
P.show = () => P.poll.start();
P.hide = () => P.poll.stop();

const bar = (pct, cls = '') => h('div', { class: 'hbar ' + cls }, h('i', { style: { width: K.clamp(pct, 0, 100) + '%' } }));

P.refresh = async () => {
    const [bf, mem, top, snd, adv, diag] = await Promise.all([
        K.api('/brutefir/cpu'), K.api('/system/memory'), K.api('/system/topcpu'),
        K.api('/system/sndstat'), K.api('/system/advanced'), K.api('/audio/diagnostics?history=1')]);

    K.clear(P.bf).append(...(bf.ok
        ? (bf.procs.length ? [K.kv('Total', `${Number(bf.total).toFixed(1)} %`), bar(bf.total, bf.total > 60 ? 'warn' : ''),
            ...bf.procs.map(p => K.kv(`${p.name} [${p.pid}]`, `${p.cpu} %`))] : [h('p', { class: 'muted' }, 'BruteFIR is not running.')])
        : [h('p', { class: 'muted' }, bf.error || 'unavailable')]));

    if (mem.ok) {
        const usedPct = (mem.total - mem.available) / mem.total * 100;
        K.clear(P.ram).append(K.kv('Used', `${K.fmtBytes(mem.total - mem.available)} of ${K.fmtBytes(mem.total)}`), bar(usedPct, usedPct > 85 ? 'bad' : usedPct > 70 ? 'warn' : ''),
            K.kv('Available', K.fmtBytes(mem.available)), K.kv('Free', K.fmtBytes(mem.free)));
    } else K.clear(P.ram).append(h('p', { class: 'muted' }, mem.error || 'unavailable'));

    if (top.ok) {
        K.clear(P.top).append(...top.procs.slice(0, 6).map(p => K.kv(`${p.name} [${p.pid}]`, `${p.cpu} %`, p.cpu >= top.threshold ? 'warn' : '')));
    } else K.clear(P.top).append(h('p', { class: 'muted' }, top.error || 'unavailable'));

    K.clear(P.dev).append(snd.ok ? h('pre', { class: 'mono' }, snd.lines.join('\n')) : h('p', { class: 'muted' }, snd.error || 'unavailable'));

    // FreeBSD-only extras: the DAC's sysctl tree and the uaudio integrity log
    const extras = [];
    if (adv.ok) adv.sections.forEach(s => extras.push(h('details', {}, h('summary', {}, s.title), h('pre', { class: 'mono' }, s.output))));
    if (diag.ok && diag.supported !== false) {
        const d = diag.deltas || {};
        Object.keys(d).forEach(k => extras.push(K.kv(k, String(d[k]))));
    }
    K.clear(P.adv).append(...extras);
};

// System and application buttons from commands.conf.  Anything the panel marks
// confirm=yes (reboot, power off) asks first; nothing here runs on a bare tap.
P.actions = () => {
    const wrap = h('div', { class: 'col-inner' });
    const paint = () => {
        const cmds = K.commands().filter(c => (c.group === 'system' || c.group === 'apps') && c.type === 'WRITE');
        K.clear(wrap);
        ['apps', 'system'].forEach(g => {
            const list = cmds.filter(c => c.group === g);
            if (!list.length) return;
            wrap.append(K.card(g === 'apps' ? 'Applications' : 'System', h('div', { class: 'btn-row wrap' }, list.map(c =>
                h('button', { class: 'btn ' + (g === 'system' ? 'danger-soft' : ''), type: 'button', onclick: () => K.runCommand(c) }, c.what)))));
        });
    };
    K.onReady(paint);
    return wrap;
};

K.registerPage(P);
})();
