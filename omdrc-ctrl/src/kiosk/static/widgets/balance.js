/* Channel balance: the power average of left vs right RMS over a selectable
 * window (powers are added before the logarithm, so loud passages count for
 * more than quiet ones), shown as a pointer on a ±6 dB scale. */
(() => {
'use strict';
const { h } = K;
const WINDOWS = [1, 3, 5, 15, 30, 60];   // seconds; frames arrive ~18 times a second, each a 300 ms RMS
const SCALE_DB = 6, FLOOR_DB = -60;

K.Balance = class Balance {
    constructor(host) {
        this.window = K.pref('balance.window', 15);
        if (!WINDOWS.includes(this.window)) this.window = 15;
        this.samples = []; this.lastAt = 0;
        // two looks, switchable: 'split' = grey left half / red right half with a marker,
        // 'bar' = black track and a bar growing from the centre towards the heavier side
        this.look = K.pref('balance.look', 'split') === 'bar' ? 'bar' : 'split';
        this.readout = h('span', { class: 'bal-readout' }, '—');
        this.winBtn = h('button', { class: 'chip', type: 'button', onclick: () => this.cycle() }, `${this.window} s`);
        this.lookBtn = h('button', { class: 'chip', type: 'button', title: 'Balance display style', onclick: () => this.toggleLook() });
        this.pointer = h('div', { class: 'bal-pointer' });
        this.fill = h('div', { class: 'bal-fill' });
        this.track = h('div', { class: 'bal-track', role: 'img' }, this.fill, h('i', { class: 'bal-centre' }), this.pointer);
        K.clear(host).append(
            h('div', { class: 'bal-head' }, h('span', { class: 'lbl' }, 'Balance'), this.readout, this.lookBtn, this.winBtn),
            this.track,
            h('div', { class: 'bal-scale' }, h('span', { class: 'l' }, 'L'), h('span', {}, '−6'), h('span', {}, '0'), h('span', {}, '+6'), h('span', { class: 'r' }, 'R')));
        this.applyLook();
        this.paint(NaN);
    }
    applyLook() {
        this.track.classList.toggle('split', this.look === 'split');
        this.track.classList.toggle('bar', this.look === 'bar');
        this.lookBtn.textContent = this.look === 'split' ? 'Split' : 'Bar';
    }
    toggleLook() {
        this.look = this.look === 'split' ? 'bar' : 'split';
        K.setPref('balance.look', this.look);
        this.applyLook();
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
        const x = ok ? K.clamp(db / SCALE_DB, -1, 1) : 0;          // -1 .. +1
        this.pointer.style.left = `${50 + 50 * x}%`;
        this.pointer.classList.toggle('off', !ok);
        // bar look: from the centre towards the heavier channel
        this.fill.style.width = `${Math.abs(x) * 50}%`;
        this.fill.style.left = x >= 0 ? '50%' : `${50 + 50 * x}%`;
        this.fill.classList.toggle('r', x > 0);
        this.fill.classList.toggle('l', x < 0);
        this.readout.classList.toggle('r', ok && db > 0.05);
        this.readout.classList.toggle('l', ok && db < -0.05);
    }
};
})();
