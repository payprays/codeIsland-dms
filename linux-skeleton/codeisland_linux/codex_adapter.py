from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import rpc_call
from .jsonl_tail import read_initial_line_entries as read_initial_jsonl_entries
from .jsonl_tail import read_jsonl, read_jsonl_chunk
from .live_watcher import (
    DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS,
    DEFAULT_STALE_SCAN_INTERVAL_SECONDS,
    IncrementalReplayCursor,
    InitialReplayCursor,
    LiveWatcherConfig,
    daemon_session_ids,
    end_daemon_sessions_not_running,
    running_keys_missing_from_daemon,
    watch_jsonl_sessions,
)
from .protocol import EventEnvelope, default_socket_path, now_iso


MAX_TEXT_CHARS = 360
DEFAULT_HISTORY_LINES = 120


@dataclass(slots=True)
class CodexFileContext:
    codex_session_id: str
    daemon_session_id: str
    project_root: str | None
    title: str
    terminal_app: str | None
    current_task_id: str | None = None
    call_names: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RunningCodexSession:
    path: Path | None
    pid: int
    cwd: str | None
    terminal_app: str | None
    terminal_pid: int | None = None
    terminal_pane: str | None = None
    terminal_socket: str | None = None
    synthetic_id: str | None = None


class CodexAdapterError(Exception):
    pass


class CodexSessionAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.contexts: dict[Path, CodexFileContext] = {}

    def translate_record(
        self,
        record: dict[str, Any],
        *,
        path: Path,
        line_number: int,
        terminal_app: str | None = None,
        running_session: RunningCodexSession | None = None,
    ) -> list[EventEnvelope]:
        if not isinstance(record, dict):
            raise CodexAdapterError("Codex JSONL record must be an object")

        record_type = record.get("type")
        timestamp = self._timestamp(record)

        if record_type == "session_meta":
            payload = self._payload(record)
            if self._is_subagent_session_payload(payload):
                self.contexts.pop(path, None)
                return []
            context = self._upsert_context(path, payload, terminal_app, running_session=running_session)
            return [
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "session.started"),
                    session_id=context.daemon_session_id,
                    kind="session.started",
                    payload={
                        "provider": "codex",
                        "source": "codex",
                        "title": context.title,
                        "project_root": context.project_root,
                        "terminal_app": context.terminal_app,
                        "codex_session_id": context.codex_session_id,
                        "cli_pid": running_session.pid if running_session is not None else payload.get("cli_pid"),
                        "terminal_pid": running_session.terminal_pid if running_session is not None else payload.get("terminal_pid"),
                        "terminal_pane": running_session.terminal_pane if running_session is not None else payload.get("terminal_pane"),
                        "terminal_socket": running_session.terminal_socket if running_session is not None else payload.get("terminal_socket"),
                        "cli_version": payload.get("cli_version"),
                    },
                    ts=timestamp,
                )
            ]

        context = self.contexts.get(path)
        if context is None:
            return []

        if record_type == "event_msg":
            return self._translate_event_msg(record, context=context, line_number=line_number, timestamp=timestamp)

        if record_type == "response_item":
            return self._translate_response_item(record, context=context, line_number=line_number, timestamp=timestamp)

        return []

    def translate_lines(
        self,
        lines: list[str],
        *,
        path: Path,
        start_line: int = 1,
        terminal_app: str | None = None,
        running_session: RunningCodexSession | None = None,
    ) -> list[EventEnvelope]:
        return self.translate_line_entries(
            list(enumerate(lines, start=start_line)),
            path=path,
            terminal_app=terminal_app,
            running_session=running_session,
        )

    def translate_line_entries(
        self,
        entries: list[tuple[int, str]],
        *,
        path: Path,
        terminal_app: str | None = None,
        running_session: RunningCodexSession | None = None,
    ) -> list[EventEnvelope]:
        events: list[EventEnvelope] = []
        for line_number, line in entries:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexAdapterError(f"invalid Codex JSONL at line {line_number}: {exc.msg}") from exc
            events.extend(self.translate_record(record, path=path, line_number=line_number, terminal_app=terminal_app, running_session=running_session))
        return events

    def read_jsonl(self, path: Path) -> list[str]:
        return read_jsonl(path)

    def read_initial_line_entries(self, path: Path, *, history_lines: int = DEFAULT_HISTORY_LINES) -> tuple[list[tuple[int, str]], int, int]:
        return read_initial_jsonl_entries(
            path,
            history_lines=history_lines,
            anchor_entries=self._anchor_line_entries,
            include_first_nonempty=True,
        )

    def read_jsonl_chunk(self, path: Path, *, offset: int, pending: str = "") -> tuple[list[str], int, str, bool]:
        return read_jsonl_chunk(path, offset=offset, pending=pending)

    async def replay_jsonl(
        self,
        socket_path: str,
        input_path: Path,
        *,
        terminal_app: str | None = None,
        history_lines: int = DEFAULT_HISTORY_LINES,
    ) -> list[dict[str, Any]]:
        lines = self.read_jsonl(input_path)
        return await self._send_events(socket_path, self.translate_initial_lines(lines, path=input_path, terminal_app=terminal_app, history_lines=history_lines))

    def translate_initial_lines(
        self,
        lines: list[str],
        *,
        path: Path,
        terminal_app: str | None = None,
        running_session: RunningCodexSession | None = None,
        history_lines: int = DEFAULT_HISTORY_LINES,
    ) -> list[EventEnvelope]:
        if history_lines <= 0 or len(lines) <= history_lines:
            return self.translate_lines(lines, path=path, terminal_app=terminal_app, running_session=running_session)

        events = self.translate_lines(lines[:1], path=path, start_line=1, terminal_app=terminal_app, running_session=running_session)
        tail_start = len(lines) - history_lines + 1
        for line_number in self._anchor_line_numbers(lines, tail_start=tail_start):
            events.extend(self.translate_lines([lines[line_number - 1]], path=path, start_line=line_number, terminal_app=terminal_app))
        events.extend(self.translate_lines(lines[tail_start - 1:], path=path, start_line=tail_start, terminal_app=terminal_app))
        return events

    async def replay_running_once(self, socket_path: str, codex_home: Path, *, history_lines: int = DEFAULT_HISTORY_LINES) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        running_sessions = discover_running_codex_sessions(codex_home=codex_home)
        await self._end_daemon_sessions_not_running(socket_path, running_sessions)
        for session in running_sessions:
            if session.path is not None:
                responses.extend(await self._replay_running_session(socket_path, session, history_lines=history_lines))
            responses.extend(await self._send_running_session_started(socket_path, session))
        return responses

    async def watch_running(
        self,
        socket_path: str,
        codex_home: Path,
        *,
        interval: float,
        history_lines: int = DEFAULT_HISTORY_LINES,
        stale_scan_interval: float = DEFAULT_STALE_SCAN_INTERVAL_SECONDS,
        daemon_refresh_interval: float = DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        await watch_jsonl_sessions(
            self,
            socket_path,
            codex_home,
            LiveWatcherConfig(
                interval=interval,
                history_lines=history_lines,
                stale_scan_interval=stale_scan_interval,
                daemon_refresh_interval=daemon_refresh_interval,
            ),
        )

    def discover_running_sessions(self, home: Path) -> list[RunningCodexSession]:
        return discover_running_codex_sessions(codex_home=home)

    def running_session_key(self, session: RunningCodexSession) -> str:
        return self._running_session_key(session)

    @staticmethod
    def running_session_path(session: RunningCodexSession) -> Path | None:
        return session.path

    def path_from_running_key(self, session_key: str) -> Path | None:
        return self._path_from_running_key(session_key)

    def daemon_session_id_for_running(self, session: RunningCodexSession) -> str:
        return self._daemon_session_id_for_running(session)

    def reset_path_context(self, path: Path) -> None:
        self.contexts.pop(path, None)

    async def send_running_session_started(self, socket_path: str, session: RunningCodexSession) -> list[dict[str, Any]]:
        return await self._send_running_session_started(socket_path, session)

    async def send_session_ended(self, socket_path: str, session_id: str) -> list[dict[str, Any]]:
        return await self._send_session_ended(socket_path, session_id)

    async def replay_initial_file(
        self,
        socket_path: str,
        session: RunningCodexSession,
        *,
        history_lines: int,
    ) -> InitialReplayCursor:
        if session.path is None:
            await self._send_running_session_started(socket_path, session)
            return InitialReplayCursor(
                session_id=self._daemon_session_id_for_running(session),
                next_line=1,
                file_offset=0,
            )
        entries, file_offset, total_lines = self.read_initial_line_entries(session.path, history_lines=history_lines)
        events = self.translate_line_entries(
            entries,
            path=session.path,
            terminal_app=session.terminal_app,
            running_session=session,
        )
        await self._send_events(socket_path, events)
        context = self.contexts.get(session.path)
        session_id = context.daemon_session_id if context is not None else self._daemon_session_id_for_running(session)
        await self._send_running_session_started(socket_path, session)
        return InitialReplayCursor(
            session_id=session_id,
            next_line=total_lines + 1,
            file_offset=file_offset,
        )

    async def replay_incremental_file(
        self,
        socket_path: str,
        session: RunningCodexSession,
        *,
        start_line: int,
        lines: list[str],
    ) -> IncrementalReplayCursor:
        if session.path is None:
            await self._send_running_session_started(socket_path, session)
            return IncrementalReplayCursor(
                session_id=self._daemon_session_id_for_running(session),
                next_line=start_line,
            )
        events = self.translate_lines(
            lines,
            path=session.path,
            start_line=start_line,
            terminal_app=session.terminal_app,
        )
        await self._send_events(socket_path, events)
        context = self.contexts.get(session.path)
        session_id = context.daemon_session_id if context is not None else self._daemon_session_id_for_running(session)
        return IncrementalReplayCursor(session_id=session_id, next_line=start_line + len(lines))

    async def _replay_running_session(self, socket_path: str, session: RunningCodexSession, *, history_lines: int) -> list[dict[str, Any]]:
        if session.path is None:
            return await self._send_running_session_started(socket_path, session)
        entries, _offset, _total_lines = self.read_initial_line_entries(session.path, history_lines=history_lines)
        events = self.translate_line_entries(
            entries,
            path=session.path,
            terminal_app=session.terminal_app,
            running_session=session,
        )
        return await self._send_events(socket_path, events)

    async def _daemon_session_ids(self, socket_path: str) -> set[str]:
        return await daemon_session_ids(socket_path, self.provider, request_id="codex-session-refresh")

    async def _end_daemon_sessions_not_running(self, socket_path: str, running_sessions: list[RunningCodexSession]) -> set[str]:
        return await end_daemon_sessions_not_running(
            socket_path,
            self.provider,
            {self._daemon_session_id_for_running(session) for session in running_sessions},
            send_session_ended=self.send_session_ended,
        )

    def _running_keys_missing_from_daemon(self, running_sessions: list[RunningCodexSession], daemon_session_ids: set[str]) -> set[str]:
        return running_keys_missing_from_daemon(self, running_sessions, daemon_session_ids)

    async def _send_running_session_started(self, socket_path: str, session: RunningCodexSession) -> list[dict[str, Any]]:
        if session.path is not None and self._is_subagent_session_path(session.path):
            return []
        session_id = self._daemon_session_id_for_running(session)
        codex_session_id = self._codex_session_id_for_running(session)
        title = Path(session.cwd).name if session.cwd else session_id
        digest = hashlib.sha256(
            json.dumps(
                {
                    "session_id": session_id,
                    "pid": session.pid,
                    "path": str(session.path) if session.path is not None else "",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return await self._send_events(socket_path, [
            EventEnvelope(
                event_id=f"evt_codex_alive_{digest}",
                session_id=session_id,
                kind="session.started",
                payload={
                    "provider": "codex",
                    "source": "codex",
                    "title": title,
                    "project_root": session.cwd,
                    "terminal_app": session.terminal_app,
                    "codex_session_id": codex_session_id,
                    "cli_pid": session.pid,
                    "terminal_pid": session.terminal_pid,
                    "terminal_pane": session.terminal_pane,
                    "terminal_socket": session.terminal_socket,
                    "synthetic": session.path is None,
                },
                ts=now_iso(),
            )
        ])

    async def _send_session_ended(self, socket_path: str, session_id: str) -> list[dict[str, Any]]:
        timestamp = now_iso()
        digest = hashlib.sha256(f"{session_id}:{timestamp}".encode("utf-8")).hexdigest()[:16]
        return await self._send_events(socket_path, [
            EventEnvelope(
                event_id=f"evt_codex_end_{digest}",
                session_id=session_id,
                kind="session.ended",
                payload={"reason": "process_closed"},
                ts=timestamp,
            )
        ])

    async def _send_events(self, socket_path: str, events: list[EventEnvelope]) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        for index, event in enumerate(events, start=1):
            response = await rpc_call(
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
                request_id=f"codex-{index}",
            )
            responses.append(response)
        return responses

    def _translate_event_msg(
        self,
        record: dict[str, Any],
        *,
        context: CodexFileContext,
        line_number: int,
        timestamp: str,
    ) -> list[EventEnvelope]:
        payload = self._payload(record)
        event_type = payload.get("type")

        if event_type == "task_started":
            turn_id = self._string(payload.get("turn_id")) or f"line-{line_number}"
            task_id = self._task_id(context, turn_id)
            context.current_task_id = task_id
            return [
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "task.started"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="task.started",
                    payload={
                        "task_id": task_id,
                        "turn_id": turn_id,
                        "model_context_window": payload.get("model_context_window"),
                        "collaboration_mode": payload.get("collaboration_mode_kind"),
                    },
                    ts=self._timestamp_from_epoch(payload.get("started_at")) or timestamp,
                )
            ]

        if event_type == "user_message":
            message = self._string(payload.get("message"))
            if not message:
                return []
            events: list[EventEnvelope] = []
            task_id = context.current_task_id
            if task_id is None:
                task_id = self._fallback_task(context, line_number)
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(context, line_number, "task.started"),
                        session_id=context.daemon_session_id,
                        task_id=task_id,
                        kind="task.started",
                        payload={"task_id": task_id, "prompt": self._trim(message), "synthetic": True},
                        ts=timestamp,
                    )
                )
            context.current_task_id = task_id
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "prompt.submitted"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="prompt.submitted",
                    payload={"task_id": task_id, "prompt": self._trim(message)},
                    ts=timestamp,
                )
            )
            return events

        if event_type == "agent_message":
            message = self._string(payload.get("message"))
            task_id = context.current_task_id
            if not message:
                return []
            events: list[EventEnvelope] = []
            if task_id is None:
                task_id = self._fallback_task(context, line_number)
                context.current_task_id = task_id
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(context, line_number, "task.started"),
                        session_id=context.daemon_session_id,
                        task_id=task_id,
                        kind="task.started",
                        payload={"task_id": task_id, "synthetic": True},
                        ts=timestamp,
                    )
                )
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "assistant.response.completed"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="assistant.response.completed",
                    payload={"task_id": task_id, "message": self._trim(message), "phase": payload.get("phase")},
                    ts=timestamp,
                )
            )
            return events

        if event_type == "task_complete":
            task_id = context.current_task_id
            summary = self._string(payload.get("last_agent_message"))
            events: list[EventEnvelope] = []
            if task_id is None:
                task_id = self._fallback_task(context, line_number)
                context.current_task_id = task_id
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(context, line_number, "task.started"),
                        session_id=context.daemon_session_id,
                        task_id=task_id,
                        kind="task.started",
                        payload={"task_id": task_id, "prompt": self._trim(summary) if summary else None, "synthetic": True},
                        ts=timestamp,
                    )
                )
            if summary:
                events.append(
                    EventEnvelope(
                        event_id=self._event_id(context, line_number, "assistant.response.completed"),
                        session_id=context.daemon_session_id,
                        task_id=task_id,
                        kind="assistant.response.completed",
                        payload={"task_id": task_id, "message": self._trim(summary), "phase": "final_answer"},
                        ts=timestamp,
                    )
                )
            events.append(
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "task.completed"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="task.completed",
                    payload={"task_id": task_id, "summary": self._trim(summary) if summary else None},
                    ts=self._timestamp_from_epoch(payload.get("completed_at")) or timestamp,
                )
            )
            context.current_task_id = None
            return events

        return []

    def _translate_response_item(
        self,
        record: dict[str, Any],
        *,
        context: CodexFileContext,
        line_number: int,
        timestamp: str,
    ) -> list[EventEnvelope]:
        payload = self._payload(record)
        item_type = payload.get("type")
        task_id = context.current_task_id
        if task_id is None:
            return []

        if item_type in {"function_call", "custom_tool_call"}:
            call_id = self._string(payload.get("call_id")) or f"line-{line_number}"
            tool_name = self._string(payload.get("name")) or item_type
            context.call_names[call_id] = tool_name
            return [
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "tool.use.started"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="tool.use.started",
                    payload={
                        "task_id": task_id,
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "input": self._trim(self._string(payload.get("arguments") or payload.get("input"))),
                    },
                    ts=timestamp,
                )
            ]

        if item_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = self._string(payload.get("call_id")) or f"line-{line_number}"
            tool_name = context.call_names.get(call_id, item_type)
            output = payload.get("output")
            failed = self._tool_output_failed(output)
            return [
                EventEnvelope(
                    event_id=self._event_id(context, line_number, "tool.use.failed" if failed else "tool.use.completed"),
                    session_id=context.daemon_session_id,
                    task_id=task_id,
                    kind="tool.use.failed" if failed else "tool.use.completed",
                    payload={
                        "task_id": task_id,
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "result": self._trim(self._string(output)),
                        "success": not failed,
                    },
                    ts=timestamp,
                )
            ]

        return []

    @classmethod
    def _anchor_line_numbers(cls, lines: list[str], *, tail_start: int) -> list[int]:
        latest_user_line: int | None = None
        latest_task_line: int | None = None
        for line_number in range(tail_start - 1, 1, -1):
            event_type = cls._event_msg_type(lines[line_number - 1])
            if latest_user_line is None and event_type == "user_message":
                latest_user_line = line_number
                continue
            if latest_user_line is not None and event_type == "task_started":
                latest_task_line = line_number
                break
        return sorted({line for line in (latest_task_line, latest_user_line) if line is not None})

    @classmethod
    def _anchor_line_entries(cls, entries: list[tuple[int, str]], *, tail_start: int) -> list[tuple[int, str]]:
        latest_user_entry: tuple[int, str] | None = None
        latest_task_entry: tuple[int, str] | None = None
        for line_number, line in reversed(entries):
            if line_number >= tail_start:
                continue
            event_type = cls._event_msg_type(line)
            if latest_user_entry is None and event_type == "user_message":
                latest_user_entry = (line_number, line)
                continue
            if latest_user_entry is not None and event_type == "task_started":
                latest_task_entry = (line_number, line)
                break
        return sorted(entry for entry in (latest_task_entry, latest_user_entry) if entry is not None)

    @staticmethod
    def _event_msg_type(line: str) -> str | None:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        value = payload.get("type")
        return value if isinstance(value, str) else None

    def _upsert_context(
        self,
        path: Path,
        payload: dict[str, Any],
        terminal_app: str | None,
        *,
        running_session: RunningCodexSession | None = None,
    ) -> CodexFileContext:
        codex_session_id = self._string(payload.get("id"))
        if not codex_session_id:
            raise CodexAdapterError("session_meta payload requires id")
        project_root = running_session.cwd if running_session is not None and running_session.cwd else self._string(payload.get("cwd"))
        title = Path(project_root).name if project_root else codex_session_id
        context = CodexFileContext(
            codex_session_id=codex_session_id,
            daemon_session_id=self._daemon_session_id(codex_session_id),
            project_root=project_root,
            title=title or codex_session_id,
            terminal_app=terminal_app,
        )
        self.contexts[path] = context
        return context

    @classmethod
    def _session_id_from_jsonl(cls, path: Path) -> str | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        return None
                    if not isinstance(record, dict) or record.get("type") != "session_meta":
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        return None
                    if cls._is_subagent_session_payload(payload):
                        return None
                    return cls._string(payload.get("id"))
        except OSError:
            return None
        return None

    @classmethod
    def _is_subagent_session_path(cls, path: Path) -> bool:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        return False
                    if not isinstance(record, dict) or record.get("type") != "session_meta":
                        continue
                    payload = record.get("payload")
                    return isinstance(payload, dict) and cls._is_subagent_session_payload(payload)
        except OSError:
            return False
        return False

    @staticmethod
    def _is_subagent_session_payload(payload: dict[str, Any]) -> bool:
        if payload.get("thread_source") == "subagent":
            return True
        source = payload.get("source")
        return isinstance(source, dict) and isinstance(source.get("subagent"), dict)

    @staticmethod
    def _running_session_key(session: RunningCodexSession) -> str:
        if session.path is not None:
            return f"file:{session.path}"
        return f"proc:{session.pid}"

    @staticmethod
    def _path_from_running_key(session_key: str) -> Path | None:
        if not session_key.startswith("file:"):
            return None
        return Path(session_key[5:])

    def _codex_session_id_for_running(self, session: RunningCodexSession) -> str:
        if session.path is not None:
            codex_session_id = self._session_id_from_jsonl(session.path)
            if codex_session_id:
                return codex_session_id
        return session.synthetic_id or f"proc-{session.pid}"

    def _daemon_session_id_for_running(self, session: RunningCodexSession) -> str:
        return self._daemon_session_id(self._codex_session_id_for_running(session))

    @staticmethod
    def _payload(record: dict[str, Any]) -> dict[str, Any]:
        payload = record.get("payload") or {}
        return payload if isinstance(payload, dict) else {}

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
    def _daemon_session_id(codex_session_id: str) -> str:
        return codex_session_id if codex_session_id.startswith("codex-") else f"codex-{codex_session_id}"

    @staticmethod
    def _task_id(context: CodexFileContext, turn_id: str) -> str:
        return f"{context.daemon_session_id}-turn-{turn_id}"

    def _fallback_task(self, context: CodexFileContext, line_number: int) -> str:
        return self._task_id(context, f"line-{line_number}")

    @staticmethod
    def _timestamp(record: dict[str, Any]) -> str:
        value = record.get("timestamp")
        if isinstance(value, str) and value:
            return value
        return now_iso()

    @staticmethod
    def _timestamp_from_epoch(value: Any) -> str | None:
        if not isinstance(value, (int, float)):
            return None
        return datetime.fromtimestamp(value, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _event_id(context: CodexFileContext, line_number: int, normalized_kind: str) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "session_id": context.daemon_session_id,
                    "line_number": line_number,
                    "kind": normalized_kind,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"evt_codex_{digest}"

    @staticmethod
    def _tool_output_failed(output: Any) -> bool:
        text = output if isinstance(output, str) else ""
        if text:
            match = re.search(r"Process exited with code (-?\d+)", text)
            if match:
                return int(match.group(1)) != 0
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                metadata = decoded.get("metadata")
                if isinstance(metadata, dict):
                    exit_code = metadata.get("exit_code")
                    if isinstance(exit_code, int):
                        return exit_code != 0
        return False


def discover_running_codex_sessions(*, codex_home: Path) -> list[RunningCodexSession]:
    sessions: dict[str, RunningCodexSession] = {}
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit() or not _is_codex_process(proc_dir):
            continue
        pid = int(proc_dir.name)
        env = _read_environ(proc_dir)
        terminal_app = _terminal_app(env)
        terminal_socket = _terminal_socket(env)
        terminal_pid = _terminal_pid(env)
        terminal_pane = _terminal_pane(env)
        cwd = _readlink_text(proc_dir / "cwd")
        fd_dir = proc_dir / "fd"
        try:
            fd_paths = list(fd_dir.iterdir())
        except OSError:
            continue
        session_paths: list[Path] = []
        seen_session_paths: set[Path] = set()
        saw_subagent_session = False
        for fd_path in fd_paths:
            target = _readlink_text(fd_path)
            if not target:
                continue
            session_path = Path(target.removesuffix(" (deleted)"))
            if not _is_codex_rollout_path(session_path, codex_home):
                continue
            if CodexSessionAdapter._is_subagent_session_path(session_path):
                saw_subagent_session = True
                continue
            if session_path in seen_session_paths:
                continue
            seen_session_paths.add(session_path)
            session_paths.append(session_path)
        if session_paths:
            session_path = _active_codex_rollout_path(session_paths)
            if session_path is None:
                continue
            _set_preferred_running_session(
                sessions,
                RunningCodexSession(
                    path=session_path,
                    pid=pid,
                    cwd=cwd,
                    terminal_app=terminal_app,
                    terminal_pid=terminal_pid,
                    terminal_pane=terminal_pane,
                    terminal_socket=terminal_socket,
                ),
            )
            continue
        if saw_subagent_session:
            continue
        _set_preferred_running_session(
            sessions,
            RunningCodexSession(
                path=None,
                pid=pid,
                cwd=cwd,
                terminal_app=terminal_app,
                terminal_pid=terminal_pid,
                terminal_pane=terminal_pane,
                terminal_socket=terminal_socket,
                synthetic_id=f"proc-{pid}",
            ),
        )
    return sorted(sessions.values(), key=lambda item: str(item.path) if item.path is not None else f"proc:{item.pid}")


def _set_preferred_running_session(sessions: dict[str, RunningCodexSession], candidate: RunningCodexSession) -> None:
    identity_key = _running_codex_identity_key(candidate)
    existing = sessions.get(identity_key)
    if existing is None or _running_codex_preference_key(candidate) > _running_codex_preference_key(existing):
        sessions[identity_key] = candidate


def _running_codex_identity_key(session: RunningCodexSession) -> str:
    if session.terminal_socket and session.terminal_pane:
        return f"terminal:{session.terminal_socket}:{session.terminal_pane}"
    if session.terminal_pid is not None and session.terminal_pane:
        return f"terminal:{session.terminal_pid}:{session.terminal_pane}"
    return f"proc:{session.pid}"


def _running_codex_preference_key(session: RunningCodexSession) -> tuple[int, int, int, str]:
    if session.path is None:
        return (0, 0, session.pid, "")
    modified_at, size = _rollout_file_freshness(session.path)
    return (1, modified_at, size, str(session.path))


def _active_codex_rollout_path(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return max(paths, key=lambda path: (*_rollout_file_freshness(path), str(path)))


def _rollout_file_freshness(path: Path) -> tuple[int, int]:
    try:
        stat_result = path.stat()
    except OSError:
        return (-1, -1)
    return (stat_result.st_mtime_ns, stat_result.st_size)


def _is_codex_process(proc_dir: Path) -> bool:
    comm = _read_text(proc_dir / "comm")
    if comm and comm.strip() == "codex":
        return True
    cmdline = _read_text(proc_dir / "cmdline")
    if not cmdline:
        return False
    first = cmdline.split("\0", 1)[0]
    return Path(first).name == "codex"


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
    if socket_path:
        match = re.search(r"gui-sock-(\d+)(?:$|/)", socket_path)
        if match:
            return int(match.group(1))
    return None


def _is_codex_rollout_path(path: Path, codex_home: Path) -> bool:
    if path.suffix != ".jsonl" or not path.name.startswith("rollout-"):
        return False
    try:
        path.relative_to(codex_home / "sessions")
    except ValueError:
        return False
    return path.exists()


def _is_codex_session_path(path: Path, codex_home: Path) -> bool:
    return _is_codex_rollout_path(path, codex_home) and not CodexSessionAdapter._is_subagent_session_path(path)


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


async def _main_async(args: argparse.Namespace) -> None:
    adapter = CodexSessionAdapter()
    codex_home = Path(args.codex_home).expanduser()
    if args.input:
        responses: list[dict[str, Any]] = []
        for input_path in args.input:
            responses.extend(
                await adapter.replay_jsonl(
                    args.socket_path,
                    Path(input_path).expanduser(),
                    terminal_app=args.terminal_app,
                    history_lines=args.history_lines,
                )
            )
        for response in responses:
            print(response)
        return

    if args.watch:
        await adapter.watch_running(
            args.socket_path,
            codex_home,
            interval=args.interval,
            history_lines=args.history_lines,
            stale_scan_interval=args.stale_scan_interval,
            daemon_refresh_interval=args.daemon_refresh_interval,
        )
        return

    responses = await adapter.replay_running_once(args.socket_path, codex_home, history_lines=args.history_lines)
    for response in responses:
        print(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay live Codex session JSONL into the CodeIsland daemon")
    parser.add_argument("--socket-path", default=default_socket_path())
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", "~/.codex"))
    parser.add_argument("--input", action="append", help="Replay an explicit Codex rollout JSONL file")
    parser.add_argument("--terminal-app", help="Terminal label to attach when replaying --input")
    parser.add_argument("--watch", action="store_true", help="Continue polling currently running Codex session files")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--history-lines", type=int, default=DEFAULT_HISTORY_LINES, help="Initial JSONL tail window per session; use 0 to replay all history")
    parser.add_argument(
        "--stale-scan-interval",
        type=float,
        default=DEFAULT_STALE_SCAN_INTERVAL_SECONDS,
        help="Seconds between daemon stale-session cleanup passes while watching; use 0 to scan every poll",
    )
    parser.add_argument(
        "--daemon-refresh-interval",
        type=float,
        default=DEFAULT_DAEMON_REFRESH_INTERVAL_SECONDS,
        help="Seconds between checks that running sessions still exist in the daemon after daemon restarts",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
