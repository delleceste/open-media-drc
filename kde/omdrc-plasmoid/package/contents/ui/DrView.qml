import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import "dr.js" as DR

/* Live dynamic range, as on the kiosk's Now page: "DR12" in its colour, what
 * it rests on, the 0..14+ gauge and the window chip; with `history` also the
 * segmented bar and Per song / Continuous.  `compact`: the value alone, for a
 * panel. */
Item {
    id: view

    required property var app
    property bool compact: false
    property bool strip: false
    property bool history: true      // the bar (and its mode switch) inside this view

    readonly property var summary: app.drSummary
    readonly property color valueColor: summary.value === null ? Kirigami.Theme.disabledTextColor
                                        : hsl(DR.hsl(summary.value).bg)
    readonly property string valueText: summary.value === null ? "—"
                                        : summary.value > 14 ? "DR14+" : "DR" + summary.value

    function hsl(c) { return Qt.hsla(c[0] / 360, c[1] / 100, c[2] / 100, 1) }

    // ── panel: the value, its colour, and a thin gauge ──────────────────────
    Item {
        anchors.fill: parent
        visible: view.compact
        PlasmaComponents.Label {
            anchors { left: parent.left; right: parent.right; top: parent.top; bottom: thinGauge.top }
            text: view.summary.value === null ? "DR —" : view.valueText
            color: view.valueColor
            font.bold: true
            fontSizeMode: Text.Fit
            minimumPixelSize: 6
            font.pixelSize: height
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        Gauge {
            id: thinGauge
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: Math.max(3, parent.height * 0.14)
            value: view.summary.value
        }
    }

    // ── desktop and popup ───────────────────────────────────────────────────
    ColumnLayout {
        anchors.fill: parent
        visible: !view.compact && !view.strip
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.Label { text: "DR"; opacity: 0.7; font.bold: true }
            PlasmaComponents.Label {
                text: view.valueText
                color: view.valueColor
                font.pixelSize: Kirigami.Theme.defaultFont.pixelSize * 1.8
                font.bold: true
            }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: view.summary.value === null ? view.summary.short : i18n("%1 s sampled", view.summary.sampled)
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
            }
            Chip {
                visible: !view.app.drPerSong
                text: DR.windowLabel(view.app.drWindow)
                tip: i18n("DR window: click for the next")
                onClicked: view.app.cycleDrWindow()
            }
        }
        Gauge {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(6, Kirigami.Units.smallSpacing * 2)
            value: view.summary.value
        }
        DrBar {
            visible: view.history
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: Kirigami.Units.gridUnit * 1.5
            blocks: view.app.drSelected
        }
        RowLayout {
            visible: view.history
            Layout.fillWidth: true
            PlasmaComponents.Label {
                text: view.app.drSelected.length ? "−" + DR.elapsedLabel(view.app.drSelected.length * DR.BLOCK_S) : ""
                font: Kirigami.Theme.smallFont
                opacity: 0.7
            }
            Item { Layout.fillWidth: true }
            ModeChip { text: i18n("Per song"); selected: view.app.drPerSong; onClicked: view.app.setConfig("drPerSong", true) }
            ModeChip { text: i18n("Continuous"); selected: !view.app.drPerSong; onClicked: view.app.setConfig("drPerSong", false) }
            Item { Layout.fillWidth: true }
            PlasmaComponents.Label { text: i18n("Latest"); font: Kirigami.Theme.smallFont; opacity: 0.7 }
        }
    }

    // A shallow desktop footer: the history receives most of the width.
    ColumnLayout {
        anchors.fill: parent
        visible: view.strip
        spacing: 1
        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.Label { text: view.valueText; color: view.valueColor; font.bold: true }
            PlasmaComponents.Label {
                visible: view.width >= 360
                Layout.fillWidth: true
                text: view.summary.value === null ? view.summary.short : i18n("%1 s sampled", view.summary.sampled)
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
            }
            Chip {
                visible: !view.app.drPerSong && view.width >= 210
                text: DR.windowLabel(view.app.drWindow)
                tip: i18n("DR window: click for the next")
                onClicked: view.app.cycleDrWindow()
            }
            ModeChip {
                visible: view.width >= 300
                text: view.app.drPerSong ? i18n("Per song") : i18n("Continuous")
                selected: true
                tip: i18n("Switch DR history mode")
                onClicked: view.app.setConfig("drPerSong", !view.app.drPerSong)
            }
        }
        DrBar {
            Layout.fillWidth: true
            Layout.fillHeight: true
            blocks: view.app.drSelected
        }
    }

    // a coloured 0..14+ track with a marker (the kiosk's K.drGauge)
    component Gauge: Item {
        property var value: null
        Rectangle {
            anchors.fill: parent
            radius: height / 2
            opacity: 0.9
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: view.hsl(DR.hsl(0).bg) }
                GradientStop { position: 5 / 14; color: view.hsl(DR.hsl(5).bg) }
                GradientStop { position: 8 / 14; color: view.hsl(DR.hsl(8).bg) }
                GradientStop { position: 10 / 14; color: view.hsl(DR.hsl(10).bg) }
                GradientStop { position: 12 / 14; color: view.hsl(DR.hsl(12).bg) }
                GradientStop { position: 1; color: view.hsl(DR.hsl(14).bg) }
            }
        }
        Rectangle {
            visible: parent.value !== null
            width: Math.max(2, parent.height * 0.45)
            height: parent.height * 1.7
            radius: width / 2
            y: (parent.height - height) / 2
            x: DR.clamp((parent.value ?? 0) / 14, 0, 1) * parent.width - width / 2
            color: "white"
            border.color: "black"
            border.width: 1
            Behavior on x { NumberAnimation { duration: 300 } }
        }
    }

    component Chip: PlasmaComponents.ToolButton {
        property string tip
        flat: false
        font: Kirigami.Theme.smallFont
        PlasmaComponents.ToolTip.text: tip
        PlasmaComponents.ToolTip.visible: hovered && tip !== ""
        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
    }

    // Quiet mode controls: transparent, with a hairline outline and small type.
    component ModeChip: PlasmaComponents.ToolButton {
        property bool selected: false
        property string tip: ""
        flat: true
        font.pixelSize: Math.max(8, Math.round(Kirigami.Theme.smallFont.pixelSize * 0.8))
        implicitWidth: contentItem.implicitWidth + 10
        implicitHeight: contentItem.implicitHeight + 4
        opacity: selected ? 0.9 : 0.6
        background: Rectangle {
            color: "transparent"
            radius: 3
            border.width: 1
            border.color: Kirigami.Theme.textColor
        }
        PlasmaComponents.ToolTip.text: tip
        PlasmaComponents.ToolTip.visible: hovered && tip !== ""
        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
    }
}
