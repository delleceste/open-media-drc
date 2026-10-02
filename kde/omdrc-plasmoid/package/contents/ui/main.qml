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
    readonly property int panelInset: Plasmoid.configuration.panelInset
    readonly property int panelMarginStart: Plasmoid.configuration.panelMarginStart
    readonly property int panelMarginEnd: Plasmoid.configuration.panelMarginEnd
    readonly property string paneOrder: Plasmoid.configuration.paneOrder

    readonly property string base: host === "" ? ""
        : "http://" + (host.indexOf(":") >= 0 && host[0] !== "[" ? "[" + host + "]" : host) + ":" + port

    // what the box says
    property var player: ({ state: "stop", title: "", artist: "", album: "", file: "" })
    property bool reachable: false
    property string page: "cover"       // cover | queue | qobuz
    property string artPath: ""
    property int artRequestSerial: 0
    property string simpleSearchQuery: ""
    property bool simpleSearchBusy: false
    property var simpleSearchAnswer: null
    property string qobuzOpenOption: ""
    property bool compactSearchRequested: false
    property bool compactSearchTyping: false
    onCompactSearchRequestedChanged: if (!compactSearchRequested) compactSearchTyping = false
    // Plasma's panel takes keyboard focus while an applet accepts input.
    Plasmoid.status: compactSearchTyping ? PlasmaCore.Types.AcceptingInputStatus
                                         : PlasmaCore.Types.ActiveStatus
    property bool stoppedSearchReady: false
    property bool autoOpenedSearch: false
    property string pollError: ""
    readonly property bool playing: player.state === "play"
    // A disc on the CD / S-PDIF input plays past MPD, which says "stop" meanwhile.
    property bool cdinActive: false
    readonly property string subtitle: [player.artist, player.album].filter(s => s).join(" — ")
    readonly property bool seekable: Number.isFinite(Number(player.duration)) && Number(player.duration) > 0
                                     && Number.isFinite(Number(player.elapsed))

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
    readonly property bool autoScreenDelay: Plasmoid.configuration.autoScreenDelay
    readonly property int manualScreenDelayMs: Math.max(0, Math.min(3000, Number(Plasmoid.configuration.screenDelayMs) || 0))
    property int estimatedScreenDelayMs: 0
    property bool hasScreenDelayEstimate: false
    readonly property int screenDelayMs: autoScreenDelay
        ? (hasScreenDelayEstimate ? estimatedScreenDelayMs : 0) : manualScreenDelayMs
    property var delayedFrames: []
    property int receivedFrameSequence: 0
    property int renderedFrameSequence: 0

    readonly property bool wantsLevels: meterStyle !== "off" || showSpectrum || showDr || showBalance
    readonly property bool wantsCover: coverMode !== "off" || !wantsLevels
    readonly property string coverUrl: !wantsCover || base === "" || !artPath ? "" : base + artPath

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

    // In a panel, while a track is loaded: the cover and the album's details.
    // Nothing of the widget's own while the pointer is on its buttons or search.
    property bool panelControlsHovered: false
    readonly property bool inPanel: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
                                    || Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property bool quietTip: inPanel && (panelControlsHovered || compactSearchRequested)
    readonly property bool albumTip: inPanel && !quietTip && problem === "" && !!player.title
    toolTipMainText: quietTip || albumTip ? "" : player.title || i18n("OMDRC Monitor")
    toolTipSubText: quietTip || albumTip ? "" : problem !== "" ? problem
        : subtitle !== "" ? subtitle + (playing ? "" : " (" + stateText() + ")")
        : stateText()
    toolTipItem: albumTip ? albumTipItem : null
    property Item albumTipItem: AlbumTip { app: root }

    // The Qobuz album of the track playing, for the tooltip: the track's own
    // card at once, the album's (description, performers, awards) after.
    property var albumInfo: null
    readonly property string qobuzTrackId: (/\/trackId\/(\d+)/.exec(player.file || "") || [])[1] || ""
    onQobuzTrackIdChanged: {
        albumInfo = null
        const id = qobuzTrackId
        if (!id || base === "") return
        request("GET", "/qobuz/track/" + id, null, function (status, data) {
            if (id !== qobuzTrackId || !data || !data.ok || !data.track) return
            const composer = data.track.composer || ""
            albumInfo = Object.assign({}, data.track.album, { trackComposer: composer })
            const albumId = data.track.album && data.track.album.id
            if (!albumId) return
            request("GET", "/qobuz/album/" + encodeURIComponent(albumId), null, function (status, data) {
                if (id !== qobuzTrackId || !data || !data.ok || !data.album) return
                const info = data.album
                delete info.track_list
                albumInfo = Object.assign(info, { trackComposer: composer })
            }, 30000)
        }, 15000)
    }

    switchWidth: Kirigami.Units.gridUnit * 8
    switchHeight: Kirigami.Units.gridUnit * 5
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar
                             || Plasmoid.formFactor === PlasmaCore.Types.MediaCenter
                             ? fullRepresentation : compactRepresentation

    compactRepresentation: Item {
        id: compactRoot
        readonly property bool horizontalPanel: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
        readonly property bool verticalPanel: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        // A panel offers no resize handle: the length is a setting, or
        // follows the chosen panes.  The panes share whatever length it is.
        readonly property int length: Math.max(root.panelLength > 0 ? root.panelLength
                                      : horizontalPanel ? compactFace.implicitWidth : compactFace.implicitHeight,
                                      compactFace.compactSearchOpen ? 350 : 0)
        Layout.minimumWidth: horizontalPanel ? length : -1
        Layout.preferredWidth: horizontalPanel ? length : -1
        Layout.maximumWidth: horizontalPanel ? length : -1
        Layout.minimumHeight: verticalPanel ? length : -1
        Layout.preferredHeight: verticalPanel ? length : -1
        Layout.maximumHeight: verticalPanel ? length : -1
        Layout.fillHeight: horizontalPanel

        // Plasma reserves its own panel padding around the compact item. Let
        // the artwork and graphs occupy that space while preserving a chosen
        // inset inside the widget itself.
        Display {
            id: compactFace
            app: root
            compact: true
            horizontalPanel: compactRoot.horizontalPanel
            verticalPanel: compactRoot.verticalPanel
            readonly property real bleed: 7
            // pixels kept clear from the panel's edges (top/left, bottom/right)
            readonly property real marginStart: Math.max(0, Math.min(2 * bleed, root.panelMarginStart))
            readonly property real marginEnd: Math.max(0, Math.min(2 * bleed, root.panelMarginEnd))
            readonly property real across: 2 * bleed - marginStart - marginEnd
            x: verticalPanel ? marginStart - bleed : 0
            y: horizontalPanel ? marginStart - bleed : 0
            width: compactRoot.width + (verticalPanel ? across : 0)
            height: compactRoot.height + (horizontalPanel ? across : 0)
            onActivated: root.expanded = !root.expanded
        }
    }

    fullRepresentation: Display {
        app: root
        Layout.minimumWidth: Kirigami.Units.gridUnit * (root.page === "cover" ? 6 : 26)
        Layout.minimumHeight: Kirigami.Units.gridUnit * (root.page === "cover" ? 3 : 24)
        Layout.preferredWidth: Kirigami.Units.gridUnit * (root.page === "cover" ? 22 : 38)
        Layout.preferredHeight: Kirigami.Units.gridUnit * (root.page === "cover" ? 10 : 40)
    }

    function silentVu() {
        return { left_rms: L.FLOOR, right_rms: L.FLOOR, left_peak: L.FLOOR, right_peak: L.FLOOR }
    }

    function stateText() {
        return player.state === "play" ? i18n("playing")
             : player.state === "pause" ? i18n("paused") : i18n("stopped")
    }

    function request(method, path, body, done, timeoutMs, headers) {
        const xhr = new XMLHttpRequest()
        const url = base + path
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let data = null
            try { data = JSON.parse(xhr.responseText) } catch (e) { }
            if (done) done(xhr.status, data)
        }
        xhr.open(method, url)
        xhr.timeout = timeoutMs || 4000
        if (body !== null) xhr.setRequestHeader("Content-Type", "application/json")
        if (headers) for (const key of Object.keys(headers)) xhr.setRequestHeader(key, headers[key])
        xhr.send(body === null ? null : JSON.stringify(body))
        return xhr
    }

    function poll() {
        if (base === "") { reachable = false; return }
        const asked = base
        request("GET", "/k/api/player", null, function (status, data) {
            if (asked !== base) return
            if (status === 200 && data && data.ok) {
                const was = reachable
                const oldFile = player.file
                reachable = true
                pollError = ""
                player = data
                if (data.state === "stop" && !cdinActive && !stoppedSearchReady
                        && !stopSearchTimer.running) stopSearchTimer.start()
                else if (data.state !== "stop") {
                    stopSearchTimer.stop()
                    stoppedSearchReady = false
                    compactSearchRequested = false
                    if (autoOpenedSearch && page === "qobuz") page = "cover"
                    autoOpenedSearch = false
                }
                if (!was || oldFile !== data.file) artPath = ""
                pollArt()
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
    onCdinActiveChanged: {
        if (cdinActive) {
            stopSearchTimer.stop()
            compactSearchRequested = false
            if (autoOpenedSearch && page === "qobuz") page = "cover"
            autoOpenedSearch = false
        }
        else if (reachable && player.state === "stop" && !stoppedSearchReady)
            stopSearchTimer.start()
    }

    function pollArt() {
        const asked = base, serial = ++artRequestSerial
        request("GET", "/qconnect/status", null, function (status, data) {
            if (asked !== base || serial !== artRequestSerial || status !== 200 || !data || !data.ok) return
            artPath = data.art || ""
        })
    }

    function readSettings() {
        request("GET", "/spectrum/settings", null, function (status, data) {
            if (status !== 200 || !data) return
            if (Number.isFinite(Number(data.floor_db))) floorDb = Number(data.floor_db)
            const margin = data.drc_delay_terms_ms && Number(data.drc_delay_terms_ms.margin)
            if (Number.isFinite(margin)) {
                estimatedScreenDelayMs = Math.max(0, Math.min(3000, Math.round(margin)))
                hasScreenDelayEstimate = true
            }
        })
    }

    function receiveLevelFrame(data) {
        // DR is a history/statistics view, not a time-critical level display.
        if (data.dr && Array.isArray(data.dr_blocks)) {
            drFrame = data
            drBlocks = data.dr_blocks
            drTrackAge = Number(data.dr.track_age_blocks) || 0
        }
        const displayFrame = Object.assign({}, data)
        delete displayFrame.dr
        delete displayFrame.dr_blocks
        const sequence = ++receivedFrameSequence
        const delay = screenDelayMs
        if (delay <= 0) {
            delayedFrames = []
            delayedFrameTimer.stop()
            renderedFrameSequence = sequence
            applyLevelFrame(displayFrame)
            return
        }
        const queue = delayedFrames.slice()
        queue.push({ due: Date.now() + delay, sequence: sequence, data: displayFrame })
        queue.sort((a, b) => a.due - b.due)
        // Bound memory if a UI thread stalls; old frames are no longer useful
        // once a newer analyzer frame is due.
        if (queue.length > 100) queue.splice(0, queue.length - 100)
        delayedFrames = queue
        scheduleDelayedFrames()
    }

    function scheduleDelayedFrames() {
        if (!delayedFrames.length) { delayedFrameTimer.stop(); return }
        delayedFrameTimer.interval = Math.max(1, delayedFrames[0].due - Date.now())
        delayedFrameTimer.restart()
    }

    function drawDelayedFrames() {
        const now = Date.now(), queue = delayedFrames.slice()
        let latest = null, count = 0
        while (count < queue.length && queue[count].due <= now) {
            if (queue[count].sequence > renderedFrameSequence
                    && (!latest || queue[count].sequence > latest.sequence))
                latest = queue[count]
            count++
        }
        if (count) delayedFrames = queue.slice(count)
        if (latest) {
            renderedFrameSequence = latest.sequence
            applyLevelFrame(latest.data)
        }
        scheduleDelayedFrames()
    }

    function applyLevelFrame(data) {
        if (data.vu && data.vu.left_rms !== undefined) {
            vu = data.vu
            updateBalance(data.vu)
        }
        if (data.dr && Array.isArray(data.dr_blocks)) {
            drFrame = data
            drBlocks = data.dr_blocks
            drTrackAge = Number(data.dr.track_age_blocks) || 0
        }
        if (Array.isArray(data.bands) && data.bands.length) {
            const key = JSON.stringify(data.bands)
            if (key !== levels.bandsKey) { levels.bandsKey = key; bands = data.bands }
            specLeft = Array.isArray(data.left) ? data.left : []
            specRight = Array.isArray(data.right) ? data.right : []
        }
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
        if (action === "play" || action === "pause" || action === "stop")
            player = Object.assign({}, player, { state: action })
        if (action === "stop" && !cdinActive) {
            stoppedSearchReady = false
            stopSearchTimer.restart()
        }
        request("POST", "/k/api/transport", { action: action }, function (status, data) {
            if (!(data && data.ok))
                console.warn("omdrc: transport", action, "failed:", data ? data.error : status)
            poll()
        })
    }

    function seek(seconds) {
        if (!seekable) return
        const value = Math.max(0, Math.min(Number(player.duration), seconds))
        player = Object.assign({}, player, { elapsed: value })
        request("POST", "/k/api/transport", { action: "seek", seconds: value }, function () { poll() })
    }
    function openPage(name) { page = name; expanded = true }
    function dismissCompactSearch() {
        compactSearchRequested = false
        stoppedSearchReady = true
        stopSearchTimer.stop()
    }

    function openQobuzOption(option) {
        qobuzOpenOption = option
        openPage("qobuz")
    }

    function simpleSearch(text) {
        const query = text.trim()
        if (!query || simpleSearchBusy || !base) return
        simpleSearchQuery = query
        simpleSearchBusy = true
        const asked = base
        request("GET", "/qobuz/search?q=" + encodeURIComponent(query) + "&sort=relevance",
                null, function (status, data) {
            if (asked !== base) return
            simpleSearchBusy = false
            simpleSearchAnswer = { query: query, data: data && data.ok ? data : null,
                                   error: data && data.error ? data.error : i18n("Search failed") }
            qobuzOpenOption = ""
            openPage("qobuz")
        }, 120000)
    }

    onBaseChanged: {
        reachable = false
        stopSearchTimer.stop()
        stoppedSearchReady = false
        compactSearchRequested = false
        autoOpenedSearch = false
        ++artRequestSerial
        artPath = ""
        player = { state: "stop", title: "", artist: "", album: "", file: "" }
        poll()
    }
    Component.onCompleted: poll()

    Timer {
        id: stopSearchTimer
        interval: 2300
        repeat: false
        onTriggered: {
            if (!root.reachable || root.player.state !== "stop" || root.cdinActive) return
            root.stoppedSearchReady = true
            if (root.page !== "cover") return
            if (Plasmoid.formFactor === PlasmaCore.Types.Horizontal
                    || Plasmoid.formFactor === PlasmaCore.Types.Vertical)
                root.compactSearchRequested = true
            else {
                root.autoOpenedSearch = true
                root.page = "qobuz"
            }
        }
    }

    Timer {
        interval: root.expanded || Plasmoid.formFactor === PlasmaCore.Types.Planar ? 1500 : 2500
        repeat: true
        running: root.base !== ""
        onTriggered: root.poll()
    }

    Timer {
        id: settingsRefresh
        interval: 5000
        repeat: true
        running: root.reachable
        onTriggered: root.readSettings()
    }

    Timer {
        id: delayedFrameTimer
        repeat: false
        onTriggered: root.drawDelayedFrames()
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
            root.delayedFrames = []
            delayedFrameTimer.stop()
            root.vu = root.silentVu()
            root.specLeft = root.bands.map(() => L.FLOOR)
            root.specRight = root.specLeft
            root.balanceSamples = []
            root.balanceDb = NaN
            root.drFrame = null
            root.drBlocks = []
            root.drTrackAge = 0
        }
        onFrame: (data) => root.receiveLevelFrame(data)
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
