from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from .client import MAX_RESPONSE_BYTES, rpc_call
from .protocol import EventEnvelope, default_socket_path, from_json_line, now_iso, to_json_line


MAX_TEXT_CHARS = 360
DEFAULT_DB_ACTIVE_SECONDS = 15 * 60
DEFAULT_DB_HISTORY_MESSAGES = 80
DEFAULT_DB_SESSION_LIMIT = 20


@dataclass(slots=True)
class SessionTracker:
    cwd: str | None
    current_task_id: str | None
    next_task_index: int


@dataclass(slots=True)
class PendingInteraction:
    session_id: str
    task_id: str | None
    kind: str


class OpenCodeAdapterError(Exception):
    pass


ProviderWriteback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class RunningOpenCodeProcess:
    pid: int
    cwd: str | None
    terminal_app: str | None
    terminal_pid: int | None = None
    terminal_pane: str | None = None
    terminal_socket: str | None = None


@dataclass(slots=True)
class OpenCodeDbSession:
    opencode_session_id: str
    daemon_session_id: str
    title: str
    directory: str | None
    time_created_ms: int
    time_updated_ms: int
    terminal_app: str | None = None
    terminal_pid: int | None = None
    terminal_pane: str | None = None
    terminal_socket: str | None = None


@dataclass(slots=True)
class OpenCodeDbMessage:
    message_id: str
    role: str | None
    parent_id: str | None
    time_created_ms: int
    time_updated_ms: int
    data: dict[str, Any]


@dataclass(slots=True)
class OpenCodeDbPart:
    part_id: str
    message_id: str
    time_created_ms: int
    time_updated_ms: int
    data: dict[str, Any]


class OpenCodeDatabaseAdapter:
    """Map the local OpenCode SQLite database into CodeIsland daemon events."""

    def __init__(self) -> None:
        self.sent_event_ids: set[str] = set()

    def translate_database(
        self,
        database_path: Path,
        *,
        active_seconds: int = DEFAULT_DB_ACTIVE_SECONDS,
        session_limit: int = DEFAULT_DB_SESSION_LIMIT,
        history_messages: int = DEFAULT_DB_HISTORY_MESSAGES,
        running_processes: list[RunningOpenCodeProcess] | None = None,
        now_ms: int | None = None,
    ) -> tuple[list[EventEnvelope], set[str]]:
        running_processes = running_processes if running_processes is not None else discover_running_opencode_processes()
        running_by_dir = {item.cwd: item for item in running_processes if item.cwd}
        events: list[EventEnvelope] = []
        active_session_ids: set[str] = set()

        connection = self._connect(database_path)
        try:
            sessions = self._load_sessions(
                connection,
                active_seconds=active_seconds,
                session_limit=session_limit,
                running_directories=set(running_by_dir),
                now_ms=now_ms,
            )
            for session in sessions:
                if session.directory and session.directory in running_by_dir:
                    process = running_by_dir[session.directory]
                    session.terminal_app = process.terminal_app
                    session.terminal_pid = process.terminal_pid
                    session.terminal_pane = process.terminal_pane
                    session.terminal_socket = process.terminal_socket
                active_session_ids.add(session.daemon_session_id)
                messages, parts_by_message = self._load_messages(connection, session.opencode_session_id, history_messages=history_messages)
                events.extend(self.translate_session(session, messages, parts_by_message))
        finally:
            connection.close()

        return events, active_session_ids

    def translate_session(
        self,
        session: OpenCodeDbSession,
        messages: list[OpenCodeDbMessage],
        parts_by_message: dict[str, list[OpenCodeDbPart]],
    ) -> list[EventEnvelope]:
        events = [
            EventEnvelope(
                event_id=self._event_id(session.daemon_session_id, "session.started", session.opencode_session_id, str(session.time_updated_ms)),
                session_id=session.daemon_session_id,
                kind="session.started",
                payload={
                    "provider": "opencode",
                    "source": "opencode",
                    "title": session.title,
                    "project_root": session.directory,
                    "workspace_hint": "opencode-db",
                    "terminal_app": session.terminal_app,
                    "terminal_pid": session.terminal_pid,
                    "terminal_pane": session.terminal_pane,
                    "terminal_socket": session.terminal_socket,
                    "opencode_session_id": session.opencode_session_id,
                },
                ts=self._timestamp_ms(session.time_created_ms),
            )
        ]

        task_by_user_message: dict[str, str] = {}
        task_started: set[str] = set()
        last_task_id: str | None = None

        for message in messages:
            parts = parts_by_message.get(message.message_id, [])
            if message.role == "user":
                task_id = self._task_id(session.daemon_session_id, message.message_id)
                task_by_user_message[message.message_id] = task_id
                task_started.add(task_id)
                last_task_id = task_id
                prompt = self._trim(self._text_from_parts(parts))
                timestamp = self._timestamp_ms(message.time_created_ms)
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(session.daemon_session_id, "task.started", message.message_id),
                        session_id=session.daemon_session_id,
                        task_id=task_id,
                        kind="task.started",
                        payload={"task_id": task_id, "prompt": prompt, "provider_message_id": message.message_id},
                        ts=timestamp,
                    )
                )
                if prompt:
                    events.append(
                        EventEnvelope(
                            event_id=self._event_id(session.daemon_session_id, "prompt.submitted", message.message_id),
                            session_id=session.daemon_session_id,
                            task_id=task_id,
                            kind="prompt.submitted",
                            payload={"task_id": task_id, "prompt": prompt, "provider_message_id": message.message_id},
                            ts=timestamp,
                        )
                    )
                continue

            if message.role != "assistant":
                continue

            task_id = task_by_user_message.get(message.parent_id or "") or last_task_id
            if task_id is None:
                task_id = self._task_id(session.daemon_session_id, message.parent_id or message.message_id)
            if task_id not in task_started:
                task_started.add(task_id)
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(session.daemon_session_id, "task.started", message.message_id, "synthetic"),
                        session_id=session.daemon_session_id,
                        task_id=task_id,
                        kind="task.started",
                        payload={"task_id": task_id, "provider_message_id": message.message_id, "synthetic": True},
                        ts=self._timestamp_ms(message.time_created_ms),
                    )
                )

            events.extend(self._translate_assistant_parts(session, message, task_id, parts))
            if self._message_finished(message) and not self._has_step_finish(parts):
                events.extend(self._assistant_completion_events(session, message, task_id, parts, completed_at_ms=message.time_updated_ms))
                last_task_id = None

            if self._has_step_finish(parts):
                last_task_id = None

        return events

    async def sync_once(
        self,
        socket_path: str,
        database_path: Path,
        *,
        active_seconds: int = DEFAULT_DB_ACTIVE_SECONDS,
        session_limit: int = DEFAULT_DB_SESSION_LIMIT,
        history_messages: int = DEFAULT_DB_HISTORY_MESSAGES,
        end_stale: bool = True,
    ) -> list[dict[str, Any]]:
        events, active_session_ids = self.translate_database(
            database_path,
            active_seconds=active_seconds,
            session_limit=session_limit,
            history_messages=history_messages,
        )
        responses = await self._send_events(socket_path, events)
        if end_stale:
            responses.extend(await self._end_daemon_sessions_not_active(socket_path, active_session_ids))
        return responses

    async def watch_database(
        self,
        socket_path: str,
        database_path: Path,
        *,
        interval: float,
        active_seconds: int = DEFAULT_DB_ACTIVE_SECONDS,
        session_limit: int = DEFAULT_DB_SESSION_LIMIT,
        history_messages: int = DEFAULT_DB_HISTORY_MESSAGES,
        end_stale: bool = True,
    ) -> None:
        while True:
            await self.sync_once(
                socket_path,
                database_path,
                active_seconds=active_seconds,
                session_limit=session_limit,
                history_messages=history_messages,
                end_stale=end_stale,
            )
            await asyncio.sleep(interval)

    async def _send_events(self, socket_path: str, events: list[EventEnvelope]) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        request_index = 1
        for event in events:
            if event.event_id in self.sent_event_ids:
                continue
            responses.append(
                await rpc_call(
                    socket_path,
                    "ingest_event",
                    {
                        "event_id": event.event_id,
                        "session_id": event.session_id,
                        "task_id": event.task_id,
                        "kind": event.kind,
                        "payload": event.payload,
                        "ts": event.ts,
                    },
                    request_id=f"opencode-db-{request_index}",
                )
            )
            self.sent_event_ids.add(event.event_id)
            request_index += 1
        return responses

    async def _end_daemon_sessions_not_active(self, socket_path: str, active_session_ids: set[str]) -> list[dict[str, Any]]:
        response = await rpc_call(socket_path, "list_sessions", {"provider": "opencode"}, request_id="opencode-db-stale-scan")
        sessions = response.get("result", {}).get("sessions", []) if response.get("ok") else []
        events: list[EventEnvelope] = []
        timestamp = now_iso()
        for session in sessions:
            if not isinstance(session, dict):
                continue
            session_id = self._string(session.get("session_id"))
            if not session_id or session_id in active_session_ids or session.get("ended_at"):
                continue
            if session.get("workspace_hint") != "opencode-db":
                continue
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session_id, "session.ended", timestamp),
                    session_id=session_id,
                    kind="session.ended",
                    payload={"reason": "opencode_db_inactive"},
                    ts=timestamp,
                )
            )
        return await self._send_events(socket_path, events)

    def _translate_assistant_parts(
        self,
        session: OpenCodeDbSession,
        message: OpenCodeDbMessage,
        task_id: str,
        parts: list[OpenCodeDbPart],
    ) -> list[EventEnvelope]:
        events: list[EventEnvelope] = []
        for part in parts:
            part_type = part.data.get("type")
            if part_type == "tool":
                tool_events = self._tool_events_for_part(session, message, task_id, part)
                events.extend(tool_events)
                continue
            if part_type != "step-finish":
                continue
            events.extend(self._assistant_completion_events(session, message, task_id, parts, completed_at_ms=part.time_created_ms))
        return events

    def _tool_events_for_part(
        self,
        session: OpenCodeDbSession,
        message: OpenCodeDbMessage,
        task_id: str,
        part: OpenCodeDbPart,
    ) -> list[EventEnvelope]:
        state = part.data.get("state")
        state = state if isinstance(state, dict) else {}
        status = self._string(state.get("status")) or self._string(part.data.get("status"))
        tool_name = self._string(part.data.get("tool")) or self._string(part.data.get("tool_name")) or self._string(part.data.get("name")) or "tool"
        call_id = self._string(part.data.get("callID")) or self._string(part.data.get("call_id")) or part.part_id
        timestamp = self._timestamp_ms(part.time_created_ms)

        if status == "running":
            return [
                EventEnvelope(
                    event_id=self._event_id(session.daemon_session_id, "tool.use.started", message.message_id, part.part_id),
                    session_id=session.daemon_session_id,
                    task_id=task_id,
                    kind="tool.use.started",
                    payload={
                        "task_id": task_id,
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "input": state.get("input") or part.data.get("input"),
                    },
                    ts=timestamp,
                )
            ]

        if status in {"completed", "error", "failed"}:
            failed = status in {"error", "failed"} or self._tool_state_failed(state)
            return [
                EventEnvelope(
                    event_id=self._event_id(session.daemon_session_id, "tool.use.failed" if failed else "tool.use.completed", message.message_id, part.part_id),
                    session_id=session.daemon_session_id,
                    task_id=task_id,
                    kind="tool.use.failed" if failed else "tool.use.completed",
                    payload={
                        "task_id": task_id,
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "result": self._trim(self._string(state.get("output") or state.get("result") or part.data.get("output"))),
                        "success": not failed,
                        "error_message": self._tool_error_message(state),
                    },
                    ts=timestamp,
                )
            ]

        return []

    def _assistant_completion_events(
        self,
        session: OpenCodeDbSession,
        message: OpenCodeDbMessage,
        task_id: str,
        parts: list[OpenCodeDbPart],
        *,
        completed_at_ms: int,
    ) -> list[EventEnvelope]:
        summary = self._trim(self._text_from_parts(parts))
        timestamp = self._timestamp_ms(completed_at_ms)
        events: list[EventEnvelope] = []
        if summary:
            events.append(
                EventEnvelope(
                    event_id=self._event_id(session.daemon_session_id, "assistant.response.completed", message.message_id),
                    session_id=session.daemon_session_id,
                    task_id=task_id,
                    kind="assistant.response.completed",
                    payload={"task_id": task_id, "message": summary, "provider_message_id": message.message_id},
                    ts=timestamp,
                )
            )
        events.append(
            EventEnvelope(
                event_id=self._event_id(session.daemon_session_id, "task.completed", message.message_id),
                session_id=session.daemon_session_id,
                task_id=task_id,
                kind="task.completed",
                payload={"task_id": task_id, "summary": summary, "provider_message_id": message.message_id},
                ts=timestamp,
            )
        )
        return events

    def _load_sessions(
        self,
        connection: sqlite3.Connection,
        *,
        active_seconds: int,
        session_limit: int,
        running_directories: set[str],
        now_ms: int | None,
    ) -> list[OpenCodeDbSession]:
        where = ["time_archived is null"]
        params: list[Any] = []
        if active_seconds > 0:
            cutoff_ms = (now_ms if now_ms is not None else int(time.time() * 1000)) - (active_seconds * 1000)
            if running_directories:
                placeholders = ",".join("?" for _ in running_directories)
                where.append(f"(time_updated >= ? or directory in ({placeholders}))")
                params.append(cutoff_ms)
                params.extend(sorted(running_directories))
            else:
                where.append("time_updated >= ?")
                params.append(cutoff_ms)
        query = f"""
            select id, directory, title, time_created, time_updated
            from session
            where {' and '.join(where)}
            order by time_updated desc, id desc
            limit ?
        """
        params.append(session_limit)
        rows = connection.execute(query, params).fetchall()
        sessions: list[OpenCodeDbSession] = []
        for row in rows:
            opencode_session_id = str(row["id"])
            directory = self._string(row["directory"])
            title = self._string(row["title"]) or (Path(directory).name if directory else opencode_session_id)
            sessions.append(
                OpenCodeDbSession(
                    opencode_session_id=opencode_session_id,
                    daemon_session_id=self._daemon_session_id(opencode_session_id),
                    title=title,
                    directory=directory,
                    time_created_ms=int(row["time_created"]),
                    time_updated_ms=int(row["time_updated"]),
                )
            )
        return list(reversed(sessions))

    def _load_messages(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        history_messages: int,
    ) -> tuple[list[OpenCodeDbMessage], dict[str, list[OpenCodeDbPart]]]:
        rows = connection.execute(
            """
            select id, session_id, time_created, time_updated, data
            from message
            where session_id = ?
            order by time_created desc, id desc
            limit ?
            """,
            (session_id, history_messages),
        ).fetchall()
        rows = list(reversed(rows))
        messages: list[OpenCodeDbMessage] = []
        message_ids: list[str] = []
        for row in rows:
            data = self._decode_json(row["data"])
            message_id = str(row["id"])
            message_ids.append(message_id)
            messages.append(
                OpenCodeDbMessage(
                    message_id=message_id,
                    role=self._string(data.get("role")),
                    parent_id=self._string(data.get("parentID")) or self._string(data.get("parent_id")),
                    time_created_ms=int(row["time_created"]),
                    time_updated_ms=int(row["time_updated"]),
                    data=data,
                )
            )

        parts_by_message = {message_id: [] for message_id in message_ids}
        if not message_ids:
            return messages, parts_by_message

        placeholders = ",".join("?" for _ in message_ids)
        part_rows = connection.execute(
            f"""
            select id, message_id, time_created, time_updated, data
            from part
            where session_id = ? and message_id in ({placeholders})
            order by time_created, id
            """,
            (session_id, *message_ids),
        ).fetchall()
        for row in part_rows:
            message_id = str(row["message_id"])
            parts_by_message.setdefault(message_id, []).append(
                OpenCodeDbPart(
                    part_id=str(row["id"]),
                    message_id=message_id,
                    time_created_ms=int(row["time_created"]),
                    time_updated_ms=int(row["time_updated"]),
                    data=self._decode_json(row["data"]),
                )
            )
        return messages, parts_by_message

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection:
        path = database_path.expanduser()
        if not path.exists():
            raise OpenCodeAdapterError(f"OpenCode database not found: {path}")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decode_json(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str):
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _daemon_session_id(opencode_session_id: str) -> str:
        return opencode_session_id if opencode_session_id.startswith("opencode-") else f"opencode-{opencode_session_id}"

    @staticmethod
    def _task_id(daemon_session_id: str, message_id: str) -> str:
        return f"{daemon_session_id}-task-{message_id}"

    @staticmethod
    def _timestamp_ms(value: int) -> str:
        return datetime.fromtimestamp(value / 1000, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _trim(value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if len(text) <= MAX_TEXT_CHARS:
            return text
        return f"{text[:MAX_TEXT_CHARS - 3]}..."

    @staticmethod
    def _text_from_parts(parts: list[OpenCodeDbPart]) -> str | None:
        texts: list[str] = []
        for part in parts:
            if part.data.get("type") != "text":
                continue
            text = part.data.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        return "\n".join(texts) if texts else None

    @staticmethod
    def _has_step_finish(parts: list[OpenCodeDbPart]) -> bool:
        return any(part.data.get("type") == "step-finish" for part in parts)

    @staticmethod
    def _message_finished(message: OpenCodeDbMessage) -> bool:
        finish = message.data.get("finish")
        if isinstance(finish, str) and finish:
            return True
        time_data = message.data.get("time")
        return isinstance(time_data, dict) and isinstance(time_data.get("completed"), int)

    @staticmethod
    def _tool_state_failed(state: dict[str, Any]) -> bool:
        if state.get("error") or state.get("error_message"):
            return True
        output = state.get("output")
        if isinstance(output, dict):
            exit_code = output.get("exit_code")
            return isinstance(exit_code, int) and exit_code != 0
        return False

    @staticmethod
    def _tool_error_message(state: dict[str, Any]) -> str | None:
        for key in ("error_message", "error", "stderr"):
            value = state.get(key)
            if isinstance(value, str) and value:
                return value
        output = state.get("output")
        if isinstance(output, dict):
            for key in ("error_message", "error", "stderr"):
                value = output.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    @staticmethod
    def _event_id(session_id: str, normalized_kind: str, *parts: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {"kind": normalized_kind, "session_id": session_id, "parts": parts},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"evt_opencode_{digest}"


def discover_running_opencode_processes() -> list[RunningOpenCodeProcess]:
    processes: list[RunningOpenCodeProcess] = []
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit() or not _is_opencode_process(proc_dir):
            continue
        pid = int(proc_dir.name)
        env = _read_environ(proc_dir)
        processes.append(
            RunningOpenCodeProcess(
                pid=pid,
                cwd=_readlink_text(proc_dir / "cwd"),
                terminal_app=_terminal_app(env),
                terminal_pid=_terminal_pid(env),
                terminal_pane=_terminal_pane(env),
                terminal_socket=_terminal_socket(env),
            )
        )
    return sorted(processes, key=lambda item: item.pid)


def default_database_path() -> Path:
    value = os.environ.get("OPENCODE_DB")
    if value:
        return Path(value).expanduser()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / "opencode" / "opencode.db"
    return Path("~/.local/share/opencode/opencode.db").expanduser()


def _is_opencode_process(proc_dir: Path) -> bool:
    comm = _read_text(proc_dir / "comm")
    if comm and comm.strip() == "opencode":
        return True
    cmdline = _read_text(proc_dir / "cmdline")
    if not cmdline:
        return False
    args = [item for item in cmdline.split("\0") if item]
    if not args:
        return False
    return any(Path(item).name == "opencode" for item in args[:3])


def _read_environ(proc_dir: Path) -> dict[str, str]:
    raw = _read_text(proc_dir / "environ")
    values: dict[str, str] = {}
    if not raw:
        return values
    for item in raw.split("\0"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key] = value
    return values


def _terminal_app(env: dict[str, str]) -> str | None:
    if env.get("GHOSTTY_RESOURCES_DIR") or env.get("GHOSTTY_BIN_DIR"):
        return "Ghostty"
    if env.get("WEZTERM_PANE") or env.get("TERM_PROGRAM") == "WezTerm":
        return "WezTerm"
    if env.get("KITTY_WINDOW_ID"):
        return "kitty"
    if env.get("ALACRITTY_WINDOW_ID"):
        return "Alacritty"
    value = env.get("TERM_PROGRAM")
    return value if value else None


def _terminal_socket(env: dict[str, str]) -> str | None:
    value = env.get("WEZTERM_UNIX_SOCKET")
    return value if value else None


def _terminal_pane(env: dict[str, str]) -> str | None:
    value = env.get("WEZTERM_PANE")
    return value if value else None


def _terminal_pid(env: dict[str, str]) -> int | None:
    socket_path = _terminal_socket(env)
    if not socket_path:
        return None
    marker = "gui-sock-"
    if marker not in socket_path:
        return None
    tail = socket_path.split(marker, 1)[1].split("/", 1)[0]
    return int(tail) if tail.isdigit() else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _readlink_text(path: Path) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        return None


class OpenCodeHookAdapter:
    def __init__(self, *, provider_writeback: ProviderWriteback | None = None) -> None:
        self.sessions: dict[str, SessionTracker] = {}
        self.pending_interactions: dict[str, PendingInteraction] = {}
        self.provider_writeback = provider_writeback or self._default_provider_writeback

    def translate_event(self, raw_event: dict[str, Any]) -> list[EventEnvelope]:
        if not isinstance(raw_event, dict):
            raise OpenCodeAdapterError("raw event must be an object")

        hook_event_name = raw_event.get("hook_event_name")
        if not isinstance(hook_event_name, str) or not hook_event_name:
            raise OpenCodeAdapterError("hook event must include hook_event_name")

        session_id = self._session_id(raw_event)
        timestamp = self._timestamp(raw_event)

        if hook_event_name == "SessionStart":
            tracker = self.sessions.get(session_id)
            cwd = self._cwd(raw_event)
            if tracker is None:
                tracker = SessionTracker(cwd=cwd, current_task_id=None, next_task_index=1)
                self.sessions[session_id] = tracker
            else:
                tracker.cwd = cwd or tracker.cwd
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "session.started"),
                    session_id=session_id,
                    kind="session.started",
                    payload={
                        "provider": "opencode",
                        "title": self._title(raw_event),
                        "project_root": tracker.cwd,
                    },
                    ts=timestamp,
                )
            ]

        tracker = self._require_session(session_id)

        if hook_event_name == "SessionEnd":
            tracker.current_task_id = None
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "session.ended"),
                    session_id=session_id,
                    kind="session.ended",
                    payload={"reason": raw_event.get("reason")},
                    ts=timestamp,
                )
            ]

        if hook_event_name == "UserPromptSubmit":
            task_id = self._new_task_id(session_id, raw_event)
            tracker.current_task_id = task_id
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "task.started"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="task.started",
                    payload={
                        "task_id": task_id,
                        "prompt": raw_event.get("prompt"),
                    },
                    ts=timestamp,
                ),
                EventEnvelope(
                    event_id=self._event_id(raw_event, "prompt.submitted"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="prompt.submitted",
                    payload={
                        "task_id": task_id,
                        "prompt": raw_event.get("prompt"),
                        "provider_message_id": raw_event.get("_opencode_message_id"),
                    },
                    ts=timestamp,
                )
            ]

        if hook_event_name == "Stop":
            task_id = tracker.current_task_id
            if task_id is None:
                return []
            tracker.current_task_id = None
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "assistant.response.completed"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="assistant.response.completed",
                    payload={
                        "task_id": task_id,
                        "message": raw_event.get("last_assistant_message"),
                    },
                    ts=timestamp,
                ),
                EventEnvelope(
                    event_id=self._event_id(raw_event, "task.completed"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="task.completed",
                    payload={
                        "task_id": task_id,
                        "summary": raw_event.get("last_assistant_message"),
                    },
                    ts=timestamp,
                )
            ]

        task_id = tracker.current_task_id
        if hook_event_name == "PreToolUse":
            if task_id is None:
                return []
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "tool.call.started"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.call.started",
                    payload={
                        "tool_name": raw_event.get("tool_name"),
                        "input": raw_event.get("tool_input"),
                    },
                    ts=timestamp,
                ),
                EventEnvelope(
                    event_id=self._event_id(raw_event, "tool.use.started"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.use.started",
                    payload={
                        "tool_name": raw_event.get("tool_name"),
                        "input": raw_event.get("tool_input"),
                    },
                    ts=timestamp,
                )
            ]

        if hook_event_name == "PostToolUse":
            if task_id is None:
                return []
            tool_output = raw_event.get("tool_output")
            activity_kind = "tool.use.failed" if self._tool_use_failed(raw_event) else "tool.use.completed"
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "tool.call.finished"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="tool.call.finished",
                    payload={
                        "tool_name": raw_event.get("tool_name"),
                        "result": tool_output,
                    },
                    ts=timestamp,
                ),
                EventEnvelope(
                    event_id=self._event_id(raw_event, activity_kind),
                    session_id=session_id,
                    task_id=task_id,
                    kind=activity_kind,
                    payload={
                        "tool_name": raw_event.get("tool_name"),
                        "result": tool_output,
                        "success": not self._tool_use_failed(raw_event),
                        "error_message": self._tool_error_message(tool_output),
                    },
                    ts=timestamp,
                )
            ]

        if hook_event_name == "PermissionRequest":
            interaction_id = raw_event.get("_opencode_request_id")
            if not isinstance(interaction_id, str) or not interaction_id:
                raise OpenCodeAdapterError("PermissionRequest requires _opencode_request_id")
            if raw_event.get("tool_name") == "AskUserQuestion":
                self.pending_interactions[interaction_id] = PendingInteraction(
                    session_id=session_id,
                    task_id=task_id,
                    kind="question",
                )
                questions = raw_event.get("tool_input", {}).get("questions") or []
                first_question = questions[0] if questions else {}
                return [
                    EventEnvelope(
                        event_id=self._event_id(raw_event, "interaction.question.requested"),
                        session_id=session_id,
                        task_id=task_id,
                        kind="interaction.question.requested",
                        payload={
                            "interaction_id": interaction_id,
                            "prompt_text": first_question.get("question") or raw_event.get("prompt") or "",
                            "options": [option.get("label") for option in first_question.get("options", []) if option.get("label")],
                        },
                        ts=timestamp,
                    ),
                    EventEnvelope(
                        event_id=self._event_id(raw_event, "question.requested"),
                        session_id=session_id,
                        task_id=task_id,
                        kind="question.requested",
                        payload={
                            "interaction_id": interaction_id,
                            "prompt_text": first_question.get("question") or raw_event.get("prompt") or "",
                            "options": [option.get("label") for option in first_question.get("options", []) if option.get("label")],
                        },
                        ts=timestamp,
                    )
                ]
            self.pending_interactions[interaction_id] = PendingInteraction(
                session_id=session_id,
                task_id=task_id,
                kind="approval",
            )
            return [
                EventEnvelope(
                    event_id=self._event_id(raw_event, "interaction.approval.requested"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="interaction.approval.requested",
                    payload={
                        "interaction_id": interaction_id,
                        "prompt_text": self._approval_prompt(raw_event),
                        "options": ["approve", "deny"],
                    },
                    ts=timestamp,
                ),
                EventEnvelope(
                    event_id=self._event_id(raw_event, "permission.requested"),
                    session_id=session_id,
                    task_id=task_id,
                    kind="permission.requested",
                    payload={
                        "interaction_id": interaction_id,
                        "tool_name": raw_event.get("tool_name"),
                        "prompt_text": self._approval_prompt(raw_event),
                        "options": ["approve", "deny"],
                        "tool_input": raw_event.get("tool_input"),
                    },
                    ts=timestamp,
                )
            ]

        return []

    def read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OpenCodeAdapterError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
            if not isinstance(decoded, dict):
                raise OpenCodeAdapterError(f"JSONL line {line_number} must decode to an object")
            events.append(decoded)
        return events

    async def replay_jsonl(self, socket_path: str, input_path: Path) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        request_index = 1
        for raw_event in self.read_jsonl(input_path):
            for normalized in self.translate_event(raw_event):
                responses.append(
                    await rpc_call(
                        socket_path,
                        "ingest_event",
                        {
                            "event_id": normalized.event_id,
                            "session_id": normalized.session_id,
                            "task_id": normalized.task_id,
                            "kind": normalized.kind,
                            "payload": normalized.payload,
                            "ts": normalized.ts,
                        },
                        request_id=f"req-{request_index}",
                    )
                )
                request_index += 1
        return responses

    async def forward_daemon_resolutions(self, socket_path: str) -> None:
        reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
        try:
            writer.write(to_json_line({"id": "req-adapter-subscribe", "method": "subscribe", "params": {"topics": ["interactions"]}}))
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                await self.handle_daemon_message(from_json_line(line))
        finally:
            writer.close()
            await writer.wait_closed()

    async def handle_daemon_message(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict) or message.get("kind") != "interaction.resolved":
            return

        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        interaction_id = payload.get("interaction_id")
        if not isinstance(interaction_id, str) or not interaction_id:
            return

        pending = self.pending_interactions.get(interaction_id)
        if pending is None:
            return

        answer_payload = payload.get("answer_payload")
        if not isinstance(answer_payload, dict):
            raise OpenCodeAdapterError(f"interaction.resolved missing answer_payload for {interaction_id}")
        action = answer_payload.get("action")
        if not isinstance(action, str) or not action:
            raise OpenCodeAdapterError(f"interaction.resolved missing action for {interaction_id}")

        if pending.kind == "approval":
            payload_to_write = self._approval_writeback_payload(interaction_id, action)
        elif pending.kind == "question":
            answer = answer_payload.get("answer")
            payload_to_write = self._question_writeback_payload(interaction_id, action, answer)
        else:
            return

        await self.provider_writeback(interaction_id, payload_to_write)
        self.pending_interactions.pop(interaction_id, None)

    def _require_session(self, session_id: str) -> SessionTracker:
        tracker = self.sessions.get(session_id)
        if tracker is None:
            raise OpenCodeAdapterError(f"received event for unknown session: {session_id}")
        return tracker

    @staticmethod
    def _session_id(raw_event: dict[str, Any]) -> str:
        value = raw_event.get("session_id")
        if not isinstance(value, str) or not value:
            raise OpenCodeAdapterError("hook event requires session_id")
        return value if value.startswith("opencode-") else f"opencode-{value}"

    @staticmethod
    def _cwd(raw_event: dict[str, Any]) -> str | None:
        value = raw_event.get("cwd")
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _title(raw_event: dict[str, Any]) -> str:
        cwd = raw_event.get("cwd")
        if isinstance(cwd, str) and cwd:
            return Path(cwd).name or raw_event["session_id"]
        return raw_event["session_id"]

    @staticmethod
    def _timestamp(raw_event: dict[str, Any]) -> str:
        value = raw_event.get("ts")
        if isinstance(value, str) and value:
            return value
        return now_iso()

    def _new_task_id(self, session_id: str, raw_event: dict[str, Any]) -> str:
        tracker = self.sessions[session_id]
        suffix = raw_event.get("_opencode_message_id")
        if isinstance(suffix, str) and suffix:
            return f"{session_id}-task-{suffix}"
        task_id = f"{session_id}-task-{tracker.next_task_index}"
        tracker.next_task_index += 1
        return task_id

    @staticmethod
    def _approval_prompt(raw_event: dict[str, Any]) -> str:
        tool_name = raw_event.get("tool_name") or "Tool"
        tool_input = raw_event.get("tool_input")
        if isinstance(tool_input, dict) and tool_input:
            return f"Allow {tool_name}: {json.dumps(tool_input, sort_keys=True)}"
        return f"Allow {tool_name}?"

    @staticmethod
    def _tool_use_failed(raw_event: dict[str, Any]) -> bool:
        tool_output = raw_event.get("tool_output")
        if isinstance(tool_output, dict):
            success = tool_output.get("success")
            if success is False:
                return True
            exit_code = tool_output.get("exit_code")
            if isinstance(exit_code, int) and exit_code != 0:
                return True
            if isinstance(tool_output.get("error"), str) and tool_output.get("error"):
                return True
            if isinstance(tool_output.get("error_message"), str) and tool_output.get("error_message"):
                return True
        return False

    @staticmethod
    def _tool_error_message(tool_output: Any) -> str | None:
        if not isinstance(tool_output, dict):
            return None
        for key in ("error_message", "error", "stderr"):
            value = tool_output.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _event_id(raw_event: dict[str, Any], normalized_kind: str) -> str:
        digest = hashlib.sha256(
            json.dumps({"kind": normalized_kind, "raw": raw_event}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return f"evt_{digest}"

    @staticmethod
    async def _default_provider_writeback(interaction_id: str, payload: dict[str, Any]) -> None:
        _ = (interaction_id, payload)

    @staticmethod
    def _approval_writeback_payload(interaction_id: str, action: str) -> dict[str, Any]:
        if action == "approve":
            response = "once"
        elif action == "deny":
            response = "reject"
        else:
            raise OpenCodeAdapterError(f"unsupported approval resolution action: {action}")
        return {"interaction_id": interaction_id, "response": response}

    @staticmethod
    def _question_writeback_payload(interaction_id: str, action: str, answer: Any) -> dict[str, Any]:
        if action == "deny":
            return {"interaction_id": interaction_id, "action": "reject"}
        if action != "answer":
            raise OpenCodeAdapterError(f"unsupported question resolution action: {action}")
        if not isinstance(answer, str) or not answer:
            raise OpenCodeAdapterError(f"question resolution missing answer for {interaction_id}")
        return {"interaction_id": interaction_id, "answers": [[answer]]}


async def _main_async(args: argparse.Namespace) -> None:
    if args.input:
        adapter = OpenCodeHookAdapter()
        responses = await adapter.replay_jsonl(args.socket_path, Path(args.input).expanduser())
        for response in responses:
            print(response)
        return

    adapter = OpenCodeDatabaseAdapter()
    database_path = Path(args.database).expanduser()
    if args.watch:
        await adapter.watch_database(
            args.socket_path,
            database_path,
            interval=args.interval,
            active_seconds=args.active_seconds,
            session_limit=args.session_limit,
            history_messages=args.history_messages,
            end_stale=not args.keep_stale,
        )
        return

    responses = await adapter.sync_once(
        args.socket_path,
        database_path,
        active_seconds=args.active_seconds,
        session_limit=args.session_limit,
        history_messages=args.history_messages,
        end_stale=not args.keep_stale,
    )
    for response in responses:
        print(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay or sync OpenCode sessions into the CodeIsland daemon")
    parser.add_argument("--socket-path", default=default_socket_path())
    parser.add_argument("--input", help="Path to JSONL file containing mapped OpenCode hook events")
    parser.add_argument("--database", default=str(default_database_path()), help="OpenCode SQLite database path")
    parser.add_argument("--watch", action="store_true", help="Poll the OpenCode SQLite database and keep daemon sessions fresh")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--active-seconds", type=int, default=DEFAULT_DB_ACTIVE_SECONDS, help="How long a non-running OpenCode DB session remains visible; use 0 to import all unarchived sessions")
    parser.add_argument("--session-limit", type=int, default=DEFAULT_DB_SESSION_LIMIT, help="Maximum DB sessions to inspect per sync")
    parser.add_argument("--history-messages", type=int, default=DEFAULT_DB_HISTORY_MESSAGES, help="Message tail window per DB session")
    parser.add_argument("--keep-stale", action="store_true", help="Do not end stale opencode-db sessions in the daemon")
    args = parser.parse_args()

    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
