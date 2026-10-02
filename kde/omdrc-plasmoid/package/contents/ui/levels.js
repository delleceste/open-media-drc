// Scales and ballistics shared by the meters and the spectrum; the same
// numbers as the kiosk's widgets (omdrc-ctrl/src/kiosk/static/widgets).
.pragma library

var FLOOR = -120;           // "no signal"
var SCALE_FLOOR = -60;      // left end of the meter scale
var KNEE = 8, FALL_FAST = 120, FALL_SLOW = 45;     // dB, dB/s, dB/s
var HOLD_MS = 1500, HOLD_FALL = 24;                // peak-hold mark
var SPECTRUM_FALL = 30;                            // dB/s

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// 0..1 along a scale that is linear in voltage, as the kiosk meters are
function voltageFrac(db, floor) {
    var c = clamp(Number(db), floor, 0);
    var a = function (v) { return Math.pow(10, v / 20); };
    return (a(c) - a(floor)) / (1 - a(floor));
}

// instant attack; a fast glide for small drops, slow for large ones
function ease(disp, tgt, dt) {
    if (tgt >= disp) return tgt;
    var rate = disp - tgt <= KNEE ? FALL_FAST : FALL_SLOW;
    return Math.max(tgt, disp - rate * dt);
}

// green -> amber -> red along a meter running from p0 to p1
function levelGradient(ctx, x0, y0, x1, y1) {
    var g = ctx.createLinearGradient(x0, y0, x1, y1);
    var stop = function (db) { return clamp(voltageFrac(db, SCALE_FLOOR), 0, 1); };
    g.addColorStop(0, "#1f8f3a");
    g.addColorStop(stop(-18), "#3fb950");
    g.addColorStop(stop(-9), "#d8c23a");
    g.addColorStop(stop(-4), "#e3892b");
    g.addColorStop(stop(-1), "#f85149");
    g.addColorStop(1, "#ff2d2d");
    return g;
}
