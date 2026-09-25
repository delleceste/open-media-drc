/* Page 3 — Dynamic range tools: the rolling estimate of what is playing (off
 * until you turn it on, and only computed while this page is showing it), the
 * measurement of the whole record on the wire, and the DR database lookup. */
(() => {
'use strict';
const { h } = K;

// The DR database's own colours: flat red at 7 and below, flat green from 14.
const badgeColor = v => v === null || v === undefined ? '' : v <= 7 ? '#ff0000' : v >= 14 ? '#00ff00'
    : ({ 8: '#ff4800', 9: '#ff9100', 10: '#ffd900', 11: '#d9ff00', 12: '#90ff00', 13: '#48ff00' })[v];
const badge = (v, big, tip) => h('span', {
    class: 'drb' + (v === null || v === undefined ? ' none' : '') + (big ? ' big' : ''),
    title: tip || null, style: v === null || v === undefined ? {} : { background: badgeColor(v) },
}, v === null || v === undefined ? '–' : String(v).padStart(2, '0'));

const P = { id: 'dr', label: 'DR', title: 'Dynamic range', on: false, sub: null, job: null, jobTimer: null };

P.mount = el => {
    P.el = el;
    // estimate card
    P.toggle = h('button', { class: 'btn big-toggle', type: 'button', role: 'switch', 'aria-checked': 'false', onclick: () => P.setOn(!P.on, true) }, 'Estimate');
    P.value = h('strong', { class: 'dr-value big' }, '—');
    P.status = h('div', { class: 'dr-status' }, 'Off — press Estimate to start collecting');
    P.gaugeHost = h('div', { class: 'drg' });
    P.gauge = K.drGauge(P.gaugeHost);
    P.winLabel = h('output', {}, '');
    P.slider = h('input', { type: 'range', min: 60, max: 5400, step: 60, oninput: e => K.drEstimate.setWindow(e.target.value) });
    P.detect = h('button', { class: 'chip', type: 'button', role: 'switch', onclick: () => K.drEstimate.setDetect(!K.drEstimate.detect) });
    P.barHost = h('div', { class: 'dr-bar tall' });
    P.detail = h('div', { class: 'dr-detail', hidden: true });
    P.oldest = h('span', {});
    P.bar = new K.DrBar(P.barHost, { onDetail: (text, count) => { P.detail.hidden = !text; P.detail.textContent = text || ''; P.oldest.textContent = count ? `−${K.dr.elapsedLabel(count * 3)}` : ''; } });
    P.estBody = h('div', { class: 'est-body' },
        h('div', { class: 'dr-head' }, P.value, P.status), P.gaugeHost,
        h('div', { class: 'win-row' }, h('span', { class: 'lbl' }, 'Rolling window'), P.slider, P.winLabel, P.detect),
        P.barHost, h('div', { class: 'dr-times' }, P.oldest, h('span', {}, 'Latest')), P.detail,
        h('p', { class: 'muted small' }, 'Recent excerpt at 44.1 kHz · indicative of what you just heard, not full-track or album DR.'));
    P.estBody.hidden = true;
    const note = (title, ...text) => h('div', { class: 'explain' }, h('strong', {}, title + ' '), ...text);
    const estCard = K.card('Estimate', P.toggle,
        note('What it is.', 'A live figure for what is playing now: the dynamic range of the last few minutes, worked out in 3-second blocks. The bar below is the same estimate you see on Now: each segment is a slice of the window (its height is how loud it was, its number is its DR, red = compressed to green = dynamic). Turning it off also hides the DR value and bar on the Now page, and nothing is computed.'),
        P.estBody);

    // measurement card
    P.measBtn = h('button', { class: 'btn', type: 'button', onclick: () => P.measStart() }, 'Measure this record');
    P.cancelBtn = h('button', { class: 'btn danger', type: 'button', onclick: () => P.measCancel(), hidden: true }, 'Cancel');
    P.measBody = h('div', { class: 'meas-body' });
    const measCard = K.card('Measure', h('div', { class: 'btn-row' }, P.measBtn, P.cancelBtn,
        K.state.features.drdb ? h('button', { class: 'btn', type: 'button', onclick: () => K.frame('/dr-alternatives', 'DR versions of this record') }, 'Compare ↗') : null),
        note('Meas.', 'Measures the whole record, not a slice: it fetches this record’s tracks again, measures each one’s DR on the copy that actually reaches your DAC, then deletes them. It takes a while and shows a DR per track and for the album.'),
        note('Compare.', 'Looks this record up in the Dynamic Range database and lists its other masters and pressings with their published DR, so you can see whether another edition has more dynamics than the one you are playing.'),
        P.measBody);

    el.append(h('div', { class: 'two-col' }, h('div', { class: 'col' }, estCard), h('div', { class: 'col' }, measCard)));
    P.paintWin();
    P.setOn(K.pref('now.dr', true));      // already running for the Now page: show it here too
};

P.paintWin = () => {
    const E = K.drEstimate;
    P.slider.value = E.windowSeconds;
    P.winLabel.textContent = K.dr.windowLabel(E.windowSeconds);
    P.detect.textContent = `Detect song change: ${E.detect ? 'On' : 'Off'}`;
    P.detect.setAttribute('aria-checked', String(E.detect));
};

// The one DR switch: `persist` is true for the user's own tap.  It is remembered, and it
// is what the Now page follows: off hides the DR value and bar there and stops the stream.
P.setOn = (on, persist = false) => {
    P.on = on;
    if (persist) K.setPref('now.dr', on);
    if (persist && !on) { const now = K.pages.find(p => p.id === 'now'); if (now && now.releaseDr) now.releaseDr(); }
    P.toggle.classList.toggle('active', on);
    P.toggle.setAttribute('aria-checked', String(on));
    P.toggle.textContent = on ? 'Estimate — on (tap to turn off)' : 'Estimate — off (tap to turn on)';
    P.estBody.hidden = !on;
    P.sync();
};

// The estimator streams only while this page is showing AND the toggle is on.
P.sync = () => {
    const want = P.on && P.visible && !document.hidden;   // the Now page's keep-alive covers the background
    if (want && !P.sub) P.sub = K.drEstimate.listen(P.paint);
    if (!want && P.sub) { P.sub.close(); P.sub = null; }
};

P.paint = E => {
    P.paintWin();
    const s = E.summary();
    P.value.textContent = s.value === null ? '—' : (s.value > 14 ? 'DR14+' : `DR${s.value}`);
    P.value.style.color = s.value === null ? '' : K.dr.color(s.value).bg;
    P.status.textContent = s.status;
    P.gauge.set(s.value);
    P.bar.render(E.selected(), E.windowSeconds);
};

// ── measurement ──────────────────────────────────────────────────────────────
P.measPaint = job => {
    P.job = job;
    const running = job && (job.status === 'queued' || job.status === 'running');
    P.measBtn.disabled = !!running;
    P.cancelBtn.hidden = !running;
    if (!job) { K.clear(P.measBody); return; }
    const n = job.tracks.length;
    const kids = [h('div', { class: 'meas-title' }, running ? `Measuring DR · ${job.album || 'this record'} · ${n} track${n === 1 ? '' : 's'}` : `DR measured here · ${job.album || 'this record'}`)];
    kids.push(h('div', { class: 'meas-bar' }, h('i', { style: { width: `${Math.round((job.fraction || 0) * 100)}%` } })));
    const where = job.current !== null && job.current !== undefined ? `Track ${job.current + 1} of ${n} · ` : '';
    kids.push(h('div', { class: 'muted small' }, running ? where + (job.message || '') : (job.message || job.status)));
    const measured = job.tracks.filter(t => t.dr !== null);
    if (measured.length) {
        const discs = Object.entries(job.discs || {});
        kids.push(h('div', { class: 'meas-album' }, badge(job.album_dr ?? null, true),
            h('span', {}, `album${running ? ' so far' : ''}`), discs.map(([d, v]) => h('span', { class: 'disc' }, `disc ${d} `, badge(v)))));
        kids.push(h('div', { class: 'meas-tracks' }, job.tracks.map((t, i) => {
            const name = `${t.track || i + 1}. ${t.title}`;
            return h('div', { class: 'meas-track' }, badge(t.dr, false, t.dr !== null ? `DR${t.dr} (${t.dr_exact}), peak ${t.peak_db} dB, RMS ${t.rms_db} dB` : (t.error || t.status)),
                h('span', {}, name));
        })));
    }
    K.clear(P.measBody).append(...kids);
};

P.measPoll = async () => {
    clearTimeout(P.jobTimer);
    if (!P.visible) return;
    const d = await K.api('/dr/measure');
    if (d && d.ok) P.measPaint(d.job);
    const running = d && d.ok && d.job && (d.job.status === 'queued' || d.job.status === 'running');
    if (running && P.visible) P.jobTimer = setTimeout(P.measPoll, 1500);
};

P.measStart = async () => {
    const d = await K.api('/dr/measure', { method: 'POST', timeout: 30000 });
    if (!d.ok) { K.toast(d.error || 'cannot measure DR', 'error'); return; }
    P.measPaint(d.job);
    P.measPoll();
};
P.measCancel = async () => { await K.api('/dr/measure/cancel', { method: 'POST' }); P.measPoll(); };

P.show = () => { P.visible = true; P.setOn(K.pref('now.dr', true)); P.measPoll(); };
P.hide = () => { P.visible = false; P.sync(); clearTimeout(P.jobTimer); };

document.addEventListener('visibilitychange', () => P.sync && P.el && P.sync());

K.registerPage(P);
})();
