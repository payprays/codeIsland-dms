.pragma library

function isoAgo(seconds) {
    return new Date(Date.now() - (seconds * 1000)).toISOString();
}

function boardSnapshot() {
    return {
        sessions: [
            {
                session_id: "claude-vibe-8387",
                provider: "claude",
                project_root: "/home/jk/Projects/life/vibe-notch",
                title: "vibe-notch",
                status: "completed",
                current_task_id: null,
                workspace_hint: null,
                terminal_app: "Ghostty",
                created_at: isoAgo(7200),
                updated_at: isoAgo(3600),
                last_event_at: isoAgo(3600),
            },
            {
                session_id: "claude-wxt",
                provider: "claude",
                project_root: "/home/jk/Projects/life/wxt",
                title: "wxt",
                status: "running",
                current_task_id: "task-claude-wxt",
                workspace_hint: null,
                terminal_app: "iTerm2",
                created_at: isoAgo(600),
                updated_at: isoAgo(45),
                last_event_at: isoAgo(45),
            },
            {
                session_id: "claude-demo",
                provider: "claude",
                project_root: "/home/jk/Projects/life/vibe-notch",
                title: "vibe-notch",
                status: "running",
                current_task_id: "task-claude-demo",
                workspace_hint: null,
                terminal_app: "Ghostty",
                created_at: isoAgo(120),
                updated_at: isoAgo(75),
                last_event_at: isoAgo(75),
            },
            {
                session_id: "codex-api",
                provider: "codex",
                project_root: "/home/jk/Projects/work/api",
                title: "api",
                status: "running",
                current_task_id: "task-codex-api",
                workspace_hint: null,
                terminal_app: "Ghostty",
                created_at: isoAgo(90),
                updated_at: isoAgo(60),
                last_event_at: isoAgo(60),
            },
            {
                session_id: "gemini-web",
                provider: "gemini",
                project_root: "/home/jk/Projects/work/web",
                title: "web",
                status: "idle",
                current_task_id: null,
                workspace_hint: null,
                terminal_app: "iTerm2",
                created_at: isoAgo(300),
                updated_at: isoAgo(60),
                last_event_at: isoAgo(60),
            },
            {
                session_id: "opencode-shell",
                provider: "opencode",
                project_root: "/home/jk/Projects/life/codeIsland",
                title: "codeIsland",
                status: "running",
                current_task_id: "task-opencode-shell",
                workspace_hint: null,
                terminal_app: "Ghostty",
                created_at: isoAgo(150),
                updated_at: isoAgo(55),
                last_event_at: isoAgo(55),
            },
        ],
        tasks: [
            {
                task_id: "task-claude-wxt",
                session_id: "claude-wxt",
                prompt: "有事情随时说，我在这儿呢。",
                status: "running",
                started_at: isoAgo(45),
                ended_at: null,
                error_code: null,
                error_message: null,
            },
            {
                task_id: "task-claude-demo",
                session_id: "claude-demo",
                prompt: "thinking_",
                status: "running",
                started_at: isoAgo(75),
                ended_at: null,
                error_code: null,
                error_message: null,
            },
            {
                task_id: "task-codex-api",
                session_id: "codex-api",
                prompt: "Fix the login bug",
                status: "running",
                started_at: isoAgo(60),
                ended_at: null,
                error_code: null,
                error_message: null,
            },
            {
                task_id: "task-opencode-shell",
                session_id: "opencode-shell",
                prompt: "Port the grouped board to DMS",
                status: "running",
                started_at: isoAgo(55),
                ended_at: null,
                error_code: null,
                error_message: null,
            },
        ],
        interactions: [],
        activities: [
            {
                event_id: "evt-claude-complete",
                session_id: "claude-vibe-8387",
                task_id: null,
                kind: "completion.enqueued",
                payload: {
                    summary: "搞定。两个 README 都加了图..."
                },
                ts: isoAgo(3600),
                seq: 1,
            },
            {
                event_id: "evt-claude-wxt",
                session_id: "claude-wxt",
                task_id: "task-claude-wxt",
                kind: "assistant.response.completed",
                payload: {
                    message: "Bro, 有事随时说，我在这儿呢。"
                },
                ts: isoAgo(45),
                seq: 2,
            },
            {
                event_id: "evt-codex-api",
                session_id: "codex-api",
                task_id: "task-codex-api",
                kind: "tool.use.started",
                payload: {
                    tool_name: "pytest"
                },
                ts: isoAgo(60),
                seq: 3,
            },
            {
                event_id: "evt-opencode-shell",
                session_id: "opencode-shell",
                task_id: "task-opencode-shell",
                kind: "tool.use.started",
                payload: {
                    tool_name: "qmlformat"
                },
                ts: isoAgo(55),
                seq: 4,
            },
        ],
        session_states: [
            {
                session_id: "claude-wxt",
                effective_status: "running",
                current_task_id: "task-claude-wxt",
                pending_interaction_id: null,
                pending_interaction_kind: null,
                active_tool_name: null,
                active_tool_status: null,
                last_user_prompt: "哦",
                last_assistant_message: "Bro, 有事随时说，我在这儿呢。",
                last_activity_kind: "assistant.response.completed",
                last_activity_at: isoAgo(45),
                completion_pending: false,
                completion_enqueued_at: null,
            },
            {
                session_id: "claude-demo",
                effective_status: "running",
                current_task_id: "task-claude-demo",
                pending_interaction_id: null,
                pending_interaction_kind: null,
                active_tool_name: null,
                active_tool_status: null,
                last_user_prompt: "thinking_",
                last_assistant_message: null,
                last_activity_kind: null,
                last_activity_at: null,
                completion_pending: false,
                completion_enqueued_at: null,
            },
            {
                session_id: "codex-api",
                effective_status: "running",
                current_task_id: "task-codex-api",
                pending_interaction_id: null,
                pending_interaction_kind: null,
                active_tool_name: "pytest",
                active_tool_status: "running",
                last_user_prompt: "Fix the login bug",
                last_assistant_message: null,
                last_activity_kind: "tool.use.started",
                last_activity_at: isoAgo(60),
                completion_pending: false,
                completion_enqueued_at: null,
            },
            {
                session_id: "opencode-shell",
                effective_status: "running",
                current_task_id: "task-opencode-shell",
                pending_interaction_id: null,
                pending_interaction_kind: null,
                active_tool_name: "qmlformat",
                active_tool_status: "running",
                last_user_prompt: "Port the grouped board to DMS",
                last_assistant_message: null,
                last_activity_kind: "tool.use.started",
                last_activity_at: isoAgo(55),
                completion_pending: false,
                completion_enqueued_at: null,
            },
        ],
        next_seq: 5,
    };
}

function board() {
    return {
        snapshot: boardSnapshot(),
        connected: true,
        warning: "",
        interaction: null,
        answerText: "",
    };
}

function approval() {
    return {
        surface: {
            connected: true,
            status: "waiting_approval",
            surfaceMode: "approval",
            headerIdentity: "demo",
            headerMeta: "OpenCode · Approval needed",
            surfaceChipText: "Approval",
            primaryTitle: "Allow tool use?",
            primaryBody: "claude wants to run bash in the project workspace.",
            metaLine: "bash · workspace write access"
        },
        interaction: {
            interaction_id: "int-approval-1",
            state: "open",
            type: "approval",
            prompt_text: "Allow tool use?"
        },
        answerText: ""
    };
}

function question() {
    return {
        surface: {
            connected: true,
            status: "waiting_answer",
            surfaceMode: "question",
            headerIdentity: "demo",
            headerMeta: "OpenCode · Question waiting",
            surfaceChipText: "Question",
            primaryTitle: "Which branch should I use?",
            primaryBody: "Please choose the target branch for this change.",
            metaLine: "current repo · feature preview"
        },
        interaction: {
            interaction_id: "int-question-1",
            state: "open",
            type: "question",
            prompt_text: "Which branch should I use?"
        },
        answerText: "main"
    };
}

function completion() {
    return {
        surface: {
            connected: true,
            status: "completed",
            surfaceMode: "completed",
            headerIdentity: "demo",
            headerMeta: "OpenCode · Finished",
            surfaceChipText: "Finished",
            primaryTitle: "Backend phase 1 is done",
            primaryBody: "All tests passed and the daemon snapshot now includes richer activity fields.",
            metaLine: "45 tests passed"
        },
        interaction: null,
        answerText: ""
    };
}

function running() {
    return {
        surface: {
            connected: true,
            status: "running",
            surfaceMode: "running",
            headerIdentity: "demo",
            headerMeta: "OpenCode · In progress",
            surfaceChipText: "Running",
            primaryTitle: "Inspecting IslandSurface.qml",
            primaryBody: "Reading the current popout structure before the next UI pass.",
            metaLine: "Read tool · active session"
        },
        interaction: null,
        answerText: ""
    };
}

function offline() {
    return {
        surface: {
            connected: false,
            status: "idle",
            surfaceMode: "quiet",
            headerIdentity: "CodeIsland",
            headerMeta: "Disconnected",
            surfaceChipText: "Offline",
            primaryTitle: "Waiting for daemon",
            primaryBody: "CodeIsland is not connected right now.",
            metaLine: "check socket · restart daemon"
        },
        interaction: null,
        answerText: ""
    };
}
