import QtQuick
import org.kde.kirigami as Kirigami
import "levels.js" as L

/* Toggle between separate channel plots and paired bars with falling peak caps.
 *
 * The bars and caps are scene-graph rectangles, drawn on the GPU and only moved
 * at each display frame; the panels, grid and labels are painted on a Canvas
 * (CPU, uploaded as a texture) only when the layout, the bands or the theme
 * change - never for a level. */
Item {
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
    onBandsChanged: relayout()
    onWidthChanged: relayout()
    onHeightChanged: relayout()
    onTextColorChanged: relayout()
    onGlassChanged: relayout()
    onSplitChannelsChanged: relayout()
    onFloorDbChanged: relayout()
    onPeakHoldChanged: {
        if (peakHold) {
            capL = l.slice(); capR = r.slice()
            capAtL = []; capAtR = []
        }
        update()
    }
    Component.onCompleted: relayout()

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
        update()
    }

    // The plot's geometry, worked out once per layout.
    property var geo: null
    property bool relayoutPending: false
    function relayout() {
        if (relayoutPending) return
        relayoutPending = true
        Qt.callLater(() => {
            relayoutPending = false
            geo = geometry()
            back.requestPaint()
            update()
        })
    }

    function geometry() {
        const n = bands.length
        const W = width, H = height
        const gap = splitChannels ? (W >= 80 ? 6 : 2) : 0
        const plotW = splitChannels ? Math.max(0, (W - gap) / 2) : W
        const titleH = H >= 28 && plotW >= 18 ? 13 : 0
        const labels = H >= 70 && plotW >= 160
        const labelH = labels ? 12 : 0, top = titleH + 2
        const plotH = Math.max(0, H - labelH - top)
        const slot = n ? plotW / n : 0
        const bw = Math.max(1, slot * (splitChannels ? 0.75 : 0.38))
        const barGap = Math.max(1, slot * 0.04)
        // bar k: channel k < n ? left : right, band k % n
        const xs = []
        for (let ch = 0; ch < 2; ch++)
            for (let i = 0; i < n; i++)
                xs.push(splitChannels
                    ? ch * (plotW + gap) + i * slot + (slot - bw) / 2
                    : i * slot + (slot - (2 * bw + barGap)) / 2 + ch * (bw + barGap))
        return { n: n, W: W, H: H, gap: gap, plotW: plotW, titleH: titleH, labels: labels,
                 labelH: labelH, top: top, plotH: plotH, slot: slot, bw: bw, xs: xs,
                 capH: plotH >= 40 ? 2 : 1, fl: floorDb }
    }

    function yOf(g, db) { return g.top + g.plotH * (1 - L.clamp((db - g.fl) / (0 - g.fl), 0, 1)) }
    // a gradient stop: the level's place on the plot, top (0 dB) at 0
    function stopAt(db) { return 1 - L.clamp((db - floorDb) / (0 - floorDb), 0, 1) }

    // Levels and caps to the bars, as they stand now.
    function update() {
        const g = geo
        if (!g || bars.count !== 2 * g.n) return
        for (let k = 0; k < 2 * g.n; k++) {
            const right = k >= g.n, i = k % g.n
            const v = right ? r[i] : l[i], cap = right ? capR[i] : capL[i]
            const bar = bars.itemAt(k)
            if (!bar) continue
            const lit = Number.isFinite(v) && v > g.fl
            bar.visible = lit
            if (lit) {
                const top = yOf(g, v)
                bar.x = g.xs[k]
                bar.y = top
                bar.width = g.bw
                bar.height = g.top + g.plotH - top
            }
            const c = caps.itemAt(k)
            if (!c) continue
            c.visible = peakHold && Number.isFinite(cap) && cap > g.fl
            if (c.visible) {
                c.x = g.xs[k]
                c.y = yOf(g, cap) - g.capH
                c.width = g.bw
                c.height = g.capH
            }
        }
    }

    Canvas {
        id: back
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const g = spectrum.geo
            if (!g) return
            const textColor = spectrum.textColor
            const every = Math.max(3, Math.ceil(36 / Math.max(1, g.slot)))
            const panels = spectrum.splitChannels ? [["L", 0], ["R", g.plotW + g.gap]] : [["", 0]]
            for (const [channel, offset] of panels) {
                ctx.globalAlpha = spectrum.glass
                ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.06)
                ctx.fillRect(offset, 0, g.plotW, g.H)
                ctx.globalAlpha = 1
                if (g.titleH) {
                    ctx.font = "10px sans-serif"
                    ctx.textAlign = "left"; ctx.textBaseline = "top"
                    ctx.fillStyle = textColor
                    if (spectrum.splitChannels) {
                        ctx.fillText(channel, offset + 2, 1)
                    } else {
                        ctx.fillText("L", 2, 1)
                        ctx.fillText("R", 16, 1)
                    }
                }
                if (g.plotH >= 40) {
                    ctx.strokeStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.12)
                    ctx.lineWidth = 1
                    for (let db = -10; db > g.fl; db -= 10) {
                        ctx.beginPath()
                        ctx.moveTo(offset, Math.round(spectrum.yOf(g, db)) + 0.5)
                        ctx.lineTo(offset + g.plotW, Math.round(spectrum.yOf(g, db)) + 0.5)
                        ctx.stroke()
                    }
                }
                if (g.labels) {
                    ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.55)
                    ctx.font = "9px sans-serif"
                    ctx.textAlign = "center"; ctx.textBaseline = "top"
                    for (let i = 0; i < g.n; i += every) {
                        const label = spectrum.bands[i].label
                        const half = ctx.measureText(label).width / 2
                        const center = i * g.slot + g.slot / 2
                        ctx.fillText(label, offset + Math.max(half + 2, Math.min(g.plotW - half - 2, center)), g.H - g.labelH + 1)
                    }
                }
            }
        }
    }

    // green -> amber -> red up the plot; the right channel a shade apart
    Gradient {
        id: leftGradient
        GradientStop { position: 0; color: "#ff2d2d" }
        GradientStop { position: spectrum.stopAt(-1); color: "#f85149" }
        GradientStop { position: spectrum.stopAt(-4); color: "#e3892b" }
        GradientStop { position: spectrum.stopAt(-9); color: "#d8c23a" }
        GradientStop { position: spectrum.stopAt(-18); color: "#3fb950" }
        GradientStop { position: 1; color: "#1f8f3a" }
    }
    Gradient {
        id: rightGradient
        GradientStop { position: 0; color: "#ff2828" }
        GradientStop { position: spectrum.stopAt(-1); color: "#f34a43" }
        GradientStop { position: spectrum.stopAt(-4); color: "#ec772d" }
        GradientStop { position: spectrum.stopAt(-9); color: "#e1b230" }
        GradientStop { position: spectrum.stopAt(-18); color: "#55af4d" }
        GradientStop { position: 1; color: "#388f35" }
    }

    // A bar: the plot-high gradient, shown from the level down.
    Repeater {
        id: bars
        model: 2 * spectrum.bands.length
        delegate: Item {
            required property int index
            clip: true
            visible: false
            Rectangle {
                y: (spectrum.geo ? spectrum.geo.top : 0) - parent.y
                width: parent.width
                height: spectrum.geo ? spectrum.geo.plotH : 0
                gradient: index >= spectrum.bands.length ? rightGradient : leftGradient
            }
            Rectangle {
                anchors.fill: parent
                visible: parent.width >= 2
                color: "transparent"
                border { width: 1; color: Qt.rgba(0, 0, 0, 0.35) }
            }
        }
        onCountChanged: spectrum.update()
    }

    Repeater {
        id: caps
        model: 2 * spectrum.bands.length
        delegate: Rectangle {
            required property int index
            visible: false
            color: index >= spectrum.bands.length ? "#ff4545" : "#ffffff"
        }
        onCountChanged: spectrum.update()
    }
}
