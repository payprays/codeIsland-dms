import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Modules.Plugins
import "./components" as Components
import "./lib/CodeIslandI18n.js" as LocalI18n
import "./lib/CodeIslandProtocol.js" as Protocol
import "./lib/CodeIslandStyle.js" as Style

PluginComponent {
    id: root

    property string configuredSocketPath: {
        if (pluginData && typeof pluginData.socketPath === "string")
            return pluginData.socketPath;

        return "";
    }
    property string socketPath: configuredSocketPath.length ? configuredSocketPath : Protocol.defaultSocketPath(Quickshell.env("XDG_RUNTIME_DIR"), Quickshell.env("UID"))
    property var daemonSnapshot: Protocol.normalizeSnapshot({
    })
    property bool hasFullSnapshot: false
    property string lastWarningMessage: ""
    property string lastTransportError: ""
    property int nextRequestId: 1
    property int surfaceFreshnessTick: 0
    property string draftAnswer: ""
    property string draftAnswerInteractionId: ""
    property bool popoutVisible: false
    property string lastAutoRevealToken: ""
    property string forcedSessionListToken: ""
    readonly property var visualStyle: Style.visualConfig(pluginData)
    readonly property string uiLocale: LocalI18n.resolveLocale(root.visualStyle.language, I18n.currentLocale || Qt.locale().name)
    readonly property string configuredDefaultViewMode: normalizedDefaultViewMode()
    readonly property bool revealOnCompletion: Style.boolValue(visualStyle, "revealOnCompletion")
    property string listViewMode: configuredDefaultViewMode
    readonly property bool daemonConnected: daemonSocket.connected
    readonly property string projectionWarning: lastWarningMessage || lastTransportError
    readonly property var islandState: Protocol.projectIslandState(daemonSnapshot, daemonConnected, projectionWarning, surfaceFreshnessTick)
    readonly property var primarySession: islandState && islandState.session ? islandState.session : null
    readonly property var primarySessionState: islandState && islandState.sessionState ? islandState.sessionState : null
    readonly property var primaryActivity: islandState && islandState.activity ? islandState.activity : null
    readonly property var primaryTask: islandState && islandState.task ? islandState.task : null
    readonly property var primaryInteraction: islandState && islandState.interaction ? islandState.interaction : null
    readonly property string projectedStatus: islandState && islandState.status ? islandState.status : "idle"
    readonly property string titleText: islandState && islandState.title ? islandState.title : Protocol.summaryTitle(primarySession)
    readonly property string detailText: Protocol.detailText(primarySession, primarySessionState, primaryTask, primaryInteraction, primaryActivity, daemonConnected, projectionWarning)
    readonly property var sessionGroups: Protocol.projectSessionGroups(daemonSnapshot, daemonConnected, projectionWarning)
    readonly property var sessionDots: Protocol.projectSessionDots(sessionGroups)
    readonly property string projectText: islandState && islandState.project ? islandState.project : Protocol.projectLabel(primarySession)
    readonly property bool forceSessionList: islandState && forcedSessionListToken.length && forcedSessionListToken === islandState.token
    readonly property bool glassBlurActive: Theme.blurForegroundLayers || Theme.transparentBlurLayers
    readonly property real glassSoftAlpha: Style.scaledAlpha(visualStyle, glassBlurActive ? 0.08 : Math.min(Theme.popupTransparency, 0.28))
    readonly property real glassCardAlpha: Style.scaledAlpha(visualStyle, glassBlurActive ? 0.16 : Math.min(Theme.popupTransparency, 0.42))
    readonly property real glassHotAlpha: Style.scaledAlpha(visualStyle, glassBlurActive ? 0.22 : Math.min(Theme.popupTransparency, 0.48))
    readonly property real glassLineAlpha: Style.scaledAlpha(visualStyle, glassBlurActive ? 0.24 : 0.16)
    readonly property real outlineLineAlpha: Style.scaledBorderAlpha(visualStyle, glassLineAlpha)
    readonly property real outlineDividerAlpha: Style.scaledBorderAlpha(visualStyle, glassBlurActive ? 0.18 : 0.12)
    readonly property var themePalette: ({
        shellOuter: Theme.floatingSurface,
        shellInner: Theme.withAlpha(Theme.surfaceContainerHigh, glassSoftAlpha),
        shellOutline: Theme.outlineMedium,
        barShell: Theme.ccPillInactiveBg,
        barCore: Theme.withAlpha(Theme.surfaceContainerHigh, glassSoftAlpha),
        barOutline: Theme.outlineMedium,
        textStrong: Theme.surfaceText,
        textMuted: Theme.surfaceTextMedium,
        textDim: Theme.surfaceVariantText,
        idleBg: Theme.surfaceHover,
        runningBg: Theme.info,
        waitingBg: Theme.warning,
        successBg: Theme.success,
        failedBg: Theme.error,
        idleAccent: Theme.surfaceVariantText,
        runningAccent: Theme.info,
        waitingAccent: Theme.warning,
        successAccent: Theme.success,
        failedAccent: Theme.error,
        chipText: Theme.buttonText,
        buttonNeutral: Theme.buttonBg,
        buttonNeutralText: Theme.buttonText,
        approveFill: Theme.success,
        denyFill: Theme.error,
        answerFill: Theme.buttonBg,
        inputFill: Theme.withAlpha(Theme.surfaceContainerHighest, glassCardAlpha),
        inputOutline: Theme.withAlpha(Theme.outline, outlineLineAlpha),
        boardOuter: "transparent",
        boardChrome: "transparent",
        boardCard: Theme.withAlpha(Theme.surfaceContainerHigh, glassCardAlpha),
        boardCardHot: Theme.withAlpha(Theme.primary, glassHotAlpha),
        boardCardBorder: Theme.withAlpha(Theme.outline, outlineLineAlpha),
        boardDivider: Theme.withAlpha(Theme.outline, outlineDividerAlpha),
        boardTab: Theme.withAlpha(Theme.surfaceVariant, glassSoftAlpha),
        boardTabActive: Theme.withAlpha(Theme.primary, glassHotAlpha),
        boardRail: Theme.primary,
        boardRailDeep: Theme.secondary,
        lineUser: Theme.primary,
        lineAgent: Theme.warning,
        lineMuted: Theme.surfaceTextMedium,
        providerClaude: Theme.warning,
        providerCodex: Theme.primary,
        providerGemini: Theme.secondary,
        providerOpenCode: Theme.success,
        providerCursor: Theme.surfaceText,
        providerOther: Theme.surfaceVariantText,
    })

    onConfiguredDefaultViewModeChanged: {
        if (!root.popoutVisible)
            root.listViewMode = root.configuredDefaultViewMode;

    }

    function nextEnvelopeId(prefix) {
        var value = prefix + "-" + root.nextRequestId;
        root.nextRequestId += 1;
        return value;
    }

    function writeEnvelope(payload) {
        if (!daemonSocket.connected)
            return false;

        daemonSocket.write(Protocol.encodeEnvelope(payload));
        daemonSocket.flush();
        return true;
    }

    function subscribeToDaemon() {
        root.writeEnvelope(Protocol.subscribeEnvelope(root.nextEnvelopeId("subscribe")));
    }

    function handleDaemonLine(line) {
        var message = Protocol.parseLine(line);
        if (!message) {
            root.lastWarningMessage = "Invalid daemon payload";
            return ;
        }
        if (message.kind === "snapshot.full") {
            root.daemonSnapshot = Protocol.normalizeSnapshot(message.payload);
            root.hasFullSnapshot = true;
            root.lastWarningMessage = "";
            return ;
        }
        if (message.kind === "snapshot.patch") {
            if (!root.hasFullSnapshot)
                return ;

            root.daemonSnapshot = Protocol.normalizeSnapshot(message.payload);
            return ;
        }
        if (message.kind === "daemon.warning") {
            root.lastWarningMessage = message.payload && message.payload.message ? message.payload.message : "daemon_warning";
            return ;
        }
        if (message.ok === false) {
            root.lastWarningMessage = message.error && message.error.message ? message.error.message : "Daemon rejected the request";
            return ;
        }
        if (message.ok === true)
            root.lastWarningMessage = "";

    }

    function reconnectLater() {
        if (!reconnectTimer.running)
            reconnectTimer.start();

    }

    function resetSnapshotState() {
        root.daemonSnapshot = Protocol.normalizeSnapshot({
        });
        root.hasFullSnapshot = false;
    }

    function respondToInteraction(action) {
        if (!root.primaryInteraction)
            return false;

        var answer = action === "answer" ? root.draftAnswer.trim() : "";
        if (action === "answer" && !answer.length) {
            root.lastWarningMessage = "Answer cannot be empty";
            return false;
        }
        var sent = root.writeEnvelope(Protocol.interactionRespondEnvelope(root.nextEnvelopeId("interaction"), root.primaryInteraction.interaction_id, action, answer));
        if (!sent) {
            root.lastWarningMessage = "Daemon socket is not connected";
            return false;
        }
        if (action === "answer") {
            root.draftAnswer = "";
            root.draftAnswerInteractionId = "";
        }
        return true;
    }

    function focusSession(sessionId) {
        if (!sessionId || !sessionId.length)
            return ;

        var sent = root.writeEnvelope(Protocol.focusSessionEnvelope(root.nextEnvelopeId("focus"), sessionId));
        if (!sent)
            root.lastWarningMessage = "Daemon socket is not connected";

    }

    function normalizeViewMode(mode) {
        if (mode === "all" || mode === "finished" || mode === "active" || mode === "focus")
            return mode;

        if (mode === "cli")
            return "all";

        return "all";
    }

    function tr(term) {
        return LocalI18n.tr(term, root.uiLocale);
    }

    function statusLabel(status) {
        return LocalI18n.statusLabel(status, root.uiLocale);
    }

    function normalizedDefaultViewMode() {
        var mode = root.visualStyle && root.visualStyle.defaultViewMode ? root.visualStyle.defaultViewMode : "all";
        if (mode === "all" || mode === "finished" || mode === "active")
            return mode;

        return "all";
    }

    function showSessionList(mode) {
        var normalizedMode = root.normalizeViewMode(mode);
        if (normalizedMode === "focus") {
            root.forcedSessionListToken = "";
            return ;
        }

        root.listViewMode = normalizedMode;
        if (root.islandState && root.islandState.token)
            root.forcedSessionListToken = root.islandState.token;

    }

    function openWithMode(mode) {
        root.showSessionList(mode);
        if (!root.popoutVisible)
            root.triggerPopout();

    }

    function toggleWithMode(mode) {
        root.showSessionList(mode);
        if (root.popoutVisible) {
            root.closePopout();
            return ;
        }
        root.triggerPopout();
    }

    function shouldAutoRevealIslandSurface() {
        if (!root.islandState || !root.islandState.autoReveal || !root.islandState.token.length)
            return false;

        if (root.islandState.surface === "completionCard" && !root.revealOnCompletion)
            return false;

        return true;
    }

    function maybeRevealIslandSurface() {
        if (!root.shouldAutoRevealIslandSurface())
            return ;

        if (root.lastAutoRevealToken === root.islandState.token)
            return ;

        root.lastAutoRevealToken = root.islandState.token;
        autoRevealTimer.restart();
    }

    layerNamespacePlugin: "codeisland"
    popoutWidth: Style.sizeValue(root.visualStyle, "boardWidth")
    popoutHeight: !forceSessionList && islandState && islandState.surface === "questionCard" ? Style.sizeValue(root.visualStyle, "boardQuestionHeight") : (!forceSessionList && islandState && (islandState.surface === "approvalCard" || islandState.surface === "completionCard") ? Style.sizeValue(root.visualStyle, "boardFocusedHeight") : Style.sizeValue(root.visualStyle, "boardHeight"))
    onIslandStateChanged: {
        if (!islandState || islandState.token !== forcedSessionListToken)
            forcedSessionListToken = "";

        maybeRevealIslandSurface();
    }
    onRevealOnCompletionChanged: {
        if (!root.revealOnCompletion && root.islandState && root.islandState.surface === "completionCard")
            autoRevealTimer.stop();

    }
    onPrimaryInteractionChanged: {
        var interactionId = primaryInteraction && primaryInteraction.interaction_id ? primaryInteraction.interaction_id : "";
        if (!primaryInteraction || primaryInteraction.type !== "question") {
            draftAnswer = "";
            draftAnswerInteractionId = "";
            return ;
        }
        if (interactionId !== draftAnswerInteractionId) {
            draftAnswer = "";
            draftAnswerInteractionId = interactionId;
        }
    }
    onSocketPathChanged: {
        resetSnapshotState();
        lastWarningMessage = "";
        lastTransportError = "";
        daemonSocket.connected = false;
        reconnectTimer.restart();
    }
    Component.onCompleted: {
        daemonSocket.connected = true;
    }

    Socket {
        id: daemonSocket

        connected: false
        path: root.socketPath
        onConnectionStateChanged: function() {
            if (daemonSocket.connected) {
                reconnectTimer.stop();
                root.lastTransportError = "";
                root.subscribeToDaemon();
                return ;
            }
            root.resetSnapshotState();
            root.reconnectLater();
        }
        onError: function(error) {
            root.lastTransportError = String(error);
            root.resetSnapshotState();
            daemonSocket.connected = false;
            root.reconnectLater();
        }

        parser: SplitParser {
            onRead: function(line) {
                root.handleDaemonLine(line);
            }
        }

    }

    Timer {
        id: reconnectTimer

        interval: 1600
        repeat: true
        running: false
        onTriggered: {
            daemonSocket.connected = false;
            Qt.callLater(function() {
                daemonSocket.connected = true;
            });
        }
    }

    Timer {
        id: autoRevealTimer

        interval: 120
        repeat: false
        onTriggered: {
            if (root.shouldAutoRevealIslandSurface() && root.islandState.token === root.lastAutoRevealToken && !root.popoutVisible)
                root.triggerPopout();

        }
    }

    Timer {
        interval: 15000
        repeat: true
        running: true
        onTriggered: root.surfaceFreshnessTick += 1
    }

    horizontalBarPill: Component {
        Components.CodeIslandPill {
            connected: root.daemonConnected
            detail: root.detailText
            project: root.projectText
            providerKey: root.islandState && root.islandState.providerKey ? root.islandState.providerKey : "other"
            sessionDots: root.sessionDots
            status: root.projectedStatus
            theme: root.themePalette
            title: root.titleText
            visualStyle: root.visualStyle
        }

    }

    verticalBarPill: Component {
        Item {
            implicitWidth: Style.size.verticalPillWidth
            implicitHeight: Style.size.verticalPillHeight

            Rectangle {
                anchors.centerIn: parent
                width: parent.width - Style.spacing.s
                height: parent.height - Style.spacing.s
                radius: Style.radius.card
                color: Style.paletteColor(root.themePalette, "shellOuter")
                border.color: Style.paletteColor(root.themePalette, "shellOutline")
                border.width: Style.size.border
            }

            Column {
                anchors.centerIn: parent
                spacing: Style.spacing.xs

                Rectangle {
                    width: Style.size.orb
                    height: Style.size.orb
                    radius: Style.radius.dot
                    color: Style.accentFor(root.projectedStatus, root.themePalette)
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Text {
                    color: Style.paletteColor(root.themePalette, "textStrong")
                    font.family: Style.fontFamily(root.visualStyle)
                    font.pixelSize: Style.font.chip
                    font.weight: Font.Bold
                    text: root.statusLabel(root.projectedStatus)
                    anchors.horizontalCenter: parent.horizontalCenter
                }

            }

        }

    }

    popoutContent: Component {
        Item {
            id: popoutRoot

            property var closePopout: null
            property var parentPopout: null
            readonly property real pluginInset: Theme.spacingS

            implicitWidth: Math.max(0, islandSurface.implicitWidth - pluginInset * 2)
            implicitHeight: Math.max(0, islandSurface.implicitHeight - pluginInset * 2)
            activeFocusOnTab: true
            focus: true

            function claimPopoutKeyboard() {
                if (popoutRoot.parentPopout && "contentHandlesKeys" in popoutRoot.parentPopout)
                    popoutRoot.parentPopout.contentHandlesKeys = true;

                Qt.callLater(function() {
                    popoutRoot.forceActiveFocus();
                    islandSurface.forceActiveFocus();
                });
            }

            onParentPopoutChanged: claimPopoutKeyboard()
            Component.onCompleted: claimPopoutKeyboard()
            Keys.onPressed: function(event) {
                if (islandSurface && islandSurface.handleSurfaceKeyEvent)
                    event.accepted = islandSurface.handleSurfaceKeyEvent(event);
            }

            Components.IslandSurface {
                id: islandSurface

                x: -popoutRoot.pluginInset
                y: -popoutRoot.pluginInset
                width: popoutRoot.width > 0 ? popoutRoot.width + popoutRoot.pluginInset * 2 : implicitWidth
                height: popoutRoot.height > 0 ? popoutRoot.height + popoutRoot.pluginInset * 2 : implicitHeight
                answerText: root.draftAnswer
                closePopout: popoutRoot.closePopout
                connected: root.daemonConnected
                forceSessionList: root.forceSessionList
                groups: root.sessionGroups
                islandState: root.islandState
                interaction: root.primaryInteraction
                locale: root.uiLocale
                parentPopout: popoutRoot.parentPopout
                theme: root.themePalette
                visualStyle: root.visualStyle
                viewMode: root.listViewMode
                warning: root.projectionWarning
                onAnswerEdited: function(answer) {
                    root.draftAnswer = answer;
                }
                onAnswerRequested: function(answer) {
                    root.draftAnswer = answer;
                    if (root.respondToInteraction("answer"))
                        root.closePopout();

                }
                onApproveRequested: {
                    if (root.respondToInteraction("approve"))
                        root.closePopout();

                }
                onDenyRequested: {
                    if (root.respondToInteraction("deny"))
                        root.closePopout();

                }
                onListRequested: function(viewMode) {
                    if (viewMode && viewMode.length)
                        root.listViewMode = root.normalizeViewMode(viewMode);

                    if (root.islandState && root.islandState.token)
                        root.forcedSessionListToken = root.islandState.token;

                }
                onPopoutVisibilityChanged: function(visible) {
                    root.popoutVisible = visible;
                }
                onSessionActivated: function(sessionId) {
                    root.closePopout();
                    root.focusSession(sessionId);
                }
            }
        }

    }

}
