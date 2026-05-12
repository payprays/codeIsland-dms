import QtQuick

import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property string status: "idle"
    property string title: "CodeIsland"
    property string detail: ""
    property string project: ""
    property string providerKey: "other"
    property var sessionDots: []
    property bool connected: false
    property var theme: null
    property var visualStyle: null

    readonly property int compactMinWidth: Style.size.barPillMinWidth
    readonly property int compactMaxWidth: Style.size.barPillMaxWidth
    readonly property var visibleDots: root.normalizedDots()

    function normalizedDots() {
        var source = root.sessionDots || [];
        var result = [];

        for (var index = 0; index < source.length; index += 1) {
            var dot = source[index];
            if (dot && typeof dot === "object") {
                result.push({
                    status: typeof dot.status === "string" && dot.status.length ? dot.status : "idle",
                    sessionId: typeof dot.sessionId === "string" ? dot.sessionId : "",
                });
            }
        }

        if (!result.length) {
            result.push({
                status: root.connected ? root.status : "idle",
                sessionId: "",
            });
        }

        return result;
    }

    function dotColor(status) {
        switch (status) {
        case "waiting_approval":
        case "waiting_answer":
            return Style.paletteColor(root.theme, "waitingAccent");
        case "running":
        case "completed":
            return Style.paletteColor(root.theme, "lineUser");
        case "failed":
        case "cancelled":
            return Style.paletteColor(root.theme, "failedAccent");
        default:
            return Style.paletteColor(root.theme, "textDim");
        }
    }

    implicitWidth: Math.min(
        root.compactMaxWidth,
        Math.max(root.compactMinWidth, dotsRow.implicitWidth)
    )
    implicitHeight: Style.size.barPillHeight

    Row {
        id: dotsRow

        anchors.centerIn: parent
        spacing: Style.sizeValue(root.visualStyle, "barSessionDotSpacing")

        Repeater {
            model: root.visibleDots

            Rectangle {
                required property var modelData

                readonly property string dotStatus: modelData && typeof modelData.status === "string" ? modelData.status : "idle"

                width: Style.sizeValue(root.visualStyle, "barSessionDot")
                height: Style.sizeValue(root.visualStyle, "barSessionDot")
                radius: width / 2
                anchors.verticalCenter: parent.verticalCenter
                color: root.dotColor(dotStatus)
                opacity: dotStatus === "running" ? 0.34 : (root.connected ? 1 : 0.48)

                SequentialAnimation on opacity {
                    running: dotStatus === "running"
                    loops: Animation.Infinite

                    NumberAnimation {
                        to: 0.28
                        duration: Style.motionValue(root.visualStyle, "breathe")
                        easing.type: Easing.InOutQuad
                    }

                    NumberAnimation {
                        to: Style.motionValue(root.visualStyle, "breatheMaxOpacity")
                        duration: Style.motionValue(root.visualStyle, "breathe")
                        easing.type: Easing.InOutQuad
                    }
                }
            }
        }
    }
}
