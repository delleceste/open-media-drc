import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import "levels.js" as L

/* OMDRC Monitor: level meters, spectrum and cover of what an Open Media DRC
 * box plays, with prev / play-pause / next on hover.  A pure client of
 * omdrcctrl's HTTP endpoints, the same ones the kiosk and the Android app use:
 *
 *   /spectrum/stream?mode=vu|music   levels (and bands), server-sent events
 *   /spectrum/settings               the spectrum's dB floor
 *   /k/api/player                    state and current song, polled
 *   /k/api/transport                 play / pause / next / prev
 *   /qconnect/art                    the cover */
PlasmoidItem {
    id: root

    readonly property string host: Plasmoid.configuration.host.trim()
    readonly property int port: Plasmoid.configuration.port
    readonly property string meterStyle: Plasmoid.configuration.meterStyle
    readonly property bool showSpectrum: Plasmoid.configuration.showSpectrum
    readonly property string coverMode: Plasmoid.configuration.coverMode
    readonly property bool showTitle: Plasmoid.configuration.showTitle
    readonly property bool streamWhenIdle: Plasmoid.configuration.streamWhenIdle

    readonly property string base: host === "" ? ""
        : "http://" + (host.indexOf(":") >= 0 && host[0] !== "[" ? "[" + host + "]" : host) + ":" + port

    // what the box says
    property var player: ({ state: "stop", title: "", artist: "", album: "", file: "" })
    property bool reachable: false
    property string pollError: ""
    readonly property bool playing: player.state === "play"
    readonly property string subtitle: [player.artist, player.album].filter(s => s).join(" — ")

    property var vu: silentVu()
    property var bands: []
    property var specLeft: []
    property var specRight: []
    property real floorDb: -40

    readonly property bool wantsLevels: meterStyle !== "off" || showSpectrum
    readonly property bool wantsCover: coverMode !== "off" || !wantsLevels
    readonly property string coverUrl: !wantsCover || base === "" || (!player.file && !player.title) ? ""
        : base + "/qconnect/art?v=" + encodeURIComponent(player.file + "|" + player.album + "|" + player.title)

    readonly property string problem: host === "" ? i18n("Set the box's address in the widget's settings.")
        : !reachable ? i18n("Cannot reach %1:%2 — %3", host, port, pollError)
        : levels.url !== "" && levels.error !== "" ? levels.error
        : ""

    Plasmoid.configurationRequired: host === ""
    Plasmoid.backgroundHints: PlasmaCore.Types.DefaultBackground | PlasmaCore.Types.ConfigurableBackground
    Plasmoid.icon: "audio-volume-high"

    toolTipMainText: player.title || i18n("OMDRC Monitor")
    toolTipSubText: problem !== "" ? problem
        : subtitle !== "" ? subtitle + (playing ? "" : " (" + stateText() + ")")
        : stateText()

    switchWidth: Kirigami.Units.gridUnit * 8
    switchHeight: Kirigami.Units.gridUnit * 5
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar
                             || Plasmoid.formFactor === PlasmaCore.Types.MediaCenter
                             ? fullRepresentation : compactRepresentation

    compactRepresentation: Display {
        app: root
        compact: true
        horizontalPanel: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
        verticalPanel: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        Layout.minimumWidth: horizontalPanel ? implicitWidth : -1
        Layout.preferredWidth: horizontalPanel ? implicitWidth : -1
        Layout.minimumHeight: verticalPanel ? implicitHeight : -1
        Layout.preferredHeight: verticalPanel ? implicitHeight : -1
        onActivated: root.expanded = !root.expanded
    }

    fullRepresentation: Display {
        app: root
        Layout.minimumWidth: Kirigami.Units.gridUnit * 6
        Layout.minimumHeight: Kirigami.Units.gridUnit * 3
        Layout.preferredWidth: Kirigami.Units.gridUnit * 22
        Layout.preferredHeight: Kirigami.Units.gridUnit * 10
    }

    function silentVu() {
        return { left_rms: L.FLOOR, right_rms: L.FLOOR, left_peak: L.FLOOR, right_peak: L.FLOOR }
    }

    function stateText() {
        return player.state === "play" ? i18n("playing")
             : player.state === "pause" ? i18n("paused") : i18n("stopped")
    }

    function request(method, path, body, done) {
        const xhr = new XMLHttpRequest()
        const url = base + path
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let data = null
            try { data = JSON.parse(xhr.responseText) } catch (e) { }
            if (done) done(xhr.status, data)
        }
        xhr.open(method, url)
        xhr.timeout = 4000
        if (body !== null) xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body === null ? null : JSON.stringify(body))
    }

    function poll() {
        if (base === "") { reachable = false; return }
        const asked = base
        request("GET", "/k/api/player", null, function (status, data) {
            if (asked !== base) return
            if (status === 200 && data && data.ok) {
                const was = reachable
                reachable = true
                pollError = ""
                player = data
                if (!was) readSettings()
            } else {
                reachable = false
                pollError = status === 0 ? i18n("no answer")
                          : data && data.error ? data.error : i18n("HTTP %1", status)
            }
        })
    }

    function readSettings() {
        request("GET", "/spectrum/settings", null, function (status, data) {
            if (status === 200 && data && Number.isFinite(Number(data.floor_db)))
                floorDb = Number(data.floor_db)
        })
    }

    function transport(action) {
        if (action === "toggle") action = playing ? "pause" : "play"
        // Show the change at once; the next poll confirms or corrects it.
        if (action === "play" || action === "pause")
            player = Object.assign({}, player, { state: action })
        request("POST", "/k/api/transport", { action: action }, function (status, data) {
            if (!(data && data.ok))
                console.warn("omdrc: transport", action, "failed:", data ? data.error : status)
            poll()
        })
    }

    onBaseChanged: {
        reachable = false
        player = { state: "stop", title: "", artist: "", album: "", file: "" }
        poll()
    }
    Component.onCompleted: poll()

    Timer {
        interval: root.expanded || Plasmoid.formFactor === PlasmaCore.Types.Planar ? 1500 : 2500
        repeat: true
        running: root.base !== ""
        onTriggered: root.poll()
    }

    // Only while there is something to show: the box enables MPD's analyzer
    // FIFO output for as long as anyone listens.
    SseStream {
        id: levels
        property string bandsKey: ""
        url: root.base !== "" && root.reachable && root.wantsLevels && (root.playing || root.streamWhenIdle)
             ? root.base + "/spectrum/stream?mode=" + (root.showSpectrum ? "music" : "vu")
             : ""
        onUrlChanged: if (url === "") {
            root.vu = root.silentVu()
            root.specLeft = root.bands.map(() => L.FLOOR)
            root.specRight = root.specLeft
        }
        onFrame: (data) => {
            if (data.vu && data.vu.left_rms !== undefined) root.vu = data.vu
            if (Array.isArray(data.bands) && data.bands.length) {
                // the bands follow the source's rate: replace them only then
                const key = JSON.stringify(data.bands)
                if (key !== bandsKey) { bandsKey = key; root.bands = data.bands }
                root.specLeft = Array.isArray(data.left) ? data.left : []
                root.specRight = Array.isArray(data.right) ? data.right : []
            }
        }
    }
}
