import QtQuick
import org.kde.kirigami as Kirigami
import "levels.js" as L

/* Band spectrum (left/right per band) with falling peak caps, after the
 * kiosk's widgets/spectrum.js.  Narrow slots draw one bar of the louder
 * channel instead of a pair. */
Canvas {
    id: spectrum

    property var bands: []         // [{freq, label, lo, hi}]
    property var leftDb: []          // dB per band
    property var rightDb: []
    property real floorDb: -40     // the box's spectrum floor (/spectrum/settings)
    property real glass: 1

    readonly property int capHoldMs: 900
    readonly property real capFall: 14
    property var l: []
    property var r: []
    property var capL: []
    property var capR: []
    property var capAt: []
    property bool settled: true

    readonly property color textColor: Kirigami.Theme.textColor

    onLeftDbChanged: settled = false
    onRightDbChanged: settled = false
    onBandsChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onTextColorChanged: requestPaint()
    onGlassChanged: requestPaint()

    FrameAnimation {
        running: !spectrum.settled && spectrum.visible
        onTriggered: spectrum.step(Math.min(0.1, frameTime))
    }

    function step(dt) {
        const n = bands.length, now = Date.now(), fl = floorDb - 5
        let still = true
        for (let i = 0; i < n; i++) {
            const tl = Number.isFinite(leftDb[i]) ? leftDb[i] : fl
            const tr = Number.isFinite(rightDb[i]) ? rightDb[i] : fl
            l[i] = Math.max(tl, (l[i] ?? fl) - L.SPECTRUM_FALL * dt)
            r[i] = Math.max(tr, (r[i] ?? fl) - L.SPECTRUM_FALL * dt)
            if (Math.abs(l[i] - tl) > 0.1 || Math.abs(r[i] - tr) > 0.1) still = false
            for (const [cap, v] of [[capL, l[i]], [capR, r[i]]]) {
                if (v >= (cap[i] ?? fl)) { cap[i] = v; capAt[i] = now }
                else if (now - (capAt[i] || 0) > capHoldMs) cap[i] = Math.max(v, cap[i] - capFall * dt)
                if (cap[i] > v + 0.1) still = false
            }
        }
        settled = still
        requestPaint()
    }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        const n = bands.length
        const W = width, H = height
        ctx.globalAlpha = glass
        ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.06)
        ctx.fillRect(0, 0, W, H)
        ctx.globalAlpha = 1
        if (!n) return
        const labels = H >= 70 && W >= 160
        const labelH = labels ? 12 : 0, top = 2, plotH = H - labelH - top
        const fl = floorDb
        const y = db => top + plotH * (1 - L.clamp((db - fl) / (0 - fl), 0, 1))
        if (plotH >= 40) {
            ctx.strokeStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.12)
            ctx.lineWidth = 1
            for (let db = -10; db > fl; db -= 10) {
                ctx.beginPath()
                ctx.moveTo(0, Math.round(y(db)) + 0.5)
                ctx.lineTo(W, Math.round(y(db)) + 0.5)
                ctx.stroke()
            }
        }
        const grad = ctx.createLinearGradient(0, top + plotH, 0, top)
        grad.addColorStop(0, "#1f6feb"); grad.addColorStop(0.45, "#3fb950")
        grad.addColorStop(0.8, "#d8c23a"); grad.addColorStop(1, "#f85149")
        const slot = W / n, pair = slot >= 6
        const bw = pair ? Math.max(2, slot * 0.38) : Math.max(1, slot * 0.75)
        const gap = pair ? Math.max(1, slot * 0.04) : 0
        const capH = plotH >= 40 ? 2 : 1
        const every = Math.max(3, Math.ceil(36 / slot))     // a label per this many bands
        for (let i = 0; i < n; i++) {
            const x0 = i * slot + (slot - (pair ? 2 * bw + gap : bw)) / 2
            const cols = pair ? [[l[i], capL[i], x0], [r[i], capR[i], x0 + bw + gap]]
                              : [[Math.max(l[i] ?? -200, r[i] ?? -200),
                                  Math.max(capL[i] ?? -200, capR[i] ?? -200), x0]]
            for (const [v, cap, x] of cols) {
                if (Number.isFinite(v) && v > fl) {
                    ctx.fillStyle = grad
                    ctx.fillRect(x, y(v), bw, top + plotH - y(v))
                }
                if (Number.isFinite(cap) && cap > fl) {
                    ctx.fillStyle = textColor
                    ctx.fillRect(x, y(cap) - capH, bw, capH)
                }
            }
            if (labels && i % every === 0) {
                ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.55)
                ctx.font = "9px sans-serif"
                ctx.textAlign = "center"; ctx.textBaseline = "top"
                const label = bands[i].label
                const half = ctx.measureText(label).width / 2
                const center = i * slot + slot / 2
                ctx.fillText(label, Math.max(half + 2, Math.min(W - half - 2, center)), H - labelH + 1)
            }
        }
    }
}
