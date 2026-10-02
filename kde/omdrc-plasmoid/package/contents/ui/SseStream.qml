import QtQuick

/* omdrcctrl's /spectrum/stream (server-sent events) read with XMLHttpRequest:
 * QML has no EventSource, but its XHR reports LOADING with the body so far.
 *
 * Two quirks of that XHR shape this.  The body only grows, so a connection
 * must not live long; and abort() does not close the socket, which goes on
 * downloading, so the box would keep its analyzer (and MPD's FIFO output) on
 * for ever after the widget stopped listening.  So every stream is asked to
 * end by itself after maxSeconds (`max_s`, confirmed by the X-Stream-Max-S
 * header), and a successor is opened rotateSeconds in, before its
 * predecessor ends: the box counts listeners and switches the analyzer off
 * when the last one leaves, and it must not see a gap at every rotation. */
Item {
    id: stream
    visible: false

    property string url: ""          // empty: closed
    property bool connected: false
    property string error: ""

    signal frame(var data)

    readonly property int maxSeconds: 20
    readonly property int rotateSeconds: 15
    property var current: null       // { xhr, pos, dead, seen }
    property var retiring: null
    property double lastData: 0

    // both fire at creation: one connection, not two
    onUrlChanged: Qt.callLater(restart)
    Component.onCompleted: Qt.callLater(restart)
    Component.onDestruction: { kill(current); kill(retiring) }

    function kill(conn) {
        if (!conn) return
        conn.dead = true
        conn.xhr.abort()             // stops the callbacks; the box ends the stream
    }

    function restart() {
        kill(current); kill(retiring)
        current = retiring = null
        connected = false
        retry.stop()
        rotate.stop()
        if (url) open()
    }

    function open() {
        const conn = { xhr: new XMLHttpRequest(), pos: 0, dead: false, seen: false }
        const xhr = conn.xhr
        xhr.onreadystatechange = function () {
            if (conn.dead) return
            if (xhr.readyState === XMLHttpRequest.HEADERS_RECEIVED && xhr.status === 200
                    && !xhr.getResponseHeader("X-Stream-Max-S")) {
                // An omdrcctrl from before bounded streams: a stream it never
                // ends would hold its analyzer on after every pause.
                kill(conn)
                if (conn === current) {
                    stream.connected = false
                    stream.error = i18n("omdrcctrl on the box is too old for the meters: update it")
                }
                return
            }
            if (xhr.readyState === XMLHttpRequest.LOADING || xhr.readyState === XMLHttpRequest.DONE)
                consume(conn)
            if (xhr.readyState === XMLHttpRequest.DONE) {
                conn.dead = true
                if (conn !== current) return       // a predecessor ending on time
                stream.connected = false
                if (xhr.status !== 200)
                    stream.error = xhr.status === 0 ? i18n("cannot reach the box")
                                : xhr.status === 404 ? i18n("spectrum analyzer disabled on the box")
                                : i18n("analyzer stream failed (HTTP %1)", xhr.status)
                // ended on time with no successor yet: at once; failed: calmly
                retry.interval = xhr.status === 200 ? 100 : 3000
                retry.restart()
            }
        }
        xhr.open("GET", url + (url.indexOf("?") >= 0 ? "&" : "?") + "max_s=" + maxSeconds)
        xhr.setRequestHeader("Accept", "text/event-stream")
        xhr.send()
        lastData = Date.now()
        if (current && !current.dead) { kill(retiring); retiring = current }
        current = conn
        rotate.restart()
    }

    function consume(conn) {
        const text = conn.xhr.responseText
        if (text.length > conn.pos) stream.lastData = Date.now()
        let end
        while ((end = text.indexOf("\n\n", conn.pos)) >= 0) {
            const block = text.substring(conn.pos, end)
            conn.pos = end + 2
            let data = ""
            for (const line of block.split("\n"))
                if (line.startsWith("data:")) data += line.substring(5)
            if (!data) continue                       // ": keepalive"
            if (!conn.seen) {
                conn.seen = true
                // the successor is live: its predecessor is no longer needed
                if (conn === current && retiring) { kill(retiring); retiring = null }
            }
            if (conn !== current) continue
            try {
                stream.frame(JSON.parse(data))
                stream.connected = true
                stream.error = ""
            } catch (e) {
                console.warn("omdrc: bad frame", e)
            }
        }
    }

    Timer {
        id: rotate
        interval: stream.rotateSeconds * 1000
        onTriggered: if (stream.url && stream.current && !stream.current.dead) stream.open()
    }

    Timer {
        id: retry
        onTriggered: if (stream.url) stream.open()
    }

    // The box sends a keepalive every second even when silent: a connection
    // that says nothing for this long is gone without having been closed.
    Timer {
        interval: 2000
        repeat: true
        running: stream.url !== ""
        onTriggered: {
            if (stream.current && !stream.current.dead && Date.now() - stream.lastData > 8000) {
                stream.connected = false
                stream.error = i18n("analyzer stream stalled")
                stream.restart()
            }
        }
    }
}
