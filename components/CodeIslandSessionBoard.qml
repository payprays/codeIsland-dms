import QtQuick 2.15
import "../lib/CodeIslandI18n.js" as LocalI18n
import "../lib/CodeIslandStyle.js" as Style

Item {
    id: root

    property var groups: []
    property bool connected: false
    property string warning: ""
    property var interaction: null
    property string answerText: ""
    property var closePopout: null
    property string viewMode: "all"
    property string locale: "en"
    property var theme: null
    property var visualStyle: null
    readonly property var filteredGroups: root.filteredGroupsFor(root.viewMode)
    readonly property var flatCards: root.flattenCards(root.filteredGroups)
    readonly property bool hasGroups: root.filteredGroups && root.filteredGroups.length > 0
    readonly property bool hasInteraction: root.interaction && root.interaction.state === "open"
    readonly property bool approvalMode: root.hasInteraction && root.interaction.type === "approval"
    readonly property bool questionMode: root.hasInteraction && root.interaction.type === "question"
    readonly property string actionPrompt: root.hasInteraction && typeof root.interaction.prompt_text === "string" ? root.interaction.prompt_text : ""
    property int selectedIndex: 0

    signal approveRequested()
    signal denyRequested()
    signal answerRequested(string answer)
    signal answerEdited(string answer)
    signal sessionActivated(string sessionId)
    signal viewModeRequested(string viewMode)

    function activateSession(sessionId) {
        if (root.closePopout)
            root.closePopout();
        root.sessionActivated(sessionId);
    }

    function flattenCards(groups) {
        var result = [];
        var source = groups || [];
        for (var groupIndex = 0; groupIndex < source.length; groupIndex += 1) {
            var cards = source[groupIndex] && source[groupIndex].cards ? source[groupIndex].cards : [];
            for (var cardIndex = 0; cardIndex < cards.length; cardIndex += 1)
                result.push(cards[cardIndex]);
        }
        return result;
    }

    function normalizedSelectedIndex() {
        if (!root.flatCards || !root.flatCards.length)
            return -1;

        return Math.max(0, Math.min(root.selectedIndex, root.flatCards.length - 1));
    }

    function selectedCard() {
        var index = root.normalizedSelectedIndex();
        return index >= 0 ? root.flatCards[index] : null;
    }

    function cardSessionId(card) {
        return card && typeof card.sessionId === "string" ? card.sessionId : "";
    }

    function selectedSessionId() {
        return root.cardSessionId(root.selectedCard());
    }

    function cardGlobalIndex(card) {
        if (!card || !root.flatCards)
            return -1;

        var sessionId = root.cardSessionId(card);
        for (var index = 0; index < root.flatCards.length; index += 1) {
            if (sessionId.length && root.cardSessionId(root.flatCards[index]) === sessionId)
                return index;

            if (!sessionId.length && root.flatCards[index] === card)
                return index;
        }
        return -1;
    }

    function cardSelected(card) {
        var sessionId = root.cardSessionId(card);
        var selectedId = root.selectedSessionId();
        if (sessionId.length && selectedId.length)
            return sessionId === selectedId;

        return root.cardGlobalIndex(card) === root.normalizedSelectedIndex();
    }

    function ensureSelectedIndex() {
        var index = root.normalizedSelectedIndex();
        root.selectedIndex = Math.max(0, index);
    }

    function selectedCardTop(index) {
        if (!root.filteredGroups || index < 0)
            return 0;

        var currentIndex = 0;
        var y = 0;
        var cardHeight = Style.sizeValue(root.visualStyle, "boardCardHeight");
        var cardSpacing = root.cardSpacing();
        for (var groupIndex = 0; groupIndex < root.filteredGroups.length; groupIndex += 1) {
            var group = root.filteredGroups[groupIndex];
            var cards = group && group.cards ? group.cards : [];
            y += root.groupHeaderBlockHeight();
            for (var cardIndex = 0; cardIndex < cards.length; cardIndex += 1) {
                if (currentIndex === index)
                    return y;

                y += cardHeight + cardSpacing;
                currentIndex += 1;
            }
            y += Style.spacing.m;
        }
        return 0;
    }

    function cardSpacing() {
        return Style.sizeValue(root.visualStyle, "boardCardSpacing");
    }

    function groupHeaderBlockHeight() {
        return Style.boolValue(root.visualStyle, "showGroupHeaders") ? 24 + root.cardSpacing() : 0;
    }

    function keepSelectedVisible() {
        var index = root.normalizedSelectedIndex();
        if (index < 0 || !sessionScroller)
            return ;

        var cardTop = root.selectedCardTop(index);
        var cardBottom = cardTop + Style.sizeValue(root.visualStyle, "boardCardHeight");
        if (cardTop < sessionScroller.contentY)
            sessionScroller.contentY = Math.max(0, cardTop - root.cardSpacing());
        else if (cardBottom > sessionScroller.contentY + sessionScroller.height)
            sessionScroller.contentY = Math.max(0, cardBottom - sessionScroller.height + root.cardSpacing());

    }

    function selectRelative(delta) {
        if (!root.flatCards || !root.flatCards.length)
            return false;

        var nextIndex = root.normalizedSelectedIndex() + delta;
        root.selectedIndex = Math.max(0, Math.min(nextIndex, root.flatCards.length - 1));
        root.keepSelectedVisible();
        return true;
    }

    function selectViewRelative(delta) {
        var modes = ["all", "finished", "active"];
        var index = modes.indexOf(root.viewMode);
        if (index < 0)
            index = 0;
        var nextIndex = (index + delta + modes.length) % modes.length;
        root.requestViewMode(modes[nextIndex]);
        return true;
    }

    function activateSelectedSession() {
        var card = root.selectedCard();
        var sessionId = card && typeof card.sessionId === "string" ? card.sessionId : "";
        if (!sessionId.length)
            return false;

        root.activateSession(sessionId);
        return true;
    }

    function keyHasPlainModifiers(event) {
        return event.modifiers === Qt.NoModifier || event.modifiers === Qt.ShiftModifier;
    }

    function handleKeyEvent(event) {
        if (answerField.activeFocus)
            return false;

        if (!root.keyHasPlainModifiers(event))
            return false;

        if (event.key === Qt.Key_1) {
            root.requestViewMode("all");
            return true;
        }
        if (event.key === Qt.Key_2) {
            root.requestViewMode("finished");
            return true;
        }
        if (event.key === Qt.Key_3) {
            root.requestViewMode("active");
            return true;
        }
        if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab)
            return root.selectViewRelative(event.key === Qt.Key_Backtab || event.modifiers === Qt.ShiftModifier ? -1 : 1);

        if (event.key === Qt.Key_Down || event.key === Qt.Key_J)
            return root.selectRelative(1);

        if (event.key === Qt.Key_Up || event.key === Qt.Key_K)
            return root.selectRelative(-1);

        if (event.key === Qt.Key_Left || event.key === Qt.Key_H)
            return root.selectViewRelative(-1);

        if (event.key === Qt.Key_Right || event.key === Qt.Key_L)
            return root.selectViewRelative(1);

        if (event.key === Qt.Key_PageDown)
            return root.selectRelative(4);

        if (event.key === Qt.Key_PageUp)
            return root.selectRelative(-4);

        if (event.key === Qt.Key_Home) {
            if (!root.flatCards.length)
                return false;
            root.selectedIndex = 0;
            root.keepSelectedVisible();
            return true;
        }

        if (event.key === Qt.Key_End) {
            if (!root.flatCards.length)
                return false;
            root.selectedIndex = root.flatCards.length - 1;
            root.keepSelectedVisible();
            return true;
        }

        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
            return root.activateSelectedSession();

        if (event.key === Qt.Key_Escape) {
            if (root.closePopout) {
                root.closePopout();
                return true;
            }
            return false;
        }

        if (event.key === Qt.Key_A && root.approvalMode) {
            root.approveRequested();
            return true;
        }

        if (event.key === Qt.Key_D && root.hasInteraction) {
            root.denyRequested();
            return true;
        }

        return false;
    }

    function totalSessions() {
        var total = 0;
        if (!root.filteredGroups)
            return total;

        for (var index = 0; index < root.filteredGroups.length; index += 1) {
            total += root.filteredGroups[index] && root.filteredGroups[index].count ? root.filteredGroups[index].count : 0;
        }
        return total;
    }

    function cardSortPriority(card) {
        if (card && typeof card.sortPriority === "number")
            return card.sortPriority;

        var status = card && card.status ? card.status : "idle";
        if (status === "completed")
            return 0;
        if (card && card.hasInteraction)
            return 1;
        if (status === "waiting_approval" || status === "waiting_answer" || status === "running")
            return 2;
        if (status === "failed" || status === "cancelled")
            return 3;
        return 9;
    }

    function cardSortStamp(card) {
        return card && typeof card.sortStamp === "string" ? card.sortStamp : "";
    }

    function compareCards(left, right) {
        var leftPriority = root.cardSortPriority(left);
        var rightPriority = root.cardSortPriority(right);
        if (leftPriority !== rightPriority)
            return leftPriority - rightPriority;

        var leftStamp = root.cardSortStamp(left);
        var rightStamp = root.cardSortStamp(right);
        if (leftStamp !== rightStamp)
            return leftStamp < rightStamp ? 1 : -1;

        var leftTitle = left && left.title ? left.title : "";
        var rightTitle = right && right.title ? right.title : "";
        if (leftTitle !== rightTitle)
            return leftTitle.localeCompare(rightTitle);

        return root.cardSessionId(left).localeCompare(root.cardSessionId(right));
    }

    function compareGroups(left, right) {
        var leftCards = left && left.cards ? left.cards : [];
        var rightCards = right && right.cards ? right.cards : [];
        if (leftCards.length && rightCards.length)
            return root.compareCards(leftCards[0], rightCards[0]);
        if (leftCards.length !== rightCards.length)
            return rightCards.length - leftCards.length;

        var leftLabel = left && left.label ? left.label : "";
        var rightLabel = right && right.label ? right.label : "";
        return leftLabel.localeCompare(rightLabel);
    }

    function cardMatchesMode(card, mode) {
        if (!card)
            return false;

        if (mode === "all")
            return true;

        if (mode === "finished")
            return card.status === "completed";

        if (mode === "active")
            return card.hasInteraction
                || card.status === "waiting_approval"
                || card.status === "waiting_answer"
                || card.status === "running"
                || card.status === "failed";

        return true;
    }

    function filteredGroupsFor(mode) {
        var source = root.groups || [];
        var result = [];

        for (var groupIndex = 0; groupIndex < source.length; groupIndex += 1) {
            var group = source[groupIndex];
            var cards = [];
            var sourceCards = group && group.cards ? group.cards : [];
            for (var cardIndex = 0; cardIndex < sourceCards.length; cardIndex += 1) {
                if (root.cardMatchesMode(sourceCards[cardIndex], mode))
                    cards.push(sourceCards[cardIndex]);
            }
            if (cards.length) {
                cards.sort(root.compareCards);
                result.push({
                    providerKey: group.providerKey,
                    label: group.label,
                    count: cards.length,
                    cards: cards,
                });
            }
        }

        result.sort(root.compareGroups);
        return result;
    }

    function requestViewMode(mode) {
        if (!mode || !mode.length)
            return ;

        root.viewMode = mode;
        root.viewModeRequested(mode);
    }

    function tr(term) {
        return LocalI18n.tr(term, root.locale);
    }

    function trf(term, values) {
        return LocalI18n.format(term, root.locale, values);
    }

    function emptyTitle() {
        if (!root.connected)
            return root.tr("Waiting for daemon");

        if (root.viewMode === "active")
            return root.tr("No active sessions");

        if (root.viewMode === "finished")
            return root.tr("No finished sessions");

        return root.tr("No sessions");
    }

    function summaryText() {
        var count = root.totalSessions();
        if (root.viewMode === "active")
            return root.trf("%1 active", [count]);

        if (root.viewMode === "finished")
            return root.trf("%1 finished", [count]);

        return root.trf("%1 sessions", [count]);
    }

    function groupTitle(group) {
        if (!group)
            return root.tr("Other") + " (0)";

        return group.label + " (" + group.count + ")";
    }

    function groupMarkerColor(group) {
        var cards = group && group.cards ? group.cards : [];
        var hasWaiting = false;
        var hasFailed = false;

        for (var index = 0; index < cards.length; index += 1) {
            var status = cards[index] && cards[index].status ? cards[index].status : "idle";
            if (status === "completed")
                return Style.paletteColor(root.theme, "successAccent");

            if (status === "waiting_approval" || status === "waiting_answer")
                hasWaiting = true;

            if (status === "failed" || status === "cancelled")
                hasFailed = true;
        }

        if (hasWaiting)
            return Style.accentFor("waiting_approval", root.theme);

        if (hasFailed)
            return Style.accentFor("failed", root.theme);

        return Style.paletteColor(root.theme, "textDim");
    }

    function isFirstGroupCard(group, card) {
        return group && group.cards && group.cards.length && group.cards[0] === card;
    }

    implicitWidth: Style.sizeValue(root.visualStyle, "boardWidth")
    implicitHeight: Style.sizeValue(root.visualStyle, "boardHeight")
    activeFocusOnTab: true
    focus: true
    onFlatCardsChanged: {
        root.ensureSelectedIndex();
        Qt.callLater(root.keepSelectedVisible);
    }
    onViewModeChanged: {
        root.ensureSelectedIndex();
        Qt.callLater(root.keepSelectedVisible);
    }
    onAnswerTextChanged: {
        if (!answerField.activeFocus && answerField.text !== root.answerText)
            answerField.text = root.answerText;

    }
    Keys.onPressed: function(event) {
        event.accepted = root.handleKeyEvent(event);
    }

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
                width: Math.max(0, parent.width - scopeTabs.width - summaryLabel.width - (parent.spacing * 2))
                height: 1
            }

            Text {
                id: summaryLabel

                anchors.verticalCenter: parent.verticalCenter
                color: Style.paletteColor(root.theme, "textDim")
                font.family: Style.fontFamily(root.visualStyle)
                font.pixelSize: Style.font.boardMeta
                font.weight: Font.Black
                text: root.summaryText()
            }

        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: topBar.bottom
            height: 1
            color: Style.paletteColor(root.theme, "boardDivider")
        }

        Flickable {
            id: sessionScroller

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: topBar.bottom
            anchors.topMargin: Style.spacing.s
            anchors.bottom: parent.bottom
            anchors.bottomMargin: root.hasInteraction ? actionPanel.height + Style.spacing.l : Style.spacing.s
            clip: true
            contentWidth: width
            contentHeight: groupsColumn.implicitHeight

            Column {
                id: groupsColumn

                width: sessionScroller.width
                spacing: Style.spacing.m
                visible: root.hasGroups

                Repeater {
                    model: root.filteredGroups || []

                    delegate: Column {
                        id: groupDelegate

                        required property var modelData

                        width: groupsColumn.width
                        spacing: root.cardSpacing()

                        Row {
                            width: parent.width
                            height: Style.boolValue(root.visualStyle, "showGroupHeaders") ? 24 : 0
                            spacing: Style.spacing.s
                            visible: Style.boolValue(root.visualStyle, "showGroupHeaders")

                            Rectangle {
                                width: 9
                                height: 9
                                radius: width / 2
                                anchors.verticalCenter: parent.verticalCenter
                                color: root.groupMarkerColor(groupDelegate.modelData)
                            }

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                color: Style.paletteColor(root.theme, "textMuted")
                                font.family: Style.fontFamily(root.visualStyle)
                                font.pixelSize: Style.font.boardBody
                                font.weight: Font.Black
                                text: root.groupTitle(groupDelegate.modelData)
                            }

                        }

                        Column {
                            width: parent.width
                            spacing: Style.spacing.s

                            Repeater {
                                model: groupDelegate.modelData.cards || []

                                delegate: CodeIslandSessionCard {
                                    required property var modelData

                                    width: parent.width
                                    card: modelData
                                    primary: modelData.hasInteraction || root.isFirstGroupCard(groupDelegate.modelData, modelData)
                                    selected: root.cardSelected(modelData)
                                    locale: root.locale
                                    theme: root.theme
                                    visualStyle: root.visualStyle
                                    onSessionActivated: function(sessionId) {
                                        root.activateSession(sessionId);
                                    }
                                }

                            }

                        }

                    }

                }

            }

        }

        Item {
            anchors.fill: sessionScroller
            visible: !root.hasGroups

            Column {
                anchors.centerIn: parent
                spacing: Style.spacing.s

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: Style.paletteColor(root.theme, "textStrong")
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardTitle
                    font.weight: Font.Black
                    text: root.emptyTitle()
                }

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    color: Style.paletteColor(root.theme, "textDim")
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.boardBody
                    text: root.warning.length ? root.warning : "codeislandd.sock"
                }

            }

        }

        Rectangle {
            id: actionPanel

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: root.questionMode ? 112 : 82
            radius: Style.radiusValue(root.visualStyle, "boardCard")
            visible: root.hasInteraction
            color: Style.alpha(Style.accentFor(root.interaction && root.interaction.type === "approval" ? "waiting_approval" : "waiting_answer", root.theme), 0.11)
            border.color: Style.alpha(Style.accentFor(root.interaction && root.interaction.type === "approval" ? "waiting_approval" : "waiting_answer", root.theme), 0.34)
            border.width: Style.size.border

            Column {
                anchors.fill: parent
                anchors.margins: Style.spacing.s
                spacing: Style.spacing.s

                Row {
                    width: parent.width
                    spacing: Style.spacing.s

                    Text {
                        width: Math.max(0, parent.width - actionButtons.width - parent.spacing)
                        color: Style.paletteColor(root.theme, "textStrong")
                        elide: Text.ElideRight
                        font.family: Style.fontFamily(root.visualStyle)
                        font.pixelSize: Style.font.boardBody
                        font.weight: Font.Black
                        maximumLineCount: 1
                        text: root.actionPrompt.length ? root.actionPrompt : root.tr("Action needed")
                    }

                    Row {
                        id: actionButtons

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
                            fillColor: Style.paletteColor(root.theme, "answerFill")
                            label: root.tr("Send")
                            theme: root.theme
                            visualStyle: root.visualStyle
                            onClicked: root.answerRequested(answerField.text)
                        }

                        CodeIslandActionButton {
                            visible: root.questionMode
                            fillColor: Style.paletteColor(root.theme, "denyFill")
                            label: root.tr("Decline")
                            theme: root.theme
                            visualStyle: root.visualStyle
                            onClicked: root.denyRequested()
                        }

                    }

                }

                Rectangle {
                    width: parent.width
                    height: Style.size.inputHeight
                    radius: Style.radius.input
                    visible: root.questionMode
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
                        onAccepted: root.answerRequested(text)
                        Component.onCompleted: text = root.answerText
                        Keys.onPressed: function(event) {
                            if (event.key === Qt.Key_Escape && root.closePopout) {
                                root.closePopout();
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

            }

        }

    }

}
