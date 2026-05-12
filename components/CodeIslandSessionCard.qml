import QtQuick 2.15
import "../lib/CodeIslandI18n.js" as LocalI18n
import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property var card: ({
    })
    property bool primary: false
    property bool selected: false
    property var theme: null
    property var visualStyle: null
    property string locale: "en"
    readonly property string providerKey: root.cardString("providerKey") || "other"
    readonly property string providerLabel: root.cardString("providerLabel") || LocalI18n.tr("Other", root.locale)
    readonly property string status: root.cardString("status") || "idle"
    readonly property string sessionId: root.cardString("sessionId")
    readonly property string title: root.cardString("title") || "CodeIsland"
    readonly property string suffix: root.cardString("suffix")
    readonly property string primaryLine: root.cardString("primaryLine") || LocalI18n.statusLabel(root.status, root.locale)
    readonly property string secondaryLine: root.cardString("secondaryLine")
    readonly property string timeText: root.cardString("timeText") || "<1m"
    readonly property string appLabel: root.cardString("appLabel") || root.providerLabel
    readonly property bool hasInteraction: !!(root.card && root.card.hasInteraction)
    property bool hovered: false
    readonly property bool runningStatus: root.status === "running"
    readonly property bool completedStatus: root.status === "completed"
    readonly property color accentColor: Style.providerAccentFor(root.providerKey, root.theme)
    readonly property color statusAccent: Style.accentFor(root.status, root.theme)
    readonly property color neutralAccent: Style.paletteColor(root.theme, "textDim")
    readonly property color selectedAccent: Style.paletteColor(root.theme, "lineUser")
    readonly property bool emphasized: root.primary || root.hasInteraction || root.selected
    readonly property bool showTimeChip: Style.boolValue(root.visualStyle, "showTimeChip")
    readonly property bool showAppChip: Style.boolValue(root.visualStyle, "showAppChip")
    readonly property bool showRightRail: root.showTimeChip || root.showAppChip
    readonly property real rightRailWidth: root.showRightRail ? Style.sizeValue(root.visualStyle, "boardRightRailWidth") : 0
    readonly property real cardRadius: Style.radiusValue(root.visualStyle, "boardCard")
    readonly property color cardFill: root.selected ? Style.paletteColor(root.theme, "boardCardHot") : (root.hovered ? (root.runningStatus ? Style.paletteColor(root.theme, "boardCardHot") : Style.alpha(root.accentColor, root.emphasized ? 0.20 : 0.14)) : (root.emphasized && !root.runningStatus ? Style.paletteColor(root.theme, "boardCardHot") : Style.paletteColor(root.theme, "boardCard")))
    readonly property color cardStroke: root.selected ? Style.alpha(root.selectedAccent, 0.98) : (root.completedStatus ? (root.primary || root.hovered ? Style.alpha(root.accentColor, 0.34) : Style.paletteColor(root.theme, "boardCardBorder")) : (root.hasInteraction ? Style.alpha(root.statusAccent, 0.56) : (root.runningStatus ? Style.alpha(Style.paletteColor(root.theme, "textStrong"), root.hovered ? 0.18 : 0.10) : (root.primary || root.hovered ? Style.alpha(root.accentColor, 0.42) : Style.paletteColor(root.theme, "boardCardBorder")))))
    readonly property color commandColor: root.runningStatus ? root.neutralAccent : Style.paletteColor(root.theme, "lineUser")
    readonly property color outputColor: root.runningStatus ? root.neutralAccent : Style.paletteColor(root.theme, "lineAgent")
    readonly property color appChipColor: root.runningStatus ? root.neutralAccent : root.accentColor

    signal sessionActivated(string sessionId)

    function cardString(key) {
        if (!root.card || typeof root.card !== "object")
            return "";

        return typeof root.card[key] === "string" ? root.card[key] : "";
    }

    implicitWidth: Style.sizeValue(root.visualStyle, "boardWidth") - (Style.spacing.xl * 4)
    implicitHeight: Style.sizeValue(root.visualStyle, "boardCardHeight")

    Rectangle {
        anchors.fill: parent
        radius: root.cardRadius
        antialiasing: true
        color: root.cardFill
        border.color: root.cardStroke
        border.width: root.selected ? 2 : Style.size.border
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 1
        anchors.rightMargin: 1
        height: 1
        radius: root.cardRadius
        color: Style.alpha(Style.paletteColor(root.theme, "textStrong"), root.emphasized || root.hovered ? 0.14 : 0.07)
        visible: parent.height > 2
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 4
        radius: Math.max(0, root.cardRadius - 4)
        antialiasing: true
        color: "transparent"
        border.color: Style.alpha(root.selectedAccent, 0.52)
        border.width: root.selected ? 2 : 0
        visible: root.selected
        z: 20
    }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: 5
        anchors.topMargin: 10
        anchors.bottomMargin: 10
        width: 4
        radius: 2
        color: root.selectedAccent
        opacity: root.selected ? 1 : 0
        visible: root.selected
        z: 21
    }

    Row {
        anchors.fill: parent
        anchors.leftMargin: Style.spacing.l
        anchors.rightMargin: Style.spacing.l
        anchors.topMargin: Style.spacing.s
        anchors.bottomMargin: Style.spacing.s
        spacing: Style.spacing.m

        CodeIslandProviderGlyph {
            providerKey: root.providerKey
            status: root.status
            theme: root.theme
            visualStyle: root.visualStyle
            anchors.verticalCenter: parent.verticalCenter
        }

        Column {
            width: Math.max(0, parent.width - Style.size.boardGlyphWidth - rightRail.width - (parent.spacing * 2))
            anchors.verticalCenter: parent.verticalCenter
            spacing: 5

            Row {
                width: parent.width
                spacing: Style.spacing.xs

                Text {
                    width: Math.max(0, parent.width - suffixText.width - parent.spacing)
                    color: Style.paletteColor(root.theme, "textStrong")
                    elide: Text.ElideRight
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardTitle
                    font.weight: Font.Black
                    maximumLineCount: 1
                    text: root.title
                }

                Text {
                    id: suffixText

                    color: Style.paletteColor(root.theme, "textDim")
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardMeta
                    font.weight: Font.DemiBold
                    text: root.suffix
                    visible: text.length > 0
                }

            }

            Row {
                width: parent.width
                spacing: Style.spacing.xs

                Text {
                    color: root.commandColor
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardBody
                    font.weight: Font.Black
                    text: ">"
                }

                Text {
                    width: Math.max(0, parent.width - Style.spacing.l)
                    color: Style.paletteColor(root.theme, "textStrong")
                    elide: Text.ElideRight
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardBody
                    font.weight: Font.DemiBold
                    maximumLineCount: 1
                    text: root.primaryLine
                }

            }

            Row {
                width: parent.width
                spacing: Style.spacing.xs

                Text {
                    color: root.outputColor
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardBody
                    font.weight: Font.Black
                    text: "$"
                }

                Text {
                    width: Math.max(0, parent.width - Style.spacing.l)
                    color: root.hasInteraction ? Style.paletteColor(root.theme, "textStrong") : Style.paletteColor(root.theme, "lineMuted")
                    elide: Text.ElideRight
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardBody
                    font.weight: root.hasInteraction ? Font.DemiBold : Font.Medium
                    maximumLineCount: 1
                    text: root.secondaryLine
                }

            }

        }

        Column {
            id: rightRail

            width: root.rightRailWidth
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.spacing.s
            visible: root.showRightRail

            Rectangle {
                anchors.right: parent.right
                width: Math.max(Style.size.boardChipMinWidth, timeLabel.implicitWidth + (Style.spacing.s * 2))
                height: Style.size.boardChipHeight
                radius: Style.radius.chip
                color: Style.alpha(Style.paletteColor(root.theme, "textStrong"), 0.1)
                visible: root.showTimeChip

                Text {
                    id: timeLabel

                    anchors.centerIn: parent
                    color: Style.paletteColor(root.theme, "textMuted")
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardChip
                    font.weight: Font.Black
                    text: root.timeText
                }

            }

            Rectangle {
                anchors.right: parent.right
                width: Math.max(Style.size.boardAppChipMinWidth, appLabelText.implicitWidth + (Style.spacing.s * 2))
                height: Style.size.boardChipHeight
                radius: Style.radius.chip
                color: Style.alpha(root.appChipColor, root.runningStatus ? 0.08 : 0.14)
                border.color: Style.alpha(root.appChipColor, root.runningStatus ? 0.16 : 0.24)
                border.width: Style.size.border
                visible: root.showAppChip

                Text {
                    id: appLabelText

                    anchors.centerIn: parent
                    elide: Text.ElideRight
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardChip
                    font.weight: Font.Black
                    maximumLineCount: 1
                    color: root.runningStatus ? Style.paletteColor(root.theme, "textMuted") : root.accentColor
                    text: root.appLabel + " ->"
                }

            }

        }

    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        cursorShape: root.sessionId.length ? Qt.PointingHandCursor : Qt.ArrowCursor
        hoverEnabled: true
        onEntered: root.hovered = true
        onExited: root.hovered = false
        onClicked: {
            if (root.sessionId.length)
                root.sessionActivated(root.sessionId);
        }
    }

}
