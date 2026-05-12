import QtQuick 2.15
import "../lib/CodeIslandI18n.js" as LocalI18n
import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property var islandState: ({
    })
    property var groups: []
    property bool connected: false
    property string warning: ""
    property var interaction: null
    property string answerText: ""
    property bool forceSessionList: false
    property string viewMode: "all"
    property var closePopout: null
    property var parentPopout: null
    property var theme: null
    property var visualStyle: null
    property string locale: "en"
    readonly property string surface: root.stateString("surface") || "collapsed"
    readonly property string status: root.stateString("status") || "idle"
    readonly property string token: root.stateString("token")
    readonly property var card: root.islandState && root.islandState.card ? root.islandState.card : ({
    })
    readonly property var activeInteraction: root.interaction || (root.islandState && root.islandState.interaction ? root.islandState.interaction : null)
    readonly property bool approvalMode: root.focusedMode && root.surface === "approvalCard"
    readonly property bool questionMode: root.focusedMode && root.surface === "questionCard"
    readonly property bool completionMode: root.focusedMode && root.surface === "completionCard"
    readonly property bool focusedMode: !root.forceSessionList && (root.surface === "approvalCard" || root.surface === "questionCard" || root.surface === "completionCard")
    readonly property string providerKey: root.cardString("providerKey") || root.stateString("providerKey") || "other"
    readonly property string providerLabel: root.cardString("providerLabel") || root.stateString("providerLabel") || root.tr("Other")
    readonly property string sessionId: root.cardString("sessionId") || root.stateString("sessionId")
    readonly property string targetSessionId: root.sessionId || root.firstString(root.stateArray("completionQueue")) || root.firstString(root.stateArray("rotationQueue"))
    readonly property string titleText: root.cardString("title") || root.stateString("title") || "CodeIsland"
    readonly property string suffixText: root.cardString("suffix")
    readonly property string primaryLine: root.stateString("primaryTitle") || root.cardString("primaryLine") || root.statusLabel(root.status)
    readonly property string secondaryLine: root.stateString("primaryBody") || root.cardString("secondaryLine")
    readonly property string metaLine: root.stateString("metaLine")
    readonly property string timeText: root.cardString("timeText") || "<1m"
    readonly property string appLabel: root.cardString("appLabel") || root.providerLabel
    readonly property string surfaceChipText: root.stateString("surfaceChipText") || root.statusLabel(root.status)
    readonly property color accentColor: Style.providerAccentFor(root.providerKey, root.theme)
    readonly property color statusAccent: Style.accentFor(root.status, root.theme)
    readonly property bool runningStatus: root.status === "running"
    readonly property bool completedStatus: root.status === "completed"
    readonly property bool completionLikeStatus: root.completedStatus || root.completionMode
    readonly property color neutralAccent: Style.paletteColor(root.theme, "textDim")
    readonly property color surfaceAccent: root.runningStatus ? root.neutralAccent : root.statusAccent
    readonly property color focusedCardStroke: root.completionLikeStatus ? Style.paletteColor(root.theme, "boardCardBorder") : Style.alpha(root.surfaceAccent, 0.48)
    readonly property bool showTimeChip: Style.boolValue(root.visualStyle, "showTimeChip")
    readonly property bool showAppChip: Style.boolValue(root.visualStyle, "showAppChip")
    readonly property bool showRightRail: root.showTimeChip || root.showAppChip
    readonly property real rightRailWidth: root.showRightRail ? Style.sizeValue(root.visualStyle, "boardRightRailWidth") : 0
    readonly property real focusedCardRadius: Style.radiusValue(root.visualStyle, "boardCard") + 2

    signal approveRequested()
    signal denyRequested()
    signal answerRequested(string answer)
    signal answerEdited(string answer)
    signal sessionActivated(string sessionId)
    signal listRequested(string viewMode)
    signal popoutVisibilityChanged(bool visible)

    function stateString(key) {
        if (!root.islandState || typeof root.islandState !== "object")
            return "";

        return typeof root.islandState[key] === "string" ? root.islandState[key] : "";
    }

    function tr(term) {
        return LocalI18n.tr(term, root.locale);
    }

    function statusLabel(status) {
        return LocalI18n.statusLabel(status, root.locale);
    }

    function stateArray(key) {
        if (!root.islandState || typeof root.islandState !== "object")
            return [];

        var value = root.islandState[key];
        return value && typeof value.length === "number" ? value : [];
    }

    function firstString(values) {
        var source = values || [];
        for (var index = 0; index < source.length; index += 1) {
            if (typeof source[index] === "string" && source[index].length)
                return source[index];
        }
        return "";
    }

    function cardString(key) {
        if (!root.card || typeof root.card !== "object")
            return "";

        return typeof root.card[key] === "string" ? root.card[key] : "";
    }

    function emitPopoutVisibility() {
        var visible = !!(root.parentPopout && root.parentPopout.shouldBeVisible);
        root.popoutVisibilityChanged(visible);
        if (visible)
            Qt.callLater(root.forceActiveFocus);

    }

    function closeIfPossible() {
        if (root.closePopout)
            root.closePopout();

    }

    function activateSession(sessionId) {
        if (!sessionId || !sessionId.length)
            return false;

        if (root.completionMode)
            root.listRequested(root.viewMode);

        root.closeIfPossible();
        root.sessionActivated(sessionId);
        return true;
    }

    function activateTargetOrList() {
        if (root.activateSession(root.targetSessionId))
            return true;

        root.listRequested(root.viewMode);
        return true;
    }

    function submitAnswer() {
        var answer = answerField.text.trim();
        if (!answer.length)
            return ;

        root.answerRequested(answer);
    }

    function requestViewMode(mode) {
        if (!mode || !mode.length)
            return ;

        root.listRequested(mode);
    }

    function keyHasPlainModifiers(event) {
        return event.modifiers === Qt.NoModifier || event.modifiers === Qt.ShiftModifier;
    }

    function requestViewRelative(delta) {
        var modes = ["all", "finished", "active"];
        var index = modes.indexOf(root.viewMode);
        if (index < 0)
            index = 0;
        root.listRequested(modes[(index + delta + modes.length) % modes.length]);
    }

    function handleFocusedKeyEvent(event) {
        if (answerField.activeFocus)
            return false;

        if (!root.keyHasPlainModifiers(event))
            return false;

        if (event.key === Qt.Key_Escape) {
            root.closeIfPossible();
            return true;
        }

        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_O) {
            return root.activateTargetOrList();
        }

        if (event.key === Qt.Key_A && root.approvalMode) {
            root.approveRequested();
            return true;
        }

        if (event.key === Qt.Key_D && (root.approvalMode || root.questionMode)) {
            root.denyRequested();
            return true;
        }

        if (event.key === Qt.Key_1) {
            root.listRequested("all");
            return true;
        }
        if (event.key === Qt.Key_2) {
            root.listRequested("finished");
            return true;
        }
        if (event.key === Qt.Key_3) {
            root.listRequested("active");
            return true;
        }

        if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
            root.requestViewRelative(event.key === Qt.Key_Backtab || event.modifiers === Qt.ShiftModifier ? -1 : 1);
            return true;
        }

        if (event.key === Qt.Key_L || event.key === Qt.Key_B || event.key === Qt.Key_Space) {
            root.listRequested(root.viewMode);
            return true;
        }

        return false;
    }

    function handleSurfaceKeyEvent(event) {
        if (!root.focusedMode)
            return sessionBoard.handleKeyEvent(event);

        return root.handleFocusedKeyEvent(event);
    }

    function claimPopoutKeyboard() {
        if (root.parentPopout && "contentHandlesKeys" in root.parentPopout)
            root.parentPopout.contentHandlesKeys = true;

        Qt.callLater(root.forceActiveFocus);
    }

    implicitWidth: Style.sizeValue(root.visualStyle, "boardWidth")
    implicitHeight: root.focusedMode ? (root.questionMode ? Style.sizeValue(root.visualStyle, "boardQuestionHeight") : Style.sizeValue(root.visualStyle, "boardFocusedHeight")) : Style.sizeValue(root.visualStyle, "boardHeight")
    activeFocusOnTab: true
    focus: true
    onAnswerTextChanged: {
        if (!answerField.activeFocus && answerField.text !== root.answerText)
            answerField.text = root.answerText;

    }
    onParentPopoutChanged: {
        root.emitPopoutVisibility();
        root.claimPopoutKeyboard();
    }
    Component.onCompleted: root.claimPopoutKeyboard()
    Component.onDestruction: root.popoutVisibilityChanged(false)
    Keys.onPressed: function(event) {
        event.accepted = root.handleSurfaceKeyEvent(event);
    }

    Connections {
        function onShouldBeVisibleChanged() {
            root.emitPopoutVisibility();
        }

        target: root.parentPopout
    }

    CodeIslandSessionBoard {
        id: sessionBoard

        anchors.fill: parent
        visible: !root.focusedMode
        answerText: root.answerText
        connected: root.connected
        viewMode: root.viewMode
        groups: root.groups
        interaction: root.activeInteraction
        locale: root.locale
        theme: root.theme
        visualStyle: root.visualStyle
        warning: root.warning
        onAnswerEdited: function(answer) {
            root.answerEdited(answer);
        }
        onAnswerRequested: function(answer) {
            root.answerRequested(answer);
        }
        onApproveRequested: root.approveRequested()
        onDenyRequested: root.denyRequested()
        onSessionActivated: function(sessionId) {
            root.activateSession(sessionId);
        }
        onViewModeRequested: function(viewMode) {
            root.listRequested(viewMode);
        }
    }

    Item {
        anchors.fill: parent
        visible: root.focusedMode

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: Style.size.boardRailWidth
            radius: Style.radius.board

            gradient: Gradient {
                GradientStop {
                    position: 0
                    color: Style.paletteColor(root.theme, "boardRail")
                }

                GradientStop {
                    position: 1
                    color: Style.paletteColor(root.theme, "boardRailDeep")
                }

            }

        }

        Rectangle {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: Style.size.boardRailWidth
            radius: Style.radius.board

            gradient: Gradient {
                GradientStop {
                    position: 0
                    color: Style.paletteColor(root.theme, "boardRail")
                }

                GradientStop {
                    position: 1
                    color: Style.paletteColor(root.theme, "boardRailDeep")
                }

            }

        }

        Rectangle {
            anchors.fill: parent
            anchors.leftMargin: Style.size.boardRailWidth
            anchors.rightMargin: Style.size.boardRailWidth
            color: Style.paletteColor(root.theme, "boardChrome")
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: Style.size.boardRailWidth + Style.spacing.s
            anchors.rightMargin: Style.size.boardRailWidth + Style.spacing.s
            height: 1
            color: Style.alpha(Style.paletteColor(root.theme, "textStrong"), 0.08)
        }

        Item {
            anchors.fill: parent
            anchors.leftMargin: Style.size.boardRailWidth + Style.spacing.s
            anchors.rightMargin: Style.size.boardRailWidth + Style.spacing.s
            anchors.topMargin: Style.spacing.s
            anchors.bottomMargin: Style.spacing.s

            Row {
                id: topBar

                width: parent.width
                height: Style.size.boardTopHeight
                spacing: Style.spacing.s

                CodeIslandViewTabs {
                    id: scopeTabs

                    anchors.verticalCenter: parent.verticalCenter
                    currentMode: root.viewMode
                    locale: root.locale
                    theme: root.theme
                    visualStyle: root.visualStyle
                    onModeRequested: function(mode) {
                        root.requestViewMode(mode);
                    }
                }

                Item {
                    width: Math.max(0, parent.width - scopeTabs.width - focusLabel.width - (parent.spacing * 2))
                    height: 1
                }

                Text {
                    id: focusLabel

                    anchors.verticalCenter: parent.verticalCenter
                    color: Style.paletteColor(root.theme, "textDim")
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardMeta
                    font.weight: Font.Black
                    text: root.tr("Focus")
                }

            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: topBar.bottom
                height: 1
                color: Style.paletteColor(root.theme, "boardDivider")
            }

            Column {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: topBar.bottom
                anchors.topMargin: Style.spacing.m
                anchors.bottom: parent.bottom
                spacing: Style.spacing.m

                Row {
                    width: parent.width
                    height: 26
                    spacing: Style.spacing.s

                    Rectangle {
                        id: providerDot

                        width: 9
                        height: 9
                        radius: width / 2
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.runningStatus ? root.neutralAccent : (root.completedStatus ? Style.paletteColor(root.theme, "successAccent") : root.accentColor)
                    }

                    Text {
                        id: providerHeader

                        anchors.verticalCenter: parent.verticalCenter
                        color: Style.paletteColor(root.theme, "textMuted")
                        font.family: Style.fontFamily(root.visualStyle)
                        font.pixelSize: Style.font.boardBody
                        font.weight: Font.Black
                        text: root.providerLabel + " (1)"
                    }

                    Item {
                        width: Math.max(0, parent.width - providerDot.width - providerHeader.implicitWidth - statusChip.width - Style.spacing.s * 3)
                        height: 1
                    }

                    Rectangle {
                        id: statusChip

                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(Style.size.boardChipMinWidth, chipText.implicitWidth + Style.spacing.s * 2)
                        height: Style.size.boardChipHeight
                        radius: Style.radius.chip
                        color: Style.alpha(root.surfaceAccent, root.runningStatus ? 0.08 : 0.13)
                        border.color: Style.alpha(root.surfaceAccent, root.runningStatus ? 0.16 : 0.28)
                        border.width: Style.size.border

                        Text {
                            id: chipText

                            anchors.centerIn: parent
                            color: root.surfaceAccent
                            font.family: Style.fontFamily(root.visualStyle)
                            font.pixelSize: Style.font.boardChip
                            font.weight: Font.Black
                            text: root.surfaceChipText
                        }

                    }

                }

                Rectangle {
                    id: focusedCard

                    width: parent.width
                    height: Math.max(330, focusedCardContent.implicitHeight + Style.spacing.l * 2)
                    radius: root.focusedCardRadius
                    antialiasing: true
                    color: root.completionLikeStatus ? Style.paletteColor(root.theme, "boardCard") : Style.alpha(root.surfaceAccent, 0.14)
                    border.color: root.focusedCardStroke
                    border.width: Style.size.border

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.leftMargin: 1
                        anchors.rightMargin: 1
                        height: 1
                        radius: focusedCard.radius
                        color: Style.alpha(Style.paletteColor(root.theme, "textStrong"), 0.14)
                    }

                    Column {
                        id: focusedCardContent

                        anchors.fill: parent
                        anchors.margins: Style.spacing.l
                        spacing: Style.spacing.m

                        Row {
                            width: parent.width
                            spacing: Style.spacing.m

                            CodeIslandProviderGlyph {
                                providerKey: root.providerKey
                                status: root.status
                                theme: root.theme
                                visualStyle: root.visualStyle
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Column {
                                width: Math.max(0, parent.width - Style.size.boardGlyphWidth - rightRail.width - parent.spacing * 2)
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Style.spacing.xs

                                Row {
                                    width: parent.width
                                    spacing: Style.spacing.xs

                                    Text {
                                        width: Math.max(0, parent.width - suffixLabel.width - parent.spacing)
                                        color: Style.paletteColor(root.theme, "textStrong")
                                        elide: Text.ElideRight
                                        font.family: Style.fontFamily(root.visualStyle)
                                        font.pixelSize: Style.font.boardTitle + 3
                                        font.weight: Font.Black
                                        maximumLineCount: 1
                                        text: root.titleText
                                    }

                                    Text {
                                        id: suffixLabel

                                        color: Style.paletteColor(root.theme, "textDim")
                                        font.family: Style.fontFamily(root.visualStyle)
                                        font.pixelSize: Style.font.boardMeta
                                        font.weight: Font.DemiBold
                                        text: root.suffixText
                                        visible: text.length > 0
                                    }

                                }

                                Text {
                                    width: parent.width
                                    color: Style.paletteColor(root.theme, "textMuted")
                                    elide: Text.ElideRight
                                    font.family: Style.fontFamily(root.visualStyle)
                                    font.pixelSize: Style.font.boardBody
                                    maximumLineCount: 1
                                    text: root.metaLine
                                    visible: text.length > 0
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
                                    width: Math.max(Style.size.boardChipMinWidth, timeLabel.implicitWidth + Style.spacing.s * 2)
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
                                    width: Math.max(Style.size.boardAppChipMinWidth, appLabelText.implicitWidth + Style.spacing.s * 2)
                                    height: Style.size.boardChipHeight
                                    radius: Style.radius.chip
                                    color: Style.alpha(root.runningStatus ? root.neutralAccent : root.accentColor, root.runningStatus ? 0.08 : 0.14)
                                    border.color: Style.alpha(root.runningStatus ? root.neutralAccent : root.accentColor, root.runningStatus ? 0.16 : 0.24)
                                    border.width: Style.size.border
                                    visible: root.showAppChip

                                    Text {
                                        id: appLabelText

                                        anchors.centerIn: parent
                                        color: root.runningStatus ? Style.paletteColor(root.theme, "textMuted") : root.accentColor
                                        elide: Text.ElideRight
                                        font.family: Style.fontFamily(root.visualStyle)
                                        font.pixelSize: Style.font.boardChip
                                        font.weight: Font.Black
                                        maximumLineCount: 1
                                        text: root.appLabel
                                    }

                                }

                            }

                        }

                        Rectangle {
                            width: parent.width
                            height: 1
                            color: Style.alpha(Style.paletteColor(root.theme, "textStrong"), 0.08)
                        }

                        Column {
                            width: parent.width
                            spacing: Style.spacing.s

                            Row {
                                width: parent.width
                                spacing: Style.spacing.xs

                                Text {
                                    color: root.completedStatus ? root.neutralAccent : (root.questionMode ? Style.paletteColor(root.theme, "lineUser") : Style.paletteColor(root.theme, "lineAgent"))
                                    font.family: Style.fontFamily(root.visualStyle)
                                    font.pixelSize: Style.font.boardBody
                                    font.weight: Font.Black
                                    text: root.questionMode ? ">" : "$"
                                }

                                Text {
                                    width: Math.max(0, parent.width - Style.spacing.l)
                                    color: Style.paletteColor(root.theme, "textStrong")
                                    font.family: Style.fontFamily(root.visualStyle)
                                    font.pixelSize: Style.font.boardTitle
                                    font.weight: Font.Black
                                    maximumLineCount: 3
                                    wrapMode: Text.WordWrap
                                    elide: Text.ElideRight
                                    text: root.primaryLine
                                }

                            }

                            Row {
                                width: parent.width
                                spacing: Style.spacing.xs

                                Text {
                                    color: root.completedStatus ? root.neutralAccent : (root.questionMode ? Style.paletteColor(root.theme, "lineAgent") : Style.paletteColor(root.theme, "lineUser"))
                                    font.family: Style.fontFamily(root.visualStyle)
                                    font.pixelSize: Style.font.boardBody
                                    font.weight: Font.Black
                                    text: root.questionMode ? "$" : ">"
                                }

                                Text {
                                    width: Math.max(0, parent.width - Style.spacing.l)
                                    color: Style.paletteColor(root.theme, "textMuted")
                                    font.family: Style.fontFamily(root.visualStyle)
                                    font.pixelSize: Style.font.boardBody
                                    font.weight: Font.DemiBold
                                    maximumLineCount: 4
                                    wrapMode: Text.WordWrap
                                    elide: Text.ElideRight
                                    text: root.secondaryLine
                                }

                            }

                        }

                        Rectangle {
                            width: parent.width
                            height: root.questionMode ? Style.size.inputHeight : 0
                            visible: root.questionMode
                            radius: Style.radius.input
                            color: Style.paletteColor(root.theme, "inputFill")
                            border.color: Style.paletteColor(root.theme, "inputOutline")
                            border.width: Style.size.border

                            TextInput {
                                id: answerField

                                anchors.fill: parent
                                anchors.leftMargin: Style.spacing.m
                                anchors.rightMargin: Style.spacing.m
                                color: Style.paletteColor(root.theme, "textStrong")
                                font.family: Style.fontFamily(root.visualStyle)
                                font.pixelSize: Style.font.boardBody
                                selectByMouse: true
                                verticalAlignment: TextInput.AlignVCenter
                                onTextChanged: root.answerEdited(text)
                                onAccepted: root.submitAnswer()
                                Component.onCompleted: text = root.answerText
                                Keys.onPressed: function(event) {
                                    if (event.key === Qt.Key_Escape) {
                                        root.closeIfPossible();
                                        event.accepted = true;
                                    }
                                }
                            }

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.leftMargin: Style.spacing.m
                                color: Style.paletteColor(root.theme, "textDim")
                                font.family: Style.fontFamily(root.visualStyle)
                                font.pixelSize: Style.font.boardBody
                                visible: root.questionMode && !answerField.text.length && !answerField.activeFocus
                                text: root.tr("answer")
                            }

                        }

                        Row {
                            width: parent.width
                            spacing: Style.spacing.s

                            CodeIslandActionButton {
                                visible: root.approvalMode
                                fillColor: Style.paletteColor(root.theme, "approveFill")
                                label: root.tr("Approve")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.approveRequested()
                            }

                            CodeIslandActionButton {
                                visible: root.approvalMode
                                fillColor: Style.paletteColor(root.theme, "denyFill")
                                label: root.tr("Deny")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.denyRequested()
                            }

                            CodeIslandActionButton {
                                visible: root.questionMode
                                enabled: answerField.text.trim().length > 0
                                fillColor: Style.paletteColor(root.theme, "answerFill")
                                label: root.tr("Send")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.submitAnswer()
                            }

                            CodeIslandActionButton {
                                visible: root.questionMode
                                fillColor: Style.paletteColor(root.theme, "denyFill")
                                label: root.tr("Decline")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.denyRequested()
                            }

                            CodeIslandActionButton {
                                visible: root.completionMode
                                enabled: root.targetSessionId.length > 0
                                fillColor: Style.paletteColor(root.theme, "answerFill")
                                label: root.tr("Open")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.activateSession(root.targetSessionId)
                            }

                            CodeIslandActionButton {
                                visible: root.completionMode
                                outlined: true
                                fillColor: root.statusAccent
                                label: root.tr("List")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.listRequested(root.viewMode)
                            }

                            CodeIslandActionButton {
                                visible: !root.completionMode
                                enabled: root.targetSessionId.length > 0
                                outlined: true
                                fillColor: root.statusAccent
                                label: root.tr("Open")
                                theme: root.theme
                                visualStyle: root.visualStyle
                                onClicked: root.activateSession(root.targetSessionId)
                            }

                        }

                    }

                }

                Text {
                    width: parent.width
                    color: Style.paletteColor(root.theme, "textDim")
                    elide: Text.ElideRight
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardMeta
                    maximumLineCount: 1
                    text: root.warning
                    visible: root.warning.length > 0
                }

            }

        }

    }

}
