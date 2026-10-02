import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid

/* The widget's face: the configured panes (cover, meters, spectrum) side by
 * side or stacked, the track underneath on the desktop and in the popup, and
 * small transport controls on the cover while the pointer is on it.
 * Used by both representations; `app` is the PlasmoidItem holding the data. */
Item {
    id: face

    required property var app
    property bool compact: false
    // in a panel: the thickness is fixed and the panes are laid along it
    property bool horizontalPanel: false
    property bool verticalPanel: false
    property bool compactSearchOpen: false
    property bool controlsShown: false

    signal activated()

    readonly property bool panel: horizontalPanel || verticalPanel
    readonly property bool editing: !!(panel && Plasmoid.containment
                                     && Plasmoid.containment.corona.editMode)
    readonly property string meterStyle: app.meterStyle
    readonly property bool bottomMeters: !compact && app.page === "cover" && (app.showDr || app.showBalance)
    readonly property bool coverBehind: app.coverMode === "background" && panes.some(p => p !== "cover")
    readonly property var panes: {
        const list = []
        if (app.coverMode === "pane") list.push("cover")
        if (meterStyle !== "off") list.push(app.showSpectrum && app.spectrumBelow ? "meterspectrum" : "meters")
        if (app.showSpectrum && (meterStyle === "off" || !app.spectrumBelow)) list.push("spectrum")
        if (!bottomMeters && app.showDr) list.push("dr")
        if (!bottomMeters && app.showBalance) list.push("balance")
        if (!list.length) list.push("cover")   // nothing chosen: at least show what plays
        const order = (app.paneOrder || "cover,meters,spectrum,dr,balance").split(",")
        const key = p => p === "meterspectrum" ? "meters" : p
        return list.sort((a, b) => order.indexOf(key(a)) - order.indexOf(key(b)))
    }
    readonly property bool showTrack: !compact && app.page === "cover" && app.showTitle
    // panes laid out in a row, or a column
    readonly property bool row: horizontalPanel || (!verticalPanel && paneArea.width >= paneArea.height)

    function aspect(pane) {        // width / height a pane would like
        if (pane === "cover") return 1
        if (pane === "spectrum") return row ? 3.5 : 1.4
        if (pane === "meterspectrum") return row ? 2.0 : 1.2
        if (pane === "dr" || pane === "balance") return row ? 2.5 : 1.2
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

    function movePane(from, to) {
        if (from < 0 || to < 0 || from >= panes.length || to >= panes.length || from === to) return
        const key = p => p === "meterspectrum" ? "meters" : p
        const order = panes.map(key)
        const moved = order.splice(from, 1)[0]
        order.splice(to, 0, moved)
        const saved = (app.paneOrder || "cover,meters,spectrum,dr,balance").split(",")
        app.setConfig("paneOrder", order.concat(saved.filter(p => !order.includes(p))).join(","))
    }

    function paneAt(along) {
        let start = 0
        for (let i = 0; i < panes.length; i++) {
            if (along < start + extents[i] + spacing / 2) return i
            start += extents[i] + spacing
        }
        return panes.length - 1
    }

    // In a panel the long side follows the content.
    implicitWidth: horizontalPanel ? Math.round(height * aspectSum + spacing * (panes.length - 1)) : Kirigami.Units.gridUnit * 16
    implicitHeight: verticalPanel ? Math.round(width * aspectSum + spacing * (panes.length - 1)) : Kirigami.Units.gridUnit * 8

    // The custom background (Plasma draws its own, or none, otherwise).  Off a
    // panel the content keeps a margin from its edge, as on Plasma's own.
    readonly property bool customBackground: app.backgroundMode === "custom" || app.backgroundMode === "transparent"
    readonly property real inset: panel ? Math.max(0, Math.min(12, app.panelInset || 0))
                                 : customBackground ? Kirigami.Units.smallSpacing * 2 : 0

    Rectangle {
        anchors.fill: parent
        visible: face.customBackground
        color: face.app.backgroundMode === "transparent"
             ? Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                       Kirigami.Theme.backgroundColor.b, face.app.backgroundOpacity / 100)
             : face.app.backgroundColor
        radius: face.panel ? Kirigami.Units.cornerRadius / 2 : Kirigami.Units.cornerRadius
    }

    Item {
        id: paneArea
        visible: !face.compactSearchOpen && (face.compact || face.app.page === "cover")
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: track.visible ? track.top : bottomRow.visible ? bottomRow.top : parent.bottom }
        anchors.leftMargin: face.inset
        anchors.rightMargin: face.inset
        anchors.topMargin: face.inset
        anchors.bottomMargin: track.visible || bottomRow.visible ? Kirigami.Units.smallSpacing : face.inset

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
                id: paneLoader
                required property string modelData
                required property int index
                readonly property real offset: face.extents.slice(0, index).reduce((s, e) => s + e + face.spacing, 0)
                readonly property real extent: face.extents[index] || 0
                x: face.row ? Math.round(offset) : 0
                y: face.row ? 0 : Math.round(offset)
                width: face.row ? Math.round(extent) : paneArea.width
                height: face.row ? paneArea.height : Math.round(extent)
                sourceComponent: modelData === "cover" ? coverPane
                               : modelData === "meterspectrum" ? meterSpectrumPane
                               : modelData === "spectrum" ? spectrumPane
                               : modelData === "dr" ? drPane
                               : modelData === "balance" ? balancePane : metersPane
                opacity: paneDrag.active ? 0.65 : 1
                TapHandler {
                    enabled: !face.editing && face.panes.length > 1
                    acceptedButtons: Qt.LeftButton
                    acceptedModifiers: Qt.ControlModifier
                    onTapped: face.movePane(paneLoader.index, (paneLoader.index + 1) % face.panes.length)
                }
                DragHandler {
                    id: paneDrag
                    enabled: !face.editing && face.panes.length > 1
                    acceptedButtons: Qt.LeftButton
                    acceptedModifiers: Qt.ControlModifier
                    target: null
                    property real lastAlong: 0
                    onTranslationChanged: lastAlong = face.row ? translation.x : translation.y
                    onActiveChanged: {
                        if (active) { lastAlong = 0; return }
                        if (Math.abs(lastAlong) < 2) return
                        const centre = face.row ? paneLoader.x + paneLoader.width / 2
                                                : paneLoader.y + paneLoader.height / 2
                        face.movePane(paneLoader.index, face.paneAt(centre + lastAlong))
                        lastAlong = 0
                    }
                }
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
            app: face.app
            style: face.meterStyle
            pair: face.app.meterPair
            vu: face.app.vu
            interactionsEnabled: !face.editing
            glass: face.coverBehind ? 0.5 : 1
        }
    }
    Component {
        id: meterSpectrumPane
        Item {
            Meters {
                app: face.app
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: parent.height * 0.58
                style: face.meterStyle
                pair: face.app.meterPair
                vu: face.app.vu
                interactionsEnabled: !face.editing
                glass: face.coverBehind ? 0.5 : 1
            }
            Spectrum {
                anchors { left: parent.left; right: parent.right }
                y: parent.height * 0.61
                height: parent.height * 0.39
                bands: face.app.bands
                leftDb: face.app.specLeft
                rightDb: face.app.specRight
                floorDb: face.app.floorDb
                glass: face.coverBehind ? 0.5 : 1
            }
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
    Component {
        id: drPane
        DrView { app: face.app; history: true }
    }
    Component {
        id: balancePane
        BalanceView { app: face.app }
    }

    RowLayout {
        id: bottomRow
        visible: face.bottomMeters
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: face.inset }
        height: Kirigami.Units.gridUnit * 3
        spacing: Kirigami.Units.smallSpacing
        DrView {
            visible: face.app.showDr
            app: face.app
            strip: true
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: face.app.showBalance ? Math.max(0, bottomRow.width - bottomRow.spacing) * 0.72 : bottomRow.width
        }
        BalanceView {
            visible: face.app.showBalance
            app: face.app
            strip: true
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: face.app.showDr ? Math.max(0, bottomRow.width - bottomRow.spacing) * 0.28 : bottomRow.width
        }
    }

    ColumnLayout {
        id: track
        visible: face.showTrack && face.app.player.title !== ""
        anchors { left: parent.left; right: parent.right; bottom: bottomRow.visible ? bottomRow.top : parent.bottom; margins: face.inset }
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
        enabled: face.compact && !face.compactSearchOpen && !face.editing
        acceptedButtons: Qt.LeftButton
        onClicked: face.activated()
    }

    RowLayout {
        anchors { fill: parent; margins: face.inset }
        visible: face.compact && face.compactSearchOpen && !face.editing
        spacing: 1
        QQC2.TextField {
            id: simpleInput
            Layout.fillWidth: true
            Layout.fillHeight: true
            placeholderText: i18n("Search Qobuz")
            text: face.app.simpleSearchQuery
            onTextEdited: face.app.simpleSearchQuery = text
            onAccepted: face.app.simpleSearch(text)
        }
        PlasmaComponents.ToolButton {
            icon.name: "edit-find"
            text: face.app.simpleSearchBusy ? i18n("Searching…") : i18n("Search")
            display: QQC2.AbstractButton.IconOnly
            enabled: !face.app.simpleSearchBusy
            onClicked: face.app.simpleSearch(simpleInput.text)
        }
        PlasmaComponents.ToolButton {
            text: i18n("AI")
            onClicked: face.app.openQobuzOption("ai")
        }
        PlasmaComponents.ToolButton {
            icon.name: "view-filter"
            text: i18n("Filters")
            display: QQC2.AbstractButton.IconOnly
            onClicked: face.app.openQobuzOption("filters")
        }
        PlasmaComponents.ToolButton {
            icon.name: "go-previous"
            text: i18n("Back to cover")
            display: QQC2.AbstractButton.IconOnly
            onClicked: face.compactSearchOpen = false
        }
    }

    ColumnLayout {
        anchors { fill: parent; margins: face.inset }
        visible: !face.compact && face.app.page !== "cover"
        spacing: Kirigami.Units.smallSpacing
        RowLayout {
            Layout.fillWidth: true
            PlasmaComponents.ToolButton {
                icon.name: "media-optical-audio"
                text: i18n("Cover")
                onClicked: face.app.page = "cover"
            }
            PlasmaComponents.ToolButton {
                icon.name: "view-list-details"
                text: i18n("Queue")
                onClicked: face.app.page = "queue"
            }
            PlasmaComponents.ToolButton {
                icon.name: "edit-find"
                text: i18n("Qobuz")
                onClicked: face.app.page = "qobuz"
            }
            Item { Layout.fillWidth: true }
        }
        QueueView {
            app: face.app
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: face.app.page === "queue"
        }
        QobuzView {
            app: face.app
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: face.app.page === "qobuz"
        }
    }

    Timer {
        id: hoverClose
        interval: 600
        onTriggered: if (!hover.hovered && !controlsHover.hovered) face.controlsShown = false
    }
    HoverHandler {
        id: hover
        onHoveredChanged: {
            if (hovered) { face.controlsShown = true; hoverClose.stop() }
            else hoverClose.restart()
        }
    }

    Item {
        id: coverHoverZone
        x: paneArea.x
        y: paneArea.y
        width: face.panes.indexOf("cover") >= 0 && face.row ? face.extents[0] : paneArea.width
        height: face.panes.indexOf("cover") >= 0 && !face.row ? face.extents[0] : paneArea.height
        visible: paneArea.visible && !face.editing
    }

    // Controls stay within the artwork. The cover and meters retain their colors.
    Item {
        id: controls
        objectName: "controls"
        x: coverHoverZone.x
        y: coverHoverZone.y
        width: face.panel ? Math.min(face.width, coverHoverZone.width + controls.size * 6 + 8)
                          : coverHoverZone.width
        height: coverHoverZone.height
        opacity: paneArea.visible && !face.editing && face.controlsShown && face.app.host !== "" ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        HoverHandler {
            id: controlsHover
            onHoveredChanged: {
                if (hovered) { face.controlsShown = true; hoverClose.stop() }
                else if (!hover.hovered) hoverClose.restart()
            }
        }

        readonly property real size: face.panel ? Math.max(24, Math.min(30, height * 0.74))
            : Math.max(12, Math.min(height * 0.24, width / 5.5, Kirigami.Units.iconSizes.medium))
        SeekRing {
            id: seekRing
            app: face.app
            visible: !face.compact && face.app.seekable && face.panes.indexOf("cover") >= 0
                     && (controlsHover.hovered || dragging)
            readonly property real side: Math.min(controls.width, controls.height)
            width: side
            height: side
            x: (controls.width - width) / 2
            y: (controls.height - height) / 2
        }
        Row {
            visible: !face.panel
            anchors { top: parent.top; right: parent.right; margins: 1 }
            spacing: 2
            PlasmaComponents.ToolButton {
                icon.name: "edit-find"
                text: i18n("Search Qobuz")
                display: QQC2.AbstractButton.IconOnly
                width: controls.size
                height: controls.size
                padding: 1
                onClicked: face.app.openPage("qobuz")
            }
            PlasmaComponents.ToolButton {
                icon.name: "view-list-details"
                text: i18n("Play queue")
                display: QQC2.AbstractButton.IconOnly
                width: controls.size
                height: controls.size
                padding: 1
                onClicked: face.app.openPage("queue")
            }
        }
        Rectangle {
            id: transportBox
            visible: !face.panel
            anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter; bottomMargin: 1 }
            width: transportButtons.width + 4
            height: transportButtons.height + 2
            radius: height / 3
            color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                           Kirigami.Theme.backgroundColor.b, 0.72)
            Row {
                id: transportButtons
                anchors.centerIn: parent
                spacing: 0
            Repeater {
                model: [
                    { action: "prev", icon: "media-skip-backward", tip: i18n("Previous") },
                    { action: "toggle",
                      icon: face.app.playing ? "media-playback-pause" : "media-playback-start",
                      tip: face.app.playing ? i18n("Pause") : i18n("Play") },
                    { action: "next", icon: "media-skip-forward", tip: i18n("Next") },
                    { action: "stop", icon: "media-playback-stop", tip: i18n("Stop") },
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
                    onClicked: {
                        face.app.transport(modelData.action)
                        if (modelData.action === "stop") {
                            if (face.compact) {
                                face.compactSearchOpen = true
                                Qt.callLater(() => simpleInput.forceActiveFocus())
                            } else face.app.openPage("qobuz")
                        }
                    }
                }
            }
            }
        }
        Rectangle {
            visible: face.panel
            anchors { left: parent.left; leftMargin: coverHoverZone.width + 2
                      verticalCenter: parent.verticalCenter }
            width: panelActions.width + 4
            height: panelActions.height + 2
            radius: height / 3
            color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                           Kirigami.Theme.backgroundColor.b, 0.78)
            Row {
                id: panelActions
                anchors.centerIn: parent
                spacing: 0
                Repeater {
                    model: [
                        { action: "prev", icon: "media-skip-backward", tip: i18n("Previous") },
                        { action: "toggle", icon: face.app.playing ? "media-playback-pause" : "media-playback-start",
                          tip: face.app.playing ? i18n("Pause") : i18n("Play") },
                        { action: "next", icon: "media-skip-forward", tip: i18n("Next") },
                        { action: "stop", icon: "media-playback-stop", tip: i18n("Stop") },
                        { action: "search", icon: "edit-find", tip: i18n("Search Qobuz") },
                        { action: "queue", icon: "view-list-details", tip: i18n("Play queue") },
                    ]
                    delegate: PlasmaComponents.ToolButton {
                        required property var modelData
                        width: controls.size
                        height: controls.size
                        padding: 2
                        icon.name: modelData.icon
                        icon.width: controls.size - 4
                        icon.height: controls.size - 4
                        display: QQC2.AbstractButton.IconOnly
                        text: modelData.tip
                        enabled: face.app.reachable
                        onClicked: {
                            if (modelData.action === "search") face.app.openPage("qobuz")
                            else if (modelData.action === "queue") face.app.openPage("queue")
                            else {
                                face.app.transport(modelData.action)
                                if (modelData.action === "stop") {
                                    face.compactSearchOpen = true
                                    Qt.callLater(() => simpleInput.forceActiveFocus())
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
