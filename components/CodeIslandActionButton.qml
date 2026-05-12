import QtQuick 2.15

import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property string label: ""
    property var theme: null
    property var visualStyle: null
    property color fillColor: Style.paletteColor(root.theme, "buttonNeutral")
    property color textColor: Style.paletteColor(root.theme, "buttonNeutralText")
    property bool outlined: false
    property bool hovered: false

    signal clicked()

    implicitWidth: Math.max(Style.size.buttonMinWidth, buttonText.implicitWidth + (Style.spacing.l * 2))
    implicitHeight: Style.size.buttonHeight

    Rectangle {
        id: background

        anchors.fill: parent
        radius: Style.radiusValue(root.visualStyle, "button")
        color: root.enabled ? (root.outlined ? "transparent" : root.fillColor) : Style.paletteColor(root.theme, "buttonNeutral")
        border.color: root.enabled ? root.fillColor : Style.paletteColor(root.theme, "shellOutline")
        border.width: Style.size.border
        opacity: root.enabled ? 1 : 0.5
    }

    Rectangle {
        anchors.fill: parent
        radius: Style.radiusValue(root.visualStyle, "button")
        color: Style.alpha(root.outlined ? root.fillColor : root.textColor, root.hovered ? 0.12 : 0)
        visible: root.enabled
    }

    Text {
        id: buttonText

        anchors.centerIn: parent
        color: root.outlined ? root.fillColor : root.textColor
        font.family: Style.fontFamily(root.visualStyle)
        font.pixelSize: Style.font.button
        font.weight: Font.DemiBold
        text: root.label
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        enabled: root.enabled
        hoverEnabled: true
        onEntered: root.hovered = true
        onExited: root.hovered = false
        onClicked: root.clicked()
    }
}
