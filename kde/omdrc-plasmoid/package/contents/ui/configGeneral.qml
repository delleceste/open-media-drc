import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    property alias cfg_host: hostField.text
    property alias cfg_port: portField.value
    property string cfg_meterStyle
    property alias cfg_showSpectrum: spectrumBox.checked
    property string cfg_coverMode
    property alias cfg_showTitle: titleBox.checked
    property alias cfg_streamWhenIdle: idleBox.checked

    readonly property var meterStyles: [
        { value: "bars", text: i18n("Level bars") },
        { value: "needles", text: i18n("VU needles") },
        { value: "off", text: i18n("None") },
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

    Kirigami.FormLayout {
        QQC2.TextField {
            id: hostField
            Kirigami.FormData.label: i18n("Box:")
            placeholderText: i18n("host name or address of omdrcctrl")
        }
        QQC2.SpinBox {
            id: portField
            Kirigami.FormData.label: i18n("Port:")
            from: 1
            to: 65535
            textFromValue: (value) => String(value)   // no thousands separator
        }

        Item { Kirigami.FormData.isSection: true }

        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Meters:")
            model: meterStyles
            textRole: "text"
            currentIndex: indexOf(meterStyles, cfg_meterStyle)
            onActivated: cfg_meterStyle = meterStyles[currentIndex].value
        }
        QQC2.CheckBox {
            id: spectrumBox
            Kirigami.FormData.label: i18n("Spectrum:")
            text: i18n("Show the spectrum analyzer")
        }
        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Cover:")
            model: coverModes
            textRole: "text"
            currentIndex: indexOf(coverModes, cfg_coverMode)
            onActivated: cfg_coverMode = coverModes[currentIndex].value
        }
        QQC2.CheckBox {
            id: titleBox
            Kirigami.FormData.label: i18n("Track:")
            text: i18n("Show title and artist on the desktop and in the popup")
        }

        Item { Kirigami.FormData.isSection: true }

        QQC2.CheckBox {
            id: idleBox
            Kirigami.FormData.label: i18n("Analyzer:")
            text: i18n("Keep streaming while paused or stopped")
        }
        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 22
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
            text: i18n("Off by default: while nothing plays the widget closes its stream, so the box can switch its analyzer FIFO output off.")
        }
    }
}
