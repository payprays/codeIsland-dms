from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from codeisland_linux.client import rpc_call
from codeisland_linux.opencode_adapter import OpenCodeDatabaseAdapter, OpenCodeHookAdapter, RunningOpenCodeProcess
from codeisland_linux.protocol import EventEnvelope, from_json_line, now_iso, to_json_line
from codeisland_linux.server import Phase0Daemon


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def write_opencode_database(path: Path, *, session_id: str = "ses_demo", updated_ms: int = 6_000) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            create table session (
                id text primary key,
                project_id text not null,
                parent_id text,
                slug text not null,
                directory text not null,
                title text not null,
                version text not null,
                share_url text,
                summary_additions integer,
                summary_deletions integer,
                summary_files integer,
                summary_diffs text,
                revert text,
                permission text,
                time_created integer not null,
                time_updated integer not null,
                time_compacting integer,
                time_archived integer,
                workspace_id text
            );
            create table message (
                id text primary key,
                session_id text not null,
                time_created integer not null,
                time_updated integer not null,
                data text not null
            );
            create table part (
                id text primary key,
                message_id text not null,
                session_id text not null,
                time_created integer not null,
                time_updated integer not null,
                data text not null
            );
            """
        )
        connection.execute(
            """
            insert into session (
                id, project_id, slug, directory, title, version, time_created, time_updated, time_archived
            ) values (?, 'project-demo', 'demo', '/tmp/demo', 'Implement OpenCode support', '1.14.25', ?, ?, null)
            """,
            (session_id, 1_000, updated_ms),
        )
        connection.execute(
            "insert into message (id, session_id, time_created, time_updated, data) values (?, ?, ?, ?, ?)",
            ("msg_user", session_id, 2_000, 2_000, json.dumps({"role": "user", "time": {"created": 2_000}})),
        )
        connection.execute(
            "insert into part (id, message_id, session_id, time_created, time_updated, data) values (?, ?, ?, ?, ?, ?)",
            ("part_user_text", "msg_user", session_id, 2_000, 2_000, json.dumps({"type": "text", "text": "Add OpenCode support"})),
        )
        connection.execute(
            "insert into message (id, session_id, time_created, time_updated, data) values (?, ?, ?, ?, ?)",
            (
                "msg_assistant",
                session_id,
                3_000,
                5_000,
                json.dumps({"role": "assistant", "parentID": "msg_user", "finish": "stop", "time": {"created": 3_000, "completed": 5_000}}),
            ),
        )
        connection.execute(
            "insert into part (id, message_id, session_id, time_created, time_updated, data) values (?, ?, ?, ?, ?, ?)",
            (
                "part_tool",
                "msg_assistant",
                session_id,
                3_500,
                3_500,
                json.dumps({"type": "tool", "tool": "bash", "callID": "call_1", "state": {"status": "completed", "output": "ok"}}),
            ),
        )
        connection.execute(
            "insert into part (id, message_id, session_id, time_created, time_updated, data) values (?, ?, ?, ?, ?, ?)",
            ("part_assistant_text", "msg_assistant", session_id, 4_000, 4_000, json.dumps({"type": "text", "text": "Done."})),
        )
        connection.execute(
            "insert into part (id, message_id, session_id, time_created, time_updated, data) values (?, ?, ?, ?, ?, ?)",
            ("part_finish", "msg_assistant", session_id, 5_000, 5_000, json.dumps({"type": "step-finish", "reason": "stop"})),
        )
        connection.commit()
    finally:
        connection.close()


class OpenCodeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.writebacks: list[tuple[str, dict[str, object]]] = []

        async def fake_writeback(interaction_id: str, payload: dict[str, object]) -> None:
            self.writebacks.append((interaction_id, payload))

        self.adapter = OpenCodeHookAdapter(provider_writeback=fake_writeback)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = str(Path(self.temp_dir.name) / "codeislandd.sock")
        self.daemon = Phase0Daemon(self.socket_path)
        await self.daemon.start()
        self.serve_task = asyncio.create_task(self.daemon.serve_forever())
        await asyncio.sleep(0.05)

    async def asyncTearDown(self) -> None:
        self.serve_task.cancel()
        try:
            await self.serve_task
        except asyncio.CancelledError:
            pass
        await self.daemon.shutdown()
        self.temp_dir.cleanup()

    def test_session_start_maps_to_normalized_event(self) -> None:
        events = self.adapter.translate_event(
            {
                "hook_event_name": "SessionStart",
                "session_id": "opencode-ses_demo",
                "cwd": "/tmp/demo",
                "ts": "2026-04-22T10:00:00Z",
            }
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.kind, "session.started")
        self.assertEqual(event.session_id, "opencode-ses_demo")
        self.assertEqual(event.payload["project_root"], "/tmp/demo")
        self.assertEqual(event.payload["title"], "demo")

    def test_user_prompt_creates_task_and_stop_completes_it(self) -> None:
        self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_demo", "cwd": "/tmp/demo", "ts": "2026-04-22T10:00:00Z"}
        )
        task_events = self.adapter.translate_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "opencode-ses_demo",
                "cwd": "/tmp/demo",
                "prompt": "hello",
                "_opencode_message_id": "msg-1",
                "ts": "2026-04-22T10:00:01Z",
            }
        )
        stop_events = self.adapter.translate_event(
            {
                "hook_event_name": "Stop",
                "session_id": "opencode-ses_demo",
                "cwd": "/tmp/demo",
                "last_assistant_message": "done",
                "ts": "2026-04-22T10:00:02Z",
            }
        )

        self.assertEqual([event.kind for event in task_events], ["task.started", "prompt.submitted"])
        self.assertEqual(task_events[0].task_id, "opencode-ses_demo-task-msg-1")
        self.assertEqual(task_events[1].payload["prompt"], "hello")
        self.assertEqual([event.kind for event in stop_events], ["assistant.response.completed", "task.completed"])
        self.assertEqual(stop_events[0].payload["message"], "done")
        self.assertEqual(stop_events[1].task_id, "opencode-ses_demo-task-msg-1")

    def test_permission_request_branches_to_approval_and_question(self) -> None:
        self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_demo", "cwd": "/tmp/demo", "ts": "2026-04-22T10:00:00Z"}
        )
        self.adapter.translate_event(
            {"hook_event_name": "UserPromptSubmit", "session_id": "opencode-ses_demo", "cwd": "/tmp/demo", "prompt": "hello", "ts": "2026-04-22T10:00:01Z"}
        )

        approval = self.adapter.translate_event(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "opencode-ses_demo",
                "cwd": "/tmp/demo",
                "tool_name": "Bash",
                "tool_input": {"command": "pwd"},
                "_opencode_request_id": "req-1",
                "ts": "2026-04-22T10:00:02Z",
            }
        )
        question = self.adapter.translate_event(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "opencode-ses_demo",
                "cwd": "/tmp/demo",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which branch?",
                            "options": [{"label": "main"}, {"label": "feature"}],
                        }
                    ]
                },
                "_opencode_request_id": "req-2",
                "ts": "2026-04-22T10:00:03Z",
            }
        )

        self.assertEqual([event.kind for event in approval], ["interaction.approval.requested", "permission.requested"])
        self.assertEqual(approval[0].payload["interaction_id"], "req-1")
        self.assertEqual([event.kind for event in question], ["interaction.question.requested", "question.requested"])
        self.assertEqual(question[0].payload["interaction_id"], "req-2")
        self.assertEqual(question[0].payload["options"], ["main", "feature"])
        self.assertEqual(self.adapter.pending_interactions["req-1"].kind, "approval")
        self.assertEqual(self.adapter.pending_interactions["req-2"].kind, "question")

    def test_post_tool_use_failure_emits_failed_activity(self) -> None:
        self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_demo", "cwd": "/tmp/demo", "ts": "2026-04-22T10:00:00Z"}
        )
        self.adapter.translate_event(
            {"hook_event_name": "UserPromptSubmit", "session_id": "opencode-ses_demo", "cwd": "/tmp/demo", "prompt": "hello", "ts": "2026-04-22T10:00:01Z"}
        )

        events = self.adapter.translate_event(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "opencode-ses_demo",
                "cwd": "/tmp/demo",
                "tool_name": "Bash",
                "tool_output": {"exit_code": 1, "stderr": "boom"},
                "ts": "2026-04-22T10:00:02Z",
            }
        )

        self.assertEqual([event.kind for event in events], ["tool.call.finished", "tool.use.failed"])
        self.assertEqual(events[1].payload["error_message"], "boom")

    def test_duplicate_replay_produces_stable_event_id(self) -> None:
        raw_event = {
            "hook_event_name": "SessionStart",
            "session_id": "opencode-ses_demo",
            "cwd": "/tmp/demo",
            "ts": "2026-04-22T10:00:00Z",
        }
        first = self.adapter.translate_event(raw_event)[0]
        second = self.adapter.translate_event(raw_event)[0]
        self.assertEqual(first.event_id, second.event_id)

    async def test_happy_path_fixture_reaches_completed_state(self) -> None:
        await self.adapter.replay_jsonl(self.socket_path, FIXTURES_DIR / "opencode_hook_happy_path.jsonl")

        session = self.daemon.store.sessions["opencode-ses_demo"]
        session_state = self.daemon.store.session_states["opencode-ses_demo"]
        tasks = [task for task in self.daemon.store.tasks.values() if task.session_id == "opencode-ses_demo"]
        self.assertEqual(session.status, "completed")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "done")
        self.assertEqual(session_state.effective_status, "completed")
        self.assertTrue(session_state.completion_pending)
        self.assertIn("prompt.submitted", [event.kind for event in self.daemon.store.activities])
        self.assertIn("assistant.response.completed", [event.kind for event in self.daemon.store.activities])

    async def test_approval_fixture_opens_blocking_interaction(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(to_json_line({"id": "sub-approval", "method": "subscribe", "params": {"topics": ["interactions"]}}))
        await writer.drain()
        _ = from_json_line(await reader.readline())
        try:
            await self.adapter.replay_jsonl(self.socket_path, FIXTURES_DIR / "opencode_hook_approval.jsonl")

            messages: list[dict[str, object]] = []
            for _ in range(20):
                try:
                    messages.append(from_json_line(await asyncio.wait_for(reader.readline(), timeout=0.2)))
                except TimeoutError:
                    break

            session = self.daemon.store.sessions["opencode-ses_approval"]
            interactions = [item for item in self.daemon.store.interactions.values() if item.session_id == "opencode-ses_approval"]
            self.assertEqual(session.status, "waiting_approval")
            self.assertEqual(len(interactions), 1)
            self.assertEqual(interactions[0].state, "open")
            self.assertTrue(any(message.get("kind") == "interaction.opened" for message in messages))
            self.assertIn("permission.requested", [event.kind for event in self.daemon.store.activities])
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_approval_resolution_writes_back_once(self) -> None:
        forward_task = asyncio.create_task(self.adapter.forward_daemon_resolutions(self.socket_path))
        try:
            await self.adapter.replay_jsonl(self.socket_path, FIXTURES_DIR / "opencode_hook_approval.jsonl")
            response = await rpc_call(
                self.socket_path,
                "interaction_respond",
                {"interaction_id": "req-approval-1", "action": "approve", "answer": None},
                request_id="req-approve",
            )
            self.assertTrue(response["ok"])

            for _ in range(20):
                if self.writebacks:
                    break
                await asyncio.sleep(0.05)

            self.assertEqual(self.writebacks, [("req-approval-1", {"interaction_id": "req-approval-1", "response": "once"})])
            self.assertNotIn("req-approval-1", self.adapter.pending_interactions)
        finally:
            forward_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await forward_task

    async def test_deny_resolution_writes_back_once(self) -> None:
        forward_task = asyncio.create_task(self.adapter.forward_daemon_resolutions(self.socket_path))
        try:
            await self.adapter.replay_jsonl(self.socket_path, FIXTURES_DIR / "opencode_hook_approval.jsonl")
            response = await rpc_call(
                self.socket_path,
                "interaction_respond",
                {"interaction_id": "req-approval-1", "action": "deny", "answer": None},
                request_id="req-deny",
            )
            self.assertTrue(response["ok"])

            for _ in range(20):
                if self.writebacks:
                    break
                await asyncio.sleep(0.05)

            self.assertEqual(self.writebacks, [("req-approval-1", {"interaction_id": "req-approval-1", "response": "reject"})])
            self.assertNotIn("req-approval-1", self.adapter.pending_interactions)
        finally:
            forward_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await forward_task

    async def test_resolution_is_not_forwarded_twice(self) -> None:
        await self.adapter.replay_jsonl(self.socket_path, FIXTURES_DIR / "opencode_hook_approval.jsonl")

        resolved_message = {
            "kind": "interaction.resolved",
            "payload": {
                "interaction_id": "req-approval-1",
                "state": "answered",
                "answer_payload": {"action": "approve", "answer": None},
            },
        }
        await self.adapter.handle_daemon_message(resolved_message)
        await self.adapter.handle_daemon_message(resolved_message)

        self.assertEqual(self.writebacks, [("req-approval-1", {"interaction_id": "req-approval-1", "response": "once"})])

    async def test_question_resolution_writes_back_once(self) -> None:
        session_events = self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_question", "cwd": "/tmp/question", "ts": "2026-04-22T10:00:00Z"}
        )
        self.daemon.store.apply_event(session_events[0])
        question_events = self.adapter.translate_event(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "opencode-ses_question",
                "cwd": "/tmp/question",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which branch?",
                            "options": [{"label": "main"}, {"label": "feature"}],
                        }
                    ]
                },
                "_opencode_request_id": "req-question-1",
                "ts": "2026-04-22T10:00:01Z",
            }
        )

        self.assertEqual(question_events[0].kind, "interaction.question.requested")
        self.daemon.store.apply_event(question_events[0])
        response = await rpc_call(
            self.socket_path,
            "interaction_respond",
            {"interaction_id": "req-question-1", "action": "answer", "answer": "main"},
            request_id="req-question-answer",
        )
        self.assertTrue(response["ok"])

        resolved_message = {
            "kind": "interaction.resolved",
            "payload": {
                "interaction_id": "req-question-1",
                "state": "answered",
                "answer_payload": {"action": "answer", "answer": "main"},
            },
        }
        await self.adapter.handle_daemon_message(resolved_message)

        self.assertEqual(self.writebacks, [("req-question-1", {"interaction_id": "req-question-1", "answers": [["main"]]})])
        self.assertNotIn("req-question-1", self.adapter.pending_interactions)

    async def test_question_deny_writes_back_once(self) -> None:
        session_events = self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_question", "cwd": "/tmp/question", "ts": "2026-04-22T10:00:00Z"}
        )
        self.daemon.store.apply_event(session_events[0])
        question_events = self.adapter.translate_event(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "opencode-ses_question",
                "cwd": "/tmp/question",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which branch?",
                            "options": [{"label": "main"}, {"label": "feature"}],
                        }
                    ]
                },
                "_opencode_request_id": "req-question-deny",
                "ts": "2026-04-22T10:00:01Z",
            }
        )

        self.daemon.store.apply_event(question_events[0])
        response = await rpc_call(
            self.socket_path,
            "interaction_respond",
            {"interaction_id": "req-question-deny", "action": "deny", "answer": None},
            request_id="req-question-deny",
        )
        self.assertTrue(response["ok"])

        resolved_message = {
            "kind": "interaction.resolved",
            "payload": {
                "interaction_id": "req-question-deny",
                "state": "answered",
                "answer_payload": {"action": "deny", "answer": None},
            },
        }
        await self.adapter.handle_daemon_message(resolved_message)

        self.assertEqual(self.writebacks, [("req-question-deny", {"interaction_id": "req-question-deny", "action": "reject"})])
        self.assertNotIn("req-question-deny", self.adapter.pending_interactions)

    def test_database_session_maps_to_normalized_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "opencode.db"
            write_opencode_database(database_path)

            events, active_ids = OpenCodeDatabaseAdapter().translate_database(
                database_path,
                active_seconds=60,
                now_ms=6_000,
                running_processes=[],
            )

        self.assertEqual(active_ids, {"opencode-ses_demo"})
        self.assertEqual(
            [event.kind for event in events],
            [
                "session.started",
                "task.started",
                "prompt.submitted",
                "tool.use.completed",
                "assistant.response.completed",
                "task.completed",
            ],
        )
        self.assertEqual(events[0].payload["provider"], "opencode")
        self.assertEqual(events[0].payload["workspace_hint"], "opencode-db")
        self.assertEqual(events[0].payload["title"], "Implement OpenCode support")
        self.assertEqual(events[2].payload["prompt"], "Add OpenCode support")
        self.assertEqual(events[-2].payload["message"], "Done.")

    async def test_database_replay_reaches_completed_store_state(self) -> None:
        database_path = Path(self.temp_dir.name) / "opencode.db"
        write_opencode_database(database_path)

        responses = await OpenCodeDatabaseAdapter().sync_once(
            self.socket_path,
            database_path,
            active_seconds=0,
            end_stale=True,
        )

        self.assertTrue(all(response["ok"] for response in responses))
        session = self.daemon.store.sessions["opencode-ses_demo"]
        state = self.daemon.store.session_states["opencode-ses_demo"]
        self.assertEqual(session.provider, "opencode")
        self.assertEqual(session.workspace_hint, "opencode-db")
        self.assertEqual(session.status, "completed")
        self.assertEqual(state.last_user_prompt, "Add OpenCode support")
        self.assertEqual(state.last_assistant_message, "Done.")

    async def test_stale_database_sessions_are_ended_without_touching_hook_sessions(self) -> None:
        adapter = OpenCodeDatabaseAdapter()
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_live",
            session_id="opencode-live",
            kind="session.started",
            payload={"provider": "opencode", "title": "live", "project_root": "/tmp/live", "workspace_hint": "opencode-db"},
            ts=now_iso(),
        ))
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_stale",
            session_id="opencode-stale",
            kind="session.started",
            payload={"provider": "opencode", "title": "stale", "project_root": "/tmp/stale", "workspace_hint": "opencode-db"},
            ts=now_iso(),
        ))
        self.daemon.store.apply_event(EventEnvelope(
            event_id="evt_hook",
            session_id="opencode-hook",
            kind="session.started",
            payload={"provider": "opencode", "title": "hook", "project_root": "/tmp/hook"},
            ts=now_iso(),
        ))

        await adapter._end_daemon_sessions_not_active(self.socket_path, {"opencode-live"})

        self.assertIsNone(self.daemon.store.sessions["opencode-live"].ended_at)
        self.assertIsNotNone(self.daemon.store.sessions["opencode-stale"].ended_at)
        self.assertIsNone(self.daemon.store.sessions["opencode-hook"].ended_at)

    def test_running_process_directory_keeps_old_database_session_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "opencode.db"
            write_opencode_database(database_path, updated_ms=1_000)

            events, active_ids = OpenCodeDatabaseAdapter().translate_database(
                database_path,
                active_seconds=1,
                now_ms=60_000,
                running_processes=[RunningOpenCodeProcess(pid=123, cwd="/tmp/demo", terminal_app="WezTerm", terminal_pid=456)],
            )

        self.assertEqual(active_ids, {"opencode-ses_demo"})
        self.assertEqual(events[0].payload["terminal_app"], "WezTerm")
        self.assertEqual(events[0].payload["terminal_pid"], 456)

    async def test_question_resolution_is_not_forwarded_twice(self) -> None:
        self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_question", "cwd": "/tmp/question", "ts": "2026-04-22T10:00:00Z"}
        )
        self.adapter.translate_event(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "opencode-ses_question",
                "cwd": "/tmp/question",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which branch?",
                            "options": [{"label": "main"}, {"label": "feature"}],
                        }
                    ]
                },
                "_opencode_request_id": "req-question-1",
                "ts": "2026-04-22T10:00:01Z",
            }
        )

        resolved_message = {
            "kind": "interaction.resolved",
            "payload": {
                "interaction_id": "req-question-1",
                "state": "answered",
                "answer_payload": {"action": "answer", "answer": "main"},
            },
        }
        await self.adapter.handle_daemon_message(resolved_message)
        await self.adapter.handle_daemon_message(resolved_message)

        self.assertEqual(self.writebacks, [("req-question-1", {"interaction_id": "req-question-1", "answers": [["main"]]})])

    async def test_question_deny_is_not_forwarded_twice(self) -> None:
        self.adapter.translate_event(
            {"hook_event_name": "SessionStart", "session_id": "opencode-ses_question", "cwd": "/tmp/question", "ts": "2026-04-22T10:00:00Z"}
        )
        self.adapter.translate_event(
            {
                "hook_event_name": "PermissionRequest",
                "session_id": "opencode-ses_question",
                "cwd": "/tmp/question",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which branch?",
                            "options": [{"label": "main"}, {"label": "feature"}],
                        }
                    ]
                },
                "_opencode_request_id": "req-question-deny",
                "ts": "2026-04-22T10:00:01Z",
            }
        )

        resolved_message = {
            "kind": "interaction.resolved",
            "payload": {
                "interaction_id": "req-question-deny",
                "state": "answered",
                "answer_payload": {"action": "deny", "answer": None},
            },
        }
        await self.adapter.handle_daemon_message(resolved_message)
        await self.adapter.handle_daemon_message(resolved_message)

        self.assertEqual(self.writebacks, [("req-question-deny", {"interaction_id": "req-question-deny", "action": "reject"})])
