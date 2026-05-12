import QtQuick 2.15

import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property string status: "idle"
    property string providerKey: "other"
    property bool connected: false
    property bool animated: false
    property var theme: null
    property color accentColor: Style.accentFor(root.status, root.theme)

    readonly property bool running: root.status === "running"
    readonly property bool alerting: root.status === "waiting_approval" || root.status === "waiting_answer"
    readonly property bool finished: root.status === "completed"
    readonly property bool failed: root.status === "failed" || root.status === "cancelled"
    readonly property color neutralColor: Style.paletteColor(root.theme, "textDim")
    readonly property color statusColor: root.connected ? (root.running ? root.neutralColor : root.accentColor) : root.neutralColor
    readonly property string iconName: root.iconNameFor(root.providerKey)
    readonly property string iconSource: "../assets/provider-icons/" + root.iconName + ".svg"

    implicitWidth: Style.size.barMarkWidth
    implicitHeight: Style.size.barMarkHeight

    function iconNameFor(providerKey) {
        switch (providerKey) {
        case "claude":
        case "codex":
        case "cursor":
        case "gemini":
        case "opencode":
            return providerKey;
        default:
            return "code-island";
        }
    }

    Image {
        id: providerIcon

        anchors.centerIn: parent
        width: 17
        height: 17
        asynchronous: true
        fillMode: Image.PreserveAspectFit
        mipmap: true
        opacity: status === Image.Ready && !root.running ? (root.connected ? 0.96 : 0.52) : 0
        smooth: true
        source: root.iconSource
        sourceSize.height: Math.max(1, height * 2)
        sourceSize.width: Math.max(1, width * 2)
        visible: status === Image.Ready && !root.running
    }

    Item {
        anchors.centerIn: parent
        width: 16
        height: 12
        opacity: root.connected ? (root.running ? 0.68 : 0.94) : 0.48
        visible: root.running || providerIcon.status !== Image.Ready

        Rectangle {
            width: 4
            height: 4
            radius: 2
            x: 3
            y: 4
            color: Style.paletteColor(root.theme, "textStrong")
        }

        Rectangle {
            width: 4
            height: 4
            radius: 2
            x: 9
            y: 4
            color: Style.paletteColor(root.theme, "textStrong")
            opacity: 0.72
        }
    }

    Rectangle {
        width: 5
        height: 5
        radius: width / 2
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.rightMargin: -1
        anchors.topMargin: -1
        color: root.failed ? Style.paletteColor(root.theme, "failedAccent") : (root.finished ? Style.paletteColor(root.theme, "successAccent") : root.statusColor)
        border.width: 0
        opacity: root.running ? 0.52 : (root.connected ? 1 : 0.58)
    }

    SequentialAnimation on scale {
        running: root.animated && root.alerting
        loops: Animation.Infinite

        NumberAnimation {
            to: root.alerting ? 1.1 : 1.06
            duration: Style.motion.pulseHalf
            easing.type: Easing.InOutQuad
        }

        NumberAnimation {
            to: 1.0
            duration: Style.motion.pulseHalf
            easing.type: Easing.InOutQuad
        }
    }
}
