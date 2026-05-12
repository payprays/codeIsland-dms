from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeisland_linux.claude_adapter import ClaudeSessionAdapter, RunningClaudeSession, _is_claude_jsonl_path
from codeisland_linux.protocol import EventEnvelope, now_iso
from codeisland_linux.server import Phase0Daemon
from codeisland_linux.store import InMemoryDaemonStore


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class ClaudeAdapterTests(unittest.TestCase):
    def test_project_sample_maps_to_normalized_events(self) -> None:
        adapter = ClaudeSessionAdapter()
        input_path = FIXTURES_DIR / "claude_project_sample.jsonl"

        events = adapter.translate_lines(adapter.read_jsonl(input_path), path=input_path, terminal_app="WezTerm")

        self.assertEqual(
            [event.kind for event in events],
            [
                "session.started",
                "task.started",
                "prompt.submitted",
                "assistant.response.completed",
                "tool.use.started",
                "tool.use.completed",
                "assistant.response.completed",
                "task.completed",
            ],
        )
        self.assertEqual(events[0].session_id, "claude-ses_claude_demo")
        self.assertEqual(events[0].payload["provider"], "claude")
        self.assertEqual(events[0].payload["source"], "claude-code")
        self.assertEqual(events[0].payload["terminal_app"], "WezTerm")
        self.assertEqual(events[2].payload["prompt"], "Add Claude Code support")
        self.assertEqual(events[4].payload["tool_name"], "Bash")
        self.assertEqual(events[-2].payload["message"], "Done.")

    def test_project_sample_reaches_completed_store_state(self) -> None:
        adapter = ClaudeSessionAdapter()
        input_path = FIXTURES_DIR / "claude_project_sample.jsonl"
        store = InMemoryDaemonStore()

        for event in adapter.translate_lines(adapter.read_jsonl(input_path), path=input_path, terminal_app="WezTerm"):
            store.apply_event(event)

        session_id = "claude-ses_claude_demo"
        session = store.sessions[session_id]
        state = store.session_states[session_id]

        self.assertEqual(session.provider, "claude")
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.terminal_app, "WezTerm")
        self.assertEqual(state.last_user_prompt, "Add Claude Code support")
        self.assertEqual(state.last_assistant_message, "Done.")
        self.assertTrue(state.completion_pending)

    def test_running_session_metadata_reaches_session_started(self) -> None:
        adapter = ClaudeSessionAdapter()
        input_path = FIXTURES_DIR / "claude_project_sample.jsonl"

        events = adapter.translate_initial_lines(
            adapter.read_jsonl(input_path),
            path=input_path,
            terminal_app="WezTerm",
            running_session=RunningClaudeSession(
                path=input_path,
                pid=1234,
                cwd="/tmp/live-claude",
                terminal_app="WezTerm",
                terminal_pid=5678,
                terminal_pane="9",
                terminal_socket="/run/user/1000/wezterm/gui-sock-5678",
            ),
        )

        self.assertEqual(events[0].payload["title"], "live-claude")
        self.assertEqual(events[0].payload["project_root"], "/tmp/live-claude")
        self.assertEqual(events[0].payload["cli_pid"], 1234)
        self.assertEqual(events[0].payload["terminal_pid"], 5678)
        self.assertEqual(events[0].payload["terminal_pane"], "9")
        self.assertEqual(events[0].payload["terminal_socket"], "/run/user/1000/wezterm/gui-sock-5678")

    def test_initial_history_window_keeps_latest_user_anchor(self) -> None:
        adapter = ClaudeSessionAdapter()
        input_path = FIXTURES_DIR / "claude_project_sample.jsonl"

        events = adapter.translate_initial_lines(
            adapter.read_jsonl(input_path),
            path=input_path,
            terminal_app="WezTerm",
            history_lines=2,
        )

        self.assertEqual(events[0].kind, "session.started")
        self.assertEqual(events[1].kind, "task.started")
        self.assertEqual(events[2].kind, "prompt.submitted")
        self.assertEqual(events[2].payload["prompt"], "Add Claude Code support")
        self.assertEqual(events[-1].kind, "task.completed")

    def test_local_command_and_tool_result_records_do_not_create_prompt_tasks(self) -> None:
        adapter = ClaudeSessionAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            lines = [
                {
                    "type": "user",
                    "timestamp": "2026-05-11T02:00:00.000Z",
                    "cwd": "/tmp/demo",
                    "sessionId": "ses",
                    "isMeta": True,
                    "message": {"role": "user", "content": "<local-command-caveat>ignore</local-command-caveat>"},
                },
                {
                    "type": "user",
                    "timestamp": "2026-05-11T02:00:01.000Z",
                    "cwd": "/tmp/demo",
                    "sessionId": "ses",
                    "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": "ok"}]},
                },
            ]
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

            events = adapter.translate_lines(adapter.read_jsonl(path), path=path)

        self.assertEqual([event.kind for event in events], ["session.started"])

    def test_claude_jsonl_path_accepts_project_and_transcript_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claude_home = Path(temp_dir) / ".claude"
            project_path = claude_home / "projects" / "-tmp-demo" / "ses.jsonl"
            transcript_path = claude_home / "transcripts" / "ses.jsonl"
            project_path.parent.mkdir(parents=True)
            transcript_path.parent.mkdir(parents=True)
            project_path.write_text("{}", encoding="utf-8")
            transcript_path.write_text("{}", encoding="utf-8")
            outside = Path(temp_dir) / "ses.jsonl"
            outside.write_text("{}", encoding="utf-8")

            self.assertTrue(_is_claude_jsonl_path(project_path, claude_home))
            self.assertTrue(_is_claude_jsonl_path(transcript_path, claude_home))
            self.assertFalse(_is_claude_jsonl_path(outside, claude_home))


class ClaudeAdapterAsyncTests(unittest.IsolatedAsyncioTestCase):
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
        adapter = ClaudeSessionAdapter()
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_running",
            session_id="claude-running",
            kind="session.started",
            payload={"provider": "claude", "title": "running", "project_root": "/tmp/running"},
            ts=now_iso(),
        ))
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_stale",
            session_id="claude-stale",
            kind="session.started",
            payload={"provider": "claude", "title": "stale", "project_root": "/tmp/stale"},
            ts=now_iso(),
        ))

        await adapter._end_daemon_sessions_not_running(
            self.socket_path,
            [RunningClaudeSession(path=None, pid=1, cwd="/tmp/running", terminal_app=None, synthetic_id="running")],
        )

        self.assertIsNone(self.daemon.store.sessions["claude-running"].ended_at)
        self.assertIsNotNone(self.daemon.store.sessions["claude-stale"].ended_at)

    async def test_one_shot_replay_uses_discovered_running_sessions(self) -> None:
        input_path = FIXTURES_DIR / "claude_project_sample.jsonl"
        adapter = ClaudeSessionAdapter()

        with patch(
            "codeisland_linux.claude_adapter.discover_running_claude_sessions",
            return_value=[RunningClaudeSession(path=input_path, pid=42, cwd="/tmp/claude-demo", terminal_app="WezTerm", synthetic_id=None)],
        ):
            responses = await adapter.replay_running_once(self.socket_path, Path("/tmp/claude-home"))

        self.assertTrue(all(response["ok"] for response in responses))
        session = self.daemon.store.sessions["claude-ses_claude_demo"]
        self.assertEqual(session.provider, "claude")
        self.assertEqual(session.cli_pid, 42)

    async def test_watch_replays_running_session_after_daemon_store_reset(self) -> None:
        adapter = ClaudeSessionAdapter()
        claude_home = Path(self.temp_dir.name) / "claude-home"
        running_path = Path(self.temp_dir.name) / "claude-reset.jsonl"
        running_path.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-10T00:00:00.000Z",
                "cwd": "/tmp/reset",
                "sessionId": "reset",
                "message": {"role": "user", "content": "hello"},
            }) + "\n",
            encoding="utf-8",
        )
        running = RunningClaudeSession(path=running_path, pid=1234, cwd="/tmp/reset", terminal_app="WezTerm")

        with patch("codeisland_linux.claude_adapter.discover_running_claude_sessions", return_value=[running]):
            watch_task = asyncio.create_task(
                adapter.watch_running(
                    self.socket_path,
                    claude_home,
                    interval=0.01,
                    history_lines=1,
                    stale_scan_interval=60,
                    daemon_refresh_interval=0.03,
                )
            )
            try:
                for _ in range(30):
                    if "claude-reset" in self.daemon.store.sessions:
                        break
                    await asyncio.sleep(0.01)
                self.assertIn("claude-reset", self.daemon.store.sessions)

                self.daemon.store = InMemoryDaemonStore()

                for _ in range(60):
                    if "claude-reset" in self.daemon.store.sessions:
                        break
                    await asyncio.sleep(0.01)
                self.assertIn("claude-reset", self.daemon.store.sessions)
                self.assertEqual(self.daemon.store.sessions["claude-reset"].project_root, "/tmp/reset")
            finally:
                watch_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await watch_task
