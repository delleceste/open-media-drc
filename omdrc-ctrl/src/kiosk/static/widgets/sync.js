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
    return {
        ok, lagMs: Math.round(lag), r: +r.toFixed(2), margin: +(r - second).toFixed(2),
        error: ok ? '' : (r < 0.4 ? 'the sound and the meters did not match well enough (music with clear beats works best)'
                                  : 'the match was ambiguous (a very regular beat can line up in more than one place)'),
    };
};

// Click track: match events, not shapes.  A click is a 50 ms blip on the meters but
// rings for a few hundred ms in a room, so instead of correlating the envelopes, find
// each click's onset in both and the one offset that pairs most of them - with
// irregular spacing only the right offset pairs many.
S.estimateClicks = (frames, mic, expected = 14) => {
    if (!mic || !mic.db || mic.db.length < 50) return { ok: false, error: 'not enough data' };
    const f = frames.slice().sort((a, b) => a.t - b.t);
    const fOn = [];
    for (let i = 0; i < f.length; i++) {
        const prev = f[i - 1];
        if (f[i].p > -40 && (!prev || prev.p < -55 || f[i].t - prev.t > 150)) fOn.push(f[i].t);
    }
    const mOn = [];
    for (let i = 5; i < mic.db.length; i++) {
        const floor = Math.min(...mic.db.slice(i - 5, i));
        if (mic.db[i] > -45 && mic.db[i] - floor > 12 && (!mOn.length || mic.t0 + i * mic.step - mOn[mOn.length - 1] > 200))
            mOn.push(mic.t0 + i * mic.step);
    }
    if (fOn.length < expected / 2) return { ok: false, error: `the meters showed only ${fOn.length} of the ${expected} clicks` };
    if (mOn.length < expected / 2) return { ok: false, error: `the microphone heard only ${mOn.length} of the ${expected} clicks - louder, or the phone nearer the speakers` };
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
    return { ok, lagMs: Math.round(lag), r: +(best.n / expected).toFixed(2), margin: best.n - second, matched: best.n,
             error: ok ? '' : `only ${best.n} of ${expected} clicks lined up (next best ${second})` };
};

// ── one calibration run ──────────────────────────────────────────────────────
S.running = false;
S.calibrate = async ({ seconds = 10, onTick, clicks = false } = {}) => {
    if (S.running) return { ok: false, error: 'a calibration is already running' };
    if (!S.canCalibrate()) return { ok: false, error: 'calibration needs the OMDRC Android app (microphone)' };
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
    S.running = true;
    try {
        await new Promise(r => setTimeout(r, 1500));   // let the stream settle first
        const mic = await new Promise(resolve => {
            const timer = setTimeout(() => resolve({ ok: false, error: 'the microphone did not answer' }), (seconds + 15) * 1000);
            K.onMicEnvelope = res => { clearTimeout(timer); K.onMicEnvelope = null; resolve(res); };
            let left = seconds;
            const tick = setInterval(() => { left -= 1; if (onTick) onTick(Math.max(0, left)); if (left <= 0) clearInterval(tick); }, 1000);
            if (onTick) onTick(left);
            window.OmdrcApp.startMicEnvelope(seconds * 1000, STEP);
            // precise mode: the box plays its click track once the mic is listening
            if (clicks) setTimeout(async () => {
                const r = await K.api('/k/api/clicktest', { method: 'POST' });
                if (!r.ok) { clearTimeout(timer); K.onMicEnvelope = null; resolve({ ok: false, error: r.error || 'the click track could not be played' }); }
            }, 1000);
        });
        await new Promise(r => setTimeout(r, 1500));   // frames for the last instant of sound
        if (!mic.ok) return { ok: false, error: mic.error || 'microphone error' };
        return clicks ? S.estimateClicks(frames, mic) : S.estimate(frames, mic);
    } finally {
        S.running = false;
        hold.close();
        K.streamTap = prevTap;
    }
};

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
    const res = await S.calibrate({ seconds: 8 });
    if (!res.ok || res.lagMs < 0 || res.r < 0.5) return;
    recent.push(res.lagMs);
    if (recent.length > 3) recent.shift();
    const median = recent.slice().sort((a, b) => a - b)[Math.floor(recent.length / 2)];
    if (Math.abs(median - S.delayMs()) > 15) {
        S.setDelayMs(median);
        K.toast(`Meter delay recalibrated (${why}): ${median} ms`);
    }
};
})();
