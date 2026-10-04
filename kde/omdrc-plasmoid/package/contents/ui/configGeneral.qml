import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.kquickcontrols as KQuickControls

KCM.SimpleKCM {
    id: root
    property alias cfg_host: hostField.text
    property alias cfg_port: portField.value
    property string cfg_meterStyle
    property string cfg_meterPair
    property alias cfg_showSpectrum: spectrumBox.checked
    property alias cfg_showDr: drBox.checked
    property alias cfg_showBalance: balanceBox.checked
    property alias cfg_spectrumBelow: spectrumBelowBox.checked
    property alias cfg_autoScreenDelay: autoScreenDelayBox.checked
    property int cfg_screenDelayMs
    property string cfg_coverMode
    property alias cfg_showTitle: titleBox.checked
    property string cfg_backgroundMode
    property string cfg_backgroundColor
    property int cfg_backgroundOpacity
    property int cfg_panelLength
    property alias cfg_panelInset: panelInsetField.value
    property alias cfg_panelMarginStart: panelMarginStartField.value
    property alias cfg_panelMarginEnd: panelMarginEndField.value

    readonly property var meterStyles: [
        { value: "bars", text: i18n("Level bars") },
        { value: "needles", text: i18n("VU needles") },
        { value: "off", text: i18n("None") },
    ]
    readonly property var meterPairs: [
        { value: "auto", text: i18n("Automatic") },
        { value: "side", text: i18n("Channels side by side") },
        { value: "stacked", text: i18n("Channels stacked") },
    ]
    readonly property var backgroundModes: [
        { value: "default", text: i18n("Plasma default") },
        { value: "none", text: i18n("None") },
        { value: "transparent", text: i18n("Theme color with transparency") },
        { value: "custom", text: i18n("Custom color") },
    ]
    readonly property var coverModes: [
        { value: "off", text: i18n("Hidden") },
        { value: "pane", text: i18n("Beside the meters") },
        { value: "background", text: i18n("Behind the meters") },
    ]

    function indexOf(list, value) {
        for (let i = 0; i < list.length; i++)
            if (list[i].value === value) return i
        return 0
    }

    property int boxDelayEstimateMs: -1
    property int boxHoldBackMs: -1
    // The box-wide chain-delay margin (ms), -1 until read. Shared by every screen,
    // not just this plasmoid; applied to the box live, so it is not a cfg_ value.
    property int boxMarginMs: -1
    property string estimateStatus: i18n("Enter the box address to read its delay estimate.")

    function settingsUrl() {
        const host = hostField.text.trim()
        if (!host) return ""
        const authority = host.indexOf(":") >= 0 && host[0] !== "[" ? "[" + host + "]" : host
        return "http://" + authority + ":" + portField.value + "/spectrum/settings"
    }

    function marginUrl() {
        const host = hostField.text.trim()
        if (!host) return ""
        const authority = host.indexOf(":") >= 0 && host[0] !== "[" ? "[" + host + "]" : host
        return "http://" + authority + ":" + portField.value + "/spectrum/margin"
    }

    // Set the box-wide margin live. A floor, not a lock: a late screen's
    // calibration can still raise it, and aligned screens re-absorb the change.
    function setBoxMargin(ms) {
        const url = marginUrl()
        if (!url) return
        const value = Math.max(0, Math.min(5000, Math.round(ms)))
        const xhr = new XMLHttpRequest()
        xhr.timeout = 3000
        xhr.onreadystatechange = function () {
            if (xhr.readyState === XMLHttpRequest.DONE) root.refreshDelayEstimate()
        }
        xhr.open("POST", url)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ margin_ms: value }))
        boxMarginMs = value              // optimistic; the refresh confirms it
    }

    function refreshDelayEstimate() {
        const url = settingsUrl()
        if (!url) {
            boxDelayEstimateMs = -1
            boxHoldBackMs = -1
            boxMarginMs = -1
            estimateStatus = i18n("Enter the box address to read its delay estimate.")
            return
        }
        const xhr = new XMLHttpRequest()
        xhr.timeout = 3000
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            if (url !== root.settingsUrl()) return
            let data = null
            try { data = JSON.parse(xhr.responseText) } catch (error) {}
            const terms = data && data.drc_delay_terms_ms
            const estimate = terms && Number(terms.margin)
            if (xhr.status === 200 && Number.isFinite(estimate)) {
                boxDelayEstimateMs = Math.max(0, Math.min(3000, Math.round(estimate)))
                boxHoldBackMs = Math.max(0, Math.round(Number(data.drc_delay_base_ms) || 0))
                const configured = Number(data.drc_delay_margin_ms)
                boxMarginMs = Number.isFinite(configured) ? Math.max(0, Math.round(configured)) : -1
                estimateStatus = i18n("Local screen wait estimate: %1 ms; analyzer frames are held back by %2 ms before they leave the box.", boxDelayEstimateMs, boxHoldBackMs)
            } else {
                boxDelayEstimateMs = -1
                boxHoldBackMs = -1
                boxMarginMs = -1
                estimateStatus = i18n("Could not read the estimate. Check the box address and that its analyzer is available.")
            }
        }
        xhr.open("GET", url)
        xhr.send()
    }

    Component.onCompleted: refreshDelayEstimate()

    Timer {
        interval: 5000
        repeat: true
        running: hostField.text.trim() !== ""
        onTriggered: root.refreshDelayEstimate()
    }

    Kirigami.FormLayout {
        QQC2.TextField {
            id: hostField
            Kirigami.FormData.label: i18n("Box:")
            placeholderText: i18n("host name or address of omdrcctrl")
            onEditingFinished: root.refreshDelayEstimate()
        }
        QQC2.SpinBox {
            id: portField
            Kirigami.FormData.label: i18n("Port:")
            from: 1
            to: 65535
            textFromValue: (value) => String(value)   // no thousands separator
            onValueModified: root.refreshDelayEstimate()
        }

        Item { Kirigami.FormData.isSection: true }

        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Meters:")
            model: meterStyles
            textRole: "text"
            currentIndex: indexOf(meterStyles, cfg_meterStyle)
            onActivated: cfg_meterStyle = meterStyles[currentIndex].value
        }
        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Channel layout:")
            model: meterPairs
            textRole: "text"
            currentIndex: indexOf(meterPairs, cfg_meterPair)
            onActivated: cfg_meterPair = meterPairs[currentIndex].value
        }
        QQC2.CheckBox {
            id: spectrumBox
            Kirigami.FormData.label: i18n("Spectrum:")
            text: i18n("Show the spectrum analyzer")
        }
        QQC2.CheckBox {
            id: drBox
            Kirigami.FormData.label: i18n("Meters:")
            text: i18n("Show live DR meter and history")
        }
        QQC2.CheckBox {
            id: balanceBox
            text: i18n("Show live channel balance meter")
        }
        QQC2.CheckBox {
            id: spectrumBelowBox
            text: i18n("Place spectrum below meters in desktop and popup when the cover is not a pane")
        }

        Item { Kirigami.FormData.isSection: true }

        QQC2.CheckBox {
            id: autoScreenDelayBox
            Kirigami.FormData.label: i18n("Meter and spectrum timing:")
            text: i18n("Use the box's live delay estimate")
            onToggled: root.refreshDelayEstimate()
        }
        QQC2.Label {
            Kirigami.FormData.label: i18n("Box estimate:")
            text: estimateStatus
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        QQC2.Label {
            Kirigami.FormData.label: i18n("Manual screen delay:")
            text: i18n("A 1 ms adjustment for this plasmoid only. The slider and number use milliseconds, matching the web UI's Applied screen delay.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            Kirigami.FormData.label: i18n("Delay (ms):")
            enabled: !autoScreenDelayBox.checked
            QQC2.Slider {
                id: screenDelaySlider
                from: 0; to: 3000; stepSize: 1
                value: cfg_screenDelayMs
                onMoved: cfg_screenDelayMs = Math.round(value)
                Layout.fillWidth: true
            }
            QQC2.SpinBox {
                id: screenDelaySpin
                from: 0; to: 3000; stepSize: 1
                value: cfg_screenDelayMs
                textFromValue: (value) => String(value)
                valueFromText: (text) => parseInt(text) || 0
                onValueModified: cfg_screenDelayMs = value
            }
        }
        Item { Kirigami.FormData.isSection: true }
        QQC2.Label {
            Kirigami.FormData.label: i18n("Instructions:")
            text: i18n("Every client receives its own copy of the same analyzer frames and applies its own wait before drawing. Automatic uses the effective margin reported by /spectrum/settings; it follows the box's FIR peak and partition estimate and does not measure this screen's network transit. For a local display, this is a useful starting point. To tune by ear, open the box's web UI -> Config -> Meter timing -> Tune with clicks, then set the same Applied screen delay here with Auto off. The click test uses the speakers and meters, so it needs no microphone. These settings change only this plasmoid; they do not change the box-wide margin below or the phone's profile.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Item { Kirigami.FormData.isSection: true }

        QQC2.Label {
            Kirigami.FormData.label: i18n("Box chain-delay margin:")
            text: i18n("Box-wide — one value shared by every screen, not just this plasmoid, and applied to the box at once. The box trims this much from its own measured chain delay, so the meters leave it this far ahead of the sound and each screen waits out the rest. Raise it to cut the shared lag on a fast local display (at the limit the box holds nothing back and the meters lead the sound); lower it if the meters reach a slow screen after the sound. It is a floor, not a lock: a late screen's calibration may still raise it, and screens that follow the box estimate re-absorb the change and stay aligned.")
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        RowLayout {
            Kirigami.FormData.label: i18n("Margin (ms):")
            enabled: boxMarginMs >= 0
            QQC2.SpinBox {
                id: boxMarginSpin
                from: 0; to: 5000; stepSize: 50
                value: Math.max(0, boxMarginMs)
                textFromValue: (value) => String(value)
                valueFromText: (text) => parseInt(text) || 0
                onValueModified: root.setBoxMargin(value)
            }
            QQC2.Button {
                text: i18n("Reset")
                onClicked: root.setBoxMargin(0)
            }
        }

        Item { Kirigami.FormData.isSection: true }

        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Cover:")
            model: coverModes
            textRole: "text"
            currentIndex: indexOf(coverModes, cfg_coverMode)
            onActivated: cfg_coverMode = coverModes[currentIndex].value
        }
        RowLayout {
            Kirigami.FormData.label: i18n("Length in panel:")
            QQC2.CheckBox {
                id: autoLength
                text: i18n("Automatic")
                checked: cfg_panelLength <= 0
                onToggled: cfg_panelLength = checked ? 0 : lengthField.value
            }
            QQC2.SpinBox {
                id: lengthField
                enabled: !autoLength.checked
                from: 16
                to: 4000
                stepSize: 8
                value: cfg_panelLength > 0 ? cfg_panelLength : 240
                textFromValue: (value) => i18n("%1 px", value)
                valueFromText: (text) => parseInt(text) || 240
                onValueModified: cfg_panelLength = value
            }
        }
        QQC2.CheckBox {
            id: titleBox
            Kirigami.FormData.label: i18n("Track:")
            text: i18n("Show title and artist on the desktop and in the popup")
        }
        QQC2.SpinBox {
            id: panelInsetField
            Kirigami.FormData.label: i18n("Panel edge margin:")
            from: 0
            to: 12
            textFromValue: (value) => i18n("%1 px", value)
            valueFromText: (text) => parseInt(text) || 0
        }
        RowLayout {
            Kirigami.FormData.label: i18n("Panel outer margins:")
            QQC2.Label { text: i18n("top/left") }
            QQC2.SpinBox {
                id: panelMarginStartField
                from: 0
                to: 14
                textFromValue: (value) => i18n("%1 px", value)
                valueFromText: (text) => parseInt(text) || 0
            }
            QQC2.Label { text: i18n("bottom/right") }
            QQC2.SpinBox {
                id: panelMarginEndField
                from: 0
                to: 14
                textFromValue: (value) => i18n("%1 px", value)
                valueFromText: (text) => parseInt(text) || 0
            }
        }

        RowLayout {
            Kirigami.FormData.label: i18n("Background:")
            QQC2.ComboBox {
                model: backgroundModes
                textRole: "text"
                currentIndex: indexOf(backgroundModes, cfg_backgroundMode)
                onActivated: cfg_backgroundMode = backgroundModes[currentIndex].value
            }
            KQuickControls.ColorButton {
                visible: cfg_backgroundMode === "custom"
                showAlphaChannel: true
                dialogTitle: i18n("Background color")
                color: cfg_backgroundColor
                onAccepted: (color) => cfg_backgroundColor = String(color)
            }
        }
        RowLayout {
            visible: cfg_backgroundMode === "transparent"
            Kirigami.FormData.label: i18n("Background opacity:")
            QQC2.Slider {
                from: 0; to: 100; stepSize: 5
                value: cfg_backgroundOpacity
                onMoved: cfg_backgroundOpacity = Math.round(value)
                Layout.fillWidth: true
            }
            QQC2.Label { text: cfg_backgroundOpacity + "%" }
        }
    }
}
