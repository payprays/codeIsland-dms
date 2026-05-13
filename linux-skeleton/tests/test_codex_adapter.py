from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeisland_linux.codex_adapter import (
    CodexSessionAdapter,
    RunningCodexSession,
    _active_codex_rollout_path,
    _is_codex_session_path,
    _set_preferred_running_session,
)
from codeisland_linux.protocol import EventEnvelope, now_iso
from codeisland_linux.server import Phase0Daemon
from codeisland_linux.store import InMemoryDaemonStore


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class CodexAdapterTests(unittest.TestCase):
    def test_rollout_sample_maps_to_normalized_events(self) -> None:
        adapter = CodexSessionAdapter()
        input_path = FIXTURES_DIR / "codex_rollout_sample.jsonl"

        events = adapter.translate_lines(
            adapter.read_jsonl(input_path),
            path=input_path,
            terminal_app="WezTerm",
        )

        self.assertEqual(
            [event.kind for event in events],
            [
                "session.started",
                "task.started",
                "prompt.submitted",
                "tool.use.started",
                "tool.use.completed",
                "assistant.response.completed",
                "assistant.response.completed",
                "task.completed",
            ],
        )
        self.assertEqual(events[0].session_id, "codex-019e1032-bf9d-7651-abf5-521defb1b7b9")
        self.assertEqual(events[0].payload["provider"], "codex")
        self.assertEqual(events[0].payload["terminal_app"], "WezTerm")
        self.assertEqual(events[2].payload["prompt"], "Wire Codex into CodeIsland")
        self.assertEqual(events[3].payload["tool_name"], "exec_command")

    def test_running_session_metadata_reaches_session_started(self) -> None:
        adapter = CodexSessionAdapter()
        input_path = FIXTURES_DIR / "codex_rollout_sample.jsonl"

        events = adapter.translate_initial_lines(
            adapter.read_jsonl(input_path),
            path=input_path,
            terminal_app="WezTerm",
            running_session=RunningCodexSession(
                path=input_path,
                pid=1234,
                cwd="/tmp/project",
                terminal_app="WezTerm",
                terminal_pid=5678,
                terminal_pane="9",
                terminal_socket="/run/user/1000/wezterm/gui-sock-5678",
            ),
        )

        self.assertEqual(events[0].payload["title"], "project")
        self.assertEqual(events[0].payload["project_root"], "/tmp/project")
        self.assertEqual(events[0].payload["cli_pid"], 1234)
        self.assertEqual(events[0].payload["terminal_pid"], 5678)
        self.assertEqual(events[0].payload["terminal_pane"], "9")
        self.assertEqual(events[0].payload["terminal_socket"], "/run/user/1000/wezterm/gui-sock-5678")

    def test_rollout_sample_reaches_completed_store_state(self) -> None:
        adapter = CodexSessionAdapter()
        input_path = FIXTURES_DIR / "codex_rollout_sample.jsonl"
        store = InMemoryDaemonStore()

        for event in adapter.translate_lines(adapter.read_jsonl(input_path), path=input_path, terminal_app="WezTerm"):
            store.apply_event(event)

        session_id = "codex-019e1032-bf9d-7651-abf5-521defb1b7b9"
        session = store.sessions[session_id]
        state = store.session_states[session_id]

        self.assertEqual(session.provider, "codex")
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.terminal_app, "WezTerm")
        self.assertEqual(state.last_user_prompt, "Wire Codex into CodeIsland")
        self.assertEqual(state.last_assistant_message, "Done.")
        self.assertTrue(state.completion_pending)

    def test_event_ids_are_stable_across_replays(self) -> None:
        input_path = FIXTURES_DIR / "codex_rollout_sample.jsonl"
        first = CodexSessionAdapter().translate_lines(
            input_path.read_text(encoding="utf-8").splitlines(),
            path=input_path,
        )
        second = CodexSessionAdapter().translate_lines(
            input_path.read_text(encoding="utf-8").splitlines(),
            path=input_path,
        )

        self.assertEqual([event.event_id for event in first], [event.event_id for event in second])

    def test_trims_large_text_payloads(self) -> None:
        adapter = CodexSessionAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-test.jsonl"
            long_prompt = "x" * 1000
            lines = [
                '{"timestamp":"2026-05-10T00:00:00Z","type":"session_meta","payload":{"id":"sid","cwd":"/tmp/project"}}',
                '{"timestamp":"2026-05-10T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}',
                '{"timestamp":"2026-05-10T00:00:02Z","type":"event_msg","payload":{"type":"user_message","message":"' + long_prompt + '"}}',
            ]
            path.write_text("\n".join(lines), encoding="utf-8")

            events = adapter.translate_lines(adapter.read_jsonl(path), path=path)

        self.assertLessEqual(len(events[-1].payload["prompt"]), 600)
        self.assertTrue(events[-1].payload["prompt"].endswith("..."))

    def test_initial_history_window_keeps_session_meta(self) -> None:
        adapter = CodexSessionAdapter()
        input_path = FIXTURES_DIR / "codex_rollout_sample.jsonl"

        events = adapter.translate_initial_lines(
            adapter.read_jsonl(input_path),
            path=input_path,
            terminal_app="WezTerm",
            history_lines=2,
        )

        self.assertEqual(events[0].kind, "session.started")
        self.assertEqual(events[1].kind, "task.started")
        self.assertEqual(events[2].kind, "prompt.submitted")
        self.assertEqual(events[2].payload["prompt"], "Wire Codex into CodeIsland")
        self.assertEqual(events[-1].kind, "task.completed")

    def test_initial_file_window_streams_tail_with_real_line_numbers(self) -> None:
        adapter = CodexSessionAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-large.jsonl"
            records = [
                {"timestamp": "2026-05-10T00:00:00Z", "type": "session_meta", "payload": {"id": "sid", "cwd": "/tmp/project"}},
                {"timestamp": "2026-05-10T00:00:01Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "old"}},
                {"timestamp": "2026-05-10T00:00:02Z", "type": "event_msg", "payload": {"type": "user_message", "message": "old prompt"}},
            ]
            for index in range(40):
                records.append({"timestamp": "2026-05-10T00:00:03Z", "type": "event_msg", "payload": {"type": "token_count", "index": index}})
            records.extend([
                {"timestamp": "2026-05-10T00:01:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "current"}},
                {"timestamp": "2026-05-10T00:01:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "latest prompt"}},
            ])
            for index in range(20):
                records.append({"timestamp": "2026-05-10T00:01:02Z", "type": "event_msg", "payload": {"type": "token_count", "index": index}})
            records.extend([
                {"timestamp": "2026-05-10T00:01:03Z", "type": "response_item", "payload": {"type": "function_call", "call_id": "call-1", "name": "exec_command", "arguments": "{}"}},
                {"timestamp": "2026-05-10T00:01:04Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "ok"}},
                {"timestamp": "2026-05-10T00:01:05Z", "type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "done"}},
            ])
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

            entries, offset, total_lines = adapter.read_initial_line_entries(path, history_lines=3)
            events = adapter.translate_line_entries(entries, path=path, terminal_app="WezTerm")
            expected_size = path.stat().st_size

        entry_numbers = [line_number for line_number, _line in entries]
        self.assertEqual(total_lines, len(records))
        self.assertEqual(offset, expected_size)
        self.assertEqual(entry_numbers[0], 1)
        self.assertIn(44, entry_numbers)
        self.assertIn(45, entry_numbers)
        self.assertEqual(entry_numbers[-3:], [66, 67, 68])
        self.assertEqual([event.kind for event in events], [
            "session.started",
            "task.started",
            "prompt.submitted",
            "tool.use.started",
            "tool.use.completed",
            "assistant.response.completed",
            "task.completed",
        ])
        self.assertEqual(events[2].payload["prompt"], "latest prompt")

    def test_jsonl_chunk_waits_for_complete_appended_line(self) -> None:
        adapter = CodexSessionAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-live.jsonl"
            path.write_text('{"type":"session_meta","payload":{"id":"sid"}}\n', encoding="utf-8")
            offset = path.stat().st_size
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"type":"event_msg","payload":{"type":"user_message","message":"partial"}}')

            lines, offset, pending, reset = adapter.read_jsonl_chunk(path, offset=offset)
            self.assertEqual(lines, [])
            self.assertFalse(reset)
            self.assertIn("partial", pending)

            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n")

            lines, _offset, pending, reset = adapter.read_jsonl_chunk(path, offset=offset, pending=pending)
            self.assertEqual(len(lines), 1)
            self.assertFalse(reset)
            self.assertEqual(pending, "")
            self.assertIn("user_message", lines[0])

    def test_subagent_rollout_is_not_translated_as_top_level_session(self) -> None:
        adapter = CodexSessionAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-subagent.jsonl"
            lines = [
                {
                    "timestamp": "2026-05-10T00:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "child",
                        "cwd": "/tmp/project",
                        "thread_source": "subagent",
                        "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}},
                    },
                },
                {"timestamp": "2026-05-10T00:00:01Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn"}},
                {"timestamp": "2026-05-10T00:00:02Z", "type": "event_msg", "payload": {"type": "user_message", "message": "child work"}},
            ]
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

            events = adapter.translate_lines(adapter.read_jsonl(path), path=path)

        self.assertEqual(events, [])

    def test_subagent_rollout_path_is_not_a_live_codex_session_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / "codex-home"
            session_dir = codex_home / "sessions" / "2026" / "05" / "10"
            session_dir.mkdir(parents=True)
            top_level = session_dir / "rollout-top.jsonl"
            subagent = session_dir / "rollout-subagent.jsonl"
            top_level.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "top", "cwd": "/tmp/project"}}) + "\n",
                encoding="utf-8",
            )
            subagent.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": "child", "cwd": "/tmp/project", "thread_source": "subagent"}}) + "\n",
                encoding="utf-8",
            )

            self.assertTrue(_is_codex_session_path(top_level, codex_home))
            self.assertFalse(_is_codex_session_path(subagent, codex_home))

    def test_active_rollout_path_uses_latest_open_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_path = Path(temp_dir) / "rollout-old.jsonl"
            new_path = Path(temp_dir) / "rollout-new.jsonl"
            old_path.write_text('{"type":"session_meta","payload":{"id":"old"}}\n', encoding="utf-8")
            new_path.write_text('{"type":"session_meta","payload":{"id":"new"}}\n', encoding="utf-8")
            os.utime(old_path, ns=(100, 100))
            os.utime(new_path, ns=(200, 200))

            self.assertEqual(_active_codex_rollout_path([old_path, new_path]), new_path)

    def test_running_session_preference_collapses_same_terminal_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_path = Path(temp_dir) / "rollout-old.jsonl"
            new_path = Path(temp_dir) / "rollout-new.jsonl"
            old_path.write_text('{"type":"session_meta","payload":{"id":"old"}}\n', encoding="utf-8")
            new_path.write_text('{"type":"session_meta","payload":{"id":"new"}}\n', encoding="utf-8")
            os.utime(old_path, ns=(100, 100))
            os.utime(new_path, ns=(200, 200))
            sessions: dict[str, RunningCodexSession] = {}

            _set_preferred_running_session(
                sessions,
                RunningCodexSession(
                    path=old_path,
                    pid=100,
                    cwd="/tmp/project",
                    terminal_app="WezTerm",
                    terminal_socket="/run/user/1000/wezterm/gui-sock-1",
                    terminal_pane="7",
                ),
            )
            _set_preferred_running_session(
                sessions,
                RunningCodexSession(
                    path=new_path,
                    pid=101,
                    cwd="/tmp/project",
                    terminal_app="WezTerm",
                    terminal_socket="/run/user/1000/wezterm/gui-sock-1",
                    terminal_pane="7",
                ),
            )

            self.assertEqual(len(sessions), 1)
            self.assertEqual(next(iter(sessions.values())).path, new_path)


class CodexAdapterAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = str(Path(self.temp_dir.name) / "codeislandd.sock")
        self.daemon = Phase0Daemon(self.socket_path)
        await self.daemon.start()
        self.server_task = asyncio.create_task(self.daemon.serve_forever())

    async def asyncTearDown(self) -> None:
        await self.daemon.shutdown()
        if not self.server_task.done():
            self.server_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.server_task
        self.temp_dir.cleanup()

    async def test_stale_daemon_sessions_are_ended_on_watch_startup(self) -> None:
        running_path = Path(self.temp_dir.name) / "rollout-running.jsonl"
        running_path.write_text(
            '{"timestamp":"2026-05-10T00:00:00Z","type":"session_meta","payload":{"id":"running","cwd":"/tmp/running"}}\n',
            encoding="utf-8",
        )
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_running",
            session_id="codex-running",
            kind="session.started",
            payload={"provider": "codex", "title": "running", "project_root": "/tmp/running"},
            ts=now_iso(),
        ))
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_stale",
            session_id="codex-stale",
            kind="session.started",
            payload={"provider": "codex", "title": "stale", "project_root": "/tmp/stale"},
            ts=now_iso(),
        ))

        await CodexSessionAdapter()._end_daemon_sessions_not_running(
            self.socket_path,
            [RunningCodexSession(path=running_path, pid=1, cwd="/tmp/running", terminal_app="WezTerm")],
        )

        self.assertIsNone(self.daemon.store.sessions["codex-running"].ended_at)
        self.assertIsNotNone(self.daemon.store.sessions["codex-stale"].ended_at)

    async def test_running_alive_reopens_ended_jsonl_session(self) -> None:
        running_path = Path(self.temp_dir.name) / "rollout-running.jsonl"
        running_path.write_text(
            '{"timestamp":"2026-05-10T00:00:00Z","type":"session_meta","payload":{"id":"running","cwd":"/tmp/running"}}\n',
            encoding="utf-8",
        )
        session_id = "codex-running"
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_running_start",
            session_id=session_id,
            kind="session.started",
            payload={"provider": "codex", "title": "running", "project_root": "/tmp/running"},
            ts=now_iso(),
        ))
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_running_end",
            session_id=session_id,
            kind="session.ended",
            payload={"reason": "process_closed"},
            ts=now_iso(),
        ))

        await CodexSessionAdapter()._send_running_session_started(
            self.socket_path,
            RunningCodexSession(path=running_path, pid=4321, cwd="/tmp/running", terminal_app="WezTerm"),
        )

        session = self.daemon.store.sessions[session_id]
        self.assertIsNone(session.ended_at)
        self.assertEqual(session.cli_pid, 4321)
        self.assertEqual(session.terminal_app, "WezTerm")

    async def test_process_only_codex_session_is_visible_before_jsonl_exists(self) -> None:
        await CodexSessionAdapter()._send_running_session_started(
            self.socket_path,
            RunningCodexSession(
                path=None,
                pid=9876,
                cwd="/tmp/blank-codex",
                terminal_app="WezTerm",
                synthetic_id="proc-9876",
            ),
        )

        session = self.daemon.store.sessions["codex-proc-9876"]
        self.assertIsNone(session.ended_at)
        self.assertEqual(session.provider, "codex")
        self.assertEqual(session.title, "blank-codex")
        self.assertEqual(session.project_root, "/tmp/blank-codex")
        self.assertEqual(session.cli_pid, 9876)

    async def test_one_shot_replay_ends_stale_daemon_sessions(self) -> None:
        running_path = Path(self.temp_dir.name) / "rollout-running.jsonl"
        running_path.write_text(
            '{"timestamp":"2026-05-10T00:00:00Z","type":"session_meta","payload":{"id":"running","cwd":"/tmp/running"}}\n',
            encoding="utf-8",
        )
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_stale",
            session_id="codex-stale",
            kind="session.started",
            payload={"provider": "codex", "title": "stale", "project_root": "/tmp/stale"},
            ts=now_iso(),
        ))

        with patch(
            "codeisland_linux.codex_adapter.discover_running_codex_sessions",
            return_value=[RunningCodexSession(path=running_path, pid=1234, cwd="/tmp/running", terminal_app="WezTerm")],
        ):
            await CodexSessionAdapter().replay_running_once(self.socket_path, Path(self.temp_dir.name) / "codex-home")

        self.assertIsNone(self.daemon.store.sessions["codex-running"].ended_at)
        self.assertIsNotNone(self.daemon.store.sessions["codex-stale"].ended_at)

    async def test_watch_periodically_ends_stale_daemon_sessions_added_after_startup(self) -> None:
        adapter = CodexSessionAdapter()
        codex_home = Path(self.temp_dir.name) / "codex-home"

        with patch("codeisland_linux.codex_adapter.discover_running_codex_sessions", return_value=[]):
            watch_task = asyncio.create_task(
                adapter.watch_running(
                    self.socket_path,
                    codex_home,
                    interval=0.01,
                    history_lines=1,
                    stale_scan_interval=0.03,
                )
            )
            try:
                await asyncio.sleep(0.02)
                self.daemon.store.apply_event(EventEnvelope(
                    event_id="evt_late_stale",
                    session_id="codex-late-stale",
                    kind="session.started",
                    payload={"provider": "codex", "title": "late stale", "project_root": "/tmp/stale"},
                    ts=now_iso(),
                ))

                for _ in range(30):
                    if self.daemon.store.sessions["codex-late-stale"].ended_at is not None:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNotNone(self.daemon.store.sessions["codex-late-stale"].ended_at)
            finally:
                watch_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await watch_task

    async def test_watch_retries_when_daemon_socket_is_temporarily_unavailable(self) -> None:
        adapter = CodexSessionAdapter()
        missing_socket = str(Path(self.temp_dir.name) / "missing-codeislandd.sock")
        codex_home = Path(self.temp_dir.name) / "codex-home"

        with patch("codeisland_linux.codex_adapter.discover_running_codex_sessions", return_value=[]):
            watch_task = asyncio.create_task(
                adapter.watch_running(
                    missing_socket,
                    codex_home,
                    interval=0.01,
                    history_lines=1,
                    stale_scan_interval=0.01,
                    daemon_refresh_interval=0.01,
                )
            )
            try:
                await asyncio.sleep(0.05)
                self.assertFalse(watch_task.done())
            finally:
                watch_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await watch_task

    async def test_watch_replays_running_session_after_daemon_store_reset(self) -> None:
        adapter = CodexSessionAdapter()
        codex_home = Path(self.temp_dir.name) / "codex-home"
        running_path = Path(self.temp_dir.name) / "rollout-reset.jsonl"
        running_path.write_text(
            '{"timestamp":"2026-05-10T00:00:00Z","type":"session_meta","payload":{"id":"reset","cwd":"/tmp/reset"}}\n',
            encoding="utf-8",
        )
        running = RunningCodexSession(path=running_path, pid=1234, cwd="/tmp/reset", terminal_app="WezTerm")

        with patch("codeisland_linux.codex_adapter.discover_running_codex_sessions", return_value=[running]):
            watch_task = asyncio.create_task(
                adapter.watch_running(
                    self.socket_path,
                    codex_home,
                    interval=0.01,
                    history_lines=1,
                    stale_scan_interval=60,
                    daemon_refresh_interval=0.03,
                )
            )
            try:
                for _ in range(30):
                    if "codex-reset" in self.daemon.store.sessions:
                        break
                    await asyncio.sleep(0.01)
                self.assertIn("codex-reset", self.daemon.store.sessions)

                self.daemon.store = InMemoryDaemonStore()

                for _ in range(60):
                    if "codex-reset" in self.daemon.store.sessions:
                        break
                    await asyncio.sleep(0.01)
                self.assertIn("codex-reset", self.daemon.store.sessions)
                self.assertEqual(self.daemon.store.sessions["codex-reset"].project_root, "/tmp/reset")
            finally:
                watch_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await watch_task
