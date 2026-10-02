import QtQuick
import org.kde.kirigami as Kirigami

/* The cover of what plays (omdrcctrl's /qconnect/art), or a placeholder
 * when the box has none. */
Item {
    id: cover

    property string source: ""
    property bool crop: false

    Image {
        id: image
        anchors.fill: parent
        source: cover.source
        fillMode: cover.crop ? Image.PreserveAspectCrop : Image.PreserveAspectFit
        asynchronous: true
        cache: true
        smooth: true
        sourceSize.width: 600
        sourceSize.height: 600
        visible: status === Image.Ready
    }
    Kirigami.Icon {
        anchors.centerIn: parent
        width: Math.min(parent.width, parent.height) * 0.7
        height: width
        visible: !cover.crop && image.status !== Image.Ready
        source: "media-optical-audio"
        opacity: 0.35
    }
}
