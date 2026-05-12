.pragma library

var STATUS_PRIORITY = {
    waiting_approval: 0,
    waiting_answer: 1,
    running: 2,
    failed: 3,
    completed: 4,
    idle: 5,
    cancelled: 6,
};

var BOARD_STATUS_PRIORITY = {
    completed: 0,
    waiting_approval: 1,
    waiting_answer: 1,
    running: 2,
    failed: 3,
    cancelled: 3,
    idle: 9,
};

var PROVIDER_ORDER = {
    claude: 0,
    codex: 1,
    gemini: 2,
    opencode: 3,
    cursor: 4,
    trae: 5,
    qoder: 6,
    copilot: 7,
    kimi: 8,
    other: 99,
};

function safeArray(value) {
    return Array.isArray(value) ? value.slice() : [];
}

function stringFor(value) {
    return typeof value === "string" ? value : "";
}

function defaultSocketPath(runtimeDir, uid) {
    var runtime = stringFor(runtimeDir);
    var userId = stringFor(uid);

    if (runtime.length) {
        return runtime + "/codeislandd.sock";
    }

    if (userId.length) {
        return "/tmp/codeisland-" + userId + "/codeislandd.sock";
    }

    return "/tmp/codeislandd.sock";
}

function normalizeSnapshot(payload) {
    var snapshot = payload || {};

    return {
        sessions: safeArray(snapshot.sessions),
        tasks: safeArray(snapshot.tasks),
        interactions: safeArray(snapshot.interactions),
        activities: safeArray(snapshot.activities),
        session_states: safeArray(snapshot.session_states),
        island_state: normalizeIslandState(snapshot.island_state),
        next_seq: typeof snapshot.next_seq === "number" ? snapshot.next_seq : 1,
    };
}

function normalizeIslandState(value) {
    var state = value && typeof value === "object" ? value : {};

    return {
        surface: stringFor(state.surface) || "collapsed",
        active_session_id: stringFor(state.active_session_id),
        active_task_id: stringFor(state.active_task_id),
        active_interaction_id: stringFor(state.active_interaction_id),
        status: stringFor(state.status) || "idle",
        auto_reveal: state.auto_reveal === true,
        approval_queue: safeArray(state.approval_queue),
        question_queue: safeArray(state.question_queue),
        completion_queue: safeArray(state.completion_queue),
        rotation_queue: safeArray(state.rotation_queue),
        updated_at: stringFor(state.updated_at),
    };
}

function encodeEnvelope(payload) {
    return JSON.stringify(payload) + "\n";
}

function subscribeEnvelope(requestId) {
    return {
        id: requestId,
        method: "subscribe",
        params: {
            topics: ["sessions", "tasks", "interactions"],
        },
    };
}

function interactionRespondEnvelope(requestId, interactionId, action, answer) {
    var params = {
        interaction_id: interactionId,
        action: action,
    };

    if (typeof answer === "string" && answer.trim().length) {
        params.answer = answer.trim();
    }

    return {
        id: requestId,
        method: "interaction_respond",
        params: params,
    };
}

function focusSessionEnvelope(requestId, sessionId) {
    return {
        id: requestId,
        method: "focus_session",
        params: {
            session_id: sessionId,
        },
    };
}

function parseLine(rawLine) {
    var line = stringFor(rawLine).trim();

    if (!line.length) {
        return null;
    }

    try {
        return JSON.parse(line);
    } catch (error) {
        return null;
    }
}

function priorityFor(status) {
    return Object.prototype.hasOwnProperty.call(STATUS_PRIORITY, status) ? STATUS_PRIORITY[status] : 99;
}

function boardPriorityFor(status) {
    return Object.prototype.hasOwnProperty.call(BOARD_STATUS_PRIORITY, status) ? BOARD_STATUS_PRIORITY[status] : 99;
}

function stampFor(item) {
    if (!item) {
        return "";
    }

    return stringFor(item.completion_enqueued_at || item.updated_at || item.last_activity_at || item.last_event_at || item.started_at || item.asked_at || item.created_at);
}

function compareSessions(left, right) {
    var leftPriority = priorityFor(left && left.status);
    var rightPriority = priorityFor(right && right.status);

    if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
    }

    var leftStamp = stampFor(left);
    var rightStamp = stampFor(right);

    if (leftStamp !== rightStamp) {
        return leftStamp < rightStamp ? 1 : -1;
    }

    return stringFor(left && left.session_id).localeCompare(stringFor(right && right.session_id));
}

function compareItemsByStamp(left, right) {
    var leftStamp = stampFor(left);
    var rightStamp = stampFor(right);

    if (leftStamp !== rightStamp) {
        return leftStamp < rightStamp ? 1 : -1;
    }

    return 0;
}

function sessionById(snapshot, sessionId) {
    var wanted = stringFor(sessionId);
    if (!wanted.length) {
        return null;
    }

    var sessions = safeArray(snapshot && snapshot.sessions);
    for (var index = 0; index < sessions.length; index += 1) {
        if (sessions[index] && sessions[index].session_id === wanted && !stringFor(sessions[index].ended_at).length) {
            return sessions[index];
        }
    }

    return null;
}

function taskById(snapshot, taskId) {
    var wanted = stringFor(taskId);
    if (!wanted.length) {
        return null;
    }

    var tasks = safeArray(snapshot && snapshot.tasks);
    for (var index = 0; index < tasks.length; index += 1) {
        if (tasks[index] && tasks[index].task_id === wanted) {
            return tasks[index];
        }
    }

    return null;
}

function interactionById(snapshot, interactionId) {
    var wanted = stringFor(interactionId);
    if (!wanted.length) {
        return null;
    }

    var interactions = safeArray(snapshot && snapshot.interactions);
    for (var index = 0; index < interactions.length; index += 1) {
        if (interactions[index] && interactions[index].interaction_id === wanted) {
            return interactions[index];
        }
    }

    return null;
}

function compareActivities(left, right) {
    var leftSeq = left && typeof left.seq === "number" ? left.seq : -1;
    var rightSeq = right && typeof right.seq === "number" ? right.seq : -1;

    if (leftSeq !== rightSeq) {
        return rightSeq - leftSeq;
    }

    return compareItemsByStamp(left, right);
}

function sessionStateFor(snapshot, session) {
    if (!session) {
        return null;
    }

    var states = safeArray(snapshot && snapshot.session_states);
    for (var index = 0; index < states.length; index += 1) {
        if (states[index] && states[index].session_id === session.session_id) {
            return states[index];
        }
    }

    return null;
}

function latestActivityFor(snapshot, session) {
    if (!session) {
        return null;
    }

    var activities = safeArray(snapshot && snapshot.activities).filter(function(activity) {
        return activity && activity.session_id === session.session_id;
    });

    if (!activities.length) {
        return null;
    }

    activities.sort(compareActivities);
    return activities[0];
}

function pickPrimarySession(snapshot) {
    var normalized = normalizeSnapshot(snapshot);
    var islandState = normalized.island_state;
    var activeSession = sessionById(normalized, islandState.active_session_id);
    if (activeSession) {
        return activeSession;
    }

    var fallbackState = deriveFallbackIslandState(normalized);
    activeSession = sessionById(normalized, fallbackState.active_session_id);
    if (activeSession) {
        return activeSession;
    }

    var sessions = safeArray(normalized && normalized.sessions).filter(function(session) {
        return session && !stringFor(session.ended_at).length;
    });

    if (!sessions.length) {
        return null;
    }

    sessions.sort(compareSessions);
    return sessions[0];
}

function pickCurrentTask(snapshot, session, sessionState) {
    if (!session) {
        return null;
    }

    var tasks = safeArray(snapshot && snapshot.tasks).filter(function(task) {
        return task && task.session_id === session.session_id;
    });

    if (!tasks.length) {
        return null;
    }

    var currentTaskId = stringFor(sessionState && sessionState.current_task_id)
        || stringFor(session.current_task_id);
    for (var index = 0; index < tasks.length; index += 1) {
        if (currentTaskId.length && tasks[index].task_id === currentTaskId) {
            return tasks[index];
        }
    }

    tasks.sort(compareItemsByStamp);
    return tasks[0];
}

function pickOpenInteraction(snapshot, session, sessionState) {
    if (!session) {
        return null;
    }

    var interactions = safeArray(snapshot && snapshot.interactions).filter(function(interaction) {
        return interaction
            && interaction.session_id === session.session_id
            && interaction.state === "open";
    });

    if (!interactions.length) {
        return null;
    }

    var pendingInteractionId = stringFor(sessionState && sessionState.pending_interaction_id);
    for (var index = 0; index < interactions.length; index += 1) {
        if (pendingInteractionId.length && interactions[index].interaction_id === pendingInteractionId) {
            return interactions[index];
        }
    }

    interactions.sort(compareItemsByStamp);
    return interactions[0];
}

function sortedOpenInteractions(snapshot, typeName) {
    var typeFilter = stringFor(typeName);
    var interactions = safeArray(snapshot && snapshot.interactions).filter(function(interaction) {
        return interaction
            && interaction.state === "open"
            && (!typeFilter.length || interaction.type === typeFilter)
            && sessionById(snapshot, interaction.session_id);
    });

    interactions.sort(function(left, right) {
        var leftStamp = stringFor(left && left.asked_at);
        var rightStamp = stringFor(right && right.asked_at);
        if (leftStamp !== rightStamp) {
            return leftStamp < rightStamp ? -1 : 1;
        }
        return stringFor(left && left.interaction_id).localeCompare(stringFor(right && right.interaction_id));
    });
    return interactions;
}

function deriveFallbackIslandState(snapshot) {
    var approvals = sortedOpenInteractions(snapshot, "approval");
    var questions = sortedOpenInteractions(snapshot, "question");
    var sessions = safeArray(snapshot && snapshot.sessions).filter(function(session) {
        return session && !stringFor(session.ended_at).length;
    });
    var completionSessions = [];

    for (var index = 0; index < sessions.length; index += 1) {
        var state = sessionStateFor(snapshot, sessions[index]);
        if (state && state.completion_pending === true && isRecentIso(state.completion_enqueued_at, 180)) {
            completionSessions.push(sessions[index]);
        }
    }

    completionSessions.sort(function(left, right) {
        var leftState = sessionStateFor(snapshot, left);
        var rightState = sessionStateFor(snapshot, right);
        var leftStamp = stringFor(leftState && leftState.completion_enqueued_at) || stampFor(left);
        var rightStamp = stringFor(rightState && rightState.completion_enqueued_at) || stampFor(right);
        if (leftStamp !== rightStamp) {
            return leftStamp < rightStamp ? 1 : -1;
        }
        return stringFor(left && left.session_id).localeCompare(stringFor(right && right.session_id));
    });

    var activeInteraction = null;
    var activeSession = null;
    var surface = "collapsed";
    var status = "idle";
    var updatedAt = "";
    var autoReveal = false;

    if (approvals.length) {
        activeInteraction = approvals[0];
        activeSession = sessionById(snapshot, activeInteraction.session_id);
        surface = "approvalCard";
        status = "waiting_approval";
        updatedAt = stringFor(activeInteraction.asked_at);
        autoReveal = true;
    } else if (questions.length) {
        activeInteraction = questions[0];
        activeSession = sessionById(snapshot, activeInteraction.session_id);
        surface = "questionCard";
        status = "waiting_answer";
        updatedAt = stringFor(activeInteraction.asked_at);
        autoReveal = true;
    } else if (completionSessions.length) {
        activeSession = completionSessions[0];
        var completionState = sessionStateFor(snapshot, activeSession);
        surface = "completionCard";
        status = resolveStatus(activeSession, completionState, null);
        updatedAt = stringFor(completionState && completionState.completion_enqueued_at) || stampFor(activeSession);
        autoReveal = true;
    } else {
        sessions.sort(compareSessions);
        activeSession = sessions.length ? sessions[0] : null;
        var activeState = sessionStateFor(snapshot, activeSession);
        status = resolveStatus(activeSession, activeState, null);
        updatedAt = stampFor(activeSession);
    }

    return {
        surface: surface,
        active_session_id: stringFor(activeSession && activeSession.session_id),
        active_task_id: stringFor(activeInteraction && activeInteraction.task_id) || stringFor(activeSession && activeSession.current_task_id),
        active_interaction_id: stringFor(activeInteraction && activeInteraction.interaction_id),
        status: status,
        auto_reveal: autoReveal,
        approval_queue: approvals.map(function(item) { return item.interaction_id; }),
        question_queue: questions.map(function(item) { return item.interaction_id; }),
        completion_queue: completionSessions.map(function(item) { return item.session_id; }),
        rotation_queue: sessions.map(function(item) { return item.session_id; }),
        updated_at: updatedAt,
    };
}

function resolveStatus(session, sessionState, interaction) {
    if (interaction && interaction.state === "open") {
        if (interaction.type === "approval") {
            return "waiting_approval";
        }
        if (interaction.type === "question") {
            return "waiting_answer";
        }
    }

    if (!session) {
        return "idle";
    }

    var effectiveStatus = stringFor(sessionState && sessionState.effective_status);
    if (effectiveStatus.length) {
        return effectiveStatus;
    }

    return stringFor(session.status) || "idle";
}

function basename(pathValue) {
    var path = stringFor(pathValue);

    if (!path.length) {
        return "";
    }

    var pieces = path.split("/");
    return pieces[pieces.length - 1] || path;
}

function summaryTitle(session) {
    if (!session) {
        return "CodeIsland";
    }

    return stringFor(session.title) || stringFor(session.session_id) || "CodeIsland";
}

function projectLabel(session) {
    if (!session) {
        return "";
    }

    return basename(session.project_root);
}

function nonEmptyString(value) {
    var text = stringFor(value).trim();
    return text.length ? text : "";
}

function normalizedText(value) {
    return stringFor(value).replace(/\s+/g, " ").trim();
}

function friendlyBasename(value) {
    var token = stringFor(value).trim();

    if (!token.length) {
        return "";
    }

    var pieces = token.split(/[\\/]/);
    token = pieces[pieces.length - 1] || token;
    token = token.replace(/^[._\-\s]+|[._\-\s]+$/g, "");

    if (token.length < 2 || token.toLowerCase() === "codeisland") {
        return "";
    }

    return token;
}

function friendlySessionLabel(value, projectLabel) {
    var label = stringFor(value).trim();

    if (!label.length || label === "CodeIsland") {
        return "";
    }

    label = label.replace(/\s+session$/i, "").trim();

    if (!label.length || label.toLowerCase() === stringFor(projectLabel).toLowerCase()) {
        return "";
    }

    return label;
}

function genericContextLabel(value) {
    var label = stringFor(value).trim().toLowerCase();
    return label === "approval"
        || label === "question"
        || label === "running"
        || label === "ready"
        || label === "done"
        || label === "idle";
}

function contextTextFor(projectLabel, sessionLabel) {
    var parts = [];

    if (projectLabel.length) {
        parts.push(projectLabel);
    }

    if (sessionLabel.length && (!parts.length || !genericContextLabel(sessionLabel))) {
        parts.push(sessionLabel);
    }

    return parts.join(" · ");
}

function headerMetaFor(projectLabel, sessionLabel, headerIdentity, connected) {
    var parts = [];

    if (projectLabel.length && projectLabel !== headerIdentity) {
        parts.push(projectLabel);
    }

    if (sessionLabel.length && sessionLabel !== headerIdentity && !genericContextLabel(sessionLabel)) {
        parts.push(sessionLabel);
    }

    if (!connected) {
        parts.push("Offline");
    }

    return parts.join(" · ");
}

function surfaceModeFor(status, interaction, connected) {
    if (!connected) {
        return "offline";
    }

    if (interaction && interaction.state === "open") {
        if (interaction.type === "approval") {
            return "approval";
        }

        if (interaction.type === "question") {
            return "question";
        }
    }

    if (status === "running") {
        return "running";
    }

    if (status === "completed") {
        return "completed";
    }

    if (status === "failed" || status === "cancelled") {
        return "paused";
    }

    return "quiet";
}

function surfaceChipTextFor(surfaceMode) {
    switch (surfaceMode) {
    case "approval":
        return "Review";
    case "question":
        return "Reply";
    case "running":
        return "Live";
    case "completed":
        return "Recent";
    case "paused":
        return "Paused";
    case "offline":
        return "Offline";
    default:
        return "Quiet";
    }
}

function projectPopoutSurface(session, sessionState, task, interaction, activity, connected, warning) {
    var status = resolveStatus(session, sessionState, interaction);
    var projectLabel = friendlyBasename(session && session.project_root);
    var sessionLabel = friendlySessionLabel(summaryTitle(session), projectLabel);
    var interactionHeadline = normalizedText(interaction && interaction.state === "open" ? interaction.prompt_text : "");
    var taskHeadline = normalizedText(activityHeadline(session, sessionState, task, interaction, activity, connected, warning));
    var detailLine = normalizedText(activityDetail(session, sessionState, task, interaction, activity, connected, warning));
    var headerIdentity = sessionLabel.length ? sessionLabel : (projectLabel.length ? projectLabel : "CodeIsland");
    var contextText = contextTextFor(projectLabel, sessionLabel);
    var headerMeta = headerMetaFor(projectLabel, sessionLabel, headerIdentity, connected);
    var surfaceMode = surfaceModeFor(status, interaction, connected);
    var primaryTitle = "";
    var primaryBody = "";
    var metaLine = "";

    if (taskHeadline.length && taskHeadline === interactionHeadline) {
        taskHeadline = "";
    }

    switch (surfaceMode) {
    case "approval":
    case "question":
        primaryTitle = interactionHeadline.length
            ? interactionHeadline
            : (surfaceMode === "approval"
                ? "A decision is waiting in this thread."
                : "A reply is waiting in this thread.");
        primaryBody = surfaceMode === "approval"
            ? "Review the request here and choose whether to approve it or deny it."
            : "Reply below to keep the active thread moving without leaving your current app.";
        break;
    case "running":
        primaryTitle = taskHeadline.length ? taskHeadline : "The current task is in motion.";
        if (detailLine.length) {
            primaryBody = detailLine;
        } else if (contextText.length) {
            primaryBody = contextText;
        } else {
            primaryBody = "The active thread is working through the current task.";
        }
        break;
    case "completed":
        primaryTitle = taskHeadline.length ? taskHeadline : "The latest task wrapped cleanly.";
        primaryBody = detailLine.length
            ? detailLine
            : "The latest task finished and the shell is ready for whatever comes next.";
        break;
    case "paused":
        primaryTitle = taskHeadline.length ? taskHeadline : "The last task needs another pass.";
        primaryBody = detailLine.length
            ? detailLine
            : "The thread is paused until the next task, answer, or approval arrives.";
        break;
    case "offline":
        primaryTitle = "The live session is temporarily offline.";
        primaryBody = "Reconnect to restore live approvals, questions, and task updates.";
        break;
    default:
        primaryTitle = "Nothing needs your input right now.";
        primaryBody = contextText.length
            ? contextText
            : "The shell is standing by for the next task, question, or approval.";
        break;
    }

    if (contextText.length && contextText !== primaryBody && contextText !== primaryTitle) {
        metaLine = contextText;
    } else if (headerMeta.length && headerMeta !== primaryBody && headerMeta !== primaryTitle) {
        metaLine = headerMeta;
    } else if (!connected) {
        metaLine = "Waiting for the daemon to reconnect.";
    }

    return {
        connected: !!connected,
        status: status,
        surfaceMode: surfaceMode,
        headerIdentity: headerIdentity,
        headerMeta: headerMeta,
        surfaceChipText: surfaceChipTextFor(surfaceMode),
        primaryTitle: primaryTitle,
        primaryBody: primaryBody,
        metaLine: metaLine,
    };
}

function surfaceModeFromIslandSurface(surface, status, interaction, connected) {
    if (!connected) {
        return "offline";
    }

    if (surface === "approvalCard") {
        return "approval";
    }
    if (surface === "questionCard") {
        return "question";
    }
    if (surface === "completionCard") {
        return status === "failed" || status === "cancelled" ? "paused" : "completed";
    }

    return surfaceModeFor(status, interaction, connected);
}

function islandSurfaceToken(wireState, surface, session, interaction) {
    var parts = [
        surface,
        stringFor(session && session.session_id),
        stringFor(interaction && interaction.interaction_id),
        stringFor(wireState && wireState.updated_at),
        stringFor(wireState && wireState.status),
    ];

    return parts.join(":");
}

function projectIslandState(snapshot, connected, warning, freshnessTick) {
    void freshnessTick;

    var normalized = normalizeSnapshot(snapshot);
    var wireState = normalized.island_state;
    var staleCompletion = wireState.surface === "completionCard" && !isRecentIso(wireState.updated_at, 180);
    if ((!wireState.active_session_id.length && wireState.surface === "collapsed") || staleCompletion) {
        wireState = deriveFallbackIslandState(normalized);
    }

    var session = sessionById(normalized, wireState.active_session_id) || pickPrimarySession(normalized);
    var sessionState = sessionStateFor(normalized, session);
    var interaction = interactionById(normalized, wireState.active_interaction_id)
        || pickOpenInteraction(normalized, session, sessionState);
    var task = taskById(normalized, wireState.active_task_id)
        || pickCurrentTask(normalized, session, sessionState);
    var activity = latestActivityFor(normalized, session);
    var status = stringFor(wireState.status) || resolveStatus(session, sessionState, interaction);
    var surface = stringFor(wireState.surface) || "collapsed";

    if (!connected) {
        surface = "sessionList";
        status = "idle";
    } else if ((surface === "approvalCard" || surface === "questionCard") && !(interaction && interaction.state === "open")) {
        surface = "collapsed";
    }

    var projectedSurface = projectPopoutSurface(session, sessionState, task, interaction, activity, connected, warning);
    projectedSurface.surfaceMode = surfaceModeFromIslandSurface(surface, status, interaction, connected);
    projectedSurface.status = status;
    projectedSurface.surfaceChipText = surfaceChipTextFor(projectedSurface.surfaceMode);

    var card = session ? projectSessionCard(normalized, session, connected, warning) : null;
    var providerKey = card ? card.providerKey : providerKeyFor(session);
    var project = card ? card.project : friendlyBasename(session && session.project_root);
    var title = card ? card.title : summaryTitle(session);
    var token = islandSurfaceToken(wireState, surface, session, interaction);
    var focusSurface = surface === "approvalCard" || surface === "questionCard" || surface === "completionCard";

    return {
        connected: !!connected,
        surface: surface,
        surfaceMode: projectedSurface.surfaceMode,
        status: status,
        autoReveal: !!(connected && wireState.auto_reveal && focusSurface),
        token: token,
        session: session,
        sessionState: sessionState,
        task: task,
        interaction: interaction,
        activity: activity,
        card: card,
        sessionId: stringFor(session && session.session_id),
        taskId: stringFor(task && task.task_id),
        interactionId: stringFor(interaction && interaction.interaction_id),
        providerKey: providerKey,
        providerLabel: providerLabelFor(providerKey),
        project: project,
        title: title,
        headerIdentity: projectedSurface.headerIdentity,
        headerMeta: projectedSurface.headerMeta,
        surfaceChipText: projectedSurface.surfaceChipText,
        primaryTitle: projectedSurface.primaryTitle,
        primaryBody: projectedSurface.primaryBody,
        metaLine: projectedSurface.metaLine,
        approvalQueue: safeArray(wireState.approval_queue),
        questionQueue: safeArray(wireState.question_queue),
        completionQueue: safeArray(wireState.completion_queue),
        rotationQueue: safeArray(wireState.rotation_queue),
    };
}

function activityPayload(event, key) {
    if (!event || !event.payload || typeof event.payload !== "object") {
        return "";
    }

    return nonEmptyString(event.payload[key]);
}

function toolLabel(sessionState, activity) {
    return nonEmptyString(sessionState && sessionState.active_tool_name)
        || activityPayload(activity, "tool_name");
}

function responseLabel(sessionState, activity) {
    return nonEmptyString(sessionState && sessionState.last_assistant_message)
        || activityPayload(activity, "message")
        || activityPayload(activity, "summary");
}

function failureLabel(activity) {
    return activityPayload(activity, "error_message")
        || activityPayload(activity, "error")
        || activityPayload(activity, "stderr");
}

function activityHeadline(session, sessionState, task, interaction, activity, connected, warning) {
    if (!connected) {
        return warning && warning.length ? warning : "Waiting to reconnect to the primary session.";
    }

    if (interaction && interaction.state === "open") {
        if (interaction.type === "approval") {
            return "Approval is blocking the current thread.";
        }
        if (interaction.type === "question") {
            return "A reply is needed to keep the thread moving.";
        }
    }

    var toolName = toolLabel(sessionState, activity);
    if (toolName.length) {
        if (activity && activity.kind === "tool.use.failed") {
            return "The last tool run failed.";
        }
        if ((sessionState && sessionState.active_tool_status === "running") || (activity && activity.kind === "tool.use.started")) {
            return "Using " + toolName + ".";
        }
    }

    if (sessionState && sessionState.completion_pending) {
        return "The latest task is ready to wrap up.";
    }

    var response = responseLabel(sessionState, activity);
    if (response.length && activity && (activity.kind === "assistant.response.completed" || activity.kind === "completion.enqueued")) {
        return response;
    }

    var prompt = nonEmptyString(task && task.prompt);
    if (prompt.length) {
        return prompt;
    }

    if (session && stringFor(session.status) === "completed") {
        return "The latest task finished cleanly.";
    }

    if (session && (stringFor(session.status) === "failed" || stringFor(session.status) === "cancelled")) {
        return "The thread is paused until the next task begins.";
    }

    if (session && stringFor(session.status) === "running") {
        return "The primary thread is actively working.";
    }

    return "The primary thread is ready for the next step.";
}

function activityDetail(session, sessionState, task, interaction, activity, connected, warning) {
    if (!connected) {
        return warning && warning.length ? "Daemon offline • " + warning : "Waiting for daemon socket";
    }

    if (interaction && nonEmptyString(interaction.prompt_text).length) {
        return nonEmptyString(interaction.prompt_text);
    }

    var toolName = toolLabel(sessionState, activity);
    var failure = failureLabel(activity);
    if (toolName.length && failure.length && activity && activity.kind === "tool.use.failed") {
        return toolName + " failed • " + failure;
    }
    if (toolName.length && ((sessionState && sessionState.active_tool_status === "running") || (activity && activity.kind === "tool.use.started"))) {
        return "Running tool • " + toolName;
    }

    var response = responseLabel(sessionState, activity);
    if (response.length) {
        return response;
    }

    var prompt = nonEmptyString(task && task.prompt);
    if (prompt.length) {
        return prompt;
    }

    if (session && stringFor(session.project_root).length) {
        return stringFor(session.project_root);
    }

    if (warning && warning.length) {
        return warning;
    }

    return "No active CodeIsland session";
}

function detailText(session, sessionState, task, interaction, activity, connected, warning) {
    return activityDetail(session, sessionState, task, interaction, activity, connected, warning);
}

function canonicalProvider(value) {
    var raw = stringFor(value).trim();

    if (!raw.length) {
        return "";
    }

    var token = raw.toLowerCase().replace(/[\s_\-]+/g, "");

    if (token.indexOf("claude") !== -1) {
        return "claude";
    }
    if (token.indexOf("codex") !== -1) {
        return "codex";
    }
    if (token.indexOf("gemini") !== -1) {
        return "gemini";
    }
    if (token.indexOf("opencode") !== -1 || token.indexOf("openaiagent") !== -1) {
        return "opencode";
    }
    if (token.indexOf("cursor") !== -1) {
        return "cursor";
    }
    if (token.indexOf("trae") !== -1) {
        return "trae";
    }
    if (token.indexOf("qoder") !== -1) {
        return "qoder";
    }
    if (token.indexOf("copilot") !== -1) {
        return "copilot";
    }
    if (token.indexOf("kimi") !== -1) {
        return "kimi";
    }

    return raw.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "other";
}

function providerKeyFor(session) {
    var fields = [
        session && session.provider,
        session && session.source,
        session && session.source_kind,
        session && session.cli,
        session && session.client,
        session && session.app_name,
    ];

    for (var index = 0; index < fields.length; index += 1) {
        var key = canonicalProvider(fields[index]);
        if (key.length) {
            return key;
        }
    }

    var sessionId = stringFor(session && session.session_id).toLowerCase();
    if (sessionId.indexOf("claude") === 0) {
        return "claude";
    }
    if (sessionId.indexOf("codex") === 0) {
        return "codex";
    }
    if (sessionId.indexOf("gemini") === 0) {
        return "gemini";
    }
    if (sessionId.indexOf("opencode") === 0) {
        return "opencode";
    }

    return "other";
}

function providerLabelFor(providerKey) {
    switch (providerKey) {
    case "claude":
        return "Claude";
    case "codex":
        return "Codex";
    case "gemini":
        return "Gemini";
    case "opencode":
        return "OpenCode";
    case "cursor":
        return "Cursor";
    case "trae":
        return "Trae";
    case "qoder":
        return "Qoder";
    case "copilot":
        return "Copilot";
    case "kimi":
        return "Kimi";
    default:
        return "Other";
    }
}

function providerRank(providerKey) {
    return Object.prototype.hasOwnProperty.call(PROVIDER_ORDER, providerKey)
        ? PROVIDER_ORDER[providerKey]
        : PROVIDER_ORDER.other;
}

function compareProviderGroups(left, right) {
    var leftPriority = typeof (left && left.sortPriority) === "number" ? left.sortPriority : 99;
    var rightPriority = typeof (right && right.sortPriority) === "number" ? right.sortPriority : 99;

    if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
    }

    var leftStamp = stringFor(left && left.sortStamp);
    var rightStamp = stringFor(right && right.sortStamp);

    if (leftStamp !== rightStamp) {
        return leftStamp < rightStamp ? 1 : -1;
    }

    var leftRank = providerRank(left && left.providerKey);
    var rightRank = providerRank(right && right.providerKey);

    if (leftRank !== rightRank) {
        return leftRank - rightRank;
    }

    return stringFor(left && left.label).localeCompare(stringFor(right && right.label));
}

function statusTokenFor(status) {
    switch (status) {
    case "waiting_approval":
        return "approval_";
    case "waiting_answer":
        return "reply_";
    case "running":
        return "thinking_";
    case "completed":
        return "done";
    case "failed":
        return "failed";
    case "cancelled":
        return "cancelled";
    default:
        return "idle";
    }
}

function compareSessionCards(left, right) {
    var leftPriority = typeof (left && left.sortPriority) === "number" ? left.sortPriority : boardPriorityFor(left && left.status);
    var rightPriority = typeof (right && right.sortPriority) === "number" ? right.sortPriority : boardPriorityFor(right && right.status);

    if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
    }

    var leftStamp = stringFor(left && left.sortStamp);
    var rightStamp = stringFor(right && right.sortStamp);

    if (leftStamp !== rightStamp) {
        return leftStamp < rightStamp ? 1 : -1;
    }

    var titleCompare = stringFor(left && left.title).localeCompare(stringFor(right && right.title));
    if (titleCompare !== 0) {
        return titleCompare;
    }

    return stringFor(left && left.sessionId).localeCompare(stringFor(right && right.sessionId));
}

function sessionPromptText(sessionState, task, activity) {
    return nonEmptyString(sessionState && sessionState.last_user_prompt)
        || nonEmptyString(task && task.prompt)
        || activityPayload(activity, "prompt");
}

function sessionAppLabel(session, providerKey) {
    var direct = nonEmptyString(session && session.terminal_app)
        || nonEmptyString(session && session.terminal)
        || nonEmptyString(session && session.app_name)
        || nonEmptyString(session && session.app)
        || nonEmptyString(session && session.window_app);

    if (direct.length) {
        return direct;
    }

    var workspaceHint = nonEmptyString(session && session.workspace_hint);
    if (workspaceHint.length && workspaceHint.indexOf("/") === -1) {
        return workspaceHint;
    }

    return providerLabelFor(providerKey);
}

function shortSessionSuffix(session) {
    var sessionId = stringFor(session && session.session_id);
    if (!sessionId.length) {
        return "";
    }

    var pieces = sessionId.split(/[-_]/);
    var suffix = pieces.length ? pieces[pieces.length - 1] : sessionId;
    if (!suffix.length || suffix === sessionId) {
        return "";
    }

    return "#" + suffix;
}

function relativeTimeText(value) {
    var stamp = stampFor(value);
    if (!stamp.length) {
        return "";
    }

    var parsed = Date.parse(stamp);
    if (isNaN(parsed)) {
        return "";
    }

    var seconds = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
    if (seconds < 60) {
        return "<1m";
    }

    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
        return minutes + "m";
    }

    var hours = Math.floor(minutes / 60);
    if (hours < 24) {
        return hours + "h";
    }

    return Math.floor(hours / 24) + "d";
}

function isRecentIso(value, secondsLimit) {
    var stamp = stringFor(value);
    if (!stamp.length) {
        return false;
    }

    var parsed = Date.parse(stamp);
    if (isNaN(parsed)) {
        return false;
    }

    return Math.max(0, (Date.now() - parsed) / 1000) <= secondsLimit;
}

function projectSessionCard(snapshot, session, connected, warning) {
    var sessionState = sessionStateFor(snapshot, session);
    var task = pickCurrentTask(snapshot, session, sessionState);
    var interaction = pickOpenInteraction(snapshot, session, sessionState);
    var activity = latestActivityFor(snapshot, session);
    var status = resolveStatus(session, sessionState, interaction);
    var providerKey = providerKeyFor(session);
    var project = friendlyBasename(session && session.project_root);
    var sessionLabel = friendlySessionLabel(summaryTitle(session), project);
    var title = sessionLabel.length ? sessionLabel : (project.length ? project : summaryTitle(session));
    var suffix = shortSessionSuffix(session);
    var promptLine = sessionPromptText(sessionState, task, activity);
    var headline = normalizedText(activityHeadline(session, sessionState, task, interaction, activity, connected, warning));
    var detail = normalizedText(activityDetail(session, sessionState, task, interaction, activity, connected, warning));
    var sortStamp = stringFor(sessionState && sessionState.completion_enqueued_at)
        || stampFor(activity)
        || stampFor(task)
        || stampFor(sessionState)
        || stampFor(session);
    var primaryLine = "";
    var secondaryLine = "";

    if (interaction && interaction.state === "open") {
        primaryLine = normalizedText(interaction.prompt_text) || headline;
        secondaryLine = promptLine.length ? promptLine : statusTokenFor(status);
    } else {
        primaryLine = promptLine.length ? promptLine : headline;
        secondaryLine = detail.length && detail !== primaryLine ? detail : statusTokenFor(status);
    }

    if (!secondaryLine.length || secondaryLine === primaryLine) {
        secondaryLine = statusTokenFor(status);
    }

    return {
        sessionId: stringFor(session && session.session_id),
        providerKey: providerKey,
        providerLabel: providerLabelFor(providerKey),
        status: status,
        title: title,
        suffix: suffix,
        project: project,
        primaryLine: primaryLine,
        secondaryLine: secondaryLine,
        timeText: relativeTimeText({ updated_at: sortStamp }) || relativeTimeText(session || activity || task),
        sortPriority: boardPriorityFor(status),
        sortStamp: sortStamp,
        appLabel: sessionAppLabel(session, providerKey),
        hasInteraction: !!(interaction && interaction.state === "open"),
        interactionType: stringFor(interaction && interaction.type),
    };
}

function projectSessionGroups(snapshot, connected, warning) {
    var sessions = safeArray(snapshot && snapshot.sessions);
    var cards = [];
    var grouped = {};
    var groups = [];

    sessions.sort(compareSessions);
    for (var index = 0; index < sessions.length; index += 1) {
        var session = sessions[index];
        if (!session) {
            continue;
        }
        if (stringFor(session.ended_at).length) {
            continue;
        }

        var card = projectSessionCard(snapshot, session, connected, warning);
        cards.push(card);
    }

    cards.sort(compareSessionCards);
    for (var cardIndex = 0; cardIndex < cards.length; cardIndex += 1) {
        var card = cards[cardIndex];
        var providerKey = card.providerKey;
        if (!grouped[providerKey]) {
            grouped[providerKey] = {
                providerKey: providerKey,
                label: card.providerLabel,
                sortPriority: card.sortPriority,
                sortStamp: card.sortStamp,
                count: 0,
                cards: [],
            };
            groups.push(grouped[providerKey]);
        }

        grouped[providerKey].cards.push(card);
        grouped[providerKey].count += 1;
    }

    groups.sort(compareProviderGroups);
    return groups;
}

function projectSessionDots(groups) {
    var source = safeArray(groups);
    var dots = [];

    for (var groupIndex = 0; groupIndex < source.length; groupIndex += 1) {
        var cards = safeArray(source[groupIndex] && source[groupIndex].cards);
        for (var cardIndex = 0; cardIndex < cards.length; cardIndex += 1) {
            var card = cards[cardIndex];
            if (!card)
                continue;

            dots.push({
                sessionId: stringFor(card.sessionId),
                status: stringFor(card.status) || "idle",
                providerKey: stringFor(card.providerKey) || "other",
            });
        }
    }

    return dots;
}
