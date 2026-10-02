import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import "levels.js" as L
import "dr.js" as DR

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
    readonly property string meterPair: Plasmoid.configuration.meterPair
    readonly property bool showSpectrum: Plasmoid.configuration.showSpectrum
    readonly property bool spectrumBelow: Plasmoid.configuration.spectrumBelow
    readonly property string coverMode: Plasmoid.configuration.coverMode
    readonly property bool showTitle: Plasmoid.configuration.showTitle
    readonly property string backgroundMode: Plasmoid.configuration.backgroundMode
    readonly property color backgroundColor: Plasmoid.configuration.backgroundColor
    readonly property int backgroundOpacity: Plasmoid.configuration.backgroundOpacity
    readonly property int panelLength: Plasmoid.configuration.panelLength   // 0: automatic

    readonly property string base: host === "" ? ""
        : "http://" + (host.indexOf(":") >= 0 && host[0] !== "[" ? "[" + host + "]" : host) + ":" + port

    // what the box says
    property var player: ({ state: "stop", title: "", artist: "", album: "", file: "" })
    property bool reachable: false
    property string pollError: ""
    readonly property bool playing: player.state === "play"
    // A disc on the CD / S-PDIF input plays past MPD, which says "stop" meanwhile.
    property bool cdinActive: false
    readonly property string subtitle: [player.artist, player.album].filter(s => s).join(" — ")

    property var vu: silentVu()
    property var bands: []
    property var specLeft: []
    property var specRight: []
    property real floorDb: -40
    property var drFrame: null
    property var drBlocks: []
    property int drTrackAge: 0
    property real balanceDb: NaN
    property var balanceSamples: []
    readonly property int drWindow: Plasmoid.configuration.drWindow
    readonly property bool drPerSong: Plasmoid.configuration.drPerSong
    readonly property var drSelected: DR.selected(drBlocks, drTrackAge, drWindow, drPerSong)
    readonly property var drSummary: DR.summary(drFrame, drBlocks, drTrackAge, drWindow, drPerSong)
    readonly property bool showDr: Plasmoid.configuration.showDr
    readonly property bool showBalance: Plasmoid.configuration.showBalance
    readonly property string balanceLook: Plasmoid.configuration.balanceLook
    readonly property real balanceWindow: Plasmoid.configuration.balanceWindow

    readonly property bool wantsLevels: meterStyle !== "off" || showSpectrum || showDr || showBalance
    readonly property bool wantsCover: coverMode !== "off" || !wantsLevels
    readonly property string coverUrl: !wantsCover || base === "" || (!player.file && !player.title) ? ""
        : base + "/qconnect/art?v=" + encodeURIComponent(player.file + "|" + player.album + "|" + player.title)

    readonly property string problem: host === "" ? i18n("Set the box's address in the widget's settings.")
        : !reachable ? i18n("Cannot reach %1:%2 — %3", host, port, pollError)
        : levels.url !== "" && levels.error !== "" ? levels.error
        : ""

    Plasmoid.configurationRequired: host === ""
    Plasmoid.backgroundHints: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
        || Plasmoid.formFactor === PlasmaCore.Types.Vertical ? PlasmaCore.Types.NoBackground
        : backgroundMode === "default"
        ? PlasmaCore.Types.DefaultBackground | PlasmaCore.Types.ConfigurableBackground
        : PlasmaCore.Types.NoBackground
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
        // A panel offers no resize handle: the length is a setting, or
        // follows the chosen panes.  The panes share whatever length it is.
        readonly property int length: root.panelLength > 0 ? root.panelLength
                                      : horizontalPanel ? implicitWidth : implicitHeight
        Layout.minimumWidth: horizontalPanel ? length : -1
        Layout.preferredWidth: horizontalPanel ? length : -1
        Layout.maximumWidth: horizontalPanel ? length : -1
        Layout.minimumHeight: verticalPanel ? length : -1
        Layout.preferredHeight: verticalPanel ? length : -1
        Layout.maximumHeight: verticalPanel ? length : -1
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
                if (playing || !wantsLevels) cdinActive = false
                else pollCdin()
            } else {
                reachable = false
                pollError = status === 0 ? i18n("no answer")
                          : data && data.error ? data.error : i18n("HTTP %1", status)
            }
        })
    }

    function pollCdin() {
        request("GET", "/cdin/status", null, function (status, data) {
            cdinActive = status === 200 && !!data && data.active === true
        })
    }

    function readSettings() {
        request("GET", "/spectrum/settings", null, function (status, data) {
            if (status === 200 && data && Number.isFinite(Number(data.floor_db)))
                floorDb = Number(data.floor_db)
        })
    }

    function setConfig(key, value) { Plasmoid.configuration[key] = value }
    function cycleMeterStyle() {
        const styles = ["bars", "needles"], i = styles.indexOf(meterStyle)
        if (meterStyle !== "off") setConfig("meterStyle", styles[(i + 1) % styles.length])
    }
    function cycleDrWindow() {
        const i = DR.WINDOWS.indexOf(drWindow)
        setConfig("drWindow", DR.WINDOWS[(i + 1) % DR.WINDOWS.length])
    }
    function toggleBalanceLook() { setConfig("balanceLook", balanceLook === "split" ? "bar" : "split") }
    function cycleBalanceWindow() {
        const ws = [0.3, 1, 3, 5, 15, 30, 60], i = ws.indexOf(balanceWindow)
        setConfig("balanceWindow", ws[(i + 1) % ws.length])
    }
    function updateBalance(vu) {
        const now = Date.now(), l = Number(vu.left_rms), r = Number(vu.right_rms)
        let samples = balanceSamples.slice()
        if (samples.length && now - samples[samples.length - 1].at > 1000) samples = []
        if (Number.isFinite(l) && Number.isFinite(r) && Math.max(l, r) > -60)
            samples.push({ at: now, left: Math.pow(10, l / 10), right: Math.pow(10, r / 10) })
        samples = samples.filter(s => s.at >= now - Math.max(60000, balanceWindow * 1000))
        balanceSamples = samples
        const start = Math.min(now - balanceWindow * 1000, samples.length ? samples[samples.length - 1].at : now)
        let left = 0, right = 0
        for (const s of samples) if (s.at >= start) { left += s.left; right += s.right }
        balanceDb = left || right ? 10 * Math.log10(Math.max(right, 1e-12) / Math.max(left, 1e-12)) : NaN
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
        url: root.base !== "" && root.reachable && root.wantsLevels && (root.playing || root.cdinActive)
             ? root.base + "/spectrum/stream?mode=" + (root.showSpectrum ? "music" : root.showDr ? "dr" : "vu")
             : ""
        onUrlChanged: if (url === "") {
            root.vu = root.silentVu()
            root.specLeft = root.bands.map(() => L.FLOOR)
            root.specRight = root.specLeft
            root.balanceSamples = []
            root.balanceDb = NaN
            root.drFrame = null
            root.drBlocks = []
            root.drTrackAge = 0
        }
        onFrame: (data) => {
            if (data.vu && data.vu.left_rms !== undefined) {
                root.vu = data.vu
                root.updateBalance(data.vu)
            }
            if (data.dr && Array.isArray(data.dr_blocks)) {
                root.drFrame = data
                root.drBlocks = data.dr_blocks
                root.drTrackAge = Number(data.dr.track_age_blocks) || 0
            }
            if (Array.isArray(data.bands) && data.bands.length) {
                // the bands follow the source's rate: replace them only then
                const key = JSON.stringify(data.bands)
                if (key !== bandsKey) { bandsKey = key; root.bands = data.bands }
                root.specLeft = Array.isArray(data.left) ? data.left : []
                root.specRight = Array.isArray(data.right) ? data.right : []
            }
        }
    }

    // Music mode carries FFT bands; DR mode carries the rolling three-second
    // history.  When both views are enabled each subscription asks only for
    // the data it needs, while the server shares the FIFO reader.
    SseStream {
        id: drStream
        url: root.base !== "" && root.reachable && root.showDr && root.showSpectrum
             && (root.playing || root.cdinActive) ? root.base + "/spectrum/stream?mode=dr" : ""
        onFrame: (data) => {
            if (data.dr && Array.isArray(data.dr_blocks)) {
                root.drFrame = data
                root.drBlocks = data.dr_blocks
                root.drTrackAge = Number(data.dr.track_age_blocks) || 0
            }
        }
    }
}
