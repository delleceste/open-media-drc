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
    readonly property bool compactSearchOpen: compact && app.compactSearchRequested
    readonly property bool controlsShown: hover.hovered

    signal activated()

    readonly property bool panel: horizontalPanel || verticalPanel
    readonly property bool editing: !!(panel && Plasmoid.containment
                                     && Plasmoid.containment.corona.editMode)
    readonly property string meterStyle: app.meterStyle
    readonly property bool bottomMeters: !compact && app.page === "cover" && (app.showDr || app.showBalance)
    readonly property bool coverBehind: app.coverMode === "background" && panes.some(p => p !== "cover")
    // On the desktop and in the popup, with cover, meters and spectrum all on:
    // the cover fills the left 10 columns of a 16-column grid; the right 6
    // hold meters above spectrum. DR and balance span the bottom row.
    readonly property bool grid: !compact && !panel && app.coverMode === "pane"
                                 && meterStyle !== "off" && app.showSpectrum
    readonly property var panes: {
        const list = []
        if (app.coverMode === "pane") list.push("cover")
        if (meterStyle !== "off") list.push(app.showSpectrum && app.spectrumBelow && !grid ? "meterspectrum" : "meters")
        if (app.showSpectrum && (meterStyle === "off" || !app.spectrumBelow || grid)) list.push("spectrum")
        if (!bottomMeters && app.showDr) list.push("dr")
        if (!bottomMeters && app.showBalance) list.push("balance")
        if (!list.length) list.push("cover")   // nothing chosen: at least show what plays
        const order = (app.paneOrder || "cover,meters,spectrum,dr,balance").split(",")
        const key = p => p === "meterspectrum" ? "meters" : p
        return list.sort((a, b) => order.indexOf(key(a)) - order.indexOf(key(b)))
    }
    readonly property bool showTrack: !compact && app.page === "cover" && app.showTitle
    readonly property real coverTitleHeight: showTrack && app.player.title
        ? Kirigami.Units.gridUnit * (app.subtitle ? 2.1 : 1.2) : 0
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
    // each pane's length along the layout: the cover a square, with more room
    // on the desktop than in a panel; the others share what is left by aspect
    readonly property var extents: {
        const along = row ? paneArea.width : paneArea.height
        const across = row ? paneArea.height : paneArea.width
        const free = Math.max(0, along - spacing * (panes.length - 1))
        const coverSpace = row ? Math.max(0, across - coverTitleHeight)
                               : across + coverTitleHeight
        const cover = panes.length > 1 ? Math.min(coverSpace, free * (panel || compact ? 0.45 : 0.6)) : free
        const weight = p => p === "cover" ? 0 : row ? aspect(p) : 1 / aspect(p)
        const total = panes.reduce((s, p) => s + weight(p), 0)
        const rest = free - (panes.indexOf("cover") >= 0 ? cover : 0)
        return panes.map(p => p === "cover" ? cover : rest * weight(p) / total)
    }

    // each pane's rectangle in paneArea
    readonly property var rects: {
        const W = paneArea.width, H = paneArea.height
        if (grid) {
            const coverW = Math.round(Math.min(Math.max(0, W - spacing) * 10 / 16,
                                               Math.max(0, H - coverTitleHeight)))
            const rightX = coverW + spacing
            const rightW = Math.max(0, W - rightX)
            const metersH = Math.round(Math.max(0, H - spacing) * 3 / 10)
            const spectrumY = metersH + spacing
            const at = {
                cover: { x: 0, y: 0, w: coverW, h: H },
                meters: { x: rightX, y: 0, w: rightW, h: metersH },
                spectrum: { x: rightX, y: spectrumY, w: rightW,
                            h: Math.max(0, H - spectrumY) }
            }
            return panes.map(p => at[p])
        }
        let offset = 0
        return panes.map((p, i) => {
            const e = extents[i] || 0
            const r = row ? { x: offset, y: 0, w: e, h: H } : { x: 0, y: offset, w: W, h: e }
            offset += e + spacing
            return r
        })
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

    // the pane under a point of paneArea, or the nearest one
    function paneAt(px, py) {
        let best = panes.length - 1, bestDist = Infinity
        for (let i = 0; i < rects.length; i++) {
            const r = rects[i]
            const dx = Math.max(r.x - px, 0, px - r.x - r.w)
            const dy = Math.max(r.y - py, 0, py - r.y - r.h)
            const d = dx * dx + dy * dy
            if (d < bestDist) { best = i; bestDist = d }
        }
        return best
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
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: bottomRow.visible ? bottomRow.top : parent.bottom }
        anchors.leftMargin: face.inset
        anchors.rightMargin: face.inset
        anchors.topMargin: face.inset
        anchors.bottomMargin: bottomRow.visible ? Kirigami.Units.smallSpacing : face.inset

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
                readonly property var rect: face.rects[index] || { x: 0, y: 0, w: 0, h: 0 }
                x: Math.round(rect.x)
                y: Math.round(rect.y)
                width: Math.round(rect.w)
                height: Math.round(rect.h)
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
                    property point moved: Qt.point(0, 0)
                    onTranslationChanged: moved = translation
                    onActiveChanged: {
                        if (active) { moved = Qt.point(0, 0); return }
                        if (Math.abs(moved.x) < 2 && Math.abs(moved.y) < 2) return
                        face.movePane(paneLoader.index, face.paneAt(paneLoader.x + paneLoader.width / 2 + moved.x,
                                                                    paneLoader.y + paneLoader.height / 2 + moved.y))
                        moved = Qt.point(0, 0)
                    }
                }
            }
        }
    }

    Component {
        id: coverPane
        Item {
            Cover {
                id: artwork
                readonly property real side: Math.max(0, Math.min(parent.width,
                    parent.height - (coverTrack.visible ? coverTrack.implicitHeight + Kirigami.Units.smallSpacing : 0)))
                x: face.grid ? 0 : (parent.width - width) / 2
                y: 0
                width: side
                height: side
                source: face.app.coverUrl
            }
            ColumnLayout {
                id: coverTrack
                visible: face.showTrack && face.app.player.title !== ""
                anchors { top: artwork.bottom; topMargin: Kirigami.Units.smallSpacing
                          horizontalCenter: artwork.horizontalCenter }
                width: artwork.width
                spacing: 0
                HoverHandler {
                    id: trackHover
                    onHoveredChanged: {
                        if (hovered) {
                            if (!titlePan.running && titleScroll.contentWidth > titleScroll.width) titlePan.start()
                            if (!subtitlePan.running && subtitleScroll.contentWidth > subtitleScroll.width) subtitlePan.start()
                        } else {
                            if (!titlePan.running) titleScroll.contentX = 0
                            if (!subtitlePan.running) subtitleScroll.contentX = 0
                        }
                    }
                }
                Flickable {
                    id: titleScroll
                    Layout.fillWidth: true
                    Layout.preferredHeight: titleLabel.implicitHeight
                    contentWidth: Math.max(width, titleLabel.width)
                    contentHeight: height
                    flickableDirection: Flickable.HorizontalFlick
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: false
                    clip: true
                    NumberAnimation {
                        id: titlePan
                        target: titleScroll
                        property: "contentX"
                        from: 0
                        to: Math.max(0, titleScroll.contentWidth - titleScroll.width)
                        duration: Math.max(1200, to * 30)
                        easing.type: Easing.Linear
                        onStopped: if (!trackHover.hovered) titleScroll.contentX = 0
                    }
                    PlasmaComponents.Label {
                        id: titleLabel
                        x: Math.max(0, (titleScroll.width - width) / 2)
                        width: implicitWidth
                        text: face.app.player.title || ""
                        font.bold: true
                        font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                        wrapMode: Text.NoWrap
                    }
                }
                Flickable {
                    id: subtitleScroll
                    Layout.fillWidth: true
                    Layout.preferredHeight: subtitleLabel.implicitHeight
                    visible: subtitleLabel.text !== ""
                    contentWidth: Math.max(width, subtitleLabel.width)
                    contentHeight: height
                    flickableDirection: Flickable.HorizontalFlick
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: false
                    clip: true
                    NumberAnimation {
                        id: subtitlePan
                        target: subtitleScroll
                        property: "contentX"
                        from: 0
                        to: Math.max(0, subtitleScroll.contentWidth - subtitleScroll.width)
                        duration: Math.max(1200, to * 30)
                        easing.type: Easing.Linear
                        onStopped: if (!trackHover.hovered) subtitleScroll.contentX = 0
                    }
                    PlasmaComponents.Label {
                        id: subtitleLabel
                        x: Math.max(0, (subtitleScroll.width - width) / 2)
                        width: implicitWidth
                        text: face.app.subtitle
                        opacity: 0.7
                        font.pixelSize: Math.max(9, Kirigami.Theme.smallFont.pixelSize - 1)
                        wrapMode: Text.NoWrap
                    }
                }
            }
        }
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
        // the buttons' own tooltips, not the widget's big one, while on them
        HoverHandler { onHoveredChanged: face.app.panelControlsHovered = hovered }
        QQC2.TextField {
            id: simpleInput
            Layout.fillWidth: true
            Layout.fillHeight: true
            placeholderText: i18n("Search Qobuz")
            text: face.app.simpleSearchQuery
            onTextEdited: face.app.simpleSearchQuery = text
            onAccepted: face.app.simpleSearch(text)
            onActiveFocusChanged: if (!activeFocus) face.app.compactSearchTyping = false
            Keys.onEscapePressed: face.app.compactSearchTyping = false
            // The panel declines keyboard focus: ask Plasma for it on a click,
            // never when the field opens by itself on stop.
            TapHandler {
                onTapped: {
                    face.app.compactSearchTyping = true
                    simpleInput.forceActiveFocus(Qt.MouseFocusReason)
                }
            }
        }
        PlasmaComponents.ToolButton {
            icon.name: "edit-find"
            text: face.app.simpleSearchBusy ? i18n("Searching…") : i18n("Search")
            display: QQC2.AbstractButton.IconOnly
            PlasmaComponents.ToolTip.text: text
            PlasmaComponents.ToolTip.visible: hovered && text !== ""
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
            enabled: !face.app.simpleSearchBusy
            onClicked: face.app.simpleSearch(simpleInput.text)
        }
        PlasmaComponents.ToolButton {
            text: i18n("AI")
            PlasmaComponents.ToolTip.text: i18n("Ask the AI for albums")
            PlasmaComponents.ToolTip.visible: hovered
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
            onClicked: face.app.openQobuzOption("ai")
        }
        PlasmaComponents.ToolButton {
            icon.name: "view-filter"
            text: i18n("Filters")
            display: QQC2.AbstractButton.IconOnly
            PlasmaComponents.ToolTip.text: text
            PlasmaComponents.ToolTip.visible: hovered && text !== ""
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
            onClicked: face.app.openQobuzOption("filters")
        }
        PlasmaComponents.ToolButton {
            icon.name: "go-previous"
            text: i18n("Back to cover")
            display: QQC2.AbstractButton.IconOnly
            PlasmaComponents.ToolTip.text: text
            PlasmaComponents.ToolTip.visible: hovered && text !== ""
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
            onClicked: face.app.dismissCompactSearch()
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

    HoverHandler { id: hover }

    Item {
        id: coverHoverZone
        readonly property int coverIndex: face.panes.indexOf("cover")
        readonly property var coverRect: face.rects[coverIndex] || null
        readonly property real coverWidth: coverRect ? coverRect.w : Math.min(paneArea.width, paneArea.height)
        readonly property real coverHeight: coverRect ? coverRect.h : Math.min(paneArea.width, paneArea.height)
        readonly property real side: Math.max(0, Math.min(coverWidth,
            coverHeight - (coverIndex >= 0 ? face.coverTitleHeight : 0)))
        x: paneArea.x + (coverRect ? coverRect.x : 0) + (face.grid ? 0 : (coverWidth - side) / 2)
        y: paneArea.y + (coverRect ? coverRect.y : 0)
        width: side
        height: side
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
        HoverHandler { id: controlsHover }

        readonly property real size: face.panel ? Math.max(24, Math.min(30, height * 0.74))
            : Math.max(7, Math.min(height * 0.2, width / 5.5, Kirigami.Units.iconSizes.medium))
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
                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered && text !== ""
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                width: controls.size
                height: controls.size
                padding: 1
                onClicked: face.app.openPage("qobuz")
            }
            PlasmaComponents.ToolButton {
                icon.name: "view-list-details"
                text: i18n("Play queue")
                display: QQC2.AbstractButton.IconOnly
                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered && text !== ""
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                width: controls.size
                height: controls.size
                padding: 1
                onClicked: face.app.openPage("queue")
            }
        }
        Rectangle {
            id: transportBox
            visible: !face.panel
            anchors.centerIn: parent
            width: transportButtons.width + 4
            height: transportButtons.height + 2
            radius: height / 3
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                  Kirigami.Theme.textColor.b, 0.28)
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
                    PlasmaComponents.ToolTip.visible: hovered
                    PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                    onClicked: {
                        face.app.transport(modelData.action)
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
            HoverHandler { onHoveredChanged: face.app.panelControlsHovered = hovered }
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
                        PlasmaComponents.ToolTip.text: text
                        PlasmaComponents.ToolTip.visible: hovered && text !== ""
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                        text: modelData.tip
                        enabled: face.app.reachable
                        onClicked: {
                            if (modelData.action === "search") face.app.openPage("qobuz")
                            else if (modelData.action === "queue") face.app.openPage("queue")
                            else {
                                face.app.transport(modelData.action)
                            }
                        }
                    }
                }
            }
        }
    }
}
