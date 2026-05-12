// codeisland-opencode-linux - OpenCode plugin for the CodeIsland Linux daemon
// version: 1
import { connect } from "net";
import { createHash } from "crypto";
import { env, getuid, pid } from "process";

const SOCKET = env.CODEISLAND_SOCKET_PATH
  || (env.XDG_RUNTIME_DIR ? `${env.XDG_RUNTIME_DIR}/codeislandd.sock` : `/tmp/codeisland-${getuid()}/codeislandd.sock`);
const REQUEST_TIMEOUT_MS = 300000;

let requestSeq = 0;

function daemonSessionId(sessionId) {
  const value = String(sessionId || "");
  return value.startsWith("opencode-") ? value : `opencode-${value}`;
}

function terminalApp() {
  if (env.GHOSTTY_RESOURCES_DIR || env.GHOSTTY_BIN_DIR || env.TERM_PROGRAM === "ghostty") return "Ghostty";
  if (env.WEZTERM_PANE || env.TERM_PROGRAM === "WezTerm") return "WezTerm";
  if (env.KITTY_WINDOW_ID) return "kitty";
  if (env.ALACRITTY_WINDOW_ID) return "Alacritty";
  return env.TERM_PROGRAM || null;
}

function stableEventId(sessionId, kind, ...parts) {
  const payload = JSON.stringify({ kind, parts, session_id: sessionId });
  return `evt_opencode_${createHash("sha256").update(payload).digest("hex").slice(0, 16)}`;
}

function rpc(method, params, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const id = `opencode-plugin-${Date.now()}-${++requestSeq}`;
    let settled = false;
    let buffer = "";
    let sock;

    function done(value) {
      if (settled) return;
      settled = true;
      try { if (sock) sock.destroy(); } catch {}
      resolve(value);
    }

    try {
      sock = connect({ path: SOCKET }, () => {
        sock.write(JSON.stringify({ id, method, params }) + "\n");
      });
      sock.on("data", (chunk) => {
        buffer += chunk.toString("utf8");
        const newline = buffer.indexOf("\n");
        if (newline < 0) return;
        const line = buffer.slice(0, newline);
        try { done(JSON.parse(line)); } catch { done(null); }
      });
      sock.on("error", () => done(null));
      sock.on("close", () => done(null));
      sock.setTimeout(timeoutMs, () => done(null));
    } catch {
      done(null);
    }
  });
}

async function ingest(kind, sessionId, taskId, payload, ...eventKeyParts) {
  const event = {
    event_id: stableEventId(sessionId, kind, ...eventKeyParts),
    session_id: sessionId,
    task_id: taskId || null,
    kind,
    payload: payload || {},
    ts: new Date().toISOString(),
  };
  return rpc("ingest_event", event);
}

function waitForInteraction(interactionId, timeoutMs = REQUEST_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let settled = false;
    let buffer = "";
    let sock;
    let timer;

    function done(value) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { if (sock) sock.destroy(); } catch {}
      resolve(value);
    }

    try {
      sock = connect({ path: SOCKET }, () => {
        sock.write(JSON.stringify({
          id: `opencode-plugin-subscribe-${Date.now()}`,
          method: "subscribe",
          params: { topics: ["interactions"] },
        }) + "\n");
      });
      timer = setTimeout(() => done(null), timeoutMs);
      sock.on("data", (chunk) => {
        buffer += chunk.toString("utf8");
        let newline = buffer.indexOf("\n");
        while (newline >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          newline = buffer.indexOf("\n");
          let message = null;
          try { message = JSON.parse(line); } catch { continue; }
          if (message?.kind !== "interaction.resolved") continue;
          const payload = message.payload || {};
          if (payload.interaction_id !== interactionId) continue;
          done(payload.answer_payload || null);
        }
      });
      sock.on("error", () => done(null));
      sock.on("close", () => done(null));
    } catch {
      done(null);
    }
  });
}

function trim(value) {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text.length > 360 ? `${text.slice(0, 357)}...` : text;
}

export default {
  id: "codeisland-linux",
  server: async ({ client, serverUrl }) => {
    const heyApi = client?._client;
    const serverPort = serverUrl ? parseInt(serverUrl.port) || 4096 : 4096;
    const msgRoles = new Map();
    const sessions = new Map();

    function stateFor(providerSessionId) {
      if (!sessions.has(providerSessionId)) {
        sessions.set(providerSessionId, {
          cwd: "",
          currentTaskId: null,
          lastAssistantText: "",
          lastAssistantMessageId: "",
          nextTaskIndex: 1,
        });
      }
      return sessions.get(providerSessionId);
    }

    function taskIdFor(sessionId, messageId) {
      return `${sessionId}-task-${messageId}`;
    }

    function fallbackTaskId(sessionId, state) {
      const value = `${sessionId}-task-live-${state.nextTaskIndex}`;
      state.nextTaskIndex += 1;
      return value;
    }

    async function ensureTask(sessionId, providerSessionId, messageId, prompt) {
      const state = stateFor(providerSessionId);
      if (state.currentTaskId) return state.currentTaskId;
      const taskId = messageId ? taskIdFor(sessionId, messageId) : fallbackTaskId(sessionId, state);
      state.currentTaskId = taskId;
      await ingest("task.started", sessionId, taskId, {
        task_id: taskId,
        prompt: trim(prompt),
        provider_message_id: messageId || null,
        synthetic: !messageId,
      }, messageId || taskId, !messageId ? "synthetic" : "");
      return taskId;
    }

    async function replyQuestion(requestId, answers) {
      try {
        if (typeof heyApi?.request === "function") {
          await heyApi.request({ method: "POST", url: "/question/{requestID}/reply", path: { requestID: requestId }, body: { answers } });
          return;
        }
      } catch {}
      try {
        await fetch(`http://localhost:${serverPort}/question/${requestId}/reply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers }),
        });
      } catch {}
    }

    async function rejectQuestion(requestId) {
      try {
        if (typeof heyApi?.request === "function") {
          await heyApi.request({ method: "POST", url: "/question/{requestID}/reject", path: { requestID: requestId } });
          return;
        }
      } catch {}
      try {
        await fetch(`http://localhost:${serverPort}/question/${requestId}/reject`, { method: "POST", headers: { "Content-Type": "application/json" } });
      } catch {}
    }

    async function replyPermission(requestId, reply, reason) {
      try {
        if (typeof heyApi?.request === "function") {
          await heyApi.request({ method: "POST", url: "/permission/{requestID}/reply", path: { requestID: requestId }, body: { reply, message: reason } });
          return;
        }
      } catch {}
      try {
        await fetch(`http://localhost:${serverPort}/permission/${requestId}/reply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reply, message: reason }),
        });
      } catch {}
    }

    async function handleApproval(sessionId, providerSessionId, requestId, toolName, toolInput) {
      const taskId = await ensureTask(sessionId, providerSessionId, null, null);
      const promptText = `Allow ${toolName || "Tool"}: ${JSON.stringify(toolInput || {})}`;
      await ingest("interaction.approval.requested", sessionId, taskId, {
        interaction_id: requestId,
        prompt_text: promptText,
        options: ["approve", "deny"],
      }, requestId, "interaction");
      await ingest("permission.requested", sessionId, taskId, {
        interaction_id: requestId,
        tool_name: toolName,
        prompt_text: promptText,
        options: ["approve", "deny"],
        tool_input: toolInput || {},
      }, requestId, "activity");
      const answer = await waitForInteraction(requestId);
      if (!answer) return;
      await replyPermission(requestId, answer.action === "approve" ? "once" : "reject", answer.answer || undefined);
    }

    async function handleQuestion(sessionId, providerSessionId, requestId, questions) {
      const taskId = await ensureTask(sessionId, providerSessionId, null, null);
      const first = questions?.[0] || {};
      await ingest("interaction.question.requested", sessionId, taskId, {
        interaction_id: requestId,
        prompt_text: first.question || "",
        options: (first.options || []).map((item) => item.label).filter(Boolean),
      }, requestId, "interaction");
      await ingest("question.requested", sessionId, taskId, {
        interaction_id: requestId,
        prompt_text: first.question || "",
        options: (first.options || []).map((item) => item.label).filter(Boolean),
      }, requestId, "activity");
      const answer = await waitForInteraction(requestId);
      if (!answer || answer.action === "deny") {
        await rejectQuestion(requestId);
        return;
      }
      if (typeof answer.answer === "string" && answer.answer.length) {
        await replyQuestion(requestId, [[answer.answer]]);
      }
    }

    return {
      event: async ({ event }) => {
        const type = event?.type;
        const props = event?.properties || {};

        if (type === "session.created" && props.info?.id) {
          const providerSessionId = props.info.id;
          const sessionId = daemonSessionId(providerSessionId);
          const state = stateFor(providerSessionId);
          state.cwd = props.info.directory || state.cwd || "";
          await ingest("session.started", sessionId, null, {
            provider: "opencode",
            source: "opencode",
            title: props.info.title || (state.cwd ? state.cwd.split("/").pop() : providerSessionId),
            project_root: state.cwd || null,
            terminal_app: terminalApp(),
            cli_pid: pid,
            opencode_session_id: providerSessionId,
          }, providerSessionId, String(props.info.time?.updated || props.info.time?.created || ""));
          return;
        }

        if ((type === "session.deleted" || props.info?.time?.archived) && props.info?.id) {
          const providerSessionId = props.info.id;
          await ingest("session.ended", daemonSessionId(providerSessionId), null, { reason: type }, providerSessionId, type);
          sessions.delete(providerSessionId);
          return;
        }

        if (type === "session.updated" && props.info?.id) {
          const state = stateFor(props.info.id);
          if (props.info.directory) state.cwd = props.info.directory;
          return;
        }

        if (type === "message.updated" && props.info?.id && props.info?.sessionID) {
          msgRoles.set(props.info.id, { role: props.info.role, sessionID: props.info.sessionID });
          if (msgRoles.size > 200) msgRoles.delete(msgRoles.keys().next().value);
          return;
        }

        if (type === "message.part.updated" && props.part?.type === "text" && props.part?.messageID) {
          const meta = msgRoles.get(props.part.messageID);
          if (!meta) return;
          const sessionId = daemonSessionId(meta.sessionID);
          const state = stateFor(meta.sessionID);
          const text = trim(props.part.text || "");
          if (meta.role === "user" && text) {
            const taskId = taskIdFor(sessionId, props.part.messageID);
            state.currentTaskId = taskId;
            await ingest("task.started", sessionId, taskId, {
              task_id: taskId,
              prompt: text,
              provider_message_id: props.part.messageID,
            }, props.part.messageID);
            await ingest("prompt.submitted", sessionId, taskId, {
              task_id: taskId,
              prompt: text,
              provider_message_id: props.part.messageID,
            }, props.part.messageID);
          } else if (meta.role === "assistant" && text) {
            state.lastAssistantText = text;
            state.lastAssistantMessageId = props.part.messageID;
          }
          return;
        }

        if (type === "message.part.updated" && props.part?.type === "tool" && props.part?.sessionID) {
          const providerSessionId = props.part.sessionID;
          const sessionId = daemonSessionId(providerSessionId);
          const state = stateFor(providerSessionId);
          const taskId = await ensureTask(sessionId, providerSessionId, props.part.messageID || null, null);
          const status = props.part.state?.status;
          const toolName = props.part.tool || "tool";
          const partId = props.part.id || props.part.callID || toolName;
          if (status === "running" || status === "pending") {
            await ingest("tool.use.started", sessionId, taskId, {
              task_id: taskId,
              tool_name: toolName,
              call_id: props.part.callID || partId,
              input: props.part.state?.input || {},
            }, props.part.messageID || taskId, partId);
          } else if (status === "completed" || status === "error") {
            await ingest(status === "error" ? "tool.use.failed" : "tool.use.completed", sessionId, taskId, {
              task_id: taskId,
              tool_name: toolName,
              call_id: props.part.callID || partId,
              result: trim(typeof props.part.state?.output === "string" ? props.part.state.output : JSON.stringify(props.part.state?.output || "")),
              success: status !== "error",
            }, props.part.messageID || taskId, partId);
          }
          return;
        }

        if (type === "session.status" && props.sessionID && props.status?.type === "idle") {
          const providerSessionId = props.sessionID;
          const sessionId = daemonSessionId(providerSessionId);
          const state = stateFor(providerSessionId);
          const taskId = state.currentTaskId;
          if (!taskId) return;
          const messageKey = state.lastAssistantMessageId || taskId;
          if (state.lastAssistantText) {
            await ingest("assistant.response.completed", sessionId, taskId, {
              task_id: taskId,
              message: state.lastAssistantText,
              provider_message_id: state.lastAssistantMessageId || null,
            }, messageKey);
          }
          await ingest("task.completed", sessionId, taskId, {
            task_id: taskId,
            summary: state.lastAssistantText || null,
            provider_message_id: state.lastAssistantMessageId || null,
          }, messageKey);
          state.currentTaskId = null;
          return;
        }

        if (type === "permission.asked" && props.id && props.sessionID) {
          const toolName = props.permission || "permission";
          const patterns = props.patterns || [];
          const toolInput = { patterns, metadata: props.metadata };
          if (props.permission === "bash" && patterns.length) toolInput.command = patterns.join(" && ");
          if ((props.permission === "edit" || props.permission === "write") && patterns.length) toolInput.file_path = patterns[0];
          handleApproval(daemonSessionId(props.sessionID), props.sessionID, props.id, toolName, toolInput).catch(() => {});
          return;
        }

        if (type === "question.asked" && props.id && props.sessionID) {
          handleQuestion(daemonSessionId(props.sessionID), props.sessionID, props.id, props.questions || []).catch(() => {});
        }
      },
      "shell.env": async (_input, output) => {
        output.env.CODEISLAND_SOCKET_PATH = SOCKET;
      },
    };
  },
};
