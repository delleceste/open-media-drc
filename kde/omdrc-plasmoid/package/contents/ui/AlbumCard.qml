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
    property bool expanded: false
    property var tracks: []
    property string error: ""
    signal played(string mode)

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
            Image {
                Layout.preferredWidth: 54
                Layout.preferredHeight: 54
                source: card.album.image || ""
                asynchronous: true
                fillMode: Image.PreserveAspectFit
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: card.album.title + (card.album.version ? " (" + card.album.version + ")" : "")
                    font.bold: true
                    wrapMode: Text.Wrap
                }
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: card.album.artist || ""
                    elide: Text.ElideRight
                }
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: [card.album.label, card.album.year,
                           card.album.bits > 16 ? card.album.bits + "/" + card.album.rate : ""].filter(s => s).join(" · ")
                    elide: Text.ElideRight
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                }
            }
            PlasmaComponents.ToolButton {
                icon.name: "media-playback-start"
                text: i18n("Play album")
                display: QQC2.AbstractButton.IconOnly
                enabled: card.canPlay && card.album.streamable !== false
                onClicked: card.play("replace", "")
            }
            PlasmaComponents.ToolButton {
                icon.name: "list-add"
                text: i18n("Add album to queue")
                display: QQC2.AbstractButton.IconOnly
                enabled: card.canPlay && card.album.streamable !== false
                onClicked: card.play("append", "")
            }
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: !!(card.album.performers && card.album.performers.length)
            text: card.album.performers ? card.album.performers.slice(0, 4).map(p => p.name).join(", ") : ""
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: !!(card.album.ai && card.album.ai.reason)
            text: card.album.ai ? card.album.ai.reason : ""
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
        }
        Repeater {
            model: card.album.ai && card.album.ai.sources || []
            delegate: QQC2.Button {
                required property var modelData
                Layout.fillWidth: true
                flat: true
                text: modelData.title
                onClicked: Qt.openUrlExternally(modelData.url)
            }
        }
        PlasmaComponents.ToolButton {
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
