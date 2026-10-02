import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.kquickcontrols as KQuickControls

KCM.SimpleKCM {
    property alias cfg_host: hostField.text
    property alias cfg_port: portField.value
    property string cfg_meterStyle
    property alias cfg_showSpectrum: spectrumBox.checked
    property string cfg_coverMode
    property alias cfg_showTitle: titleBox.checked
    property string cfg_backgroundMode
    property string cfg_backgroundColor
    property int cfg_panelLength

    readonly property var meterStyles: [
        { value: "bars", text: i18n("Level bars") },
        { value: "needles", text: i18n("VU needles") },
        { value: "off", text: i18n("None") },
    ]
    readonly property var backgroundModes: [
        { value: "default", text: i18n("Plasma default") },
        { value: "none", text: i18n("None") },
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
    }
}
