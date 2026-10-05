import QtQuick
import org.kde.kirigami as Kirigami
import "levels.js" as L

/* Two-channel level meters, as LED-style bars or analogue needles, with the
 * kiosk's ballistics: instant attack, knee-gated fall, a held peak. */
Canvas {
    id: meters

    property string style: "bars"     // "bars" | "needles"
    property var app: null
    property bool interactionsEnabled: true
    property string pair: "auto"       // channel lanes: "auto" | "side" | "stacked"
    property var vu: ({})              // the frame's vu object: left_rms, left_peak, ...
    property real glass: 1             // < 1: a cover behind the meters shows through

    readonly property bool vertical: pair === "side" ? true : pair === "stacked" ? false : height > width * 1.1
    readonly property var keys: ["left_rms", "right_rms", "left_peak", "right_peak"]
    property var disp: ({ left_rms: L.FLOOR, right_rms: L.FLOOR, left_peak: L.FLOOR, right_peak: L.FLOOR })
    property var hold: ({ left: { db: L.FLOOR, at: 0 }, right: { db: L.FLOOR, at: 0 } })
    property bool settled: false

    readonly property color textColor: Kirigami.Theme.textColor
    readonly property color faceColor: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.10)

    onVuChanged: { settled = false }
    onStyleChanged: requestPaint()
    onGlassChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onTextColorChanged: requestPaint()

    FrameAnimation {
        running: !meters.settled && meters.visible
        onTriggered: meters.step(Math.min(0.1, frameTime))
    }

    function target(key) {
        const v = Number(meters.vu ? meters.vu[key] : NaN)
        return Number.isFinite(v) ? v : L.FLOOR
    }

    function step(dt) {
        const now = Date.now()
        let still = true
        for (const k of keys) {
            const t = target(k)
            disp[k] = L.ease(disp[k], t, dt)
            if (Math.abs(disp[k] - t) > 0.05) still = false
        }
        for (const ch of ["left", "right"]) {
            const h = hold[ch], p = disp[ch + "_peak"]
            if (p >= h.db) { h.db = p; h.at = now }
            else if (now - h.at > L.HOLD_MS) h.db = Math.max(p, h.db - L.HOLD_FALL * dt)
            if (h.db > p + 0.05) still = false
        }
        settled = still
        requestPaint()
    }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        if (style === "needles") paintNeedles(ctx)
        else paintBars(ctx)
    }

    MouseArea {
        anchors.fill: parent
        enabled: meters.interactionsEnabled
        acceptedButtons: Qt.LeftButton
        onClicked: if (meters.app) meters.app.cycleMeterStyle()
        onWheel: (event) => { if (meters.app) meters.app.cycleMeterStyle(); event.accepted = true }
    }

    // ── bars ────────────────────────────────────────────────────────────────
    function paintBars(ctx) {
        const W = width, H = height
        // along: the meter's length; across: the room for both channels
        const along = vertical ? H : W, across = vertical ? W : H
        const labels = across >= 28 && along >= 60
        const scale = across >= 44 && along >= 140
        const lead = labels ? 12 : 1, tail = 2
        const scaleW = scale ? (vertical ? 20 : 12) : 0
        const rowW = (across - scaleW - 2) / 2
        const barW = Math.max(2, Math.min(rowW * 0.78, 40))
        const len = along - lead - tail
        // position along the meter for a level; vertical meters grow upwards
        const pos = db => lead + len * L.clamp(L.voltageFrac(db, L.SCALE_FLOOR), 0, 1)
        const rect = (a, c, la, lc) => vertical ? ctx.fillRect(c, H - a - la, lc, la)
                                                : ctx.fillRect(a, c, la, lc)
        const grad = vertical ? L.levelGradient(ctx, 0, H - lead, 0, H - lead - len)
                              : L.levelGradient(ctx, lead, 0, lead + len, 0)
        const seg = Math.max(2, len / 60), gap = Math.max(1, seg * 0.3)
        const rows = [["L", disp.left_peak, disp.left_rms, hold.left.db],
                      ["R", disp.right_peak, disp.right_rms, hold.right.db]]
        rows.forEach(([label, peak, rms, held], i) => {
            const c = 1 + rowW * i + (rowW - barW) / 2
            ctx.globalAlpha = glass
            ctx.fillStyle = faceColor
            rect(lead, c, len, barW)
            ctx.globalAlpha = 1
            ctx.fillStyle = grad
            const to = pos(peak)
            for (let a = lead; a < to; a += seg + gap)
                rect(a, c + 1, Math.min(seg, to - a), barW - 2)
            if (rms > L.SCALE_FLOOR) {
                ctx.fillStyle = textColor
                rect(pos(rms) - 1, c - 1, 2, barW + 2)
            }
            if (held > L.SCALE_FLOOR) {
                ctx.fillStyle = held > -1 ? "#ff5b52" : Qt.rgba(textColor.r, textColor.g, textColor.b, 0.7)
                rect(pos(held) - 1, c, 2, barW)
            }
            if (labels) {
                ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.6)
                ctx.font = "bold " + Math.round(Math.min(11, barW + 2)) + "px sans-serif"
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                if (vertical) ctx.fillText(label, c + barW / 2, H - lead / 2)
                else ctx.fillText(label, lead / 2, c + barW / 2)
            }
        })
        if (scale) {
            ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.55)
            ctx.font = "9px sans-serif"
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            const c = across - scaleW / 2
            for (const db of len >= 300 ? [-40, -20, -12, -6, -3, 0] : [-20, -12, -6, -3, 0]) {
                const a = pos(db)
                if (vertical) ctx.fillText(String(db), c, Math.min(H - 6, Math.max(6, H - a)))
                else ctx.fillText(String(db), Math.min(W - 8, Math.max(8, a)), c)
            }
        }
    }

    // ── needles ─────────────────────────────────────────────────────────────
    function paintNeedles(ctx) {
        const side = width >= height
        const gw = side ? width / 2 : width, gh = side ? height : height / 2
        paintGauge(ctx, 0, 0, gw, gh, "L", disp.left_rms, disp.left_peak, hold.left.db)
        paintGauge(ctx, side ? gw : 0, side ? 0 : gh, gw, gh, "R",
                   disp.right_rms, disp.right_peak, hold.right.db)
    }

    function paintGauge(ctx, x, y, w, h, label, rms, peak, held) {
        const A0 = -140, SPAN = 100                 // degrees, canvas convention
        const rad = d => d * Math.PI / 180
        const ang = db => rad(A0 + SPAN * L.voltageFrac(db, L.SCALE_FLOOR))
        const pad = Math.max(1, Math.min(w, h) * 0.04)
        // the painted area; the needle's pivot stays at least a pixel inside
        // its bottom edge, so the needle never starts under the clip
        const top = y + pad, bottom = y + h - pad
        // sized so the arc spans the width or the needle (0.97 R at full scale)
        // and its hub (0.018 R) the height, so nothing reaches past the clip;
        // a gauge narrower than its face is centred in it
        const R = Math.max(4, Math.min((w / 2 - pad) / Math.sin(rad(SPAN / 2)), (bottom - top - 2) / 0.99))
        const needleW = Math.max(1, R * 0.012), hubR = Math.max(1.5, needleW * 1.5)
        // on a pixel centre, so the needle's antialiased foot does not flicker
        const cx = Math.floor(x + w / 2) + 0.5
        const cy = Math.min(Math.floor(bottom - 1 - hubR),
                            Math.round(top + R + Math.max(0, (bottom - top - R) / 2))) + 0.5
        const small = R < 40

        ctx.globalAlpha = glass
        ctx.fillStyle = faceColor
        ctx.fillRect(x + pad, y + pad, w - 2 * pad, h - 2 * pad)
        ctx.globalAlpha = 1
        ctx.save()
        ctx.beginPath()
        ctx.rect(x + pad, y + pad, w - 2 * pad, h - 2 * pad)
        ctx.clip()

        const rScale = R * 0.86, rLvl = R * 0.74
        // the scale: plain to -6 dB, then red
        ctx.lineWidth = Math.max(1, R * 0.015)
        ctx.strokeStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.8)
        ctx.beginPath(); ctx.arc(cx, cy, rScale, ang(L.SCALE_FLOOR), ang(-6)); ctx.stroke()
        ctx.strokeStyle = "#f85149"
        ctx.lineWidth = Math.max(1.5, R * 0.03)
        ctx.beginPath(); ctx.arc(cx, cy, rScale, ang(-6), ang(0)); ctx.stroke()
        ctx.lineWidth = 1
        const ticks = small ? [-20, -6, 0] : [-40, -30, -20, -12, -9, -6, -3, 0]
        for (const db of ticks) {
            const a = ang(db), t = small ? 0.06 : 0.08
            ctx.strokeStyle = db > -6 ? "#f85149" : Qt.rgba(textColor.r, textColor.g, textColor.b, 0.8)
            ctx.beginPath()
            ctx.moveTo(cx + Math.cos(a) * rScale, cy + Math.sin(a) * rScale)
            ctx.lineTo(cx + Math.cos(a) * rScale * (1 + t), cy + Math.sin(a) * rScale * (1 + t))
            ctx.stroke()
            if (!small && [-20, -6, -3, 0].indexOf(db) >= 0) {
                ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.6)
                ctx.font = Math.round(Math.max(8, R * 0.08)) + "px sans-serif"
                ctx.textAlign = "center"; ctx.textBaseline = "middle"
                ctx.fillText(String(db), cx + Math.cos(a) * rScale * 0.88, cy + Math.sin(a) * rScale * 0.88)
            }
        }
        // the level: a soft band for the RMS, a brighter one for the peak
        const grad = L.levelGradient(ctx, cx - R, 0, cx + R, 0)
        ctx.strokeStyle = grad
        ctx.lineCap = "round"
        if (rms > L.SCALE_FLOOR) {
            ctx.globalAlpha = 0.45
            ctx.lineWidth = Math.max(2, R * 0.07)
            ctx.beginPath(); ctx.arc(cx, cy, rLvl, ang(L.SCALE_FLOOR), ang(rms)); ctx.stroke()
        }
        if (peak > L.SCALE_FLOOR) {
            ctx.globalAlpha = 1
            ctx.lineWidth = Math.max(1, R * 0.025)
            ctx.beginPath(); ctx.arc(cx, cy, rLvl, ang(L.SCALE_FLOOR), ang(peak)); ctx.stroke()
        }
        ctx.globalAlpha = 1
        if (held > L.SCALE_FLOOR) {
            const a = ang(held)
            ctx.fillStyle = held > -1 ? "#ff5b52" : textColor
            ctx.beginPath()
            ctx.arc(cx + Math.cos(a) * rLvl, cy + Math.sin(a) * rLvl, Math.max(1.5, R * 0.025), 0, 2 * Math.PI)
            ctx.fill()
        }
        // the needle follows the peak
        const a = ang(Math.max(L.SCALE_FLOOR, peak))
        ctx.strokeStyle = textColor
        ctx.lineWidth = needleW
        ctx.beginPath()
        ctx.moveTo(cx, cy)
        ctx.lineTo(cx + Math.cos(a) * R * 0.97, cy + Math.sin(a) * R * 0.97)
        ctx.stroke()
        // the hub the needle turns on
        ctx.fillStyle = textColor
        ctx.beginPath(); ctx.arc(cx, cy, hubR, 0, 2 * Math.PI); ctx.fill()
        ctx.restore()

        if (h >= 24) {
            ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.6)
            ctx.font = "bold " + Math.round(Math.max(8, Math.min(12, h * 0.14))) + "px sans-serif"
            ctx.textAlign = "left"; ctx.textBaseline = "bottom"
            ctx.fillText(label, x + pad + 3, y + h - pad - 2)
        }
    }
}
