from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from codeisland_linux.client import rpc_call
from codeisland_linux.protocol import from_json_line, to_json_line
from codeisland_linux.server import Phase0Daemon


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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

    async def test_subscribe_receives_full_snapshot_then_patch(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(to_json_line({"id": "sub-1", "method": "subscribe", "params": {"topics": ["sessions"]}}))
        await writer.drain()

        first = from_json_line(await reader.readline())
        self.assertEqual(first["kind"], "snapshot.full")
        self.assertIn("activities", first["payload"])
        self.assertIn("session_states", first["payload"])
        self.assertIn("island_state", first["payload"])
        self.assertEqual(first["payload"]["island_state"]["surface"], "collapsed")

        result = await rpc_call(
            self.socket_path,
            "ingest_event",
            {
                "event_id": "evt_session",
                "session_id": "ses_a",
                "kind": "session.started",
                "payload": {"title": "A", "project_root": "/tmp/a"},
                "ts": "2026-04-22T10:00:00Z",
            },
            request_id="req-ingest",
        )
        self.assertTrue(result["ok"])

        next_message = from_json_line(await reader.readline())
        self.assertIn(next_message["kind"], {"session.status.changed", "interaction.opened", "snapshot.patch"})
        writer.close()
        await writer.wait_closed()

    async def test_interaction_response_round_trip(self) -> None:
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_session",
            "session_id": "ses_b",
            "kind": "session.started",
            "payload": {"title": "B", "project_root": "/tmp/b"},
            "ts": "2026-04-22T10:00:00Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_task",
            "session_id": "ses_b",
            "task_id": "task_b",
            "kind": "task.started",
            "payload": {"task_id": "task_b", "prompt": "approve me"},
            "ts": "2026-04-22T10:00:01Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_interaction",
            "session_id": "ses_b",
            "task_id": "task_b",
            "kind": "interaction.approval.requested",
            "payload": {"interaction_id": "int_b", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            "ts": "2026-04-22T10:00:02Z",
        })

        response = await rpc_call(self.socket_path, "interaction_respond", {
            "interaction_id": "int_b",
            "action": "approve",
            "answer": None,
        }, request_id="req-respond")
        self.assertTrue(response["ok"])

        session_result = await rpc_call(self.socket_path, "get_session", {"session_id": "ses_b"}, request_id="req-get")
        self.assertEqual(session_result["result"]["session"]["status"], "running")
        self.assertIn("activities", session_result["result"])
        self.assertIn("session_state", session_result["result"])
        self.assertIn("permission.resolved", [item["kind"] for item in session_result["result"]["activities"]])
        self.assertEqual(session_result["result"]["session_state"]["effective_status"], "running")

    async def test_interaction_respond_emits_resolved_event_to_subscriber(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(to_json_line({"id": "sub-resolved", "method": "subscribe", "params": {"topics": ["interactions"]}}))
        await writer.drain()
        _ = from_json_line(await reader.readline())

        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_session",
            "session_id": "ses_resolve",
            "kind": "session.started",
            "payload": {"title": "Resolve", "project_root": "/tmp/resolve"},
            "ts": "2026-04-22T10:00:00Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_task",
            "session_id": "ses_resolve",
            "task_id": "task_resolve",
            "kind": "task.started",
            "payload": {"task_id": "task_resolve", "prompt": "approve me"},
            "ts": "2026-04-22T10:00:01Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_interaction",
            "session_id": "ses_resolve",
            "task_id": "task_resolve",
            "kind": "interaction.approval.requested",
            "payload": {"interaction_id": "int_resolve", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            "ts": "2026-04-22T10:00:02Z",
        })

        await rpc_call(self.socket_path, "interaction_respond", {
            "interaction_id": "int_resolve",
            "action": "approve",
            "answer": None,
        }, request_id="req-respond")

        seen_resolved = False
        for _ in range(8):
            message = from_json_line(await reader.readline())
            if message.get("kind") == "interaction.resolved":
                self.assertEqual(message["payload"]["interaction_id"], "int_resolve")
                self.assertEqual(message["payload"]["answer_payload"]["action"], "approve")
                seen_resolved = True
                break

        self.assertTrue(seen_resolved)
        writer.close()
        await writer.wait_closed()

    async def test_question_deny_emits_resolved_event_to_subscriber(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(to_json_line({"id": "sub-question-deny", "method": "subscribe", "params": {"topics": ["interactions"]}}))
        await writer.drain()
        _ = from_json_line(await reader.readline())

        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_session_q",
            "session_id": "ses_question",
            "kind": "session.started",
            "payload": {"title": "Question", "project_root": "/tmp/question"},
            "ts": "2026-04-22T10:00:00Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_question_q",
            "session_id": "ses_question",
            "kind": "interaction.question.requested",
            "payload": {"interaction_id": "int_question", "prompt_text": "Which branch?", "options": ["main", "feature"]},
            "ts": "2026-04-22T10:00:01Z",
        })

        response = await rpc_call(self.socket_path, "interaction_respond", {
            "interaction_id": "int_question",
            "action": "deny",
            "answer": None,
        }, request_id="req-question-deny")
        self.assertTrue(response["ok"])

        seen_resolved = False
        for _ in range(8):
            message = from_json_line(await reader.readline())
            if message.get("kind") == "interaction.resolved":
                self.assertEqual(message["payload"]["interaction_id"], "int_question")
                self.assertEqual(message["payload"]["answer_payload"]["action"], "deny")
                self.assertIsNone(message["payload"]["answer_payload"]["answer"])
                seen_resolved = True
                break

        self.assertTrue(seen_resolved)
        writer.close()
        await writer.wait_closed()

    async def test_malformed_ingest_event_returns_structured_error(self) -> None:
        response = await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_bad",
            "session_id": "ses_bad",
            "payload": {},
        }, request_id="req-bad")

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_request")

    async def test_create_cancel_and_retry_task_methods(self) -> None:
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_session",
            "session_id": "ses_c",
            "kind": "session.started",
            "payload": {"title": "C", "project_root": "/tmp/c"},
            "ts": "2026-04-22T10:00:00Z",
        })

        created = await rpc_call(self.socket_path, "create_task", {
            "session_id": "ses_c",
            "prompt": "do work",
        }, request_id="req-create")
        self.assertTrue(created["ok"])
        task_id = created["result"]["task_id"]

        cancelled = await rpc_call(self.socket_path, "cancel_task", {"task_id": task_id}, request_id="req-cancel")
        self.assertTrue(cancelled["ok"])

        retried = await rpc_call(self.socket_path, "retry_task", {"task_id": task_id}, request_id="req-retry")
        self.assertTrue(retried["ok"])

    async def test_open_interaction_emitted_once(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(to_json_line({"id": "sub-open", "method": "subscribe", "params": {"topics": ["interactions"]}}))
        await writer.drain()
        _ = from_json_line(await reader.readline())

        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_session",
            "session_id": "ses_i",
            "kind": "session.started",
            "payload": {"title": "I", "project_root": "/tmp/i"},
            "ts": "2026-04-22T10:00:00Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_task",
            "session_id": "ses_i",
            "task_id": "task_i",
            "kind": "task.started",
            "payload": {"task_id": "task_i", "prompt": "hello"},
            "ts": "2026-04-22T10:00:01Z",
        })
        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_interaction",
            "session_id": "ses_i",
            "task_id": "task_i",
            "kind": "interaction.approval.requested",
            "payload": {"interaction_id": "int_i", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            "ts": "2026-04-22T10:00:02Z",
        })

        seen_open_events = 0
        for _ in range(4):
            message = from_json_line(await reader.readline())
            if message["kind"] == "interaction.opened":
                seen_open_events += 1

        await rpc_call(self.socket_path, "ingest_event", {
            "event_id": "evt_progress",
            "session_id": "ses_i",
            "task_id": "task_i",
            "kind": "task.progress",
            "payload": {"message": "still blocked"},
            "ts": "2026-04-22T10:00:03Z",
        })

        for _ in range(2):
            message = from_json_line(await reader.readline())
            if message["kind"] == "interaction.opened":
                seen_open_events += 1

        self.assertEqual(seen_open_events, 1)
        writer.close()
        await writer.wait_closed()

    async def test_snapshot_patch_includes_phase1_activity_fields(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        writer.write(to_json_line({"id": "sub-phase1", "method": "subscribe", "params": {"topics": ["sessions"]}}))
        await writer.drain()
        _ = from_json_line(await reader.readline())
        try:
            await rpc_call(self.socket_path, "ingest_event", {
                "event_id": "evt_session_phase1",
                "session_id": "ses_phase1",
                "kind": "session.started",
                "payload": {"title": "Phase1", "project_root": "/tmp/phase1"},
                "ts": "2026-04-22T10:00:00Z",
            })
            await rpc_call(self.socket_path, "ingest_event", {
                "event_id": "evt_task_phase1",
                "session_id": "ses_phase1",
                "task_id": "task_phase1",
                "kind": "task.started",
                "payload": {"task_id": "task_phase1", "prompt": "hello"},
                "ts": "2026-04-22T10:00:01Z",
            })
            await rpc_call(self.socket_path, "ingest_event", {
                "event_id": "evt_prompt_phase1",
                "session_id": "ses_phase1",
                "task_id": "task_phase1",
                "kind": "prompt.submitted",
                "payload": {"task_id": "task_phase1", "prompt": "hello"},
                "ts": "2026-04-22T10:00:02Z",
            })

            seen_patch = None
            for _ in range(20):
                try:
                    message = from_json_line(await asyncio.wait_for(reader.readline(), timeout=0.2))
                except TimeoutError:
                    break
                if message.get("kind") == "snapshot.patch" and any(item["session_id"] == "ses_phase1" for item in message["payload"]["sessions"]):
                    seen_patch = message

            self.assertIsNotNone(seen_patch)
            self.assertIn("activities", seen_patch["payload"])
            self.assertIn("session_states", seen_patch["payload"])
            self.assertIn("prompt.submitted", [item["kind"] for item in seen_patch["payload"]["activities"]])
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_broadcast_survives_subscriber_set_mutation(self) -> None:
        class MutatingWriter:
            def __init__(self, daemon: Phase0Daemon) -> None:
                self.daemon = daemon
                self.writes: list[bytes] = []

            def write(self, payload: bytes) -> None:
                self.writes.append(payload)

            async def drain(self) -> None:
                self.daemon.subscribers.discard(self)

        writer = MutatingWriter(self.daemon)
        self.daemon.subscribers.add(writer)

        await self.daemon._broadcast([{"kind": "snapshot.patch", "payload": {}}])

        self.assertEqual(len(writer.writes), 1)
        self.assertNotIn(writer, self.daemon.subscribers)
