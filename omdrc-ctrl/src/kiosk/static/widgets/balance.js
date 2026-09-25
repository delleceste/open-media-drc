/* Channel balance: the power average of left vs right RMS over a selectable
 * window (powers are added before the logarithm, so loud passages count for
 * more than quiet ones), shown as a pointer on a ±6 dB scale. */
(() => {
'use strict';
const { h } = K;
const WINDOWS = [5, 15, 30, 60];
const SCALE_DB = 6, FLOOR_DB = -60;

K.Balance = class Balance {
    constructor(host) {
        this.window = K.pref('balance.window', 15);
        if (!WINDOWS.includes(this.window)) this.window = 15;
        this.samples = []; this.lastAt = 0;
        this.readout = h('span', { class: 'bal-readout' }, '—');
        this.winBtn = h('button', { class: 'chip', type: 'button', onclick: () => this.cycle() }, `${this.window} s`);
        this.pointer = h('div', { class: 'bal-pointer' });
        K.clear(host).append(
            h('div', { class: 'bal-head' }, h('span', { class: 'lbl' }, 'Balance'), this.readout, this.winBtn),
            h('div', { class: 'bal-track', role: 'img' }, h('i', { class: 'bal-centre' }), this.pointer),
            h('div', { class: 'bal-scale' }, h('span', {}, 'L'), h('span', {}, '−6'), h('span', {}, '0'), h('span', {}, '+6'), h('span', {}, 'R')));
        this.paint(NaN);
    }
    cycle() {
        this.window = WINDOWS[(WINDOWS.indexOf(this.window) + 1) % WINDOWS.length];
        K.setPref('balance.window', this.window);
        this.winBtn.textContent = `${this.window} s`;
        this.average(performance.now());
    }
    clear() { this.samples = []; this.lastAt = 0; this.paint(NaN); }

    /** New frame's `vu` object. */
    update(vu) {
        const now = performance.now();
        if (this.lastAt && now - this.lastAt > 1000) this.samples = [];   // a gap: start over
        this.lastAt = now;
        const l = Number(vu && vu.left_rms), r = Number(vu && vu.right_rms);
        if (Number.isFinite(l) && Number.isFinite(r) && Math.max(l, r) > FLOOR_DB)
            this.samples.push({ at: now, left: 10 ** (l / 10), right: 10 ** (r / 10) });
        this.average(now);
    }
    average(now) {
        const start = now - this.window * 1000;
        while (this.samples.length && this.samples[0].at < now - 60000) this.samples.shift();
        let left = 0, right = 0;
        for (const s of this.samples) if (s.at >= start) { left += s.left; right += s.right; }
        if (!left && !right) return this.paint(NaN);
        this.paint(10 * Math.log10(Math.max(right, 1e-12) / Math.max(left, 1e-12)));
    }
    paint(db) {
        const ok = Number.isFinite(db);
        this.readout.textContent = !ok ? '—' : Math.abs(db) < 0.05 ? 'Centre 0.0 dB'
            : `${db < 0 ? 'L' : 'R'} ${Math.abs(db).toFixed(1)} dB`;
        this.pointer.style.left = `${ok ? 50 + 50 * K.clamp(db / SCALE_DB, -1, 1) : 50}%`;
        this.pointer.classList.toggle('off', !ok);
        this.pointer.classList.toggle('warn', ok && Math.abs(db) > 3);
    }
};
})();
