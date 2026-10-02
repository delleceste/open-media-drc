import QtQuick
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import "dr.js" as DR

/* The DR history, as the kiosk draws it (widgets/dr.js, K.DrBar): the whole
 * window across the width, the audio so far in as many segments as fit, each
 * filled as high as its level and coloured by its DR, its value at the foot;
 * a stop, pause or silence is one narrow hatched gap.  Hover tells a
 * segment's details. */
Canvas {
    id: bar

    property var blocks: []
    property int minCell: 22

    readonly property color textColor: Kirigami.Theme.textColor
    readonly property bool light: Kirigami.Theme.backgroundColor.hslLightness > 0.5
    property var cells: []          // [{ x, w, detail }]

    onBlocksChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onTextColorChanged: requestPaint()

    function hsl(c) { return Qt.hsla(c[0] / 360, c[1] / 100, c[2] / 100, 1) }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        const W = width, H = height
        ctx.fillStyle = light ? "#e6eaef" : "#0a0e13"
        ctx.fillRect(0, 0, W, H)
        const parts = DR.segments(blocks, W, minCell)
        const out = []
        if (parts.length) {
            const gaps = parts.filter(p => p.gap).length
            const flexTotal = parts.reduce((s, p) => s + (p.gap ? 0 : p.flex), 0) || 1
            const free = W - gaps * 12 - (parts.length - 1)
            let x = 0
            for (const p of parts) {
                const w = p.gap ? 12 : Math.max(1, free * p.flex / flexTotal)
                if (p.gap) {
                    ctx.save()
                    ctx.beginPath(); ctx.rect(x, 0, w, H); ctx.clip()
                    ctx.fillStyle = light ? "#d3d9e0" : "#10151b"
                    ctx.fillRect(x, 0, w, H)
                    ctx.strokeStyle = light ? "#e3e7ec" : "#1b222b"
                    ctx.lineWidth = 3
                    for (let d = -H; d < w + H; d += 6) {
                        ctx.beginPath(); ctx.moveTo(x + d, H); ctx.lineTo(x + d + H, 0); ctx.stroke()
                    }
                    ctx.restore()
                } else if (p.value !== null) {
                    const col = DR.hsl(p.value)
                    const fh = H * p.fill / 100
                    ctx.fillStyle = hsl(col.bg)
                    ctx.fillRect(x, H - fh, w, fh)
                    if (w >= 12 && H >= 14) {
                        ctx.fillStyle = p.fill >= 58 ? hsl(col.fg) : textColor
                        ctx.font = Math.round(Math.min(11, H * 0.5)) + "px monospace"
                        ctx.textAlign = "center"; ctx.textBaseline = "bottom"
                        ctx.fillText(String(DR.clamp(Math.round(p.value), 0, 99)), x + w / 2, H - 1)
                    }
                }
                out.push({ x: x, w: w, detail: p.detail })
                x += w + 1
            }
        }
        cells = out
    }

    HoverHandler { id: hover }
    readonly property string hoverDetail: {
        if (!hover.hovered) return ""
        const px = hover.point.position.x
        for (const c of cells) if (px >= c.x && px < c.x + c.w) return c.detail
        return ""
    }
    PlasmaComponents.ToolTip.text: hoverDetail
    PlasmaComponents.ToolTip.visible: hoverDetail !== ""
    PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
}
