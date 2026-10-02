import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

/* The widget's face: the configured panes (cover, meters, spectrum) side by
 * side or stacked, the track underneath on the desktop and in the popup, and
 * prev / play-pause / next over everything while the pointer is on it.
 * Used by both representations; `app` is the PlasmoidItem holding the data. */
Item {
    id: face

    required property var app
    property bool compact: false
    // in a panel: the thickness is fixed and the panes are laid along it
    property bool horizontalPanel: false
    property bool verticalPanel: false

    signal activated()

    readonly property bool panel: horizontalPanel || verticalPanel
    readonly property string meterStyle: app.meterStyle
    readonly property bool coverBehind: app.coverMode === "background" && panes.some(p => p !== "cover")
    readonly property var panes: {
        const list = []
        if (app.coverMode === "pane") list.push("cover")
        if (meterStyle !== "off") list.push("meters")
        if (app.showSpectrum) list.push("spectrum")
        if (!list.length) list.push("cover")   // nothing chosen: at least show what plays
        return list
    }
    readonly property bool showTrack: !compact && app.showTitle
    // panes laid out in a row, or a column
    readonly property bool row: horizontalPanel || (!verticalPanel && paneArea.width >= paneArea.height)

    function aspect(pane) {        // width / height a pane would like
        if (pane === "cover") return 1
        if (pane === "spectrum") return row ? 3.5 : 1.4
        if (meterStyle === "needles") return row ? 2.6 : 0.75
        return row ? 2.6 : 0.6
    }
    readonly property real aspectSum: panes.reduce((s, p) => s + (row ? aspect(p) : 1 / aspect(p)), 0)
    readonly property real spacing: panes.length > 1 ? Math.max(2, Kirigami.Units.smallSpacing) : 0
    // each pane's length along the layout: the cover a square (at most 45%
    // of the room when it shares it), the others what is left, by aspect
    readonly property var extents: {
        const along = row ? paneArea.width : paneArea.height
        const across = row ? paneArea.height : paneArea.width
        const free = Math.max(0, along - spacing * (panes.length - 1))
        const cover = panes.length > 1 ? Math.min(across, free * 0.45) : free
        const weight = p => p === "cover" ? 0 : row ? aspect(p) : 1 / aspect(p)
        const total = panes.reduce((s, p) => s + weight(p), 0)
        const rest = free - (panes.indexOf("cover") >= 0 ? cover : 0)
        return panes.map(p => p === "cover" ? cover : rest * weight(p) / total)
    }

    // In a panel the long side follows the content.
    implicitWidth: horizontalPanel ? Math.round(height * aspectSum + spacing * (panes.length - 1)) : Kirigami.Units.gridUnit * 16
    implicitHeight: verticalPanel ? Math.round(width * aspectSum + spacing * (panes.length - 1)) : Kirigami.Units.gridUnit * 8

    Item {
        id: paneArea
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: track.visible ? track.top : parent.bottom }
        anchors.bottomMargin: track.visible ? Kirigami.Units.smallSpacing : 0

        Cover {
            visible: face.coverBehind
            anchors.fill: parent
            source: face.app.coverUrl
            opacity: 0.45
            crop: true
        }

        Repeater {
            model: face.panes
            delegate: Loader {
                required property string modelData
                required property int index
                readonly property real offset: face.extents.slice(0, index).reduce((s, e) => s + e + face.spacing, 0)
                readonly property real extent: face.extents[index] || 0
                x: face.row ? Math.round(offset) : 0
                y: face.row ? 0 : Math.round(offset)
                width: face.row ? Math.round(extent) : paneArea.width
                height: face.row ? paneArea.height : Math.round(extent)
                sourceComponent: modelData === "cover" ? coverPane
                               : modelData === "spectrum" ? spectrumPane : metersPane
            }
        }
    }

    Component {
        id: coverPane
        Cover { source: face.app.coverUrl }
    }
    Component {
        id: metersPane
        Meters {
            style: face.meterStyle
            vu: face.app.vu
            glass: face.coverBehind ? 0.5 : 1
        }
    }
    Component {
        id: spectrumPane
        Spectrum {
            bands: face.app.bands
            leftDb: face.app.specLeft
            rightDb: face.app.specRight
            floorDb: face.app.floorDb
            glass: face.coverBehind ? 0.5 : 1
        }
    }

    ColumnLayout {
        id: track
        visible: face.showTrack && face.app.player.title !== ""
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        spacing: 0
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: face.app.player.title || ""
            font.bold: true
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: face.app.subtitle
            visible: text !== ""
            elide: Text.ElideRight
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            horizontalAlignment: Text.AlignHCenter
        }
    }

    // Not configured, or the box does not answer: say so where the panes are.
    Rectangle {
        anchors.centerIn: paneArea
        width: problemLabel.width + Kirigami.Units.largeSpacing * 2
        height: problemLabel.implicitHeight + Kirigami.Units.smallSpacing * 2
        visible: !face.compact && face.app.problem !== "" && !hover.hovered
        radius: Kirigami.Units.cornerRadius
        color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                       Kirigami.Theme.backgroundColor.b, 0.85)
        PlasmaComponents.Label {
            id: problemLabel
            anchors.centerIn: parent
            width: Math.min(implicitWidth, paneArea.width - Kirigami.Units.largeSpacing * 3)
            text: face.app.problem
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            font: Kirigami.Theme.smallFont
        }
    }
    Kirigami.Icon {
        anchors { right: parent.right; top: parent.top }
        width: Math.min(Kirigami.Units.iconSizes.small, parent.height / 2)
        height: width
        visible: face.compact && face.app.problem !== ""
        source: face.app.host === "" ? "configure" : "dialog-warning"
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: face.activated()
    }

    HoverHandler { id: hover }

    // prev / play-pause / next while hovered
    Rectangle {
        id: controls
        objectName: "controls"
        anchors.fill: paneArea
        color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                       Kirigami.Theme.backgroundColor.b, 0.55)
        radius: Kirigami.Units.cornerRadius
        opacity: hover.hovered && face.app.host !== "" ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }

        readonly property real size: Math.max(12, Math.min(height * 0.85, width / 3.3,
                                                          Kirigami.Units.iconSizes.huge))
        Row {
            anchors.centerIn: parent
            spacing: Math.min(Kirigami.Units.largeSpacing, controls.size * 0.15)
            Repeater {
                model: [
                    { action: "prev", icon: "media-skip-backward", tip: i18n("Previous") },
                    { action: "toggle",
                      icon: face.app.playing ? "media-playback-pause" : "media-playback-start",
                      tip: face.app.playing ? i18n("Pause") : i18n("Play") },
                    { action: "next", icon: "media-skip-forward", tip: i18n("Next") },
                ]
                delegate: PlasmaComponents.ToolButton {
                    required property var modelData
                    width: controls.size
                    height: controls.size
                    padding: Math.max(1, controls.size * 0.12)
                    icon.name: modelData.icon
                    icon.width: controls.size - 2 * padding
                    icon.height: controls.size - 2 * padding
                    display: QQC2.AbstractButton.IconOnly
                    text: modelData.tip
                    enabled: face.app.reachable
                    PlasmaComponents.ToolTip.text: modelData.tip
                    PlasmaComponents.ToolTip.visible: hovered && !face.panel
                    PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                    onClicked: face.app.transport(modelData.action)
                }
            }
        }
    }
}
