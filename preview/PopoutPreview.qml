import QtQuick 2.15
import QtQuick.Window 2.15
import "../components" as Components
import "../lib/CodeIslandProtocol.js" as Protocol
import "../lib/CodeIslandStyle.js" as Style
import "fixtures.js" as Fixtures

Window {
    id: root

    property string previewMode: previewModeFromArguments()
    property var currentFixture: fixtureFor(previewMode)
    property string lastEvent: "none"
    property string lastPayload: ""
    property string lastEditedAnswer: currentFixture && currentFixture.answerText ? currentFixture.answerText : ""
    readonly property bool boardMode: previewMode === "board"
    readonly property var currentGroups: currentFixture && currentFixture.snapshot ? Protocol.projectSessionGroups(currentFixture.snapshot, currentFixture.connected, currentFixture.warning) : Protocol.projectSessionGroups(Fixtures.boardSnapshot(), true, "")
    readonly property var currentIslandState: islandStateFor(previewMode)
    readonly property var currentSurface: currentIslandState
    readonly property string currentStatus: typeof currentSurface.status === "string" ? currentSurface.status : "idle"
    readonly property string currentModeLabel: {
        var label = typeof currentSurface.surfaceChipText === "string" && currentSurface.surfaceChipText.length ? currentSurface.surfaceChipText : previewMode;
        return label.toUpperCase();
    }
    readonly property color previewAccent: Style.accentFor(currentStatus)
    readonly property color previewBackdrop: Style.backgroundFor(currentStatus)
    readonly property int shellMargin: Style.spacing.l
    readonly property int shellSpacing: Style.spacing.m
    readonly property int stagePadding: Style.spacing.s
    readonly property int previewMarkerInset: Style.spacing.s
    readonly property int previewMarkerThickness: Style.spacing.s
    readonly property int previewMarkerLength: Style.spacing.xl * 2
    readonly property int stageRailLength: Style.spacing.xl * 3
    readonly property int popoutFramePadding: Style.spacing.m
    readonly property int popoutFrameTabWidth: Style.spacing.xl * 3
    readonly property int popoutFrameTabHeight: Style.spacing.xs
    readonly property int bannerTitleSize: (Style.font.title * 2) + Style.spacing.l
    readonly property int bannerWatermarkSize: bannerTitleSize + (Style.spacing.xl * 2)
    readonly property int modeBadgeHeight: Style.size.buttonHeight + Style.spacing.l
    property bool autoSavePreview: true
    property string captureDirectory: "/tmp"
    property string captureFilePrefix: "codeisland-popout-preview"
    property string lastCapturePath: ""
    property string lastCaptureStatus: "idle"
    property bool captureInFlight: false
    property bool captureQueued: false
    readonly property string currentCapturePath: capturePathFor(previewMode)

    function fixtureFor(mode) {
        switch (mode) {
        case "board":
            return Fixtures.board();
        case "approval":
            return Fixtures.approval();
        case "question":
            return Fixtures.question();
        case "completion":
            return Fixtures.completion();
        case "running":
            return Fixtures.running();
        case "offline":
            return Fixtures.offline();
        default:
            return Fixtures.running();
        }
    }

    function previewModeFromArguments() {
        var args = Qt.application.arguments || [];
        for (var index = 0; index < args.length; index += 1) {
            var arg = String(args[index]);
            if (arg.indexOf("--mode=") === 0)
                return arg.substring(7);
        }
        return "board";
    }

    function islandStateFor(mode) {
        if (mode === "board")
            return Protocol.projectIslandState(currentFixture.snapshot, currentFixture.connected, currentFixture.warning);

        var fixture = currentFixture || {
        };
        var surface = fixture.surface || {
        };
        var surfaceName = "collapsed";
        if (mode === "approval")
            surfaceName = "approvalCard";
        else if (mode === "question")
            surfaceName = "questionCard";
        else if (mode === "completion")
            surfaceName = "completionCard";
        else if (mode === "offline")
            surfaceName = "sessionList";
        var status = typeof surface.status === "string" && surface.status.length ? surface.status : "running";
        var title = typeof surface.headerIdentity === "string" && surface.headerIdentity.length ? surface.headerIdentity : "demo";
        var providerKey = mode === "completion" ? "opencode" : "codex";
        var providerLabel = providerKey === "codex" ? "Codex" : "OpenCode";
        var sessionId = "preview-" + mode;
        return {
            "connected": mode !== "offline",
            "surface": surfaceName,
            "surfaceMode": surface.surfaceMode || mode,
            "status": status,
            "autoReveal": mode === "approval" || mode === "question" || mode === "completion",
            "token": mode + ":preview",
            "sessionId": sessionId,
            "interactionId": fixture.interaction && fixture.interaction.interaction_id ? fixture.interaction.interaction_id : "",
            "providerKey": providerKey,
            "providerLabel": providerLabel,
            "project": "codeIsland",
            "title": title,
            "headerIdentity": title,
            "headerMeta": surface.headerMeta || providerLabel,
            "surfaceChipText": surface.surfaceChipText || Style.labelFor(status),
            "primaryTitle": surface.primaryTitle || title,
            "primaryBody": surface.primaryBody || "",
            "metaLine": surface.metaLine || "",
            "interaction": fixture.interaction || null,
            "card": {
                "sessionId": sessionId,
                "providerKey": providerKey,
                "providerLabel": providerLabel,
                "status": status,
                "title": title,
                "suffix": "#demo",
                "project": "codeIsland",
                "primaryLine": surface.primaryTitle || title,
                "secondaryLine": surface.primaryBody || surface.metaLine || "",
                "timeText": "<1m",
                "appLabel": "Ghostty",
                "hasInteraction": mode === "approval" || mode === "question",
                "interactionType": mode === "approval" ? "approval" : (mode === "question" ? "question" : "")
            }
        };
    }

    function capturePathFor(mode) {
        return captureDirectory + "/" + captureFilePrefix + "-" + mode + ".png";
    }

    function scheduleCapture(reason) {
        if (!autoSavePreview || !captureSurface.width || !captureSurface.height)
            return ;

        lastCaptureStatus = "queued · " + reason;
        captureTimer.restart();
    }

    function savePreview(reason) {
        if (!autoSavePreview)
            return ;

        if (captureInFlight) {
            captureQueued = true;
            lastCaptureStatus = "waiting · " + reason;
            return ;
        }
        captureInFlight = true;
        lastCaptureStatus = "capturing · " + reason;
        var targetPath = capturePathFor(previewMode);
        var requested = captureSurface.grabToImage(function(result) {
            captureInFlight = false;
            if (!result) {
                lastCaptureStatus = "failed · no image result";
            } else if (result.saveToFile(targetPath)) {
                lastCapturePath = targetPath;
                lastCaptureStatus = "saved · " + targetPath;
            } else {
                lastCaptureStatus = "failed · could not write " + targetPath;
            }
            if (captureQueued) {
                captureQueued = false;
                captureTimer.restart();
            }
        });
        if (!requested) {
            captureInFlight = false;
            lastCaptureStatus = "failed · grab request rejected";
        }
    }

    width: Style.size.boardWidth + (Style.spacing.xl * 3) + (Style.spacing.edge * 2)
    height: Style.size.boardHeight + (Style.spacing.xl * 21) + (Style.spacing.m * 7)
    visible: true
    color: Style.palette.shellOuter
    title: "CodeIsland Popout Preview"
    onPreviewModeChanged: {
        currentFixture = fixtureFor(previewMode);
        lastEvent = "switched";
        lastPayload = previewMode;
        lastEditedAnswer = currentFixture && currentFixture.answerText ? currentFixture.answerText : "";
        root.scheduleCapture("mode change");
    }
    Component.onCompleted: {
        root.scheduleCapture("startup");
        startupRecaptureTimer.restart();
    }

    Timer {
        id: captureTimer

        interval: 700
        repeat: false
        onTriggered: root.savePreview("render settled")
    }

    Timer {
        id: startupRecaptureTimer

        interval: 1500
        repeat: false
        onTriggered: root.savePreview("late startup settle")
    }

    Rectangle {
        id: captureSurface

        anchors.fill: parent
        color: root.color

        Rectangle {
            anchors.fill: parent
            anchors.margins: Style.spacing.xs
            radius: Style.radius.card + Style.spacing.s
            color: Style.alpha(root.previewAccent, 0.06)
            border.color: Style.alpha(root.previewAccent, 0.22)
            border.width: Style.size.border
        }

        Rectangle {
            anchors.fill: parent
            anchors.margins: Style.spacing.s
            radius: Style.radius.card + Style.spacing.s
            color: Style.alpha(Style.palette.shellInner, 0.98)
            border.color: Style.alpha(Style.palette.shellOutline, 0.96)
            border.width: Style.size.border
        }

        Item {
            anchors.fill: parent

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.leftMargin: root.previewMarkerInset
                anchors.topMargin: root.previewMarkerInset
                width: root.previewMarkerLength
                height: root.previewMarkerThickness
                radius: height / 2
                color: Style.palette.textStrong
            }

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.leftMargin: root.previewMarkerInset
                anchors.topMargin: root.previewMarkerInset
                width: root.previewMarkerThickness
                height: root.previewMarkerLength
                radius: width / 2
                color: root.previewAccent
            }

            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: root.previewMarkerInset
                anchors.topMargin: root.previewMarkerInset
                width: root.previewMarkerLength
                height: root.previewMarkerThickness
                radius: height / 2
                color: root.previewAccent
            }

            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: root.previewMarkerInset
                anchors.topMargin: root.previewMarkerInset
                width: root.previewMarkerThickness
                height: root.previewMarkerLength
                radius: width / 2
                color: Style.palette.textStrong
            }

            Rectangle {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.leftMargin: root.previewMarkerInset
                anchors.bottomMargin: root.previewMarkerInset
                width: root.previewMarkerLength
                height: root.previewMarkerThickness
                radius: height / 2
                color: root.previewAccent
            }

            Rectangle {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.leftMargin: root.previewMarkerInset
                anchors.bottomMargin: root.previewMarkerInset
                width: root.previewMarkerThickness
                height: root.previewMarkerLength
                radius: width / 2
                color: Style.palette.textStrong
            }

            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: root.previewMarkerInset
                anchors.bottomMargin: root.previewMarkerInset
                width: root.previewMarkerLength
                height: root.previewMarkerThickness
                radius: height / 2
                color: Style.palette.textStrong
            }

            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: root.previewMarkerInset
                anchors.bottomMargin: root.previewMarkerInset
                width: root.previewMarkerThickness
                height: root.previewMarkerLength
                radius: width / 2
                color: root.previewAccent
            }

        }

        Column {
            anchors.fill: parent
            anchors.margins: root.shellMargin
            spacing: root.shellSpacing

            Rectangle {
                width: parent.width
                radius: Style.radius.card + Style.spacing.xs
                border.color: Style.alpha(root.previewAccent, 0.58)
                border.width: Style.size.border
                implicitHeight: previewBanner.implicitHeight + (Style.spacing.l * 2)

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: Style.size.border
                    radius: parent.radius - Style.size.border
                    color: Style.alpha(Style.palette.shellInner, 0.9)
                }

                Text {
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.m
                    anchors.verticalCenter: parent.verticalCenter
                    color: Style.alpha(Style.palette.textStrong, 0.06)
                    font.pixelSize: root.bannerWatermarkSize
                    font.weight: Font.Black
                    opacity: 1
                    text: "PREVIEW"
                }

                Column {
                    id: previewBanner

                    anchors.fill: parent
                    anchors.margins: Style.spacing.l
                    spacing: Style.spacing.s

                    Row {
                        spacing: Style.spacing.xs

                        Rectangle {
                            implicitWidth: previewTag.implicitWidth + (Style.spacing.m * 2)
                            implicitHeight: Style.size.chipHeight
                            radius: Style.radius.chip
                            color: Style.alpha(Style.palette.textStrong, 0.08)
                            border.color: Style.alpha(Style.palette.textStrong, 0.18)
                            border.width: Style.size.border

                            Text {
                                id: previewTag

                                anchors.centerIn: parent
                                color: Style.palette.textStrong
                                font.pixelSize: Style.font.chip
                                font.weight: Font.Bold
                                text: "DEDICATED SCREENSHOT SURFACE"
                            }

                        }

                        Rectangle {
                            implicitWidth: windowOnlyTag.implicitWidth + (Style.spacing.s * 2)
                            implicitHeight: Style.size.chipHeight
                            radius: Style.radius.chip
                            color: Style.alpha(root.previewAccent, 0.16)
                            border.color: Style.alpha(root.previewAccent, 0.34)
                            border.width: Style.size.border

                            Text {
                                id: windowOnlyTag

                                anchors.centerIn: parent
                                color: root.previewAccent
                                font.pixelSize: Style.font.chip
                                font.weight: Font.Bold
                                text: "WINDOW ONLY"
                            }

                        }

                    }

                    Text {
                        color: Style.palette.textStrong
                        font.pixelSize: root.bannerTitleSize
                        font.weight: Font.Black
                        font.letterSpacing: Style.spacing.edge + Style.size.border
                        text: "CODEISLAND PREVIEW"
                    }

                    Text {
                        width: parent.width - (modeBadge.implicitWidth + Style.spacing.m)
                        color: Style.palette.textMuted
                        font.pixelSize: Style.font.detail
                        wrapMode: Text.WordWrap
                        text: "Preview-only shell around the real CodeIsland popout so screenshots unmistakably show CodeIsland instead of a generic terminal or tool window."
                    }

                    Row {
                        width: parent.width
                        spacing: Style.spacing.m

                        Column {
                            width: Math.max(0, parent.width - modeBadge.implicitWidth - parent.spacing)
                            spacing: Style.spacing.xs

                            Text {
                                color: Style.palette.textStrong
                                font.pixelSize: Style.font.chip
                                font.weight: Font.Bold
                                text: "CURRENT MODE"
                            }

                            Text {
                                width: parent.width
                                color: Style.palette.textMuted
                                font.pixelSize: Style.font.body
                                maximumLineCount: 2
                                wrapMode: Text.WordWrap
                                text: "The banner and mode badge are preview-only framing. The embedded interaction surface below remains the real CodeIsland DMS component."
                            }

                        }

                        Rectangle {
                            id: modeBadge

                            anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: modeBadgeText.implicitWidth + (Style.spacing.l * 2)
                            implicitHeight: root.modeBadgeHeight
                            radius: Style.radius.button + Style.spacing.xs
                            color: root.previewAccent
                            border.color: Qt.lighter(root.previewAccent, 1.18)
                            border.width: Style.size.border

                            Text {
                                id: modeBadgeText

                                anchors.centerIn: parent
                                color: Style.palette.chipText
                                font.pixelSize: Style.font.title + Style.font.body
                                font.weight: Font.Black
                                font.letterSpacing: Style.size.border
                                text: root.currentModeLabel
                            }

                        }

                    }

                }

                gradient: Gradient {
                    GradientStop {
                        position: 0
                        color: Style.alpha(root.previewAccent, 0.34)
                    }

                    GradientStop {
                        position: 0.42
                        color: Style.alpha(root.previewAccent, 0.14)
                    }

                    GradientStop {
                        position: 1
                        color: Style.palette.shellOuter
                    }

                }

            }

            Column {
                width: parent.width
                spacing: Style.spacing.xs

                Text {
                    color: Style.palette.textStrong
                    font.pixelSize: Style.font.chip
                    font.weight: Font.Bold
                    text: "SHOT MODE"
                }

                Flow {
                    width: parent.width
                    spacing: Style.spacing.xs

                    Repeater {
                        model: ["board", "approval", "question", "completion", "running", "offline"]

                        delegate: Rectangle {
                            required property string modelData

                            width: label.implicitWidth + (Style.spacing.m * 2)
                            height: Style.size.buttonHeight
                            radius: Style.radius.button
                            color: root.previewMode === modelData ? root.previewAccent : Style.alpha(Style.palette.shellInner, 0.98)
                            border.color: root.previewMode === modelData ? Qt.lighter(root.previewAccent, 1.18) : Style.alpha(Style.palette.shellOutline, 0.92)
                            border.width: Style.size.border

                            Text {
                                id: label

                                anchors.centerIn: parent
                                color: root.previewMode === parent.modelData ? Style.palette.chipText : Style.palette.textStrong
                                font.pixelSize: Style.font.button
                                font.weight: Font.Bold
                                text: parent.modelData.toUpperCase()
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.previewMode = parent.modelData
                            }

                        }

                    }

                }

            }

            Rectangle {
                width: parent.width
                radius: Style.radius.input
                color: Style.alpha(Style.palette.shellOuter, 0.92)
                border.color: Style.alpha(Style.palette.shellOutline, 0.94)
                border.width: Style.size.border
                implicitHeight: previewMeta.implicitHeight + (Style.spacing.s * 2)

                Column {
                    id: previewMeta

                    anchors.fill: parent
                    anchors.margins: Style.spacing.s
                    spacing: Style.spacing.xs

                    Text {
                        color: Style.palette.textStrong
                        font.pixelSize: Style.font.chip
                        font.weight: Font.Bold
                        text: "SIGNAL TELEMETRY"
                    }

                    Text {
                        color: Style.palette.textStrong
                        font.pixelSize: Style.font.body
                        font.weight: Font.Medium
                        text: "Current mode: " + root.previewMode + " · status: " + root.currentStatus
                    }

                    Text {
                        color: Style.palette.textMuted
                        font.pixelSize: Style.font.detail
                        text: "Last event: " + root.lastEvent + (root.lastPayload.length ? " · " + root.lastPayload : "")
                    }

                    Text {
                        color: Style.palette.textMuted
                        font.pixelSize: Style.font.detail
                        text: "Last edited answer: " + (root.lastEditedAnswer.length ? root.lastEditedAnswer : "—")
                    }

                    Text {
                        color: Style.palette.textMuted
                        font.pixelSize: Style.font.detail
                        text: "Capture target: " + root.currentCapturePath
                    }

                    Text {
                        width: parent.width
                        color: Style.palette.textMuted
                        font.pixelSize: Style.font.detail
                        wrapMode: Text.WordWrap
                        text: "Capture status: " + root.lastCaptureStatus
                    }

                }

            }

            Item {
                width: parent.width
                height: Math.max(0, parent.height - y)

                Rectangle {
                    anchors.fill: parent
                    radius: Style.radius.card + Style.spacing.xs
                    color: Style.alpha(root.previewBackdrop, 0.46)
                    border.color: Style.alpha(root.previewAccent, 0.34)
                    border.width: Style.size.border
                }

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: Style.spacing.edge
                    radius: Style.radius.card + Style.spacing.xs - Style.spacing.edge
                    color: Style.alpha(Style.palette.shellOuter, 0.76)
                    border.color: Style.alpha(Style.palette.textStrong, 0.05)
                    border.width: Style.size.border
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.margins: Style.spacing.m
                    width: root.previewMarkerThickness
                    radius: width / 2
                    color: root.previewAccent
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.leftMargin: Style.spacing.m
                    anchors.topMargin: Style.spacing.m
                    width: root.stageRailLength
                    height: root.previewMarkerThickness
                    radius: height / 2
                    color: Style.palette.textStrong
                }

                Rectangle {
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.rightMargin: Style.spacing.m
                    anchors.bottomMargin: Style.spacing.m
                    width: root.stageRailLength - Style.spacing.l
                    height: root.previewMarkerThickness
                    radius: height / 2
                    color: root.previewAccent
                }

                Column {
                    anchors.fill: parent
                    anchors.margins: root.stagePadding
                    spacing: Style.spacing.s

                    Row {
                        spacing: Style.spacing.xs

                        Rectangle {
                            width: Style.size.orb
                            height: Style.size.orb
                            radius: Style.radius.dot
                            color: root.previewAccent
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            color: Style.palette.textStrong
                            font.pixelSize: Style.font.chip
                            font.weight: Font.Bold
                            text: "REAL CODEISLAND SURFACE BELOW"
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            color: Style.palette.textDim
                            font.pixelSize: Style.font.chip
                            text: "· preview frame only"
                        }

                    }

                    Item {
                        width: parent.width
                        height: Math.max(0, parent.height - y)

                        Text {
                            anchors.centerIn: parent
                            color: Style.alpha(root.previewAccent, 0.08)
                            font.pixelSize: root.bannerTitleSize + (Style.spacing.xl * 2)
                            font.weight: Font.Black
                            font.letterSpacing: Style.spacing.xs
                            rotation: -14
                            text: "CODEISLAND"
                        }

                        Rectangle {
                            id: previewFrame

                            width: previewIsland.implicitWidth + (root.popoutFramePadding * 2)
                            height: previewIsland.implicitHeight + (root.popoutFramePadding * 2)
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.top: parent.top
                            radius: Style.radius.card + Style.spacing.s
                            color: Style.alpha(Style.palette.shellInner, 0.64)
                            border.color: Style.alpha(root.previewAccent, 0.78)
                            border.width: Style.size.border
                        }

                        Rectangle {
                            anchors.left: previewFrame.left
                            anchors.top: previewFrame.top
                            anchors.leftMargin: root.popoutFramePadding
                            anchors.topMargin: root.popoutFramePadding
                            width: root.popoutFrameTabWidth
                            height: root.popoutFrameTabHeight
                            radius: height / 2
                            color: Style.palette.textStrong
                        }

                        Rectangle {
                            anchors.left: previewFrame.left
                            anchors.top: previewFrame.top
                            anchors.leftMargin: root.popoutFramePadding
                            anchors.topMargin: root.popoutFramePadding
                            width: root.popoutFrameTabHeight
                            height: root.popoutFrameTabWidth
                            radius: width / 2
                            color: root.previewAccent
                        }

                        Rectangle {
                            anchors.right: previewFrame.right
                            anchors.bottom: previewFrame.bottom
                            anchors.rightMargin: root.popoutFramePadding
                            anchors.bottomMargin: root.popoutFramePadding
                            width: root.popoutFrameTabWidth
                            height: root.popoutFrameTabHeight
                            radius: height / 2
                            color: root.previewAccent
                        }

                        Rectangle {
                            anchors.right: previewFrame.right
                            anchors.bottom: previewFrame.bottom
                            anchors.rightMargin: root.popoutFramePadding
                            anchors.bottomMargin: root.popoutFramePadding
                            width: root.popoutFrameTabHeight
                            height: root.popoutFrameTabWidth
                            radius: width / 2
                            color: Style.palette.textStrong
                        }

                        Rectangle {
                            id: previewFrameCap

                            width: root.popoutFrameTabWidth
                            height: root.popoutFrameTabHeight
                            anchors.horizontalCenter: previewFrame.horizontalCenter
                            anchors.bottom: previewFrame.top
                            radius: Style.radius.button
                            color: Style.palette.textStrong

                            Rectangle {
                                width: root.popoutFrameTabWidth - (Style.spacing.m * 2)
                                height: parent.height
                                anchors.centerIn: parent
                                radius: parent.radius
                                color: root.previewAccent
                            }

                        }

                        Components.IslandSurface {
                            id: previewIsland

                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.top: parent.top
                            anchors.topMargin: Style.spacing.m
                            islandState: root.currentIslandState
                            forceSessionList: root.boardMode || root.previewMode === "running" || root.previewMode === "offline"
                            groups: root.currentGroups
                            connected: root.currentFixture.connected === true
                            warning: root.currentFixture.warning || ""
                            interaction: root.currentFixture.interaction
                            answerText: root.currentFixture.answerText || ""
                            onApproveRequested: {
                                root.lastEvent = "approveRequested";
                                root.lastPayload = root.previewMode;
                            }
                            onDenyRequested: {
                                root.lastEvent = "denyRequested";
                                root.lastPayload = root.previewMode;
                            }
                            onAnswerRequested: function(answer) {
                                root.lastEvent = "answerRequested";
                                root.lastPayload = answer;
                            }
                            onAnswerEdited: function(answer) {
                                root.lastEvent = "answerEdited";
                                root.lastPayload = answer;
                                root.lastEditedAnswer = answer;
                            }
                            onListRequested: {
                                root.lastEvent = "listRequested";
                                root.lastPayload = root.previewMode;
                            }
                            onSessionActivated: function(sessionId) {
                                root.lastEvent = "sessionActivated";
                                root.lastPayload = sessionId;
                            }
                        }

                    }

                }

            }

        }

    }

}
