import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

/* Channel balance, as on the kiosk (widgets/balance.js): the power average of
 * left vs right RMS over a window, on a ±6 dB scale, in either look -- "split"
 * (grey left half, red right half, a pointer) or "bar" (a dark track and a bar
 * from the centre towards the heavier side).  A click on the track swaps the
 * looks; the chips do too, and cycle the window.  `compact`: the track alone. */
Item {
    id: view

    required property var app
    property bool compact: false

    readonly property real db: app.balanceDb          // NaN: nothing to average
    readonly property bool ok: Number.isFinite(db)
    readonly property real pos: ok ? Math.max(-1, Math.min(1, db / 6)) : 0
    readonly property bool split: app.balanceLook !== "bar"
    readonly property bool light: Kirigami.Theme.backgroundColor.hslLightness > 0.5
    readonly property color leftColor: light ? "#8c959f" : "#d3d9e0"
    readonly property color rightColor: "#e5484d"
    readonly property color rightText: light ? "#cf222e" : "#ff7a7f"
    readonly property string readout: !ok ? "—" : Math.abs(db) < 0.05 ? i18n("Centre 0.0 dB")
        : (db < 0 ? "L " : "R ") + Math.abs(db).toFixed(1) + " dB"
    // a short window glides fast, a long one slowly (the kiosk's applySpeed)
    readonly property int glide: app.balanceWindow <= 0.3 ? 60 : app.balanceWindow <= 1 ? 120 : 250

    ColumnLayout {
        anchors.fill: parent
        spacing: view.compact ? 0 : Kirigami.Units.smallSpacing / 2

        RowLayout {
            visible: !view.compact
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.Label { text: i18n("Balance"); opacity: 0.7; font.bold: true }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: view.readout
                font.family: "monospace"
                color: view.ok && view.db > 0.05 ? view.rightText
                     : view.ok && view.db < -0.05 ? view.leftColor : Kirigami.Theme.textColor
                elide: Text.ElideRight
            }
            PlasmaComponents.ToolButton {
                flat: false
                font: Kirigami.Theme.smallFont
                text: view.split ? i18n("Split") : i18n("Bar")
                onClicked: view.app.toggleBalanceLook()
            }
            PlasmaComponents.ToolButton {
                flat: false
                font: Kirigami.Theme.smallFont
                text: view.app.balanceWindow + " s"
                onClicked: view.app.cycleBalanceWindow()
            }
        }

        // the track
        Item {
            id: track
            Layout.fillWidth: true
            Layout.fillHeight: view.compact
            Layout.preferredHeight: view.compact ? -1 : Kirigami.Units.gridUnit * 0.9
            Layout.leftMargin: view.compact ? 2 : 3
            Layout.rightMargin: view.compact ? 2 : 3

            Rectangle {
                anchors.fill: parent
                radius: Math.min(height / 2, Kirigami.Units.cornerRadius)
                border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.25)
                clip: true
                color: view.split ? "transparent" : (view.light ? "#d0d7de" : "#000000")
                Row {
                    visible: view.split
                    anchors.fill: parent
                    anchors.margins: 1
                    Rectangle { width: parent.width / 2; height: parent.height; color: view.leftColor }
                    Rectangle { width: parent.width / 2; height: parent.height; color: view.rightColor }
                }
                // bar look: from the centre towards the heavier side
                Rectangle {
                    visible: !view.split
                    height: parent.height - 2
                    y: 1
                    radius: height / 3
                    width: Math.abs(view.pos) * parent.width / 2
                    x: view.pos >= 0 ? parent.width / 2 : parent.width / 2 - width
                    color: view.pos > 0 ? view.rightColor : view.leftColor
                    Behavior on width { NumberAnimation { duration: view.glide } }
                    Behavior on x { NumberAnimation { duration: view.glide } }
                }
                Rectangle {          // the centre
                    x: parent.width / 2 - width / 2
                    width: view.split ? 2 : 1
                    height: parent.height
                    color: view.split ? Qt.rgba(0, 0, 0, 0.55) : Kirigami.Theme.disabledTextColor
                }
            }
            // split look: the pointer
            Rectangle {
                visible: view.split
                width: Math.max(4, Math.min(parent.height * 0.6, 11))
                height: parent.height + Math.min(8, parent.height * 0.6)
                y: (parent.height - height) / 2
                x: parent.width * (0.5 + 0.5 * view.pos) - width / 2
                radius: width / 2.5
                color: "#2f7dff"
                opacity: view.ok ? 1 : 0.3
                border.color: Kirigami.Theme.backgroundColor
                border.width: 1.5
                Behavior on x { NumberAnimation { duration: view.glide } }
            }
            MouseArea {
                anchors.fill: parent
                visible: !view.compact
                cursorShape: Qt.PointingHandCursor
                onClicked: view.app.toggleBalanceLook()
            }
        }

        RowLayout {        // L  −6  0  +6  R
            visible: !view.compact
            Layout.fillWidth: true
            spacing: 0
            PlasmaComponents.Label { text: "L"; color: view.leftColor; font.bold: true; font.pixelSize: Kirigami.Theme.smallFont.pixelSize * 1.1 }
            Item { Layout.fillWidth: true }
            PlasmaComponents.Label { text: "−6"; opacity: 0.7; font: Kirigami.Theme.smallFont }
            Item { Layout.fillWidth: true }
            PlasmaComponents.Label { text: "0"; opacity: 0.7; font: Kirigami.Theme.smallFont }
            Item { Layout.fillWidth: true }
            PlasmaComponents.Label { text: "+6"; opacity: 0.7; font: Kirigami.Theme.smallFont }
            Item { Layout.fillWidth: true }
            PlasmaComponents.Label { text: "R"; color: view.rightColor; font.bold: true; font.pixelSize: Kirigami.Theme.smallFont.pixelSize * 1.1 }
        }
    }
}
