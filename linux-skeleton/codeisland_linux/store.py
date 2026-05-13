from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4
from typing import Any

from .protocol import (
    DerivedSessionState,
    EventEnvelope,
    Interaction,
    Session,
    Task,
    derive_island_surface_state,
    now_iso,
    project_recent_activities,
)


class ContractError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InMemoryDaemonStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.tasks: dict[str, Task] = {}
        self.interactions: dict[str, Interaction] = {}
        self.activities: list[EventEnvelope] = []
        self.session_states: dict[str, DerivedSessionState] = {}
        self.seen_event_ids: set[str] = set()
        self.next_seq = 1

    def apply_event(self, event: EventEnvelope) -> list[dict[str, Any]]:
        if event.event_id in self.seen_event_ids:
            return [{"kind": "daemon.warning", "payload": {"message": "duplicate_event_ignored", "event_id": event.event_id}}]

        handler_name = f"_handle_{event.kind.replace('.', '_')}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            raise ContractError("invalid_request", f"unknown event kind: {event.kind}")

        event.seq = self.next_seq
        emitted_events = handler(event) or []
        self.next_seq += 1
        self.seen_event_ids.add(event.event_id)
        patches: list[dict[str, Any]] = list(emitted_events)

        session = self.sessions.get(event.session_id)
        if session is not None:
            self._refresh_session_state(event.session_id)
            patches.append({
                "kind": "session.status.changed",
                "seq": event.seq,
                "payload": {"session_id": session.session_id, "status": session.status},
            })

        patches.append(self._snapshot_patch(seq=event.seq, event=event))
        return patches

    def respond_to_interaction(self, interaction_id: str, action: str, answer: str | None) -> list[dict[str, Any]]:
        if action not in {"approve", "deny", "answer"}:
            raise ContractError("invalid_request", f"unsupported interaction action: {action}")

        interaction = self.interactions.get(interaction_id)
        if interaction is None:
            raise ContractError("unknown_interaction", f"unknown interaction: {interaction_id}")
        if interaction.state != "open":
            raise ContractError("duplicate_response", f"interaction already resolved: {interaction_id}")

        if interaction.type == "approval" and action not in {"approve", "deny"}:
            raise ContractError("invalid_request", "approval interactions require approve or deny")
        if interaction.type == "question":
            if action not in {"answer", "deny"}:
                raise ContractError("invalid_request", "question interactions require answer or deny")
            if action == "answer" and not answer:
                raise ContractError("invalid_request", "question interactions require a non-empty answer")

        seq = self._next_sequence()

        interaction.state = "answered"
        interaction.answered_at = now_iso()
        interaction.answer_payload = {"action": action, "answer": answer}

        session = self.sessions[interaction.session_id]
        session.status = "running"
        session.updated_at = interaction.answered_at
        session.last_event_at = interaction.answered_at

        if interaction.task_id and interaction.task_id in self.tasks:
            task = self.tasks[interaction.task_id]
            task.status = "running"

        synthetic_kind = "permission.resolved"
        if interaction.type == "question":
            synthetic_kind = "question.answered" if action == "answer" else "question.rejected"
        activity = self._record_synthetic_activity(
            session_id=interaction.session_id,
            kind=synthetic_kind,
            payload={
                "interaction_id": interaction_id,
                "action": action,
                "answer": answer,
                "interaction_type": interaction.type,
            },
            ts=interaction.answered_at,
            task_id=interaction.task_id,
            seq=seq,
        )
        self._refresh_session_state(interaction.session_id)

        return [
            {
                "kind": "interaction.resolved",
                "seq": seq,
                "payload": {"interaction_id": interaction_id, "state": interaction.state, "answer_payload": interaction.answer_payload},
            },
            {
                "kind": "session.status.changed",
                "seq": seq,
                "payload": {"session_id": session.session_id, "status": session.status},
            },
            self._snapshot_patch(seq=seq, event=activity),
        ]

    def create_task(self, session_id: str, prompt: str | None) -> tuple[str, list[dict[str, Any]]]:
        session = self.sessions.get(session_id)
        if session is None:
            raise ContractError("unknown_session", f"unknown session: {session_id}")
        if session.status in {"completed", "failed", "cancelled"}:
            raise ContractError("invalid_request", f"cannot create task for terminal session: {session_id}")

        seq = self._next_sequence()
        task_id = f"task_{uuid4().hex[:12]}"
        timestamp = now_iso()
        self.tasks[task_id] = Task(
            task_id=task_id,
            session_id=session_id,
            prompt=prompt,
            status="queued",
            started_at=None,
            ended_at=None,
            error_code=None,
            error_message=None,
        )
        session.current_task_id = task_id
        session.updated_at = timestamp
        session.last_event_at = timestamp
        self._refresh_session_state(session_id)

        return task_id, [self._snapshot_patch(seq=seq)]

    def cancel_task(self, task_id: str) -> list[dict[str, Any]]:
        task = self.tasks.get(task_id)
        if task is None:
            raise ContractError("unknown_task", f"unknown task: {task_id}")
        if task.status in {"done", "error", "cancelled"}:
            raise ContractError("invalid_interaction_state", f"task already terminal: {task_id}")

        seq = self._next_sequence()
        timestamp = now_iso()
        task.status = "cancelled"
        task.ended_at = timestamp
        session = self.sessions[task.session_id]
        self._clear_current_task_if_matches(session.session_id, task.task_id)
        session.status = "cancelled"
        session.updated_at = timestamp
        session.last_event_at = timestamp
        self._close_open_interactions_for_session(task.session_id, state="cancelled", timestamp=timestamp)
        self._refresh_session_state(task.session_id)
        return [
            {
                "kind": "session.status.changed",
                "seq": seq,
                "payload": {"session_id": session.session_id, "status": session.status},
            },
            self._snapshot_patch(seq=seq),
        ]

    def retry_task(self, task_id: str) -> list[dict[str, Any]]:
        task = self.tasks.get(task_id)
        if task is None:
            raise ContractError("unknown_task", f"unknown task: {task_id}")
        if task.status not in {"error", "cancelled"}:
            raise ContractError("invalid_interaction_state", f"task is not retryable: {task_id}")

        seq = self._next_sequence()
        timestamp = now_iso()
        task.status = "queued"
        task.ended_at = None
        task.error_code = None
        task.error_message = None
        session = self.sessions[task.session_id]
        session.status = "running"
        session.current_task_id = task.task_id
        session.updated_at = timestamp
        session.last_event_at = timestamp
        self._refresh_session_state(task.session_id)
        return [
            {
                "kind": "session.status.changed",
                "seq": seq,
                "payload": {"session_id": session.session_id, "status": session.status},
            },
            self._snapshot_patch(seq=seq),
        ]

    def _handle_session_started(self, event: EventEnvelope) -> list[dict[str, Any]]:
        payload = event.payload
        timestamp = event.ts
        existing = self.sessions.get(event.session_id)
        title = payload.get("title") or event.session_id
        project_root = payload.get("project_root")
        provider = self._provider_from_payload(payload, default=existing.provider if existing is not None else "opencode")
        terminal_app = self._string_from_payload(payload, "terminal_app", "terminal", "app_name", "app")
        terminal_pid = self._int_from_payload(payload, "terminal_pid", "window_pid")
        terminal_pane = self._string_from_payload(payload, "terminal_pane", "wezterm_pane", "pane_id")
        terminal_socket = self._string_from_payload(payload, "terminal_socket", "wezterm_socket")
        cli_pid = self._int_from_payload(payload, "cli_pid", "pid")
        if existing is None:
            self.sessions[event.session_id] = Session(
                session_id=event.session_id,
                provider=provider,
                project_root=project_root,
                title=title,
                status="running",
                current_task_id=None,
                workspace_hint=payload.get("workspace_hint"),
                terminal_app=terminal_app,
                terminal_pid=terminal_pid,
                terminal_pane=terminal_pane,
                terminal_socket=terminal_socket,
                cli_pid=cli_pid,
                created_at=timestamp,
                updated_at=timestamp,
                last_event_at=timestamp,
                ended_at=None,
            )
            return []

        existing.title = title
        existing.provider = provider
        existing.project_root = project_root or existing.project_root
        existing.terminal_app = terminal_app or existing.terminal_app
        existing.terminal_pid = terminal_pid if terminal_pid is not None else existing.terminal_pid
        existing.terminal_pane = terminal_pane or existing.terminal_pane
        existing.terminal_socket = terminal_socket or existing.terminal_socket
        existing.cli_pid = cli_pid if cli_pid is not None else existing.cli_pid
        existing.ended_at = None
        if existing.status not in {"completed", "failed", "cancelled"}:
            existing.status = "running"
        existing.updated_at = timestamp
        existing.last_event_at = timestamp

        return []

    def _handle_task_started(self, event: EventEnvelope) -> list[dict[str, Any]]:
        if event.session_id not in self.sessions:
            raise ContractError("unknown_session", f"unknown session: {event.session_id}")
        task_id = event.payload.get("task_id") or event.task_id
        if not task_id:
            raise ContractError("invalid_request", "task.started requires task_id")
        existing_task = self.tasks.get(task_id)
        if existing_task is not None:
            if existing_task.session_id != event.session_id:
                raise ContractError("invalid_request", f"task does not belong to session: {task_id}")
            raise ContractError("invalid_request", f"duplicate task.started for existing task: {task_id}")
        self.tasks[task_id] = Task(
            task_id=task_id,
            session_id=event.session_id,
            prompt=event.payload.get("prompt"),
            status="running",
            started_at=event.ts,
            ended_at=None,
            error_code=None,
            error_message=None,
        )
        session = self.sessions[event.session_id]
        session.current_task_id = task_id
        session.status = "running"
        session.updated_at = event.ts
        session.last_event_at = event.ts
        state = self._ensure_session_state(event.session_id)
        state.active_tool_name = None
        state.active_tool_status = None
        state.completion_pending = False
        state.completion_enqueued_at = None
        return []

    def _handle_task_progress(self, event: EventEnvelope) -> list[dict[str, Any]]:
        session = self.sessions.get(event.session_id)
        if session is None:
            raise ContractError("unknown_session", f"unknown session: {event.session_id}")
        session.updated_at = event.ts
        session.last_event_at = event.ts
        return []

    def _handle_prompt_submitted(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_tool_use_started(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_tool_use_completed(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_tool_use_failed(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_permission_requested(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_question_requested(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_assistant_response_completed(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_message_delta(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._handle_task_progress(event)

    def _handle_tool_call_started(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._handle_task_progress(event)

    def _handle_tool_call_finished(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._handle_task_progress(event)

    def _handle_interaction_approval_requested(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._open_interaction(event, "approval", "waiting_approval")

    def _handle_interaction_question_requested(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._open_interaction(event, "question", "waiting_answer")

    def _handle_task_completed(self, event: EventEnvelope) -> list[dict[str, Any]]:
        task = self._require_task(event)
        task.status = "done"
        task.ended_at = event.ts
        session = self.sessions[event.session_id]
        self._clear_current_task_if_matches(event.session_id, task.task_id)
        session.status = "completed"
        session.updated_at = event.ts
        session.last_event_at = event.ts
        self._close_open_interactions_for_session(event.session_id, state="cancelled", timestamp=event.ts)
        self._record_synthetic_activity(
            session_id=event.session_id,
            kind="completion.enqueued",
            payload={"task_id": task.task_id, "summary": event.payload.get("summary")},
            ts=event.ts,
            task_id=task.task_id,
            seq=event.seq if event.seq is not None else self.next_seq,
        )
        return []

    def _handle_task_failed(self, event: EventEnvelope) -> list[dict[str, Any]]:
        task = self._require_task(event)
        task.status = "error"
        task.ended_at = event.ts
        task.error_code = event.payload.get("error_code")
        task.error_message = event.payload.get("error_message")
        session = self.sessions[event.session_id]
        self._clear_current_task_if_matches(event.session_id, task.task_id)
        session.status = "failed"
        session.updated_at = event.ts
        session.last_event_at = event.ts
        self._close_open_interactions_for_session(event.session_id, state="cancelled", timestamp=event.ts)
        state = self._ensure_session_state(event.session_id)
        state.active_tool_name = None
        state.active_tool_status = None
        state.completion_pending = False
        return []

    def _handle_session_ended(self, event: EventEnvelope) -> list[dict[str, Any]]:
        session = self.sessions.get(event.session_id)
        if session is None:
            raise ContractError("unknown_session", f"unknown session: {event.session_id}")
        if session.status not in {"completed", "failed", "cancelled"}:
            session.status = "cancelled"
        self._cancel_non_terminal_tasks_for_session(event.session_id, timestamp=event.ts)
        self._clear_current_task_if_terminal(event.session_id)
        session.updated_at = event.ts
        session.last_event_at = event.ts
        session.ended_at = event.ts
        self._close_open_interactions_for_session(event.session_id, state="cancelled", timestamp=event.ts)
        state = self._ensure_session_state(event.session_id)
        state.active_tool_name = None
        state.active_tool_status = None
        return []

    def _handle_permission_resolved(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_question_answered(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_question_rejected(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_completion_enqueued(self, event: EventEnvelope) -> list[dict[str, Any]]:
        self._require_session_for_activity(event)
        self._record_activity(event)
        return self._handle_task_progress(event)

    def _handle_provider_error(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._handle_task_progress(event)

    def _handle_heartbeat(self, event: EventEnvelope) -> list[dict[str, Any]]:
        return self._handle_task_progress(event)

    def _open_interaction(self, event: EventEnvelope, interaction_type: str, session_status: str) -> list[dict[str, Any]]:
        session = self.sessions.get(event.session_id)
        if session is None:
            raise ContractError("unknown_session", f"unknown session: {event.session_id}")
        if session.status in {"completed", "failed", "cancelled"}:
            raise ContractError("invalid_request", f"cannot open interaction for terminal session: {event.session_id}")
        interaction_id = event.payload.get("interaction_id")
        if not interaction_id:
            raise ContractError("invalid_request", f"{event.kind} requires interaction_id")
        existing = self.interactions.get(interaction_id)
        if existing is not None:
            if existing.state == "open":
                return [{"kind": "daemon.warning", "seq": event.seq, "payload": {"message": "duplicate_interaction_ignored", "interaction_id": interaction_id}}]
            raise ContractError("duplicate_response", f"interaction already resolved: {interaction_id}")

        explicit_task_id = event.task_id or event.payload.get("task_id")
        task_id = explicit_task_id or session.current_task_id
        if explicit_task_id:
            task = self._require_task_id_for_session(event.session_id, explicit_task_id)
            if task.status in {"done", "error", "cancelled"}:
                raise ContractError("invalid_request", f"cannot open interaction for terminal task: {explicit_task_id}")

        interaction = Interaction(
            interaction_id=interaction_id,
            session_id=event.session_id,
            task_id=task_id,
            type=interaction_type,
            prompt_text=event.payload.get("prompt_text") or "",
            options=event.payload.get("options"),
            state="open",
            asked_at=event.ts,
            answered_at=None,
            answer_payload=None,
        )
        self.interactions[interaction_id] = interaction
        session.status = session_status
        session.updated_at = event.ts
        session.last_event_at = event.ts
        if task_id and task_id in self.tasks:
            self.tasks[task_id].status = "blocked"
        return [{"kind": "interaction.opened", "seq": event.seq, "payload": asdict(interaction)}]

    def _close_open_interactions_for_session(self, session_id: str, *, state: str, timestamp: str) -> None:
        for interaction in self.interactions.values():
            if interaction.session_id != session_id or interaction.state != "open":
                continue
            interaction.state = state
            interaction.answered_at = timestamp

    def _cancel_non_terminal_tasks_for_session(self, session_id: str, *, timestamp: str) -> None:
        for task in self.tasks.values():
            if task.session_id != session_id or task.status in {"done", "error", "cancelled"}:
                continue
            task.status = "cancelled"
            task.ended_at = timestamp

    def _clear_current_task_if_matches(self, session_id: str, task_id: str) -> None:
        session = self.sessions[session_id]
        if session.current_task_id == task_id:
            session.current_task_id = None

    def _clear_current_task_if_terminal(self, session_id: str) -> None:
        session = self.sessions[session_id]
        current_task_id = session.current_task_id
        if current_task_id is None:
            return
        task = self.tasks.get(current_task_id)
        if task is None or task.status in {"done", "error", "cancelled"}:
            session.current_task_id = None

    def _next_sequence(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def _require_task(self, event: EventEnvelope) -> Task:
        task_id = event.task_id or event.payload.get("task_id")
        if not task_id:
            raise ContractError("unknown_task", f"unknown task: {task_id}")
        return self._require_task_id_for_session(event.session_id, task_id)

    def _require_task_id_for_session(self, session_id: str, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise ContractError("unknown_task", f"unknown task: {task_id}")
        if task.session_id != session_id:
            raise ContractError("invalid_request", f"task does not belong to session: {task_id}")
        return task

    def _require_session_for_activity(self, event: EventEnvelope) -> Session:
        session = self.sessions.get(event.session_id)
        if session is None:
            raise ContractError("unknown_session", f"unknown session: {event.session_id}")
        return session

    @staticmethod
    def _provider_from_payload(payload: dict[str, Any], *, default: str) -> str:
        for key in ("provider", "source", "source_kind"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower().replace(" ", "-")
        return default

    @staticmethod
    def _string_from_payload(payload: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _int_from_payload(payload: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
        return None

    def _record_activity(self, event: EventEnvelope) -> EventEnvelope:
        self.activities.append(event)
        state = self._ensure_session_state(event.session_id)
        self._apply_activity_to_state(state, event)
        return event

    def _record_synthetic_activity(
        self,
        *,
        session_id: str,
        kind: str,
        payload: dict[str, Any],
        ts: str,
        task_id: str | None,
        seq: int,
    ) -> EventEnvelope:
        event = EventEnvelope(
            event_id=f"evt_{uuid4().hex[:16]}",
            session_id=session_id,
            kind=kind,
            payload=payload,
            ts=ts,
            task_id=task_id,
            seq=seq,
        )
        return self._record_activity(event)

    def _ensure_session_state(self, session_id: str) -> DerivedSessionState:
        state = self.session_states.get(session_id)
        if state is None:
            state = DerivedSessionState(
                session_id=session_id,
                effective_status="idle",
                current_task_id=None,
                pending_interaction_id=None,
                pending_interaction_kind=None,
                active_tool_name=None,
                active_tool_status=None,
                last_user_prompt=None,
                last_assistant_message=None,
                last_activity_kind=None,
                last_activity_at=None,
                completion_pending=False,
                completion_enqueued_at=None,
            )
            self.session_states[session_id] = state
        return state

    def _apply_activity_to_state(self, state: DerivedSessionState, event: EventEnvelope) -> None:
        state.last_activity_kind = event.kind
        state.last_activity_at = event.ts

        if event.kind == "prompt.submitted":
            prompt = event.payload.get("prompt")
            state.last_user_prompt = prompt if isinstance(prompt, str) and prompt else state.last_user_prompt
            state.active_tool_name = None
            state.active_tool_status = None
            state.completion_pending = False
            state.completion_enqueued_at = None
            return

        if event.kind == "tool.use.started":
            tool_name = event.payload.get("tool_name")
            state.active_tool_name = tool_name if isinstance(tool_name, str) and tool_name else None
            state.active_tool_status = "running"
            return

        if event.kind in {"tool.use.completed", "tool.use.failed"}:
            state.active_tool_name = None
            state.active_tool_status = None
            return

        if event.kind == "assistant.response.completed":
            message = event.payload.get("message")
            if isinstance(message, str) and message:
                state.last_assistant_message = message
            return

        if event.kind == "completion.enqueued":
            summary = event.payload.get("summary")
            if isinstance(summary, str) and summary:
                state.last_assistant_message = summary
            state.active_tool_name = None
            state.active_tool_status = None
            state.completion_pending = True
            state.completion_enqueued_at = event.ts

    def _refresh_session_state(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            self.session_states.pop(session_id, None)
            return

        state = self._ensure_session_state(session_id)
        state.effective_status = session.status
        state.current_task_id = session.current_task_id

        open_interactions = [item for item in self.interactions.values() if item.session_id == session_id and item.state == "open"]
        if open_interactions:
            latest = max(open_interactions, key=lambda item: item.asked_at)
            state.pending_interaction_id = latest.interaction_id
            state.pending_interaction_kind = latest.type
        else:
            state.pending_interaction_id = None
            state.pending_interaction_kind = None

        if session.status in {"failed", "cancelled"}:
            state.completion_pending = False

    def _snapshot_patch(self, *, seq: int, event: EventEnvelope | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sessions": [asdict(item) for item in self.sessions.values()],
            "tasks": [asdict(item) for item in self.tasks.values()],
            "interactions": [asdict(item) for item in self.interactions.values()],
            "activities": [asdict(item) for item in project_recent_activities(self.activities)],
            "session_states": [asdict(item) for item in self.session_states.values()],
            "island_state": asdict(derive_island_surface_state(
                sessions=self.sessions,
                interactions=self.interactions,
                session_states=self.session_states,
            )),
        }
        if event is not None:
            payload["event"] = self._event_payload(event)
        return {"kind": "snapshot.patch", "seq": seq, "payload": payload}

    @staticmethod
    def _event_payload(event: EventEnvelope) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "session_id": event.session_id,
            "task_id": event.task_id,
            "kind": event.kind,
            "payload": event.payload,
            "ts": event.ts,
            "seq": event.seq,
        }
