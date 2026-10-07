import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

Rectangle {
    id: card
    required property var app
    required property var album
    property string query: ""
    property bool canPlay: false
    // A terse one-line "artist / title" row with a small thumbnail, used by the
    // Recent tab so more entries fit; the full card keeps the details.
    property bool compact: false
    // A prominent cover with the same small text, used by the Discover tab so
    // new releases read at a glance.
    property bool bigCover: false
    property bool expanded: false
    property var tracks: []
    property string error: ""
    signal played(string mode)

    // Slightly smaller than the theme default throughout, so the browser is
    // denser and shows more albums at once.
    readonly property int titleSize: Kirigami.Theme.smallFont.pixelSize
    readonly property int bodySize: Math.max(9, Kirigami.Theme.smallFont.pixelSize - 1)
    readonly property int coverSide: compact ? 32 : bigCover ? 92 : 46

    width: parent ? parent.width : 320
    implicitHeight: body.implicitHeight + 2 * Kirigami.Units.smallSpacing
    radius: Kirigami.Units.cornerRadius
    color: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                   Kirigami.Theme.backgroundColor.b, 0.6)
    border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                          Kirigami.Theme.textColor.b, 0.18)

    function clock(seconds) {
        if (!Number.isFinite(Number(seconds))) return ""
        const n = Math.max(0, Math.floor(seconds))
        return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0")
    }
    function play(mode, start) {
        if (album.source === "local") {
            card.error = ""
            app.request("POST", "/qobuz/local/play", { album_id: String(album.id), mode: mode, tracks: album.tracks }, function (status, data) {
                if (!data || !data.ok) { card.error = data && data.error || i18n("Could not queue the album"); return }
                card.played(mode); app.poll()
            }, 90000)
            return
        }
        const body = { album_id: String(album.id), mode: mode, query: query }
        if (start) body.start = String(start)
        card.error = ""
        app.request("POST", "/qobuz/play", body, function (status, data) {
            if (!data || !data.ok) { card.error = data && data.error || i18n("Could not queue the album"); return }
            card.played(mode)
            app.poll()
        }, 90000)
    }
    function toggleTracks() {
        expanded = !expanded
        if (!expanded || tracks.length) return
        if (album.source === "local") { tracks = album.tracks || []; return }
        app.request("GET", "/qobuz/album/" + encodeURIComponent(String(album.id)), null,
                    function (status, data) {
            if (!data || !data.ok) { card.error = data && data.error || i18n("Could not load tracks"); return }
            card.tracks = data.album && data.album.track_list || []
        }, 30000)
    }

    ColumnLayout {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: Kirigami.Units.smallSpacing }
        spacing: Kirigami.Units.smallSpacing
        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            Item {
                Layout.preferredWidth: card.coverSide
                Layout.preferredHeight: card.coverSide
                Layout.alignment: Qt.AlignTop
                Image {
                    anchors.fill: parent
                    source: card.album.image || ""
                    asynchronous: true
                    fillMode: Image.PreserveAspectFit
                }
                PlasmaComponents.Label {
                    anchors { right: parent.right; bottom: parent.bottom }
                    visible: card.album.source === "local"
                    text: "⌂"
                    Accessible.name: i18n("Local collection")
                    font.bold: true
                    padding: 2
                    background: Rectangle { color: Kirigami.Theme.backgroundColor; radius: 3 }
                }
            }
            // Recent: one terse line, "artist / title".
            PlasmaComponents.Label {
                Layout.fillWidth: true
                visible: card.compact
                text: [card.album.artist, card.album.title].filter(s => s).join(" / ")
                elide: Text.ElideRight
                font.pixelSize: card.titleSize
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: !card.compact
                spacing: 0
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: card.album.title + (card.album.version ? " (" + card.album.version + ")" : "")
                    font.bold: true
                    font.pixelSize: card.titleSize
                    wrapMode: Text.Wrap
                }
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: card.album.artist || ""
                    elide: Text.ElideRight
                    font.pixelSize: card.bodySize
                }
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: [card.album.label, card.album.year,
                           card.album.bits > 16 ? card.album.bits + "/" + card.album.rate : ""].filter(s => s).join(" · ")
                    elide: Text.ElideRight
                    opacity: 0.7
                    font.pixelSize: card.bodySize
                }
            }
            PlasmaComponents.ToolButton {
                icon.name: "media-playback-start"
                icon.width: card.compact ? card.titleSize : undefined
                icon.height: card.compact ? card.titleSize : undefined
                text: i18n("Play album")
                display: QQC2.AbstractButton.IconOnly
                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered && text !== ""
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                enabled: card.canPlay && card.album.streamable !== false
                onClicked: card.play("replace", "")
            }
            PlasmaComponents.ToolButton {
                icon.name: "list-add"
                icon.width: card.compact ? card.titleSize : undefined
                icon.height: card.compact ? card.titleSize : undefined
                text: i18n("Add album to queue")
                display: QQC2.AbstractButton.IconOnly
                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered && text !== ""
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                enabled: card.canPlay && card.album.streamable !== false
                onClicked: card.play("append", "")
            }
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: !card.compact && !!(card.album.performers && card.album.performers.length)
            text: card.album.performers ? card.album.performers.slice(0, 4).map(p => p.name).join(", ") : ""
            wrapMode: Text.Wrap
            font.pixelSize: card.bodySize
            opacity: 0.7
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: !card.compact && !!(card.album.ai && card.album.ai.reason)
            text: card.album.ai ? card.album.ai.reason : ""
            wrapMode: Text.Wrap
            font.pixelSize: card.bodySize
        }
        Repeater {
            model: card.compact ? [] : (card.album.ai && card.album.ai.sources || [])
            delegate: QQC2.Button {
                required property var modelData
                Layout.fillWidth: true
                flat: true
                text: modelData.title
                onClicked: Qt.openUrlExternally(modelData.url)
            }
        }
        PlasmaComponents.ToolButton {
            visible: !card.compact
            text: card.expanded ? i18n("Hide tracks") : i18n("Tracks")
            icon.name: card.expanded ? "arrow-up" : "arrow-down"
            onClicked: card.toggleTracks()
        }
        Repeater {
            model: card.expanded ? card.tracks : []
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: modelData.number + ". " + modelData.title
                    elide: Text.ElideRight
                    font: Kirigami.Theme.smallFont
                }
                PlasmaComponents.Label { text: card.clock(modelData.duration); opacity: 0.7 }
                PlasmaComponents.ToolButton {
                    icon.name: "media-playback-start"
                    text: i18n("Play from this track")
                    display: QQC2.AbstractButton.IconOnly
                    PlasmaComponents.ToolTip.text: text
                    PlasmaComponents.ToolTip.visible: hovered && text !== ""
                    PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                    enabled: card.canPlay && modelData.streamable !== false
                    onClicked: card.play("replace", modelData.id)
                }
            }
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: card.error !== ""
            text: card.error
            wrapMode: Text.Wrap
            color: Kirigami.Theme.negativeTextColor
        }
    }
}
