import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

Item {
    id: view
    required property var app
    property var songs: []
    property string version: ""
    property int count: 0
    property string error: ""
    property bool loading: false

    function clock(seconds) {
        if (!Number.isFinite(Number(seconds))) return ""
        const n = Math.max(0, Math.floor(seconds))
        return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0")
    }
    function refresh() {
        if (!visible || !app.reachable || loading) return
        loading = true
        app.request("GET", "/k/api/queue", null, function (status, data) {
            loading = false
            if (!data || !data.ok) { error = data && data.error || i18n("Queue unavailable"); return }
            error = ""
            songs = data.songs || []
            version = String(data.version || "")
            count = data.length || 0
        }, 8000)
    }
    function edit(action, song) {
        app.request("POST", "/k/api/queue", { action: action, id: song ? song.id : "" }, function (status, data) {
            if (!data || !data.ok) error = data && data.error || i18n("Could not change the queue")
            refresh()
            app.poll()
        })
    }
    function jump(song) {
        app.request("POST", "/k/api/transport", { action: "jump", pos: song.pos }, function (status, data) {
            if (!data || !data.ok) error = data && data.error || i18n("Could not play the track")
            app.poll()
        })
    }

    onVisibleChanged: if (visible) refresh()
    Connections {
        target: view.app
        function onPlayerChanged() {
            if (view.visible && view.app.player.version !== view.version) view.refresh()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.smallSpacing
        RowLayout {
            Layout.fillWidth: true
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: i18n("Play queue · %1", view.count)
                font.bold: true
            }
            PlasmaComponents.ToolButton {
                icon.name: "view-refresh"
                text: i18n("Refresh")
                display: QQC2.AbstractButton.IconOnly
                onClicked: view.refresh()
            }
            PlasmaComponents.ToolButton {
                icon.name: "edit-clear-list"
                text: i18n("Clear queue")
                display: QQC2.AbstractButton.IconOnly
                enabled: view.count > 0
                onClicked: clearDialog.open()
            }
        }
        PlasmaComponents.Label {
            visible: view.error !== ""
            text: view.error
            color: Kirigami.Theme.negativeTextColor
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        QQC2.ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 0
                Repeater {
                    model: view.songs
                    delegate: RowLayout {
                        id: row
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.minimumHeight: Math.max(38, title.implicitHeight + artist.implicitHeight + 8)
                            radius: Kirigami.Units.cornerRadius / 2
                            color: String(row.modelData.id) === String(view.app.player.songid)
                                ? Kirigami.Theme.highlightColor : "transparent"
                            ColumnLayout {
                                anchors { fill: parent; margins: 4 }
                                spacing: 0
                                PlasmaComponents.Label {
                                    id: title
                                    Layout.fillWidth: true
                                    text: row.modelData.pos + ". " + row.modelData.title
                                    elide: Text.ElideRight
                                    font.bold: String(row.modelData.id) === String(view.app.player.songid)
                                }
                                PlasmaComponents.Label {
                                    id: artist
                                    Layout.fillWidth: true
                                    text: [row.modelData.artist, row.modelData.album].filter(s => s).join(" · ")
                                    visible: text !== ""
                                    elide: Text.ElideRight
                                    opacity: 0.7
                                    font: Kirigami.Theme.smallFont
                                }
                            }
                            TapHandler { onTapped: view.jump(row.modelData) }
                        }
                        PlasmaComponents.Label { text: view.clock(row.modelData.duration); opacity: 0.7 }
                        PlasmaComponents.ToolButton {
                            icon.name: "list-remove"
                            text: i18n("Remove track")
                            display: QQC2.AbstractButton.IconOnly
                            onClicked: view.edit("remove", row.modelData)
                        }
                    }
                }
                PlasmaComponents.Label {
                    visible: view.count > view.songs.length
                    text: i18n("… and %1 more", view.count - view.songs.length)
                }
                PlasmaComponents.Label {
                    visible: !view.loading && view.count === 0 && view.error === ""
                    text: i18n("The queue is empty")
                }
            }
        }
    }
    QQC2.Dialog {
        id: clearDialog
        title: i18n("Clear play queue?")
        modal: true
        standardButtons: QQC2.Dialog.Ok | QQC2.Dialog.Cancel
        onAccepted: view.edit("clear", null)
    }
}
