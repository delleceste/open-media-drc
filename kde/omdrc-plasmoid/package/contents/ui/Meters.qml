import QtQuick
import QtQuick.Shapes
import org.kde.kirigami as Kirigami
import "levels.js" as L

/* Two-channel level meters, as LED-style bars or analogue needles, with the
 * kiosk's ballistics: instant attack, knee-gated fall, a held peak.
 *
 * What moves is drawn by the scene graph, on the GPU: the lit segments are a
 * still picture revealed by a clipping item, the markers and the needle are
 * rectangles, the level bands are shapes.  Only what stands still - faces,
 * scales, labels, the segments themselves - is painted, on a Canvas, and only
 * when the size, the style or the theme changes: a Canvas is drawn by the CPU
 * and uploaded as a texture, too costly to redo at every display frame. */
Item {
    id: meters

    property string style: "bars"     // "bars" | "needles"
    property var app: null
    property bool interactionsEnabled: true
    property string pair: "auto"       // channel lanes: "auto" | "side" | "stacked"
    property var vu: ({})              // the frame's vu object: left_rms, left_peak, ...
    property real glass: 1             // < 1: a cover behind the meters shows through
    property bool paused: false        // the animations paused: needles say so under 0 dB

    readonly property bool vertical: pair === "side" ? true : pair === "stacked" ? false : height > width * 1.1
    readonly property bool needles: style === "needles"
    readonly property var keys: ["left_rms", "right_rms", "left_peak", "right_peak"]
    property var disp: ({ left_rms: L.FLOOR, right_rms: L.FLOOR, left_peak: L.FLOOR, right_peak: L.FLOOR })
    property var hold: ({ left: { db: L.FLOOR, at: 0 }, right: { db: L.FLOOR, at: 0 } })
    property bool settled: false

    readonly property color textColor: Kirigami.Theme.textColor
    readonly property color faceColor: Qt.rgba(textColor.r, textColor.g, textColor.b, 0.10)

    // The geometry of the still parts, worked out once per layout.
    property var geo: null

    onVuChanged: { settled = false }
    onStyleChanged: relayout()
    onVerticalChanged: relayout()
    onGlassChanged: relayout()
    onWidthChanged: relayout()
    onHeightChanged: relayout()
    onTextColorChanged: relayout()
    Component.onCompleted: relayout()

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
        for (let i = 0; i < keys.length; i++) {      // indexed: no iterator per frame
            const k = keys[i], t = target(k)
            disp[k] = L.ease(disp[k], t, dt)
            if (Math.abs(disp[k] - t) > 0.05) still = false
        }
        for (let i = 0; i < 2; i++) {
            const h = i ? hold.right : hold.left, p = i ? disp.right_peak : disp.left_peak
            if (p >= h.db) { h.db = p; h.at = now }
            else if (now - h.at > L.HOLD_MS) h.db = Math.max(p, h.db - L.HOLD_FALL * dt)
            if (h.db > p + 0.05) still = false
        }
        settled = still
        update()
    }

    // Size, style or theme changed: the still parts are worked out and painted
    // again (once, at the end of the current event), then the moving ones placed.
    property bool relayoutPending: false
    function relayout() {
        if (relayoutPending) return
        relayoutPending = true
        Qt.callLater(() => {
            relayoutPending = false
            geo = needles ? needleGeometry() : barGeometry()
            under.requestPaint()
            over.requestPaint()
            for (let i = 0; i < 2; i++) {
                const row = rows.itemAt(i)
                if (row) row.lit.requestPaint()
            }
            update()
        })
    }

    // The moving parts, placed for the levels on display now.
    function update() {
        if (!geo || geo.needles !== needles) return          // a relayout is on its way
        for (let i = 0; i < 2; i++) {
            const rms = i ? disp.right_rms : disp.left_rms
            const peak = i ? disp.right_peak : disp.left_peak
            const held = i ? hold.right.db : hold.left.db
            if (needles) {
                const g = gauges.itemAt(i)
                if (g) placeGauge(g, geo.gauges[i], rms, peak, held)
            } else {
                const row = rows.itemAt(i)
                if (row) placeBar(row, geo.rows[i], rms, peak, held)
            }
        }
    }

    Canvas {
        id: under
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (!meters.geo) return
            if (meters.geo.needles) meters.geo.gauges.forEach(g => meters.paintGaugeFace(ctx, g))
            else meters.paintBarFace(ctx, meters.geo)
        }
    }

    MouseArea {
        anchors.fill: parent
        enabled: meters.interactionsEnabled
        acceptedButtons: Qt.LeftButton
        onClicked: if (meters.app) meters.app.cycleMeterStyle()
        onWheel: (event) => { if (meters.app) meters.app.cycleMeterStyle(); event.accepted = true }
    }

    // ── bars ────────────────────────────────────────────────────────────────
    function barGeometry() {
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
        return {
            needles: false, W: W, H: H, vertical: vertical, across: across, labels: labels, scale: scale,
            lead: lead, len: len, scaleW: scaleW, barW: barW,
            seg: Math.max(2, len / 60), gap: Math.max(1, Math.max(2, len / 60) * 0.3),
            rows: [0, 1].map(i => ({ label: i ? "R" : "L", c: 1 + rowW * i + (rowW - barW) / 2 })),
        }
    }

    // position along the meter for a level; vertical meters grow upwards
    function barPos(g, db) { return g.lead + g.len * L.clamp(L.voltageFrac(db, L.SCALE_FLOOR), 0, 1) }
    // a rectangle given along/across the meter, in item coordinates
    function barRect(g, a, c, la, lc) {
        return g.vertical ? { x: c, y: g.H - a - la, w: lc, h: la } : { x: a, y: c, w: la, h: lc }
    }

    function paintBarFace(ctx, g) {
        const rect = (a, c, la, lc) => { const r = barRect(g, a, c, la, lc); ctx.fillRect(r.x, r.y, r.w, r.h) }
        for (const row of g.rows) {
            ctx.globalAlpha = glass
            ctx.fillStyle = faceColor
            rect(g.lead, row.c, g.len, g.barW)
            ctx.globalAlpha = 1
            if (g.labels) {
                ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.6)
                ctx.font = "bold " + Math.round(Math.min(11, g.barW + 2)) + "px sans-serif"
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                if (g.vertical) ctx.fillText(row.label, row.c + g.barW / 2, g.H - g.lead / 2)
                else ctx.fillText(row.label, g.lead / 2, row.c + g.barW / 2)
            }
        }
        if (g.scale) {
            ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.55)
            ctx.font = "9px sans-serif"
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            const c = g.across - g.scaleW / 2
            for (const db of g.len >= 300 ? [-40, -20, -12, -6, -3, 0] : [-20, -12, -6, -3, 0]) {
                const a = barPos(g, db)
                if (g.vertical) ctx.fillText(String(db), c, Math.min(g.H - 6, Math.max(6, g.H - a)))
                else ctx.fillText(String(db), Math.min(g.W - 8, Math.max(8, a)), c)
            }
        }
    }

    // Every segment of a row lit, painted once: the level shows as much of it
    // as its clip lets through.
    function paintBarSegments(ctx, g, row) {
        const grad = g.vertical ? L.levelGradient(ctx, 0, g.H - g.lead, 0, g.H - g.lead - g.len)
                                : L.levelGradient(ctx, g.lead, 0, g.lead + g.len, 0)
        ctx.fillStyle = grad
        const to = g.lead + g.len
        for (let a = g.lead; a < to; a += g.seg + g.gap) {
            const r = barRect(g, a, row.c + 1, Math.min(g.seg, to - a), g.barW - 2)
            ctx.fillRect(r.x, r.y, r.w, r.h)
        }
    }

    function setRect(item, r) { item.x = r.x; item.y = r.y; item.width = r.w; item.height = r.h }
    // the same as setRect(item, barRect(...)), without an object per call
    function setBarRect(item, g, a, c, la, lc) {
        if (g.vertical) { item.x = c; item.y = g.H - a - la; item.width = lc; item.height = la }
        else { item.x = a; item.y = c; item.width = la; item.height = lc }
    }

    function placeBar(item, row, rms, peak, held) {
        const g = geo
        const to = barPos(g, peak)
        setBarRect(item.clipper, g, g.lead, row.c + 1, Math.max(0, to - g.lead), g.barW - 2)
        item.rmsMark.visible = rms > L.SCALE_FLOOR
        if (item.rmsMark.visible) setBarRect(item.rmsMark, g, barPos(g, rms) - 1, row.c - 1, 2, g.barW + 2)
        item.holdMark.visible = held > L.SCALE_FLOOR
        if (item.holdMark.visible) {
            setBarRect(item.holdMark, g, barPos(g, held) - 1, row.c, 2, g.barW)
            item.holdRed = held > -1
        }
    }

    Repeater {
        id: rows
        model: meters.needles ? 0 : 2
        delegate: Item {
            id: row
            required property int index
            anchors.fill: parent
            property alias clipper: clipper
            property alias lit: lit
            property alias rmsMark: rmsMark
            property alias holdMark: holdMark
            Item {
                id: clipper
                clip: true
                Canvas {
                    id: lit
                    x: -clipper.x
                    y: -clipper.y
                    width: meters.width
                    height: meters.height
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        if (meters.geo && !meters.geo.needles) meters.paintBarSegments(ctx, meters.geo, meters.geo.rows[row.index])
                    }
                }
            }
            Rectangle { id: rmsMark; color: meters.textColor; visible: false }
            property bool holdRed: false
            Rectangle {
                id: holdMark
                visible: false
                color: row.holdRed ? "#ff5b52" : Qt.rgba(meters.textColor.r, meters.textColor.g, meters.textColor.b, 0.7)
            }
            Component.onCompleted: { lit.requestPaint(); meters.update() }
        }
    }

    // ── needles ─────────────────────────────────────────────────────────────
    readonly property real gaugeA0: -140                // degrees, canvas convention
    readonly property real gaugeSpan: 100
    function rad(d) { return d * Math.PI / 180 }
    function gaugeDeg(db) { return gaugeA0 + gaugeSpan * L.voltageFrac(db, L.SCALE_FLOOR) }

    function needleGeometry() {
        const side = width >= height
        const gw = side ? width / 2 : width, gh = side ? height : height / 2
        return { needles: true, gauges: [gaugeGeometry(0, 0, gw, gh, "L"),
                          gaugeGeometry(side ? gw : 0, side ? 0 : gh, gw, gh, "R")] }
    }

    function gaugeGeometry(x, y, w, h, label) {
        const pad = Math.max(1, Math.min(w, h) * 0.04)
        // the painted area; the needle's pivot stays at least a pixel inside
        // its bottom edge, so the needle never starts under the clip
        const top = y + pad, bottom = y + h - pad
        // sized so the arc spans the width or the needle (0.97 R at full scale)
        // and its hub (0.018 R) the height, so nothing reaches past the clip;
        // a gauge narrower than its face is centred in it
        const R = Math.max(4, Math.min((w / 2 - pad) / Math.sin(rad(gaugeSpan / 2)), (bottom - top - 2) / 0.99))
        const needleW = Math.max(1, R * 0.012), hubR = Math.max(1.5, needleW * 1.5)
        // on a pixel centre, so the needle's antialiased foot does not flicker
        const cx = Math.floor(x + w / 2) + 0.5
        const cy = Math.min(Math.floor(bottom - 1 - hubR),
                            Math.round(top + R + Math.max(0, (bottom - top - R) / 2))) + 0.5
        return { x: x, y: y, w: w, h: h, label: label, pad: pad, R: R, needleW: needleW, hubR: hubR,
                 cx: cx, cy: cy, small: R < 40, rScale: R * 0.86, rLvl: R * 0.74,
                 // the clip: the face, in item coordinates
                 fx: x + pad, fy: y + pad, fw: w - 2 * pad, fh: h - 2 * pad }
    }

    function paintGaugeFace(ctx, g) {
        const ang = db => rad(gaugeDeg(db))
        const R = g.R, cx = g.cx, cy = g.cy, rScale = g.rScale, small = g.small
        ctx.globalAlpha = glass
        ctx.fillStyle = faceColor
        ctx.fillRect(g.fx, g.fy, g.fw, g.fh)
        ctx.globalAlpha = 1
        ctx.save()
        ctx.beginPath()
        ctx.rect(g.fx, g.fy, g.fw, g.fh)
        ctx.clip()
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
        ctx.restore()
    }

    function paintGaugeLabel(ctx, g) {
        if (g.h < 24) return
        ctx.fillStyle = Qt.rgba(textColor.r, textColor.g, textColor.b, 0.6)
        ctx.font = "bold " + Math.round(Math.max(8, Math.min(12, g.h * 0.14))) + "px sans-serif"
        ctx.textAlign = "left"; ctx.textBaseline = "bottom"
        ctx.fillText(g.label, g.x + g.pad + 3, g.y + g.h - g.pad - 2)
    }

    // An arc of radius r and width w from the scale's floor to `deg`, with round
    // ends, as one simple outline: the outer edge, the end cap, the inner edge
    // back, the start cap.  It never crosses itself, so the renderer has no
    // intersections to resolve, and nothing is rebuilt from a string.
    function setBand(band, g, deg, r, w) {
        const ox = g.cx - g.fx, oy = g.cy - g.fy                   // centre, in the clip
        const h = w / 2, a0 = gaugeA0, a1 = Math.max(deg, a0 + 0.01)
        const c0 = rad(a0), c1 = rad(a1)
        band.outer.centerX = ox; band.outer.centerY = oy
        band.outer.radiusX = band.outer.radiusY = r + h
        band.outer.startAngle = a0; band.outer.sweepAngle = a1 - a0
        band.endCap.centerX = ox + Math.cos(c1) * r; band.endCap.centerY = oy + Math.sin(c1) * r
        band.endCap.radiusX = band.endCap.radiusY = h
        band.endCap.startAngle = a1
        band.inner.centerX = ox; band.inner.centerY = oy
        band.inner.radiusX = band.inner.radiusY = Math.max(0, r - h)
        band.inner.startAngle = a1; band.inner.sweepAngle = a0 - a1
        band.startCap.centerX = ox + Math.cos(c0) * r; band.startCap.centerY = oy + Math.sin(c0) * r
        band.startCap.radiusX = band.startCap.radiusY = h
        band.startCap.startAngle = a0 + 180
    }

    function placeGauge(item, g, rms, peak, held) {
        const R = g.R
        if (item.x !== g.fx || item.width !== g.fw) {
            item.x = g.fx; item.y = g.fy; item.width = g.fw; item.height = g.fh
        }
        // the level: a soft band for the RMS, a brighter one for the peak
        item.rmsBand.visible = rms > L.SCALE_FLOOR
        if (item.rmsBand.visible) setBand(item.rmsBand, g, gaugeDeg(rms), g.rLvl, Math.max(2, R * 0.07))
        item.peakBand.visible = peak > L.SCALE_FLOOR
        if (item.peakBand.visible) setBand(item.peakBand, g, gaugeDeg(peak), g.rLvl, Math.max(1, R * 0.025))
        item.gradStart = g.cx - R - g.fx
        item.gradEnd = g.cx + R - g.fx
        item.holdDot.visible = held > L.SCALE_FLOOR
        if (item.holdDot.visible) {
            const a = rad(gaugeDeg(held)), d = Math.max(1.5, R * 0.025)
            item.holdDot.width = item.holdDot.height = 2 * d
            item.holdDot.x = g.cx + Math.cos(a) * g.rLvl - d - g.fx
            item.holdDot.y = g.cy + Math.sin(a) * g.rLvl - d - g.fy
            item.holdRed = held > -1
        }
        // the needle follows the peak: up from its pivot, turned to the level
        const len = R * 0.97
        item.needle.width = g.needleW
        item.needle.height = len
        item.needle.x = g.cx - g.needleW / 2 - g.fx
        item.needle.y = g.cy - len - g.fy
        item.needleTurn.angle = gaugeDeg(Math.max(L.SCALE_FLOOR, peak)) + 90
        // the hub the needle turns on
        item.hub.width = item.hub.height = 2 * g.hubR
        item.hub.x = g.cx - g.hubR - g.fx
        item.hub.y = g.cy - g.hubR - g.fy
        // "Paused" under the 0 dB mark's label, kept inside the face
        const zero = rad(gaugeDeg(0)), size = Math.round(Math.max(8, R * 0.08))
        const zx = g.cx + Math.cos(zero) * g.rScale * 0.88, zy = g.cy + Math.sin(zero) * g.rScale * 0.88
        item.pausedNote.font.pixelSize = size
        item.pausedNote.x = Math.max(0, Math.min(g.fw - item.pausedNote.implicitWidth - 2,
                                                 zx - item.pausedNote.implicitWidth / 2 - g.fx))
        item.pausedNote.y = zy + size * (g.small ? 0.4 : 0.9) - g.fy
    }

    component LevelGradient: LinearGradient {
        GradientStop { position: 0; color: "#1f8f3a" }
        GradientStop { position: L.clamp(L.voltageFrac(-18, L.SCALE_FLOOR), 0, 1); color: "#3fb950" }
        GradientStop { position: L.clamp(L.voltageFrac(-9, L.SCALE_FLOOR), 0, 1); color: "#d8c23a" }
        GradientStop { position: L.clamp(L.voltageFrac(-4, L.SCALE_FLOOR), 0, 1); color: "#e3892b" }
        GradientStop { position: L.clamp(L.voltageFrac(-1, L.SCALE_FLOOR), 0, 1); color: "#f85149" }
        GradientStop { position: 1; color: "#ff2d2d" }
    }

    // A level band (setBand): four arcs, each continuing where the last ended.
    component LevelBand: Shape {
        id: band
        property real gradStart: 0
        property real gradEnd: 1
        property alias outer: outer
        property alias endCap: endCap
        property alias inner: inner
        property alias startCap: startCap
        anchors.fill: parent
        visible: false
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeWidth: -1
            pathHints: ShapePath.PathSolid | ShapePath.PathNonIntersecting
            fillGradient: LevelGradient { x1: band.gradStart; x2: band.gradEnd; y1: 0; y2: 0 }
            PathAngleArc { id: outer; moveToStart: true }
            PathAngleArc { id: endCap; moveToStart: false; sweepAngle: 180 }
            PathAngleArc { id: inner; moveToStart: false }
            PathAngleArc { id: startCap; moveToStart: false; sweepAngle: 180 }
        }
    }

    Repeater {
        id: gauges
        model: meters.needles ? 2 : 0
        delegate: Item {
            id: gauge
            clip: true
            property real gradStart: 0
            property real gradEnd: 1
            property alias rmsBand: rmsBand
            property alias peakBand: peakBand
            property alias holdDot: holdDot
            property alias needle: needle
            property alias needleTurn: needleTurn
            property alias hub: hub
            property alias pausedNote: pausedNote
            LevelBand { id: rmsBand; opacity: 0.45; gradStart: gauge.gradStart; gradEnd: gauge.gradEnd }
            LevelBand { id: peakBand; gradStart: gauge.gradStart; gradEnd: gauge.gradEnd }
            property bool holdRed: false
            Rectangle {
                id: holdDot
                radius: width / 2
                antialiasing: true
                visible: false
                color: gauge.holdRed ? "#ff5b52" : meters.textColor
            }
            Rectangle {
                id: needle
                color: meters.textColor
                antialiasing: true
                // turns on the foot of the needle (bindings: set again only on resize)
                transform: Rotation { id: needleTurn; origin.x: needle.width / 2; origin.y: needle.height }
            }
            Rectangle { id: hub; radius: width / 2; color: meters.textColor; antialiasing: true }
            Text {
                id: pausedNote
                visible: meters.paused
                text: i18n("Paused")
                color: meters.textColor
                opacity: 0.8
                onImplicitWidthChanged: meters.update()
            }
            Component.onCompleted: meters.update()
        }
    }

    Canvas {
        id: over
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (meters.geo && meters.geo.needles) meters.geo.gauges.forEach(g => meters.paintGaugeLabel(ctx, g))
        }
    }
}
