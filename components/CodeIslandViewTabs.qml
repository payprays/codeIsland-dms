import QtQuick 2.15
import "../lib/CodeIslandI18n.js" as LocalI18n
import "../lib/CodeIslandStyle.js" as Style

Row {
    id: root

    property string currentMode: "all"
    property string locale: "en"
    property var theme: null
    property var visualStyle: null
    readonly property var tabs: [
        {
            key: "all",
        },
        {
            key: "finished",
        },
        {
            key: "active",
        },
    ]

    signal modeRequested(string mode)

    spacing: Style.spacing.xs

    Repeater {
        model: root.tabs

        delegate: Rectangle {
            id: tab

            required property var modelData
            readonly property bool active: root.currentMode === modelData.key
            property bool hovered: false

            width: tabLabel.implicitWidth + (Style.spacing.s * 2)
            height: Style.size.boardChipHeight
            radius: 4
            color: active ? Style.paletteColor(root.theme, "boardTabActive") : (hovered ? Style.alpha(Style.paletteColor(root.theme, "textStrong"), 0.07) : Style.paletteColor(root.theme, "boardTab"))
            border.color: active ? Style.alpha(Style.paletteColor(root.theme, "lineUser"), 0.30) : Style.paletteColor(root.theme, "boardCardBorder")
            border.width: Style.size.border

            Text {
                id: tabLabel

                anchors.centerIn: parent
                color: tab.active ? Style.paletteColor(root.theme, "lineUser") : Style.paletteColor(root.theme, "textDim")
                font.family: Style.fontFamily(root.visualStyle)
                font.pixelSize: Style.font.boardChip
                font.weight: Font.Black
                text: LocalI18n.tabLabel(tab.modelData.key, root.locale)
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                hoverEnabled: true
                onEntered: tab.hovered = true
                onExited: tab.hovered = false
                onClicked: root.modeRequested(tab.modelData.key)
            }
        }
    }
}
