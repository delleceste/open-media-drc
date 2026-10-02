import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

/* The Qobuz browser uses the same catalog and AI endpoints as the kiosk page.
 * The widget keeps only the Qobuz controls here; playback stays on upmpdcli. */
Item {
    id: view
    required property var app
    property var status: ({ enabled: false, renderer: false, token: false })
    property string tab: "recent"             // recent | results | discover | awarded
    property string query: ""
    property bool aiMode: false
    property var aiSettings: ({})
    property string aiProvider: "claude_account"
    property string aiModel: ""
    property string aiKey: ""
    property var labels: []
    property var selectedLabels: []
    property string dateMode: "any"
    property int lastYears: 2
    property int fromYear: new Date().getFullYear() - 5
    property int toYear: new Date().getFullYear()
    property string sort: "relevance"
    property bool awardedOnly: false
    property bool hiResOnly: false
    property bool filtersOpen: false
    property var genres: [{ id: "", name: "All genres" }]
    property string genre: ""
    property var results: []
    property var recent: []
    property var discover: []
    property var awarded: []
    property bool more: false
    property int nextScan: 0
    property int nextOffset: 0
    property bool busy: false
    property string error: ""
    property string message: ""
    property var xhr: null
    property int serial: 0
    property var appliedSimpleAnswer: null
    readonly property bool usable: status.enabled && status.renderer && status.token
    readonly property var shown: tab === "results" ? results : tab === "recent" ? recent
                                  : tab === "discover" ? discover : awarded

    function pairs(scan) {
        const values = []
        function add(k, v) { values.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v))) }
        if (query.trim() && !aiMode) add("q", query.trim())
        for (const label of selectedLabels) add("label", label)
        if (dateMode === "last") add("last", lastYears)
        if (dateMode === "span") { add("from", fromYear); add("to", toYear) }
        add("sort", sort)
        if (awardedOnly) add("awarded", 1)
        if (hiResOnly) add("hires", 1)
        if (scan) add("scan", scan)
        return values.join("&")
    }
    function notice(text) { message = text; error = "" }
    function fail(data, fallback) { error = data && data.error || fallback; message = "" }
    function initialize() {
        app.request("GET", "/qobuz/status", null, function (statusCode, data) {
            if (data && data.ok) view.status = data
            else view.fail(data, i18n("Qobuz is unavailable"))
        })
        app.request("GET", "/qobuz/labels", null, function (statusCode, data) {
            if (data && data.ok) view.labels = data.labels || []
        })
        app.request("GET", "/qobuz/genres", null, function (statusCode, data) {
            if (data && data.ok) view.genres = [{ id: "", name: i18n("All genres") }].concat(data.genres || [])
        }, 15000)
        refreshAI()
        loadTab()
    }
    function refreshAI() {
        app.request("GET", "/qobuz/ai/settings", null, function (statusCode, data) {
            if (data && data.ok) {
                view.aiSettings = data
                view.aiProvider = data.provider || "claude_account"
                view.aiModel = data.model || ""
                view.applyRequestedView()
            }
        })
    }
    function toggleLabel(name) {
        selectedLabels = selectedLabels.includes(name)
            ? selectedLabels.filter(n => n !== name) : selectedLabels.concat([name])
    }
    function toggleAI() {
        if (aiMode) { aiMode = false; return }
        if (!aiSettings.configured) { settingsDialog.open(); return }
        aiMode = true
        dateMode = "any"; selectedLabels = []; awardedOnly = false; hiResOnly = false
    }
    function search(scan) {
        if (busy) { stop(); return }
        if (!usable) { fail(null, i18n("Start upmpdcli and sign in to Qobuz first")); return }
        if (!query.trim() && !selectedLabels.length) { fail(null, i18n("Enter search text or select a label")); return }
        if (aiMode && !query.trim()) { fail(null, i18n("Describe the recordings you want")); return }
        const seq = ++serial
        busy = true; error = ""; message = aiMode ? i18n("Researching reviews and Qobuz releases…") : i18n("Searching…")
        tab = "results"
        const path = aiMode ? "/qobuz/ai/recommend?" + pairs(0) : "/qobuz/search?" + pairs(scan || 0)
        xhr = app.request(aiMode ? "POST" : "GET", path, aiMode ? { prompt: query.trim() } : null,
                          function (statusCode, data) {
            if (seq !== serial) return
            busy = false; xhr = null
            if (!data || !data.ok) { fail(data, i18n("Search failed")); return }
            results = data.results || []
            more = !aiMode && !!data.more
            nextScan = data.next_scan || 0
            notice(data.ai && data.ai.summary ? data.ai.summary : i18n("%1 albums", data.count || results.length))
        }, aiMode ? 240000 : 120000, aiMode ? { "X-Qobuz-AI": "1" } : null)
    }
    function stop() {
        ++serial
        if (xhr) xhr.abort()
        xhr = null; busy = false; notice(i18n("Search stopped"))
    }
    function loadTab(offset) {
        if (!visible || tab === "results") return
        const wanted = tab
        if (!offset) more = false
        const path = tab === "recent" ? "/qobuz/played?limit=30"
                   : tab === "awarded" ? "/qobuz/awarded"
                   : "/qobuz/discover?genre=" + encodeURIComponent(genre) + "&offset=" + (offset || 0)
        app.request("GET", path, null, function (statusCode, data) {
            if (view.tab !== wanted) return
            if (!data || !data.ok) { view.fail(data, i18n("Could not load albums")); return }
            if (wanted === "recent") view.recent = data.albums || []
            else if (wanted === "awarded") view.awarded = data.albums || []
            else {
                view.discover = offset ? view.discover.concat(data.albums || []) : data.albums || []
                view.more = !!data.more
                view.nextOffset = data.next_offset || 0
            }
            view.error = ""
        }, 30000)
    }
    function setTab(value) {
        tab = value
        error = ""; message = ""
        loadTab()
    }
    function applyRequestedView() {
        if (!visible) return
        if (app.simpleSearchAnswer && appliedSimpleAnswer !== app.simpleSearchAnswer) {
            const answer = app.simpleSearchAnswer
            appliedSimpleAnswer = answer
            query = answer.query
            aiMode = false
            selectedLabels = []
            dateMode = "any"
            sort = "relevance"
            awardedOnly = false
            hiResOnly = false
            tab = "results"
            results = answer.data ? answer.data.results || [] : []
            more = answer.data ? !!answer.data.more : false
            nextScan = answer.data ? answer.data.next_scan || 0 : 0
            if (answer.data) notice(i18n("%1 albums", answer.data.count || results.length))
            else fail(null, answer.error)
        }
        if (app.qobuzOpenOption === "filters") filtersOpen = true
        else if (app.qobuzOpenOption === "ai") {
            if (!Object.prototype.hasOwnProperty.call(aiSettings, "configured")) return
            if (!aiMode) toggleAI()
        }
        app.qobuzOpenOption = ""
    }
    function saveAI() {
        app.request("POST", "/qobuz/ai/settings",
                    { provider: aiProvider, model: aiModel.trim(), key: aiKey.trim() },
                    function (statusCode, data) {
            if (!data || !data.ok) { view.fail(data, i18n("Could not save AI settings")); return }
            aiKey = ""; aiSettings = data; settingsDialog.close()
            notice(data.configured ? i18n("AI settings saved") : i18n("AI provider needs sign-in or an API key"))
        }, 15000, { "X-Qobuz-AI": "1" })
    }

    onVisibleChanged: if (visible) { initialize(); applyRequestedView() }
    Connections {
        target: view.app
        function onSimpleSearchAnswerChanged() { view.applyRequestedView() }
        function onQobuzOpenOptionChanged() { view.applyRequestedView() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.smallSpacing
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: !view.status.enabled || !view.status.renderer || !view.status.token
            text: !view.status.enabled ? i18n("Qobuz search is disabled on the box")
                : !view.status.renderer ? i18n("Start upmpdcli to search and play Qobuz")
                : !view.status.token ? i18n("Sign in to Qobuz on the box") : ""
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.ToolButton {
                icon.source: view.aiSettings.configured
                    ? view.app.base + "/k/static/img/" + (String(view.aiSettings.provider).startsWith("claude") ? "claude" : "openai") + ".svg" : ""
                text: i18n("AI")
                checkable: true
                checked: view.aiMode
                onClicked: view.toggleAI()
            }
            QQC2.TextField {
                Layout.fillWidth: true
                visible: !view.aiMode
                placeholderText: i18n("Composer, work, performer…")
                text: view.query
                onTextEdited: view.query = text
                onAccepted: view.search(0)
            }
            PlasmaComponents.ToolButton {
                icon.name: "edit-find"
                text: view.busy ? i18n("Stop search") : i18n("Search")
                onClicked: view.search(0)
            }
            PlasmaComponents.ToolButton {
                icon.name: "configure"
                text: i18n("AI settings")
                display: QQC2.AbstractButton.IconOnly
                onClicked: settingsDialog.open()
            }
        }
        QQC2.TextArea {
            Layout.fillWidth: true
            Layout.preferredHeight: 75
            visible: view.aiMode
            placeholderText: i18n("Describe the recordings you want…")
            text: view.query
            wrapMode: TextEdit.Wrap
            onTextChanged: view.query = text
        }
        PlasmaComponents.ToolButton {
            text: view.filtersOpen ? i18n("Hide filters") : i18n("Filters")
            icon.name: view.filtersOpen ? "arrow-up" : "arrow-down"
            onClicked: view.filtersOpen = !view.filtersOpen
        }
        QQC2.ScrollView {
            id: filtersScroll
            Layout.fillWidth: true
            Layout.preferredHeight: view.filtersOpen ? Math.min(view.height * 0.45, 230) : 0
            visible: view.filtersOpen
            clip: true
            ColumnLayout {
                width: filtersScroll.availableWidth
                RowLayout {
                    Layout.fillWidth: true
                    PlasmaComponents.Label { text: i18n("Release date") }
                    QQC2.ComboBox {
                        model: [i18n("Any time"), i18n("Last years"), i18n("From–to")]
                        currentIndex: ["any", "last", "span"].indexOf(view.dateMode)
                        onActivated: view.dateMode = ["any", "last", "span"][currentIndex]
                    }
                    QQC2.SpinBox {
                        visible: view.dateMode === "last"
                        from: 1; to: 30
                        value: view.lastYears
                        onValueModified: view.lastYears = value
                    }
                    QQC2.SpinBox {
                        visible: view.dateMode === "span"
                        from: 1900; to: new Date().getFullYear()
                        value: view.fromYear
                        onValueModified: view.fromYear = value
                    }
                    QQC2.SpinBox {
                        visible: view.dateMode === "span"
                        from: 1900; to: new Date().getFullYear()
                        value: view.toYear
                        onValueModified: view.toYear = value
                    }
                }
                RowLayout {
                    PlasmaComponents.Label { text: i18n("Order") }
                    QQC2.ComboBox {
                        model: [i18n("Relevant"), i18n("Newest first")]
                        currentIndex: view.sort === "date" ? 1 : 0
                        onActivated: view.sort = currentIndex ? "date" : "relevance"
                    }
                    QQC2.CheckBox { text: i18n("Awarded"); checked: view.awardedOnly; onClicked: view.awardedOnly = checked }
                    QQC2.CheckBox { text: i18n("Hi-Res"); checked: view.hiResOnly; onClicked: view.hiResOnly = checked }
                }
                PlasmaComponents.Label { text: i18n("Labels") }
                Flow {
                    Layout.fillWidth: true
                    Layout.preferredHeight: childrenRect.height
                    spacing: Kirigami.Units.smallSpacing
                    Repeater {
                        model: view.labels
                        delegate: QQC2.CheckBox {
                            required property var modelData
                            text: modelData.name
                            checked: view.selectedLabels.includes(modelData.name)
                            onClicked: view.toggleLabel(modelData.name)
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Repeater {
                model: [{ id: "recent", name: i18n("Recent") }, { id: "results", name: i18n("Results") },
                        { id: "discover", name: i18n("Discover") }, { id: "awarded", name: i18n("Awarded") }]
                delegate: PlasmaComponents.ToolButton {
                    required property var modelData
                    text: modelData.name
                    checkable: true
                    checked: view.tab === modelData.id
                    onClicked: view.setTab(modelData.id)
                }
            }
        }
        RowLayout {
            visible: view.tab === "discover"
            PlasmaComponents.Label { text: i18n("Genre") }
            QQC2.ComboBox {
                model: view.genres
                textRole: "name"
                onActivated: { view.genre = String(view.genres[currentIndex].id); view.loadTab(0) }
            }
        }
        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: view.error !== "" || view.message !== ""
            text: view.error || view.message
            color: view.error ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            wrapMode: Text.Wrap
        }
        QQC2.ScrollView {
            id: albumScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: albumScroll.availableWidth
                spacing: Kirigami.Units.smallSpacing
                Repeater {
                    model: view.shown
                    delegate: AlbumCard {
                        required property var modelData
                        Layout.fillWidth: true
                        app: view.app
                        album: modelData
                        query: view.tab === "results" ? view.query : ""
                        canPlay: view.usable
                        onPlayed: (mode) => view.notice(mode === "append" ? i18n("Added to queue") : i18n("Playing"))
                    }
                }
                PlasmaComponents.ToolButton {
                    visible: view.more && (view.tab === "results" || view.tab === "discover")
                    text: view.tab === "results" ? i18n("Load more results") : i18n("More releases")
                    onClicked: view.tab === "results" ? view.search(view.nextScan) : view.loadTab(view.nextOffset)
                }
                PlasmaComponents.Label {
                    visible: !view.busy && !view.shown.length && view.error === ""
                    text: view.tab === "results" ? i18n("Search for albums") : i18n("No albums here yet")
                }
            }
        }
    }

    QQC2.Dialog {
        id: settingsDialog
        modal: true
        title: i18n("AI settings")
        standardButtons: QQC2.Dialog.Save | QQC2.Dialog.Cancel
        onAccepted: view.saveAI()
        ColumnLayout {
            width: 350
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: i18n("Ask AI researches reviews and matches playable Qobuz releases. The request and candidates go to the selected provider.")
                wrapMode: Text.Wrap
            }
            QQC2.ComboBox {
                Layout.fillWidth: true
                model: [{ name: i18n("Claude account"), id: "claude_account" },
                        { name: i18n("Claude API"), id: "claude" }, { name: i18n("OpenAI API"), id: "openai" }]
                textRole: "name"
                currentIndex: ["claude_account", "claude", "openai"].indexOf(view.aiProvider)
                onActivated: {
                    view.aiProvider = model[currentIndex].id
                    view.aiModel = view.aiSettings.defaults && view.aiSettings.defaults[view.aiProvider] || ""
                }
            }
            QQC2.TextField {
                Layout.fillWidth: true
                placeholderText: i18n("Model")
                text: view.aiModel
                onTextEdited: view.aiModel = text
            }
            QQC2.TextField {
                Layout.fillWidth: true
                visible: view.aiProvider !== "claude_account"
                placeholderText: view.aiSettings.configured ? i18n("Leave blank to keep saved key") : i18n("API key")
                echoMode: TextInput.Password
                text: view.aiKey
                onTextEdited: view.aiKey = text
            }
        }
    }
}
