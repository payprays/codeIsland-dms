from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import socket
import stat
import struct
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .activation import SessionActivator
from .protocol import EventEnvelope, build_snapshot, default_socket_path, from_json_line, to_json_line
from .store import ContractError, InMemoryDaemonStore


MAX_REQUEST_BYTES = 65_536


class Phase0Daemon:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.store = InMemoryDaemonStore()
        self.activator = SessionActivator()
        self.subscribers: set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        socket_file = Path(self.socket_path)
        socket_file.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(socket_file.parent, 0o700)
        if socket_file.exists() or socket_file.is_symlink():
            self._safe_remove_socket_path(socket_file)
        self.server = await asyncio.start_unix_server(self._handle_client, path=self.socket_path)
        os.chmod(self.socket_path, 0o600)

    async def serve_forever(self) -> None:
        if self.server is None:
            raise RuntimeError("server not started")
        async with self.server:
            await self.server.serve_forever()

    async def shutdown(self) -> None:
        for writer in list(self.subscribers):
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        socket_file = Path(self.socket_path)
        if socket_file.exists():
            self._safe_remove_socket_path(socket_file)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if len(line) > MAX_REQUEST_BYTES:
                    await self._write_rpc_error(writer, None, "invalid_request", "request exceeds maximum size")
                    break
                try:
                    request = from_json_line(line)
                except (UnicodeDecodeError, ValueError, TypeError):
                    await self._write_rpc_error(writer, None, "invalid_request", "request must be a JSON object line")
                    continue
                if not isinstance(request, dict):
                    await self._write_rpc_error(writer, None, "invalid_request", "request must decode to an object")
                    continue
                await self._dispatch_request(request, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            self.subscribers.discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _dispatch_request(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        request_id: str | None = None
        try:
            request_id = self._optional_string(request.get("id"), field_name="id")
            method = request.get("method")
            params = request.get("params") or {}
            self._require_same_user(writer)
            if not isinstance(method, str) or not method:
                raise ContractError("invalid_request", "method must be a non-empty string")
            if not isinstance(params, dict):
                raise ContractError("invalid_request", "params must be an object when provided")
            if method == "ping":
                result = {
                    "socket_path": self.socket_path,
                    "capabilities": [
                        "ping",
                        "subscribe",
                        "list_sessions",
                        "get_session",
                        "create_task",
                        "cancel_task",
                        "retry_task",
                        "focus_session",
                        "interaction_respond",
                        "ingest_event",
                    ],
                }
                await self._write_rpc_result(writer, request_id, result)
            elif method == "subscribe":
                self.subscribers.add(writer)
                await self._write_event(writer, build_snapshot(
                    sessions=self.store.sessions,
                    tasks=self.store.tasks,
                    interactions=self.store.interactions,
                    activities=self.store.activities,
                    session_states=self.store.session_states,
                    next_seq=self.store.next_seq,
                ))
            elif method == "list_sessions":
                provider = params.get("provider")
                status = params.get("status")
                items = [asdict(item) for item in self.store.sessions.values() if (provider is None or item.provider == provider) and (status is None or item.status == status)]
                await self._write_rpc_result(writer, request_id, {"sessions": items})
            elif method == "get_session":
                session_id = self._required_string(params.get("session_id"), field_name="session_id")
                session = self.store.sessions.get(session_id)
                if session is None:
                    raise ContractError("unknown_session", f"unknown session: {session_id}")
                related_tasks = [asdict(item) for item in self.store.tasks.values() if item.session_id == session_id]
                related_interactions = [asdict(item) for item in self.store.interactions.values() if item.session_id == session_id]
                related_activities = [asdict(item) for item in self.store.activities if item.session_id == session_id]
                session_state = self.store.session_states.get(session_id)
                await self._write_rpc_result(writer, request_id, {
                    "session": asdict(session),
                    "tasks": related_tasks,
                    "interactions": related_interactions,
                    "activities": related_activities,
                    "session_state": asdict(session_state) if session_state is not None else None,
                })
            elif method == "focus_session":
                session_id = self._required_string(params.get("session_id"), field_name="session_id")
                session = self.store.sessions.get(session_id)
                if session is None:
                    raise ContractError("unknown_session", f"unknown session: {session_id}")
                await self._write_rpc_result(writer, request_id, self.activator.focus(session))
            elif method == "create_task":
                session_id = self._required_string(params.get("session_id"), field_name="session_id")
                prompt = self._optional_string(params.get("prompt"), field_name="prompt")
                task_id, patches = self.store.create_task(session_id=session_id, prompt=prompt)
                await self._write_rpc_result(writer, request_id, {"accepted": True, "task_id": task_id})
                await self._broadcast(patches)
            elif method == "cancel_task":
                task_id = self._required_string(params.get("task_id"), field_name="task_id")
                patches = self.store.cancel_task(task_id)
                await self._write_rpc_result(writer, request_id, {"accepted": True, "task_id": task_id})
                await self._broadcast(patches)
            elif method == "retry_task":
                task_id = self._required_string(params.get("task_id"), field_name="task_id")
                patches = self.store.retry_task(task_id)
                await self._write_rpc_result(writer, request_id, {"accepted": True, "task_id": task_id})
                await self._broadcast(patches)
            elif method == "interaction_respond":
                patches = self.store.respond_to_interaction(
                    interaction_id=self._required_string(params.get("interaction_id"), field_name="interaction_id"),
                    action=self._required_string(params.get("action"), field_name="action"),
                    answer=self._optional_string(params.get("answer"), field_name="answer"),
                )
                await self._write_rpc_result(writer, request_id, {"accepted": True})
                await self._broadcast(patches)
            elif method == "ingest_event":
                event = self._parse_event(params)
                patches = self.store.apply_event(event)
                await self._write_rpc_result(writer, request_id, {"accepted": True, "seq": event.seq})
                await self._broadcast(patches)
            else:
                raise ContractError("invalid_request", f"unknown method: {method}")
        except ContractError as exc:
            await self._write_rpc_error(writer, request_id, exc.code, exc.message)

    @staticmethod
    async def _write_event(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(to_json_line(payload))
        await writer.drain()

    async def _broadcast(self, payloads: list[dict[str, Any]]) -> None:
        if not self.subscribers:
            return
        dead_writers: list[asyncio.StreamWriter] = []
        for writer in list(self.subscribers):
            try:
                for payload in payloads:
                    writer.write(to_json_line(payload))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                dead_writers.append(writer)
        for writer in dead_writers:
            self.subscribers.discard(writer)

    async def _write_rpc_result(self, writer: asyncio.StreamWriter, request_id: str | None, result: dict[str, Any]) -> None:
        await self._write_event(writer, {"id": request_id, "ok": True, "result": result})

    async def _write_rpc_error(self, writer: asyncio.StreamWriter, request_id: str | None, code: str, message: str) -> None:
        await self._write_event(writer, {"id": request_id, "ok": False, "error": {"code": code, "message": message}})

    @staticmethod
    def _required_string(value: Any, *, field_name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ContractError("invalid_request", f"{field_name} must be a non-empty string")
        return value

    @staticmethod
    def _optional_string(value: Any, *, field_name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ContractError("invalid_request", f"{field_name} must be a string when provided")
        return value

    def _parse_event(self, params: dict[str, Any]) -> EventEnvelope:
        if not isinstance(params, dict):
            raise ContractError("invalid_request", "event params must be an object")
        event_id = self._required_string(params.get("event_id"), field_name="event_id")
        session_id = self._required_string(params.get("session_id"), field_name="session_id")
        kind = self._required_string(params.get("kind"), field_name="kind")
        payload = params.get("payload") or {}
        if not isinstance(payload, dict):
            raise ContractError("invalid_request", "payload must be an object")
        ts = self._optional_string(params.get("ts"), field_name="ts")
        task_id = self._optional_string(params.get("task_id"), field_name="task_id")
        return EventEnvelope(
            event_id=event_id,
            session_id=session_id,
            kind=kind,
            payload=payload,
            ts=ts or EventEnvelope.__dataclass_fields__["ts"].default_factory(),
            task_id=task_id,
        )

    @staticmethod
    def _safe_remove_socket_path(socket_file: Path) -> None:
        try:
            stat_result = socket_file.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(stat_result.st_mode):
            raise RuntimeError(f"refusing to remove non-socket path: {socket_file}")
        if stat_result.st_uid != os.getuid():
            raise RuntimeError(f"refusing to remove socket not owned by current user: {socket_file}")
        socket_file.unlink()

    @staticmethod
    def _require_same_user(writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None:
            raise ContractError("permission_denied", "missing peer socket information")
        peer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, _gid = struct.unpack("3i", peer)
        if pid <= 0 or uid != os.getuid():
            raise ContractError("permission_denied", "connection is not authorized for this daemon")


async def _main_async(socket_path: str) -> None:
    daemon = Phase0Daemon(socket_path)
    await daemon.start()
    try:
        await daemon.serve_forever()
    finally:
        await daemon.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 codeisland daemon skeleton")
    parser.add_argument("--socket-path", default=default_socket_path())
    args = parser.parse_args()
    asyncio.run(_main_async(args.socket_path))


if __name__ == "__main__":
    main()
