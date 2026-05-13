from __future__ import annotations

import unittest

from codeisland_linux.protocol import SNAPSHOT_ACTIVITY_SESSION_LIMIT, EventEnvelope, now_iso
from codeisland_linux.store import ContractError, InMemoryDaemonStore


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryDaemonStore()
        self.session_id = "ses_test"
        self.task_id = "task_test"

    def test_happy_path_transitions_to_completed(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Test Session", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id, "prompt": "hello"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_prompt_submitted",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="prompt.submitted",
            payload={"task_id": self.task_id, "prompt": "hello"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_assistant_response",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="assistant.response.completed",
            payload={"task_id": self.task_id, "message": "done"},
            ts=now_iso(),
        ))
        patches = self.store.apply_event(EventEnvelope(
            event_id="evt_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id, "summary": "done"},
            ts=now_iso(),
        ))

        self.assertEqual(self.store.sessions[self.session_id].status, "completed")
        self.assertEqual(self.store.tasks[self.task_id].status, "done")
        self.assertIsNone(self.store.sessions[self.session_id].current_task_id)
        self.assertTrue(any(item["kind"] == "snapshot.patch" for item in patches))
        state = self.store.session_states[self.session_id]
        self.assertEqual(state.last_user_prompt, "hello")
        self.assertEqual(state.last_assistant_message, "done")
        self.assertTrue(state.completion_pending)
        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        self.assertIn("activities", snapshot_patch["payload"])
        self.assertIn("session_states", snapshot_patch["payload"])
        self.assertEqual(snapshot_patch["payload"]["island_state"]["surface"], "completionCard")
        self.assertEqual(snapshot_patch["payload"]["island_state"]["active_session_id"], self.session_id)
        self.assertEqual(snapshot_patch["payload"]["island_state"]["completion_queue"], [self.session_id])
        self.assertIn("completion.enqueued", [activity["kind"] for activity in snapshot_patch["payload"]["activities"]])

    def test_old_completion_does_not_drive_island_surface(self) -> None:
        old_ts = "2020-01-01T00:00:00Z"
        self.store.apply_event(EventEnvelope(
            event_id="evt_old_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Old Session", "project_root": "/tmp/old"},
            ts=old_ts,
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_old_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id, "prompt": "hello"},
            ts=old_ts,
        ))
        patches = self.store.apply_event(EventEnvelope(
            event_id="evt_old_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id, "summary": "done"},
            ts=old_ts,
        ))

        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        island_state = snapshot_patch["payload"]["island_state"]
        self.assertEqual(island_state["surface"], "collapsed")
        self.assertEqual(island_state["status"], "completed")
        self.assertEqual(island_state["completion_queue"], [])

    def test_new_task_started_clears_completion_surface_before_prompt(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Test Session", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id, "prompt": "old"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id, "summary": "done"},
            ts=now_iso(),
        ))

        new_task_id = "task_new"
        patches = self.store.apply_event(EventEnvelope(
            event_id="evt_new_task_started",
            session_id=self.session_id,
            task_id=new_task_id,
            kind="task.started",
            payload={"task_id": new_task_id, "prompt": "new"},
            ts=now_iso(),
        ))

        state = self.store.session_states[self.session_id]
        self.assertFalse(state.completion_pending)
        self.assertIsNone(state.completion_enqueued_at)
        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        island_state = snapshot_patch["payload"]["island_state"]
        self.assertEqual(island_state["surface"], "collapsed")
        self.assertEqual(island_state["status"], "running")
        self.assertFalse(island_state["auto_reveal"])
        self.assertEqual(island_state["completion_queue"], [])

    def test_duplicate_event_is_ignored(self) -> None:
        event = EventEnvelope(
            event_id="evt_duplicate",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Dupe", "project_root": "/tmp/test"},
            ts=now_iso(),
        )
        self.store.apply_event(event)
        patches = self.store.apply_event(event)

        self.assertEqual(len(self.store.sessions), 1)
        self.assertEqual(patches[0]["kind"], "daemon.warning")

    def test_snapshot_activity_projection_keeps_recent_per_session(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Activity Cap", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))

        patches: list[dict[str, object]] = []
        for index in range(SNAPSHOT_ACTIVITY_SESSION_LIMIT + 8):
            patches = self.store.apply_event(EventEnvelope(
                event_id=f"evt_prompt_{index}",
                session_id=self.session_id,
                kind="prompt.submitted",
                payload={"prompt": f"prompt {index}"},
                ts=f"2026-04-22T10:{index:02d}:00Z",
            ))

        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        activities = snapshot_patch["payload"]["activities"]
        prompts = [activity["payload"]["prompt"] for activity in activities]
        self.assertEqual(len(activities), SNAPSHOT_ACTIVITY_SESSION_LIMIT)
        self.assertNotIn("prompt 0", prompts)
        self.assertIn(f"prompt {SNAPSHOT_ACTIVITY_SESSION_LIMIT + 7}", prompts)

    def test_session_started_preserves_provider_from_payload(self) -> None:
        patches = self.store.apply_event(EventEnvelope(
            event_id="evt_codex_session",
            session_id=self.session_id,
            kind="session.started",
            payload={
                "provider": "Codex",
                "title": "Codex",
                "project_root": "/tmp/codex",
                "terminal_app": "Ghostty",
                "terminal_pid": 4321,
                "terminal_pane": "7",
                "terminal_socket": "/run/user/1000/wezterm/gui-sock-4321",
                "cli_pid": 1234,
            },
            ts=now_iso(),
        ))

        self.assertEqual(self.store.sessions[self.session_id].provider, "codex")
        self.assertEqual(self.store.sessions[self.session_id].terminal_app, "Ghostty")
        self.assertEqual(self.store.sessions[self.session_id].terminal_pid, 4321)
        self.assertEqual(self.store.sessions[self.session_id].terminal_pane, "7")
        self.assertEqual(self.store.sessions[self.session_id].cli_pid, 1234)
        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        self.assertEqual(snapshot_patch["payload"]["sessions"][0]["provider"], "codex")
        self.assertEqual(snapshot_patch["payload"]["sessions"][0]["terminal_app"], "Ghostty")
        self.assertEqual(snapshot_patch["payload"]["sessions"][0]["terminal_pid"], 4321)

    def test_approval_response_is_idempotent(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Approval", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_approval",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_1", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            ts=now_iso(),
        ))

        patches = self.store.respond_to_interaction("int_1", "approve", None)

        self.assertEqual(self.store.sessions[self.session_id].status, "running")
        self.assertEqual(self.store.interactions["int_1"].state, "answered")
        self.assertTrue(any(item["kind"] == "interaction.resolved" for item in patches))

        with self.assertRaises(ContractError) as context:
            self.store.respond_to_interaction("int_1", "approve", None)
        self.assertEqual(context.exception.code, "duplicate_response")

    def test_snapshot_surface_prioritizes_approval_over_question(self) -> None:
        other_session_id = "ses_question"
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_approval",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Approval", "project_root": "/tmp/approval"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_question",
            session_id=other_session_id,
            kind="session.started",
            payload={"title": "Question", "project_root": "/tmp/question"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_question_surface",
            session_id=other_session_id,
            kind="interaction.question.requested",
            payload={"interaction_id": "int_question_surface", "prompt_text": "Which branch?"},
            ts=now_iso(),
        ))
        patches = self.store.apply_event(EventEnvelope(
            event_id="evt_approval_surface",
            session_id=self.session_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_approval_surface", "prompt_text": "Allow?"},
            ts=now_iso(),
        ))

        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        island_state = snapshot_patch["payload"]["island_state"]
        self.assertEqual(island_state["surface"], "approvalCard")
        self.assertTrue(island_state["auto_reveal"])
        self.assertEqual(island_state["active_session_id"], self.session_id)
        self.assertEqual(island_state["active_interaction_id"], "int_approval_surface")
        self.assertEqual(island_state["approval_queue"], ["int_approval_surface"])
        self.assertEqual(island_state["question_queue"], ["int_question_surface"])

    def test_known_invalid_event_does_not_advance_sequence(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Known Invalid", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))

        invalid_event = EventEnvelope(
            event_id="evt_invalid_task_started",
            session_id=self.session_id,
            kind="task.started",
            payload={},
            ts=now_iso(),
        )

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(invalid_event)
        self.assertEqual(context.exception.code, "invalid_request")
        self.assertNotIn("evt_invalid_task_started", self.store.seen_event_ids)
        self.assertEqual(self.store.next_seq, 2)

        valid_event = EventEnvelope(
            event_id="evt_valid_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        )
        self.store.apply_event(valid_event)
        self.assertEqual(valid_event.seq, 2)
        self.assertEqual(self.store.next_seq, 3)

    def test_unknown_event_does_not_poison_deduplication(self) -> None:
        event = EventEnvelope(
            event_id="evt_unknown",
            session_id=self.session_id,
            kind="totally.unknown",
            payload={},
            ts=now_iso(),
        )

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(event)
        self.assertEqual(context.exception.code, "invalid_request")
        self.assertNotIn("evt_unknown", self.store.seen_event_ids)
        self.assertEqual(self.store.next_seq, 1)

    def test_duplicate_open_interaction_is_ignored(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Approval", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_approval_1",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_dupe", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            ts=now_iso(),
        ))

        patches = self.store.apply_event(EventEnvelope(
            event_id="evt_approval_2",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_dupe", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            ts=now_iso(),
        ))

        warnings = [item for item in patches if item["kind"] == "daemon.warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["payload"]["message"], "duplicate_interaction_ignored")

    def test_session_ended_cancels_open_interactions(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Approval", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_approval",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_end", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            ts=now_iso(),
        ))

        self.store.apply_event(EventEnvelope(
            event_id="evt_end",
            session_id=self.session_id,
            kind="session.ended",
            payload={},
            ts=now_iso(),
        ))

        self.assertEqual(self.store.interactions["int_end"].state, "cancelled")
        self.assertEqual(self.store.tasks[self.task_id].status, "cancelled")
        self.assertIsNone(self.store.sessions[self.session_id].current_task_id)
        self.assertIsNotNone(self.store.sessions[self.session_id].ended_at)

    def test_approval_rejects_answer_action(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Approval", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_approval",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_bad_action", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.respond_to_interaction("int_bad_action", "answer", "yes")
        self.assertEqual(context.exception.code, "invalid_request")

    def test_question_requires_non_empty_answer(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Question", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_question",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.question.requested",
            payload={"interaction_id": "int_question", "prompt_text": "Why?"},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.respond_to_interaction("int_question", "answer", None)
        self.assertEqual(context.exception.code, "invalid_request")

    def test_question_deny_is_accepted(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Question", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_question",
            session_id=self.session_id,
            kind="interaction.question.requested",
            payload={"interaction_id": "int_question", "prompt_text": "Skip?", "options": ["yes", "no"]},
            ts=now_iso(),
        ))

        patches = self.store.respond_to_interaction("int_question", "deny", None)

        self.assertEqual(self.store.sessions[self.session_id].status, "running")
        self.assertEqual(self.store.interactions["int_question"].state, "answered")
        self.assertEqual(self.store.interactions["int_question"].answer_payload, {"action": "deny", "answer": None})
        self.assertTrue(any(item["kind"] == "interaction.resolved" for item in patches))

    def test_tool_activity_updates_derived_state(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Tool", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id, "prompt": "run tool"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_prompt",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="prompt.submitted",
            payload={"task_id": self.task_id, "prompt": "run tool"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_tool_start",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="tool.use.started",
            payload={"tool_name": "Bash", "input": {"command": "pwd"}},
            ts=now_iso(),
        ))

        state = self.store.session_states[self.session_id]
        self.assertEqual(state.active_tool_name, "Bash")
        self.assertEqual(state.active_tool_status, "running")

        self.store.apply_event(EventEnvelope(
            event_id="evt_tool_done",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="tool.use.completed",
            payload={"tool_name": "Bash", "result": {"exit_code": 0}},
            ts=now_iso(),
        ))

        state = self.store.session_states[self.session_id]
        self.assertIsNone(state.active_tool_name)
        self.assertIsNone(state.active_tool_status)

    def test_interaction_resolution_records_activity(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Approval", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_approval",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="interaction.approval.requested",
            payload={"interaction_id": "int_phase1", "prompt_text": "Allow?", "options": ["approve", "deny"]},
            ts=now_iso(),
        ))

        patches = self.store.respond_to_interaction("int_phase1", "approve", None)

        self.assertIn("permission.resolved", [event.kind for event in self.store.activities])
        snapshot_patch = next(item for item in patches if item["kind"] == "snapshot.patch")
        self.assertIn("permission.resolved", [activity["kind"] for activity in snapshot_patch["payload"]["activities"]])
        state = self.store.session_states[self.session_id]
        self.assertIsNone(state.pending_interaction_id)

    def test_create_task_rejects_terminal_session(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Terminal", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.create_task(self.session_id, "new prompt")
        self.assertEqual(context.exception.code, "invalid_request")

    def test_task_failed_clears_current_task(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Failure", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        self.store.apply_event(EventEnvelope(
            event_id="evt_task_failed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.failed",
            payload={"task_id": self.task_id, "error_code": "boom", "error_message": "failed"},
            ts=now_iso(),
        ))

        self.assertEqual(self.store.tasks[self.task_id].status, "error")
        self.assertIsNone(self.store.sessions[self.session_id].current_task_id)

    def test_cancel_task_clears_current_task(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Cancel", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        self.store.cancel_task(self.task_id)

        self.assertEqual(self.store.tasks[self.task_id].status, "cancelled")
        self.assertIsNone(self.store.sessions[self.session_id].current_task_id)

    def test_interaction_rejected_for_terminal_session_without_task_id(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Stale", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_question",
                session_id=self.session_id,
                kind="interaction.question.requested",
                payload={"interaction_id": "int_no_task", "prompt_text": "Why?"},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "invalid_request")

    def test_interaction_rejected_for_terminal_task_with_explicit_task_id(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Explicit Task", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_question_terminal_task",
                session_id=self.session_id,
                task_id=self.task_id,
                kind="interaction.question.requested",
                payload={"interaction_id": "int_terminal_task", "prompt_text": "Why?", "task_id": self.task_id},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "invalid_request")

    def test_interaction_rejected_for_unknown_explicit_task_id(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Unknown Task", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_question_unknown_task",
                session_id=self.session_id,
                task_id="task_missing",
                kind="interaction.question.requested",
                payload={"interaction_id": "int_unknown_task", "prompt_text": "Why?", "task_id": "task_missing"},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "unknown_task")

    def test_interaction_rejected_for_explicit_task_from_other_session(self) -> None:
        other_session_id = "ses_other"
        other_task_id = "task_other"
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_1",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Primary", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_2",
            session_id=other_session_id,
            kind="session.started",
            payload={"title": "Other", "project_root": "/tmp/test-other"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_other",
            session_id=other_session_id,
            task_id=other_task_id,
            kind="task.started",
            payload={"task_id": other_task_id},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_cross_interaction",
                session_id=self.session_id,
                task_id=other_task_id,
                kind="interaction.question.requested",
                payload={"interaction_id": "int_cross", "prompt_text": "Why?", "task_id": other_task_id},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "invalid_request")
        self.assertEqual(self.store.sessions[self.session_id].status, "running")
        self.assertEqual(self.store.sessions[other_session_id].status, "running")
        self.assertEqual(self.store.tasks[other_task_id].status, "running")
        self.assertNotIn("int_cross", self.store.interactions)

    def test_task_completed_rejected_for_task_from_other_session(self) -> None:
        other_session_id = "ses_other"
        other_task_id = "task_other"
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_1",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Primary", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_2",
            session_id=other_session_id,
            kind="session.started",
            payload={"title": "Other", "project_root": "/tmp/test-other"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_other",
            session_id=other_session_id,
            task_id=other_task_id,
            kind="task.started",
            payload={"task_id": other_task_id},
            ts=now_iso(),
        ))

        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_cross_complete",
                session_id=self.session_id,
                task_id=other_task_id,
                kind="task.completed",
                payload={"task_id": other_task_id},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "invalid_request")
        self.assertEqual(self.store.sessions[self.session_id].status, "running")
        self.assertEqual(self.store.sessions[other_session_id].status, "running")
        self.assertEqual(self.store.tasks[other_task_id].status, "running")

    def test_terminal_session_started_does_not_reactivate_without_new_task(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Terminal", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_completed",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.completed",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        self.store.apply_event(EventEnvelope(
            event_id="evt_session_restart",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Terminal Restart", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))

        self.assertEqual(self.store.sessions[self.session_id].status, "completed")
        self.assertIsNone(self.store.sessions[self.session_id].current_task_id)

    def test_task_started_rejected_for_existing_task_from_other_session(self) -> None:
        other_session_id = "ses_other"
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_primary",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Primary", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_primary",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_session_other",
            session_id=other_session_id,
            kind="session.started",
            payload={"title": "Other", "project_root": "/tmp/other"},
            ts=now_iso(),
        ))

        next_seq_before = self.store.next_seq
        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_task_cross",
                session_id=other_session_id,
                task_id=self.task_id,
                kind="task.started",
                payload={"task_id": self.task_id},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "invalid_request")
        self.assertEqual(self.store.tasks[self.task_id].session_id, self.session_id)
        self.assertEqual(self.store.sessions[self.session_id].current_task_id, self.task_id)
        self.assertIsNone(self.store.sessions[other_session_id].current_task_id)
        self.assertNotIn("evt_task_cross", self.store.seen_event_ids)
        self.assertEqual(self.store.next_seq, next_seq_before)

    def test_task_started_rejected_for_duplicate_task_in_same_session(self) -> None:
        self.store.apply_event(EventEnvelope(
            event_id="evt_session",
            session_id=self.session_id,
            kind="session.started",
            payload={"title": "Duplicate", "project_root": "/tmp/test"},
            ts=now_iso(),
        ))
        self.store.apply_event(EventEnvelope(
            event_id="evt_task_started",
            session_id=self.session_id,
            task_id=self.task_id,
            kind="task.started",
            payload={"task_id": self.task_id},
            ts=now_iso(),
        ))

        next_seq_before = self.store.next_seq
        with self.assertRaises(ContractError) as context:
            self.store.apply_event(EventEnvelope(
                event_id="evt_task_started_duplicate",
                session_id=self.session_id,
                task_id=self.task_id,
                kind="task.started",
                payload={"task_id": self.task_id},
                ts=now_iso(),
            ))
        self.assertEqual(context.exception.code, "invalid_request")
        self.assertEqual(self.store.tasks[self.task_id].session_id, self.session_id)
        self.assertEqual(self.store.tasks[self.task_id].status, "running")
        self.assertEqual(self.store.sessions[self.session_id].current_task_id, self.task_id)
        self.assertNotIn("evt_task_started_duplicate", self.store.seen_event_ids)
        self.assertEqual(self.store.next_seq, next_seq_before)
