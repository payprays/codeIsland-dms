import QtQuick 2.15

import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property string providerKey: "other"
    property string status: "idle"
    property var theme: null
    property var visualStyle: null
    property color accentColor: Style.providerAccentFor(root.providerKey, root.theme)
    readonly property color statusAccent: Style.accentFor(root.status, root.theme)
    readonly property bool active: root.status === "running"
        || root.status === "waiting_approval"
        || root.status === "waiting_answer"
    readonly property bool running: root.status === "running"
    readonly property bool alerting: root.status === "waiting_approval" || root.status === "waiting_answer"
    readonly property bool complete: root.status === "completed"
    readonly property bool failed: root.status === "failed" || root.status === "cancelled"
    readonly property real runningMaxOpacity: Style.motionValue(root.visualStyle, "breatheMaxOpacity")
    readonly property real runningMinOpacity: Math.min(0.25, root.runningMaxOpacity * 0.5)
    readonly property color neutralColor: Style.paletteColor(root.theme, "textDim")
    readonly property color liveColor: root.running ? Style.paletteColor(root.theme, "lineUser") : root.accentColor
    readonly property color badgeColor: root.running ? Style.paletteColor(root.theme, "boardCardHot") : Style.alpha(root.accentColor, root.active ? 0.22 : 0.14)
    readonly property color badgeBorderColor: root.running ? Style.alpha(root.liveColor, Math.min(root.runningMaxOpacity, root.active ? 0.46 : 0.28)) : Style.alpha(root.alerting ? root.statusAccent : root.accentColor, root.active ? 0.62 : 0.42)
    readonly property string iconName: root.iconNameFor(root.providerKey)
    readonly property string iconSource: "../assets/provider-icons/" + root.iconName + ".svg"

    implicitWidth: Style.size.boardGlyphWidth
    implicitHeight: 44

    function iconNameFor(providerKey) {
        switch (providerKey) {
        case "claude":
        case "codex":
        case "cursor":
        case "gemini":
        case "opencode":
            return providerKey;
        default:
            return "other";
        }
    }

    Rectangle {
        id: glow

        anchors.centerIn: badge
        width: badge.width + 8
        height: badge.height + 8
        radius: 13
        color: root.complete ? Style.paletteColor(root.theme, "successAccent") : (root.alerting ? root.statusAccent : root.liveColor)
        opacity: root.running ? root.runningMinOpacity : (root.active ? 0.10 : 0.04)
        visible: root.active || root.complete || root.failed

        SequentialAnimation on opacity {
            running: root.running
            loops: Animation.Infinite

            NumberAnimation {
                to: root.runningMinOpacity
                duration: Style.motionValue(root.visualStyle, "breathe")
                easing.type: Easing.InOutQuad
            }

            NumberAnimation {
                to: root.runningMaxOpacity
                duration: Style.motionValue(root.visualStyle, "breathe")
                easing.type: Easing.InOutQuad
            }
        }
    }

    Rectangle {
        id: badge

        anchors.centerIn: parent
        width: Style.size.boardGlyphBadge
        height: Style.size.boardGlyphBadge
        radius: 10
        antialiasing: true
        color: root.badgeColor
        border.color: root.badgeBorderColor
        border.width: Style.size.border
    }

    Rectangle {
        anchors.left: badge.left
        anchors.right: badge.right
        anchors.top: badge.top
        anchors.leftMargin: 2
        anchors.rightMargin: 2
        height: 1
        radius: 1
        color: Style.alpha(Style.paletteColor(root.theme, "textStrong"), 0.18)
    }

    Item {
        id: iconGlyph

        anchors.centerIn: badge
        width: Style.size.boardGlyphIcon
        height: Style.size.boardGlyphIcon
        opacity: root.running ? root.runningMaxOpacity : 0.92

        Image {
            id: providerIcon

            anchors.fill: parent
            asynchronous: true
            fillMode: Image.PreserveAspectFit
            mipmap: true
            opacity: status === Image.Ready ? (root.running ? root.runningMaxOpacity : 0.96) : 0
            smooth: true
            source: root.iconSource
            sourceSize.height: Math.max(1, height * 2)
            sourceSize.width: Math.max(1, width * 2)
            visible: status === Image.Ready
        }

        Item {
            anchors.fill: parent
            opacity: root.running ? root.runningMaxOpacity : 0.82
            visible: providerIcon.status !== Image.Ready

            Rectangle {
                width: 20
                height: 16
                radius: 4
                anchors.centerIn: parent
                color: "transparent"
                border.color: Style.paletteColor(root.theme, "textStrong")
                border.width: 2
            }

            Rectangle {
                width: 4
                height: 4
                radius: 2
                x: 6
                y: 9
                color: Style.paletteColor(root.theme, "textStrong")
            }

            Rectangle {
                width: 4
                height: 4
                radius: 2
                x: 13
                y: 9
                color: Style.paletteColor(root.theme, "textStrong")
                opacity: 0.72
            }
        }
    }

    Rectangle {
        width: 10
        height: 10
        radius: width / 2
        anchors.right: badge.right
        anchors.top: badge.top
        anchors.rightMargin: -2
        anchors.topMargin: -2
        color: root.failed ? Style.paletteColor(root.theme, "failedAccent") : (root.complete ? Style.paletteColor(root.theme, "successAccent") : root.liveColor)
        border.color: Style.paletteColor(root.theme, "boardCard")
        border.width: Style.size.border
        opacity: root.running ? root.runningMaxOpacity : (root.active || root.complete || root.failed ? 1 : 0.72)
    }

    Rectangle {
        width: 15
        height: 2
        radius: 1
        anchors.horizontalCenter: badge.horizontalCenter
        anchors.bottom: badge.bottom
        anchors.bottomMargin: 4
        color: Style.alpha(root.running ? root.neutralColor : (root.alerting ? root.statusAccent : root.accentColor), root.active ? 0.82 : 0.48)

    }

    SequentialAnimation on scale {
        running: root.running || root.alerting
        loops: Animation.Infinite

        NumberAnimation {
            to: root.running ? 1.045 : 1.06
            duration: root.running ? Style.motionValue(root.visualStyle, "breathe") : Style.motion.pulseHalf
            easing.type: Easing.InOutQuad
        }

        NumberAnimation {
            to: 1.0
            duration: root.running ? Style.motionValue(root.visualStyle, "breathe") : Style.motion.pulseHalf
            easing.type: Easing.InOutQuad
        }
    }
}
