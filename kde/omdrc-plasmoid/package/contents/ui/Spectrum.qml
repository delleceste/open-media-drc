import QtQuick
import org.kde.kirigami as Kirigami
import "levels.js" as L

/* Toggle between separate channel plots and paired bars with falling peak caps. */
Canvas {
    id: spectrum

    property var bands: []         // [{freq, label, lo, hi}]
    property var leftDb: []          // dB per band
    property var rightDb: []
    property real floorDb: -40     // the box's spectrum floor (/spectrum/settings)
    property real glass: 1
    property bool splitChannels: true
    property bool peakHold: true
    property bool clickEnabled: true
    signal layoutToggled()

    readonly property int capHoldMs: 900
    readonly property real capFall: 14
    property var l: []
    property var r: []
    property var capL: []
    property var capR: []
    property var capAtL: []
    property var capAtR: []
    property bool settled: true

    readonly property color textColor: Kirigami.Theme.textColor

    onLeftDbChanged: settled = false
    onRightDbChanged: settled = false
    onBandsChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onTextColorChanged: requestPaint()
    onGlassChanged: requestPaint()
    onSplitChannelsChanged: requestPaint()
    onPeakHoldChanged: {
        if (peakHold) {
            capL = l.slice(); capR = r.slice()
            capAtL = []; capAtR = []
        }
        requestPaint()
    }

    TapHandler {
        enabled: spectrum.clickEnabled
        acceptedButtons: Qt.LeftButton
        acceptedModifiers: Qt.NoModifier
        onTapped: spectrum.layoutToggled()
    }

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
            if (peakHold) {
                for (const [cap, at, v] of [[capL, capAtL, l[i]], [capR, capAtR, r[i]]]) {
                    if (v >= (cap[i] ?? fl)) { cap[i] = v; at[i] = now }
                    else if (now - (at[i] || 0) > capHoldMs) cap[i] = Math.max(v, cap[i] - capFall * dt)
                    if (cap[i] > v + 0.1) still = false
                }
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
        const gap = splitChannels ? (W >= 80 ? 6 : 2) : 0
        const plotW = splitChannels ? Math.max(0, (W - gap) / 2) : W
        const titleH = H >= 28 && plotW >= 18 ? 13 : 0
        const labels = H >= 70 && plotW >= 160
        const labelH = labels ? 12 : 0, top = titleH + 2
        const plotH = Math.max(0, H - labelH - top)
        const fl = floorDb
        const y = db => top + plotH * (1 - L.clamp((db - fl) / (0 - fl), 0, 1))
        const leftGrad = ctx.createLinearGradient(0, top + plotH, 0, top)
        leftGrad.addColorStop(0, "#59616b"); leftGrad.addColorStop(0.55, "#bdc5ce")
        leftGrad.addColorStop(1, "#ffffff")
        const rightGrad = ctx.createLinearGradient(0, top + plotH, 0, top)
        rightGrad.addColorStop(0, "#4a1414"); rightGrad.addColorStop(0.55, "#c83030")
        rightGrad.addColorStop(1, "#ff4545")
        const slot = n ? plotW / n : 0
        const bw = Math.max(1, slot * (splitChannels ? 0.75 : 0.38))
        const barGap = Math.max(1, slot * 0.04)
        const capH = plotH >= 40 ? 2 : 1
        const every = Math.max(3, Math.ceil(36 / Math.max(1, slot)))
        const panels = splitChannels
            ? [["L", l, capL, 0, leftGrad, "#ffffff"],
               ["R", r, capR, plotW + gap, rightGrad, "#ff4545"]]
            : [["", l, capL, 0, leftGrad, "#ffffff"]]
        for (const [channel, values, caps, offset, panelGrad, peakColor] of panels) {
            ctx.globalAlpha = glass
            ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.06)
            ctx.fillRect(offset, 0, plotW, H)
            ctx.globalAlpha = 1
            if (titleH) {
                ctx.font = "10px sans-serif"
                ctx.textAlign = "left"; ctx.textBaseline = "top"
                if (splitChannels) {
                    ctx.fillStyle = textColor
                    ctx.fillText(channel, offset + 2, 1)
                } else {
                    ctx.fillStyle = textColor
                    ctx.fillText("L", 2, 1)
                    ctx.fillText("R", 16, 1)
                }
            }
            if (plotH >= 40) {
                ctx.strokeStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.12)
                ctx.lineWidth = 1
                for (let db = -10; db > fl; db -= 10) {
                    ctx.beginPath()
                    ctx.moveTo(offset, Math.round(y(db)) + 0.5)
                    ctx.lineTo(offset + plotW, Math.round(y(db)) + 0.5)
                    ctx.stroke()
                }
            }
            for (let i = 0; i < n; i++) {
                const x = offset + i * slot + (slot - (splitChannels ? bw : 2 * bw + barGap)) / 2
                const bars = splitChannels
                    ? [[values[i], caps[i], x, panelGrad, peakColor]]
                    : [[l[i], capL[i], x, leftGrad, "#ffffff"],
                       [r[i], capR[i], x + bw + barGap, rightGrad, "#ff4545"]]
                for (const [v, cap, barX, color, capColor] of bars) {
                    if (Number.isFinite(v) && v > fl) {
                        ctx.fillStyle = color
                        ctx.fillRect(barX, y(v), bw, top + plotH - y(v))
                        if (bw >= 2) {
                            ctx.strokeStyle = "rgba(0,0,0,0.35)"
                            ctx.lineWidth = 1
                            ctx.strokeRect(barX, y(v), bw, top + plotH - y(v))
                        }
                    }
                    if (peakHold && Number.isFinite(cap) && cap > fl) {
                        ctx.fillStyle = capColor
                        ctx.fillRect(barX, y(cap) - capH, bw, capH)
                    }
                }
                if (labels && i % every === 0) {
                    ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.55)
                    ctx.font = "9px sans-serif"
                    ctx.textAlign = "center"; ctx.textBaseline = "top"
                    const label = bands[i].label
                    const half = ctx.measureText(label).width / 2
                    const center = i * slot + slot / 2
                    ctx.fillText(label, offset + Math.max(half + 2, Math.min(plotW - half - 2, center)), H - labelH + 1)
                }
            }
        }
    }
}
