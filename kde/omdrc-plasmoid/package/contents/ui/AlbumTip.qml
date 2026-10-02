import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

/* The panel widget's tooltip while a track is loaded: the cover on the left,
 * on the right what is known of the track and its album.  A tooltip takes no
 * input, so nothing scrolls: the album's description takes whatever height
 * the lines above it leave, and is cut there. */
Item {
    id: tip

    required property var app
    readonly property var player: app.player
    readonly property var album: app.albumInfo || ({})

    readonly property int side: Kirigami.Units.gridUnit * 13
    width: side + Kirigami.Units.largeSpacing + Kirigami.Units.gridUnit * 17
    height: side
    implicitWidth: width
    implicitHeight: height

    function joined(list, sep) { return list.filter(s => s !== undefined && s !== null && String(s) !== "").join(sep) }

    readonly property string trackTitle: player.title || ""
    readonly property string albumTitle: (album.title || player.album || "")
        + (album.version ? " (" + album.version + ")" : "")
    readonly property string year: album.year ? String(album.year) : String(player.date || "").slice(0, 4)
    readonly property string facts: joined([album.label || player.label, year,
        album.technical ? String(album.technical).replace(/\s*-\s*Stereo\s*$/i, "").trim()
        : album.bits ? album.bits + "/" + album.rate : ""], " · ")
    readonly property string genre: album.genres && album.genres.length
        ? String(album.genres[album.genres.length - 1]).split("→").pop() : album.genre || ""
    readonly property string composer: album.trackComposer
        || (album.composer && !/^various/i.test(album.composer) ? album.composer : "")
    readonly property string people: joined([
        composer && composer !== (player.artist || album.artist) ? i18n("Composer: %1", composer) : "",
        album.performers && album.performers.length
            ? album.performers.slice(0, 8).map(p => p.name).join(", ") : ""], "\n")
    readonly property string awards: album.awards && album.awards.length
        ? album.awards.map(a => joined([a.name, a.publication !== a.name ? a.publication : ""], ", ")).join(" · ") : ""
    readonly property string about: joined([album.catchline, album.description], "\n\n")

    Cover {
        id: cover
        x: 0
        y: 0
        width: tip.side
        height: tip.side
        source: tip.app.coverUrl || tip.album.image_large || ""
    }

    ColumnLayout {
        anchors { left: cover.right; leftMargin: Kirigami.Units.largeSpacing
                  right: parent.right; top: parent.top; bottom: parent.bottom }
        spacing: 2

        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: tip.trackTitle
            visible: text !== ""
            font.bold: true
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: tip.player.artist || tip.album.artist || ""
            visible: text !== ""
            elide: Text.ElideRight
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: tip.albumTitle
            visible: text !== ""
            font.italic: true
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: tip.joined([tip.facts, tip.genre], " · ")
            visible: text !== ""
            font: Kirigami.Theme.smallFont
            opacity: 0.75
            elide: Text.ElideRight
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: tip.people
            visible: text !== ""
            font: Kirigami.Theme.smallFont
            opacity: 0.75
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: tip.awards
            visible: text !== ""
            font: Kirigami.Theme.smallFont
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
        // whatever height is left: as much of the description as fits
        PlasmaComponents.Label {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: Kirigami.Units.smallSpacing
            text: tip.about
            visible: text !== ""
            font: Kirigami.Theme.smallFont
            wrapMode: Text.Wrap
            verticalAlignment: Text.AlignTop
            elide: Text.ElideRight
            clip: true
        }
        Item { Layout.fillHeight: true; visible: tip.about === "" }
    }
}
