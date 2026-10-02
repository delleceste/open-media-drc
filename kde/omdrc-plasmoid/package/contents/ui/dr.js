// Dynamic range on the analyzer's 3-second blocks: the kiosk's maths and
// colours (omdrc-ctrl/src/kiosk/static/widgets/dr.js), line for line.
// A block is [[rmsL^2, rmsR^2], [peakL, peakR]].
.pragma library

var BLOCK_S = 3;
var LEVEL_FLOOR_DB = -40;
var WINDOWS = [60, 300, 900, 1800, 3600, 5400];    // the window chip cycles these

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function fromBlocks(blocks) {
    if (!blocks.length) return null;
    var channels = blocks[0][0].length, sum = 0;
    for (var ch = 0; ch < channels; ch++) {
        var rms2 = blocks.map(function (b) { return b[0][ch]; }).sort(function (a, b) { return b - a; });
        var peaks = blocks.map(function (b) { return b[1][ch]; }).sort(function (a, b) { return b - a; });
        var top = Math.max(1, Math.floor(blocks.length * 0.2));
        var rms = Math.sqrt(rms2.slice(0, top).reduce(function (a, b) { return a + b; }, 0) / top);
        var peak = peaks[Math.min(1, peaks.length - 1)];
        sum += rms > 0 && peak > 0 ? 20 * Math.log10(peak / rms) : 0;
    }
    return sum / channels;
}

// below -80 dBFS peak for a whole block: stopped, paused or silent
function silent(block) { return block[1].every(function (p) { return p <= 0.0001; }); }

function active(blocks) {
    var last = -1;
    for (var i = blocks.length - 1; i >= 0; i--) if (silent(blocks[i])) { last = i; break; }
    return blocks.slice(last + 1);
}

function levelDb(blocks) {
    var sum = 0, n = 0;
    blocks.forEach(function (b) { b[0].forEach(function (v) { sum += v; n++; }); });
    return n && sum > 0 ? 10 * Math.log10(sum / n) : -Infinity;
}

// red -> orange -> yellow -> progressively stronger greens, as HSL
// { bg: [h, s, l], fg: [h, s, l] } with h in degrees, s and l in percent
function hsl(value) {
    var stops = [[0, 0, 72, 56], [5, 7, 79, 57], [8, 27, 88, 60],
                 [10, 49, 88, 65], [12, 89, 65, 58], [14, 135, 61, 54]];
    var v = clamp(value, 0, 14);
    var up = stops.findIndex(function (s) { return s[0] >= v; });
    if (up <= 0) up = 1;
    var a = stops[up - 1], b = stops[up], f = (v - a[0]) / (b[0] - a[0]);
    var mix = function (i) { return a[i] + (b[i] - a[i]) * f; };
    return { bg: [mix(1), mix(2), mix(3)], fg: [mix(1), mix(2), 7] };
}

function windowLabel(seconds) {
    return seconds / 60 + " min";
}

function elapsedLabel(seconds) {
    var hh = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
    if (hh) return hh + " h " + String(m).padStart(2, "0") + " min";
    return m ? (s ? m + " min " + s + " s" : m + " min") : s + " s";
}

// The blocks the window covers: per song, no further back than the track.
function selected(blocks, trackAge, windowSeconds, perSong) {
    var want = windowSeconds / BLOCK_S;
    var count = perSong ? Math.min(want, trackAge) : want;
    return count > 0 ? blocks.slice(-count) : [];
}

// What to say and show (the kiosk's K.drEstimate.summary()).
function summary(frame, blocks, trackAge, windowSeconds, perSong) {
    var out = { value: null, exact: null, short: "Collecting…", sampled: 0 };
    if (!frame) return out;
    var d = frame.dr || {};
    var act = active(selected(blocks, trackAge, windowSeconds, perSong));
    var exact = act.length >= 2 ? fromBlocks(act) : null;
    out.sampled = act.length * BLOCK_S;
    if (!frame.ok) { out.short = "No audio"; return out; }
    if (frame.source !== "mpd" || d.state === "unavailable") { out.short = "Needs MPD stream"; return out; }
    if (d.state !== "ready" || exact === null) {
        out.short = d.playback_state === "pause" ? "Paused" : d.playback_state === "stop" ? "Stopped" : "Collecting…";
        return out;
    }
    out.exact = exact;
    out.value = Math.max(0, Math.round(exact));
    return out;
}

// The history bar's segments (the kiosk's K.DrBar.render): runs of audio split
// into as many cells as fit, a stop or silence one narrow gap of its own.
// Each: { flex, gap, value, fill (0..100), detail }.
function segments(blocks, width, minCell) {
    var count = blocks.length, runs = [];
    for (var i = 0; i < count;) {
        var sil = silent(blocks[i]), j = i;
        while (j < count && silent(blocks[j]) === sil) j++;
        runs.push({ start: i, end: j, silent: sil });
        i = j;
    }
    var gapRuns = runs.filter(function (r) { return r.silent; }).length;
    var audioBlocks = runs.reduce(function (n, r) { return n + (r.silent ? 0 : r.end - r.start); }, 0);
    var budget = Math.max(runs.length - gapRuns, Math.floor((width - gapRuns * 12) / minCell));
    var parts = [];
    runs.forEach(function (run) {
        if (run.silent) { parts.push(run); return; }
        var len = run.end - run.start;
        var n = Math.max(1, Math.min(len, Math.round(budget * len / audioBlocks)));
        for (var k = 0; k < n; k++)
            parts.push({ start: run.start + Math.floor(k * len / n), end: run.start + Math.floor((k + 1) * len / n), silent: false });
    });
    return parts.map(function (part) {
        var sample = blocks.slice(part.start, part.end);
        var value = sample.length && !part.silent ? fromBlocks(sample) : null;
        var level = value === null ? -Infinity : levelDb(sample);
        var from = Math.round((count - part.end) * BLOCK_S), to = Math.round((count - part.start) * BLOCK_S);
        return {
            flex: part.end - part.start,
            gap: part.silent,
            value: value,
            fill: value === null ? 0 : Math.max(8, Math.min(100, (1 - Math.max(level, LEVEL_FLOOR_DB) / LEVEL_FLOOR_DB) * 100)),
            detail: part.silent ? from + "–" + to + " s ago: stopped, paused or silent"
                  : value === null ? "" : from + "–" + to + " s ago: DR" + value.toFixed(1) + ", level " + level.toFixed(1) + " dB",
        };
    });
}
