/* Opt-in, bounded meter diagnostics. Never changes calibration or audio routing. */
(() => {
'use strict';
const MAX = 12000;
let active = false, events = [], omitted = 0, start = 0, deadline, ticker, fault = null;
let context = '', counters = {};
const details = () => {
    const d = window.OmdrcTiming?.details();
    if (!d) return null;
    const { settings, ...identity } = d;
    return identity;
};
const record = (event, data = {}) => {
    if (!active) return;
    const row = { t: Math.round(performance.now() - start), wall: Date.now(), event, ...data };
    if (events.length === MAX) { events.shift(); omitted++; }
    events.push(row);
    const key = (data.mode || 'session') + ':' + event;
    counters[key] = (counters[key] || 0) + 1;
    if (!['receive', 'age', 'draw', 'coalesced', 'stale', 'obsolete', 'outOfOrder', 'injectedDrop'].includes(event))
        console.info('OMDRC_TIMING ' + JSON.stringify(row));
};
const release = () => {
    const f = fault;
    if (!f) return;
    fault = null; clearTimeout(f.timer);
    record('faultEnd', { kind: f.kind, queued: f.queue.length });
    for (const [ev, receive] of f.queue) receive(ev);
};
const D = K.timingDebug = {
    record,
    start(seconds = 60) {
        if (active) throw new Error('Timing recording already active');
        if (!Number.isFinite(seconds) || seconds < 1 || seconds > 600) throw new Error('Duration must be 1–600 seconds');
        events = []; omitted = 0; counters = {}; start = performance.now(); active = true;
        context = JSON.stringify(details());
        record('start', { details: details(), hidden: document.hidden });
        ticker = setInterval(() => {
            const next = JSON.stringify(details());
            if (next !== context) { context = next; record('context', { details: details() }); }
            console.info('OMDRC_TIMING ' + JSON.stringify({ event: 'summary', t: Math.round(performance.now() - start), counters }));
        }, 1000);
        deadline = setTimeout(() => D.stop(), seconds * 1000);
        return { active, seconds };
    },
    stop() {
        release();
        if (active) record('stop');
        active = false; clearTimeout(deadline); clearInterval(ticker);
        return D.snapshot();
    },
    snapshot() {
        return { version: 1, active, omitted, counters: { ...counters }, details: details(), events: events.slice() };
    },
    inject({ kind = 'hold', ms = 2200, mode = 'vu', every = 3 } = {}) {
        if (!active || fault) throw new Error('Start recording first; only one fault at a time');
        if (K.sync?.running) throw new Error('Do not inject during microphone calibration');
        if (!['hold', 'drop'].includes(kind) || !['vu', 'music', 'precision', 'vu-clip', 'music-clip', 'precision-clip'].includes(mode)
            || !Number.isFinite(ms) || ms < 100 || ms > 10000 || !Number.isInteger(every) || every < 2)
            throw new Error('Invalid fault (100–10000 ms, meter mode, every >= 2)');
        fault = { kind, mode, every, n: 0, queue: [], timer: setTimeout(release, ms) };
        record('faultStart', { kind, mode, ms, every });
    },
    ingress(mode, ev, receive) {
        const f = fault;
        if (!f || f.mode !== mode) { receive(ev); return; }
        if (f.kind === 'drop') {
            if (++f.n % f.every === 0) record('injectedDrop', { mode });
            else receive(ev);
        } else if (f.queue.length < 500) f.queue.push([ev, receive]);
        else record('injectedDrop', { mode });
    }
};
document.addEventListener('visibilitychange', () => record('visibility', { hidden: document.hidden }));
})();
