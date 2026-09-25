/* Page 2 — DRC: what is applied, and the controls to change it.  Presets come
 * from commands.conf (the same buttons as the desktop panel), filter set and
 * design from /drc/geometry and /drc/design, attenuation from /drc/attenuation.
 * Chain-rebuilding actions take tens of seconds, so each one holds a modal
 * spinner until the server reports the outcome. */
(() => {
'use strict';
const { h } = K;
const PRESET_LABEL = { drc_off: 'OFF', drc_192000: '192', drc_96000: '96', drc_88200: '88.2', drc_48000: '48', drc_44100: '44.1', drc_cdin: 'CD in' };

const P = { id: 'drc', label: 'DRC', title: 'Room correction', geo: null, design: null, status: null, busy: false, attTimer: null, attDb: null };

P.mount = el => {
    P.el = el;
    P.left = h('div', { class: 'col' });
    P.right = h('div', { class: 'col' });
    el.append(h('div', { class: 'two-col' }, P.left, P.right));
    P.poll = new K.Poller(P.refreshAll, 10000);
    P.meterPoll = new K.Poller(P.pollMeters, 3000);
    K.drcState.onChange(() => { if (P.visible) P.render(); });
};

P.show = () => { P.visible = true; P.poll.start(); P.meterPoll.start(); };
P.hide = () => { P.visible = false; P.poll.stop(); P.meterPoll.stop(); };

P.refreshAll = async () => {
    if (P.busy) return;
    const [geo, design, status] = await Promise.all([K.api('/drc/geometry'), K.api('/drc/design'), K.api('/drc/status')]);
    P.geo = geo; P.design = design; P.status = status;
    await K.drcState.refresh();          // repaints through onChange
    P.render();
};

// Run a chain-rebuilding request behind the modal spinner, then re-read everything.
P.longAction = async (text, fn) => {
    if (P.busy) return;
    P.busy = true;
    const b = K.busy(text);
    let d;
    try { d = await fn(); } finally { b.done(); P.busy = false; }
    K.toast(d && d.ok ? 'Applied' : ((d && d.error) || 'failed'), d && d.ok ? 'ok' : 'error');
    await P.refreshAll();
    return d;
};

P.settle = () => {          // presets return before the chain is up: keep looking for a while
    [3000, 8000, 16000, 30000, 50000].forEach(ms => setTimeout(() => { if (P.visible && !P.busy) P.refreshAll(); }, ms));
};

P.render = () => {
    if (!P.el) return;
    const s = K.drcState.summary();
    K.clear(P.left).append(P.statusCard(s), P.presetCard(s), P.setsCard(s));
    K.clear(P.right).append(P.attCard(s), P.metersCard(s), P.rowsCard(), P.linksCard());
};

// ── status ───────────────────────────────────────────────────────────────────
P.statusCard = s => {
    const cls = !s.known ? 'warn' : s.power === 'on' ? (s.verification === 'verified' ? 'ok' : s.verification === 'mismatch' ? 'bad' : 'warn') : s.power === 'off' ? 'off' : 'warn';
    const lines = [];
    if (!s.known) lines.push(h('div', { class: 'big' }, s.text));
    else if (s.running) {
        lines.push(h('div', { class: 'big' }, `Active: ${K.fmtRate(s.rate)} @ ${s.design || 'default'}`));
        lines.push(h('div', { class: 'sub' }, `${s.geometry || ''} · ${s.verification}${s.message ? ' — ' + s.message : ''}`));
    } else {
        lines.push(h('div', { class: 'big' }, s.power === 'off' ? 'DRC off · BruteFIR not running' : 'None active · BruteFIR not running'));
        if (s.message) lines.push(h('div', { class: 'sub' }, s.message));
    }
    const ses = s.session || {};
    let saved = 'Saved session unavailable';
    if (ses.geometry) {
        saved = `Saved session: ${ses.power} · ${ses.geometry} · ${String(ses.design || '').replace(/^@/, '')} · ${ses.mode === 'resamp' ? 'auto-resample 192 kHz' : K.fmtRate(Number(ses.rate))}${ses.source_label ? ' · ' + ses.source_label : ''}`;
    }
    const restore = h('button', { class: 'btn', disabled: !s.known || s.matches || P.busy, onclick: () => P.restore() }, 'Restore saved');
    return K.card('Applied', h('div', { class: 'status-line ' + cls }, h('i', { class: 'dot' }), h('div', {}, ...lines)),
        h('div', { class: 'saved-line' }, h('span', {}, saved), restore));
};

P.restore = async () => {
    P.longAction('Restoring the saved session…', () => K.api('/drc/session', { json: { action: 'restore' }, timeout: 130000 }));
};

// ── presets ──────────────────────────────────────────────────────────────────
P.presetCard = s => {
    const cmds = K.commands().filter(c => c.group === 'drc' && c.type === 'WRITE' && c.id !== 'drc_resamp');
    const applied = c => c.id === 'drc_cdin' ? (s.session && s.session.source === 'cdin') : c.id === (s.running && s.rate ? `drc_${s.rate}` : 'drc_off');
    const grid = h('div', { class: 'preset-grid' }, cmds.map(c => h('button', {
        type: 'button', class: 'preset' + (applied(c) ? ' applied' : ''), disabled: P.busy,
        onclick: async () => {
            P.busy = true;
            let d;
            try {
                // Choosing CD again releases the bridge by taking the normal 44.1 action.
                const target = c.id === 'drc_cdin' && applied(c) ? K.commands().find(x => x.id === 'drc_44100') || c : c;
                d = await K.runCommand(target);
            } finally { P.busy = false; }
            if (d && !d.cancelled) { await K.drcState.refresh(); P.render(); P.settle(); }
        },
    }, PRESET_LABEL[c.id] || c.what)));
    return K.card('Sample rate', cmds.length ? grid : h('p', { class: 'muted' }, 'No presets configured.'));
};

// ── filter set and design ────────────────────────────────────────────────────
P.setsCard = s => {
    const kids = [];
    const g = P.geo;
    if (g && g.ok && Array.isArray(g.available)) {
        kids.push(h('div', { class: 'lbl' }, 'Filter set'));
        kids.push(K.segmented(g.available.map(v => ({ value: v, label: v })), g.geometry, async v => {
            if (v === g.geometry) return;
            const yes = await K.confirm({ title: `Switch to ${v}?`, message: 'The audio chain is rebuilt; playback is interrupted for a few seconds.', ok: 'Switch' });
            if (yes) P.longAction(`Switching filter set to ${v}…`, () => K.api('/drc/geometry', { json: { geometry: v }, timeout: 130000 }));
        }));
    }
    const d = P.design;
    if (d && d.ok && Array.isArray(d.available) && d.available.length) {
        kids.push(h('div', { class: 'lbl' }, 'Filter design'));
        kids.push(K.segmented(d.available.map(v => ({ value: v, label: String(v).replace(/^@/, '') })), d.design, async v => {
            if (v === d.design) return;
            const yes = await K.confirm({ title: `Switch design to ${String(v).replace(/^@/, '')}?`, message: 'The audio chain is rebuilt; playback is interrupted for a few seconds.', ok: 'Switch' });
            if (yes) P.longAction('Switching filter design…', () => K.api('/drc/design', { json: { design: v }, timeout: 130000 }));
        }, 'wrap'));
    }
    if (!kids.length) kids.push(h('p', { class: 'muted' }, g && g.error ? g.error : 'Reading filter sets…'));
    return K.card('Filters', ...kids);
};

// ── attenuation ──────────────────────────────────────────────────────────────
P.attCard = s => {
    const a = K.drcState.att;
    if (!a || !a.ok) return K.card('Attenuation', h('p', { class: 'muted' }, a ? (a.error || 'unavailable') : 'reading…'));
    const min = Number(a.min_db), max = Number(a.max_db);
    const cur = P.attDb !== null ? P.attDb : Number(a.db);
    const out = h('div', { class: 'att-value' }, `−${cur.toFixed(1)} dB`);
    const slider = h('input', { type: 'range', min, max, step: .1, value: cur, class: 'att-slider' });
    const set = v => {
        v = K.clamp(Math.round(v * 10) / 10, min, max);
        P.attDb = v; slider.value = v; out.textContent = `−${v.toFixed(1)} dB`;
        clearTimeout(P.attTimer);
        P.attTimer = setTimeout(async () => {          // one POST after the finger stops
            const d = await K.api('/drc/attenuation', { json: { db: v } });
            K.toast(d.ok ? (d.running ? `BruteFIR attenuation ${Number(d.db).toFixed(1)} dB` : `${Number(d.db).toFixed(1)} dB saved for next DRC start`) : (d.error || 'failed'), d.ok ? 'ok' : 'error');
            P.attDb = null;
            await K.drcState.refresh();
        }, 700);
    };
    slider.addEventListener('input', () => set(Number(slider.value)));
    const step = dv => h('button', { class: 'btn step', type: 'button', onclick: () => set((P.attDb !== null ? P.attDb : Number(a.db)) + dv) }, (dv > 0 ? '+' : '−') + Math.abs(dv));
    return K.card('Attenuation', out,
        h('div', { class: 'att-row' }, step(-1), step(-.1), slider, step(.1), step(1)),
        h('div', { class: 'saved-line' },
            h('span', { class: 'muted' }, a.running ? 'Applied live to BruteFIR' : 'Saved for the next DRC start'),
            h('button', { class: 'btn', type: 'button', onclick: async () => {
                const d = await K.api('/drc/attenuation', { json: { restore_default: true } });
                K.toast(d.ok ? 'Default attenuation restored' : (d.error || 'failed'), d.ok ? 'ok' : 'error');
                P.attDb = null; await K.drcState.refresh();
            } }, `Default (${Number(a.configured_db).toFixed(1)})`)));
};

// ── BruteFIR meters (only while it runs) ─────────────────────────────────────
P.meterBody = h('div', { class: 'meters-body' });
P.metersCard = s => K.card('BruteFIR', P.meterBody);
P.pollMeters = async () => {
    if (!P.visible) return;
    const s = K.drcState.summary();
    if (!s.running) { K.clear(P.meterBody).append(h('p', { class: 'muted' }, 'Not running.')); return; }
    const [rti, pk] = await Promise.all([K.api('/drc/brutefir-rti'), K.api('/drc/brutefir-peak')]);
    const rows = [];
    if (rti && rti.available) rows.push(K.kv('Real-time index', rti.rti === null ? 'no full processing yet' : String(rti.rti)));
    if (pk && pk.available) {
        rows.push(K.kv('Peak', `${Number(pk.peak_db).toFixed(1)} dBFS${pk.clipped ? ' — CLIPPED' : ''}`, pk.clipped ? 'bad' : (Number(pk.peak_db) > (pk.safety_limit_db ?? 6) ? 'warn' : '')));
        (pk.channels || []).forEach(c => rows.push(K.kv(`Channel ${c.channel}`, `${Number(c.peak_db).toFixed(1)} dBFS · ${c.overflow_count} overflow`)));
        rows.push(h('button', { class: 'btn', type: 'button', onclick: async () => { const d = await K.api('/drc/brutefir-peak/reset', { method: 'POST' }); K.toast(d.ok ? 'Peak hold reset' : (d.error || 'failed'), d.ok ? 'ok' : 'error'); P.pollMeters(); } }, 'Reset peak hold'));
    }
    if (!rows.length) rows.push(h('p', { class: 'muted' }, 'No meter data yet.'));
    K.clear(P.meterBody).append(...rows);
};

// ── the drc.sh status rows ───────────────────────────────────────────────────
P.rowsCard = () => {
    const st = P.status;
    return K.card('Status', st && st.ok ? st.rows.map(r => K.kv(r.key, r.value)) : h('p', { class: 'muted' }, st ? (st.error || 'unavailable') : 'reading…'));
};

P.linksCard = () => K.card('Details', h('div', { class: 'btn-row' },
    h('button', { class: 'btn', type: 'button', onclick: () => K.frame('/filter-response', 'Filter response') }, 'Filter response'),
    h('button', { class: 'btn', type: 'button', onclick: () => K.frame('/brutefir-config', 'BruteFIR configuration') }, 'BruteFIR config')));

K.registerPage(P);
})();
