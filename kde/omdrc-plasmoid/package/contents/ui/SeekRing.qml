import QtQuick
import org.kde.kirigami as Kirigami

/* Cover seek control: start at twelve o'clock and move clockwise.  A drag
 * starts only on the ring; the knob follows the nearest angle across the
 * start/end seam, so crossing twelve o'clock cannot jump through the song. */
Item {
    id: ring
    required property var app
    property bool dragging: false
    property real dragFraction: 0
    property real grabFraction: 0
    readonly property real duration: Number(app.player.duration) || 0
    readonly property real elapsed: Number(app.player.elapsed) || 0
    readonly property real fraction: dragging ? dragFraction : duration > 0 ? Math.max(0, Math.min(1, elapsed / duration)) : 0
    readonly property real radius: Math.min(width, height) * 0.43

    function clock(seconds) {
        const n = Math.max(0, Math.floor(seconds))
        return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0")
    }
    function angleAt(x, y) {
        const a = Math.atan2(x - width / 2, -(y - height / 2)) / (2 * Math.PI)
        return (a + 1) % 1
    }
    function near(a, previous) {
        const choices = [a - 1, a, a + 1]
        return choices.reduce((best, item) => Math.abs(item - previous) < Math.abs(best - previous) ? item : best)
    }

    Canvas {
        id: drawing
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const r = ring.radius, cx = width / 2, cy = height / 2
            if (r < 8) return
            ctx.lineWidth = Math.max(3, r * 0.09)
            ctx.strokeStyle = Qt.rgba(0, 0, 0, 0.72)
            ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke()
            ctx.strokeStyle = Kirigami.Theme.highlightColor
            ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * ring.fraction); ctx.stroke()
            const angle = ring.fraction * 2 * Math.PI
            ctx.fillStyle = "white"
            ctx.beginPath()
            ctx.arc(cx + r * Math.sin(angle), cy - r * Math.cos(angle), Math.max(4, r * 0.09), 0, 2 * Math.PI)
            ctx.fill()
        }
        Connections {
            target: ring
            function onFractionChanged() { drawing.requestPaint() }
            function onWidthChanged() { drawing.requestPaint() }
            function onHeightChanged() { drawing.requestPaint() }
        }
    }
    Text {
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
        text: ring.clock(ring.fraction * ring.duration) + " / " + ring.clock(ring.duration)
        color: "white"
        style: Text.Outline
        styleColor: "black"
        font.pixelSize: Math.max(8, Math.min(12, ring.height * 0.1))
    }
    MouseArea {
        anchors.fill: parent
        enabled: ring.duration > 0
        onPressed: (mouse) => {
            const d = Math.hypot(mouse.x - width / 2, mouse.y - height / 2)
            if (Math.abs(d - ring.radius) > Math.max(12, ring.radius * 0.18)) { mouse.accepted = false; return }
            ring.grabFraction = ring.fraction
            ring.dragFraction = ring.grabFraction
            ring.dragging = true
        }
        onPositionChanged: (mouse) => {
            if (!ring.dragging) return
            ring.dragFraction = Math.max(0, Math.min(1, ring.near(ring.angleAt(mouse.x, mouse.y), ring.dragFraction)))
        }
        onReleased: {
            if (!ring.dragging) return
            const target = ring.dragFraction * ring.duration
            ring.dragging = false
            ring.app.seek(target)
        }
        onCanceled: ring.dragging = false
    }
}
