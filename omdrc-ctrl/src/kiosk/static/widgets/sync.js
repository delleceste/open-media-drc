/* Meter timing: a per-device display delay, and its calibration with the phone's
 * microphone (Android app only - a page on plain http cannot open the mic).
 *
 * The server already holds the level/spectrum frames back by the chain's delay (the
 * DRC chain, or with DRC off the DAC's own buffer).  What is left - network, this
 * device, anything the model misses - differs per screen, so it is corrected here:
 * frames are drawn `sync.delayMs` after they arrive.  The value lives in this
 * device's browser storage, not on the box.
 *
 * Calibration: for ~10 s the app records the microphone and reduces it to a peak
 * envelope (10 ms steps, capture-time stamped); meanwhile the arrival time and peak
 * of every level frame is noted.  Sliding one against the other, the offset where
 * they correlate best is how long after a frame arrives its sound is heard - the
 * delay to apply.  A frame arriving after its sound (negative offset) cannot be
 * fixed here: the screen can only wait, never anticipate.
 */
(() => {
'use strict';

const S = K.sync = {};
const STEP = 10;                 // ms, the grid both series are compared on
const LAG_MIN = -600, LAG_MAX = 2500;
const FLOOR = -60;

S.delayMs = () => Math.max(0, Number(K.pref('sync.delayMs', 0)) || 0);
S.setDelayMs = ms => { K.setPref('sync.delayMs', Math.max(0, Math.min(3000, Math.round(ms)))); };
S.canCalibrate = () => !!(window.OmdrcApp && window.OmdrcApp.startMicEnvelope &&
                          (!window.OmdrcApp.micAvailable || window.OmdrcApp.micAvailable()));

// ── the maths (exported for tests) ───────────────────────────────────────────
// frames: [{t: arrival ms, p: peak dB}]   mic: {t0, step, db: []}
S.estimate = (frames, mic) => {
    if (!frames.length || !mic || !mic.db || mic.db.length < 50) return { ok: false, error: 'not enough data' };
    const clamp = v => Math.max(FLOOR, Math.min(0, v));
    // mic: 50 ms trailing max, to match the server's 50 ms peak detector
    const m = mic.db.map((_, i) => clamp(Math.max(...mic.db.slice(Math.max(0, i - 4), i + 1))));
    const micLoud = Math.max(...m);
    if (micLoud < -50) return { ok: false, error: 'the microphone heard almost nothing - is the music playing, and the phone near the speakers?' };
    const f = frames.slice().sort((a, b) => a.t - b.t);
    const at = t => {                           // frame peak at time t, linear between arrivals
        if (t <= f[0].t || t >= f[f.length - 1].t) return null;
        let lo = 0, hi = f.length - 1;
        while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (f[mid].t <= t) lo = mid; else hi = mid; }
        const a = f[lo], b = f[hi];
        // The server sends nothing while the level does not change (silence), so a
        // long gap means "still a's level" until just before b, not a slow ramp.
        if (b.t - a.t > 150) return clamp(t < b.t - 55 ? a.p : a.p + (b.p - a.p) * (t - (b.t - 55)) / 55);
        const k = (t - a.t) / (b.t - a.t || 1);
        return clamp(a.p + (b.p - a.p) * k);
    };
    // remove the slow part (1 s moving mean): what lines up is the dynamics, not the loudness
    const hp = xs => {
        const w = Math.round(1000 / STEP), out = new Array(xs.length);
        let sum = 0, n = 0;
        for (let i = 0; i < xs.length; i++) {
            sum += xs[i]; n++;
            if (i >= w) { sum -= xs[i - w]; n--; }
            out[i] = xs[i] - sum / n;
        }
        return out;
    };
    const mt = m.map((_, i) => mic.t0 + i * mic.step);
    const mh = hp(m);
    const corr = lag => {
        const xs = [], ys = [];
        for (let i = 0; i < mt.length; i++) {
            const v = at(mt[i] - lag);
            if (v !== null) { xs.push(mh[i]); ys.push(v); }
        }
        if (xs.length < 200) return null;
        const yh = hp(ys);
        let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0; const n = xs.length;
        for (let i = 0; i < n; i++) { sx += xs[i]; sy += yh[i]; sxx += xs[i] * xs[i]; syy += yh[i] * yh[i]; sxy += xs[i] * yh[i]; }
        const cov = sxy - sx * sy / n, vx = sxx - sx * sx / n, vy = syy - sy * sy / n;
        return vx > 0 && vy > 0 ? cov / Math.sqrt(vx * vy) : null;
    };
    const lags = [], rs = [];
    for (let lag = LAG_MIN; lag <= LAG_MAX; lag += STEP) { const r = corr(lag); if (r !== null) { lags.push(lag); rs.push(r); } }
    if (!rs.length) return { ok: false, error: 'the recording and the meters did not overlap' };
    let best = 0;
    rs.forEach((r, i) => { if (r > rs[best]) best = i; });
    // sub-step refinement (parabola through the peak and its neighbours)
    let lag = lags[best];
    if (best > 0 && best < rs.length - 1) {
        const a = rs[best - 1], b = rs[best], c = rs[best + 1], den = a - 2 * b + c;
        if (den < 0) lag += 0.5 * (a - c) / den * STEP;
    }
    const second = Math.max(-1, ...rs.filter((_, i) => Math.abs(lags[i] - lags[best]) > 150));
    const r = rs[best];
    const ok = r >= 0.4 && r - second >= 0.06;
    // for the log: the five highest local maxima of the correlation curve
    const peaks = rs.map((v, i) => ({ lag: lags[i], r: v }))
        .filter((p, i) => (i === 0 || rs[i - 1] <= p.r) && (i === rs.length - 1 || rs[i + 1] <= p.r))
        .sort((a, b) => b.r - a.r).slice(0, 5).map(p => `${p.lag} ms r=${p.r.toFixed(3)}`);
    return {
        diag: { micLoudDb: +micLoud.toFixed(1), lagsTried: lags.length, correlationPeaks: peaks },
        ok, lagMs: Math.round(lag), r: +r.toFixed(2), margin: +(r - second).toFixed(2),
        error: ok ? '' : (r < 0.4 ? 'the sound and the meters did not match well enough (music with clear beats works best)'
                                  : 'the match was ambiguous (a very regular beat can line up in more than one place)'),
    };
};

// Click track: match events, not shapes.  A click is a 50 ms blip on the meters but
// rings for a few hundred ms in a room, so instead of correlating the envelopes, find
// each click's onset in both and the one offset that pairs most of them - with
// irregular spacing only the right offset pairs many.
// The clicks are quiet (-30 dBFS, so normal listening volume is safe for the
// speakers) and the music is paused meanwhile, so both detectors work relative
// to the silence around them rather than to a fixed level: an onset is a jump of
// 10 dB (mic) / 15 dB (meters) over what came just before, above the room's
// noise floor (the 20th percentile of the whole recording).
S.estimateClicks = (frames, mic, expected = 14) => {
    if (!mic || !mic.db || mic.db.length < 50) return { ok: false, error: 'not enough data' };
    const f = frames.slice().sort((a, b) => a.t - b.t);
    const fOn = [];
    for (let i = 0; i < f.length; i++) {
        const prev = f[i - 1];
        if (f[i].p > -50 && (!prev || prev.p < f[i].p - 15 || f[i].t - prev.t > 150)) fOn.push(f[i].t);
    }
    const sorted = mic.db.slice().sort((a, b) => a - b);
    const micFloor = sorted[Math.floor(sorted.length * 0.2)];
    const micThreshold = Math.max(-80, micFloor + 10);
    const mOn = [];
    for (let i = 5; i < mic.db.length; i++) {
        const before = Math.min(...mic.db.slice(i - 5, i));
        if (mic.db[i] > micThreshold && mic.db[i] - before > 10 && (!mOn.length || mic.t0 + i * mic.step - mOn[mOn.length - 1] > 200))
            mOn.push(mic.t0 + i * mic.step);
    }
    const diag = { micFloorDb: +micFloor.toFixed(1), micThresholdDb: +micThreshold.toFixed(1),
                   micMaxDb: +sorted[sorted.length - 1].toFixed(1), meterOnsets: fOn, micOnsets: mOn };
    if (fOn.length < expected / 2) return { ok: false, diag, error: `the meters showed only ${fOn.length} of the ${expected} clicks` };
    if (mOn.length < expected / 2) return { ok: false, diag, error: `the microphone heard only ${mOn.length} of the ${expected} clicks - raise the volume a little, or hold the phone nearer the speakers` };
    const TOL = 40;
    const pairs = lag => {
        const d = [];
        for (const ft of fOn) {
            let best = null;
            for (const mt of mOn) { const e = mt - (ft + lag); if (Math.abs(e) <= TOL && (best === null || Math.abs(e) < Math.abs(best))) best = e; }
            if (best !== null) d.push(lag + best);
        }
        return d;
    };
    let best = { n: 0, lag: 0 }, second = 0;
    const scored = [];
    for (const mt of mOn) for (const ft of fOn) {
        const lag = mt - ft;
        if (lag < LAG_MIN || lag > LAG_MAX) continue;
        scored.push({ lag, n: pairs(lag).length });
    }
    scored.forEach(c => { if (c.n > best.n) best = c; });
    scored.forEach(c => { if (Math.abs(c.lag - best.lag) > 100) second = Math.max(second, c.n); });
    const d = pairs(best.lag).sort((a, b) => a - b);
    const lag = d.length ? d[Math.floor(d.length / 2)] : 0;
    const ok = best.n >= Math.max(6, Math.ceil(expected * 0.6)) && best.n - second >= 4;
    const seen = new Set();
    diag.candidates = scored.slice().sort((a, b) => b.n - a.n)
        .filter(c => { const k = Math.round(c.lag / 20); if (seen.has(k)) return false; seen.add(k); return true; })
        .slice(0, 6).map(c => `${Math.round(c.lag)} ms: ${c.n} paired`);
    diag.pairedLagsMs = d.map(Math.round);
    return { diag, ok, lagMs: Math.round(lag), r: +(best.n / expected).toFixed(2), margin: best.n - second, matched: best.n,
             error: ok ? '' : `only ${best.n} of ${expected} clicks lined up (next best ${second})` };
};

// ── the log ──────────────────────────────────────────────────────────────────
// Every run, manual or automatic, writes a complete plain-text log: each step with
// its time, the box's own delay model, the raw meter frames and microphone
// envelope, what the detector found and the verdict - enough to diagnose a run
// that went wrong from the text alone.  Shown live on Config (Meter timing), and
// the last one is kept on this device.  Times are ms since the run started.
const LOG = { lines: [], t0: 0, subs: new Set() };
S.onLog = fn => { LOG.subs.add(fn); return () => LOG.subs.delete(fn); };
S.lastLog = () => String(K.pref('sync.lastLog', '') || '');
const rel = wall => Math.round(wall - LOG.t0);
const logLine = text => {
    const line = `${String(rel(Date.now())).padStart(6)} ms  ${text}`;
    LOG.lines.push(line);
    LOG.subs.forEach(f => { try { f(line); } catch {} });
};
const logRaw = text => {
    LOG.lines.push(text);
    LOG.subs.forEach(f => { try { f(text); } catch {} });
};
const logDiag = diag => {
    if (!diag) return;
    for (const [k, v] of Object.entries(diag)) {
        const value = Array.isArray(v) && v.length && typeof v[0] === 'number' && v[0] > 1e12
            ? v.map(rel).join(' ')                             // wall times -> ms since start
            : Array.isArray(v) ? v.join(' | ') : String(v);
        logLine(`  ${k}: ${value || '(none)'}`);
    }
};
const stats = xs => {
    if (!xs.length) return 'none';
    const s = xs.slice().sort((a, b) => a - b), q = p => s[Math.min(s.length - 1, Math.floor(s.length * p))];
    return `min ${s[0].toFixed(1)}  p20 ${q(.2).toFixed(1)}  median ${q(.5).toFixed(1)}  p90 ${q(.9).toFixed(1)}  max ${s[s.length - 1].toFixed(1)}`;
};
const endLog = () => {
    const text = LOG.lines.join('\n');
    K.setPref('sync.lastLog', text);
    return text;
};

// ── one calibration run ──────────────────────────────────────────────────────
S.running = false;
S.calibrate = async ({ seconds = 10, onTick, clicks = false, why = 'manual' } = {}) => {
    if (S.running) return { ok: false, error: 'a calibration is already running' };
    LOG.lines = []; LOG.t0 = Date.now();
    logRaw(`OMDRC meter-timing calibration log - ${new Date(LOG.t0).toISOString()}`);
    logRaw(`mode: ${clicks ? 'precise (click track)' : 'on the music'} | trigger: ${why} | listening ${seconds} s`);
    logRaw(`page: ${location.host} | app bridge api ${window.OmdrcApp && window.OmdrcApp.apiVersion ? window.OmdrcApp.apiVersion() : 'none'} | ${navigator.userAgent}`);
    logRaw(`this device's extra display delay before the run: ${S.delayMs()} ms`);
    if (!S.canCalibrate()) {
        logLine('ABORT: no microphone bridge (calibration needs the OMDRC Android app)');
        endLog();
        return { ok: false, error: 'calibration needs the OMDRC Android app (microphone)' };
    }
    const box = await K.api('/spectrum/settings', { timeout: 4000 });
    if (box && box.ok !== false) {
        logRaw(`box: analyzer ${box.enabled ? 'on' : 'OFF'}, source ${box.source_active || box.source_now || '?'}, ${box.refresh_hz} Hz frames, vu ${box.vu_mode}, ` +
               `display delay base ${box.drc_delay_base_ms} ms + delta ${box.drc_delay_delta_ms} ms (auto-sync ${box.drc_delay_auto_sync ? 'on' : 'off'})`);
        logRaw(`box delay terms (ms): ${JSON.stringify(box.drc_delay_terms_ms || {})}`);
    } else logRaw(`box: /spectrum/settings failed (${(box && box.error) || 'no answer'})`);
    logRaw('');
    const frames = [];
    const prevTap = K.streamTap;
    K.streamTap = (mode, d, t) => {
        if (prevTap) prevTap(mode, d, t);
        if ((mode === 'vu' || mode === 'music' || mode === 'precision') && d.ok && d.vu) {
            const p = Math.max(Number(d.vu.left_peak ?? -120), Number(d.vu.right_peak ?? -120));
            if (Number.isFinite(p)) frames.push({ t, p });
        }
    };
    const hold = K.streams.open('vu', () => {});     // make sure level frames flow
    logLine('level stream (vu) opened; waiting 1.5 s for it to settle');
    S.running = true;
    let res = null;
    try {
        await new Promise(r => setTimeout(r, 1500));   // let the stream settle first
        logLine(`settled: ${frames.length} level frames so far`);
        const mic = await new Promise(resolve => {
            const timer = setTimeout(() => resolve({ ok: false, error: 'the microphone did not answer' }), (seconds + 15) * 1000);
            K.onMicEnvelope = res => { clearTimeout(timer); K.onMicEnvelope = null; if (res && res.ok) LAST_MIC.value = res; resolve(res); };
            let left = seconds;
            const tick = setInterval(() => { left -= 1; if (onTick) onTick(Math.max(0, left)); if (left <= 0) clearInterval(tick); }, 1000);
            if (onTick) onTick(left);
            logLine(`microphone: recording ${seconds * 1000} ms in ${STEP} ms steps (requested from the app)`);
            window.OmdrcApp.startMicEnvelope(seconds * 1000, STEP);
            // precise mode: the box plays its click track once the mic is listening
            if (clicks) setTimeout(async () => {
                logLine('click test: asking the box to pause the music and play the click track');
                const r = await K.api('/k/api/clicktest', { method: 'POST' });
                logLine(`click test: box answered ${JSON.stringify(r)}`);
                if (!r.ok) { clearTimeout(timer); K.onMicEnvelope = null; resolve({ ok: false, error: r.error || 'the click track could not be played' }); }
            }, 1000);
        });
        logLine(mic.ok
            ? `microphone: envelope received | source ${mic.source} | first step captured at ${rel(mic.t0)} ms | ${mic.db.length} steps of ${mic.step} ms`
            : `microphone: FAILED | ${mic.error || 'unknown error'}`);
        logLine('waiting 1.5 s for the last level frames');
        await new Promise(r => setTimeout(r, 1500));   // frames for the last instant of sound
        if (!mic.ok) { res = { ok: false, error: mic.error || 'microphone error' }; return res; }

        const gaps = frames.slice(1).map((f, i) => f.t - frames[i].t);
        logRaw('');
        logLine(`level frames: ${frames.length} from ${frames.length ? rel(frames[0].t) : '-'} to ${frames.length ? rel(frames[frames.length - 1].t) : '-'} ms`);
        logLine(`  arrival gaps (ms): ${stats(gaps)}`);
        logLine(`  peak (dBFS): ${stats(frames.map(f => f.p))}`);
        logLine(`microphone envelope (dBFS): ${stats(mic.db)}`);
        res = clicks ? S.estimateClicks(frames, mic) : S.estimate(frames, mic);
        logLine(`detector (${clicks ? 'click onsets' : 'envelope correlation'}):`);
        logDiag(res.diag);
        return res;
    } catch (e) {
        res = { ok: false, error: String(e && e.message || e) };
        logLine(`EXCEPTION: ${res.error}`);
        return res;
    } finally {
        S.running = false;
        hold.close();
        K.streamTap = prevTap;
        logRaw('');
        logLine(res && res.ok
            ? `RESULT: the meters led the sound by ${res.lagMs} ms (match ${res.r}, margin ${res.margin})`
            : `RESULT: failed | ${(res && res.error) || 'unknown'}${res && res.r !== undefined ? ` (match ${res.r})` : ''}`);
        logRaw('');
        logRaw(`--- level frames: arrival ms since start, peak dBFS (${frames.length}) ---`);
        logRaw(frames.map(f => `${rel(f.t)}:${f.p.toFixed(1)}`).join(' '));
        if (LAST_MIC.value) {
            const m = LAST_MIC.value;
            logRaw(`--- microphone envelope: first step at ${rel(m.t0)} ms, ${m.step} ms per value, peak dBFS (${m.db.length}) ---`);
            logRaw(m.db.map(v => Number(v).toFixed(1)).join(' '));
        }
        LAST_MIC.value = null;
        endLog();
    }
};
// the envelope of the run in progress, for the log's data section
const LAST_MIC = { value: null };

// ── automatic recalibration ──────────────────────────────────────────────────
// A track starting, or playback resuming after a pause, is the clearest possible
// cue: a jump from silence to sound.  When it happens on the Now page (and the user
// turned this on), listen for a few seconds and fold a confident result into the
// delay - median of the last three, applied when it moves by more than 15 ms.
const AUTO_EVERY_MS = 120000, SILENCE_DB = -55, SOUND_DB = -40, QUIET_FOR_MS = 1500;
let quietSince = null, lastAutoAt = 0;
const recent = [];
S.autoEnabled = () => S.canCalibrate() && !!K.pref('sync.auto', false);
S.onLevel = peak => {
    const now = Date.now();
    if (peak < SILENCE_DB) { if (quietSince === null) quietSince = now; return; }
    const wasQuiet = quietSince !== null && now - quietSince >= QUIET_FOR_MS;
    quietSince = null;
    if (wasQuiet && peak > SOUND_DB) S.autoRun('playback started');
};
S.onTrackChange = () => S.autoRun('new track');
S.autoRun = async why => {
    if (!S.autoEnabled() || S.running || Date.now() - lastAutoAt < AUTO_EVERY_MS) return;
    if (!(K.nowShown && K.nowShown())) return;
    lastAutoAt = Date.now();
    const res = await S.calibrate({ seconds: 8, why: `automatic (${why})` });
    const note = text => K.setPref('sync.lastLog', S.lastLog() + `\nAUTO: ${text}`);
    if (!res.ok || res.lagMs < 0 || res.r < 0.5) { note('not used (failed, negative, or match below 0.5)'); return; }
    recent.push(res.lagMs);
    if (recent.length > 3) recent.shift();
    const median = recent.slice().sort((a, b) => a - b)[Math.floor(recent.length / 2)];
    if (Math.abs(median - S.delayMs()) > 15) {
        note(`recent results ${recent.join(', ')} ms -> median ${median} ms applied (was ${S.delayMs()} ms)`);
        S.setDelayMs(median);
        K.toast(`Meter delay recalibrated (${why}): ${median} ms`);
    } else note(`recent results ${recent.join(', ')} ms -> median ${median} ms, within 15 ms of ${S.delayMs()} ms: unchanged`);
};
})();
