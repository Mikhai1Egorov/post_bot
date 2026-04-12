from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.domain.transitions import ensure_task_transition, is_task_final  # noqa: E402
from post_bot.shared.enums import TaskStatus  # noqa: E402
from post_bot.shared.errors import BusinessRuleError  # noqa: E402

class TaskTransitionTests(unittest.TestCase):
    @staticmethod
    def test_happy_path_instant() -> None:
        sequence = [
            TaskStatus.CREATED,
            TaskStatus.QUEUED,
            TaskStatus.PREPARING,
            TaskStatus.RESEARCHING,
            TaskStatus.GENERATING,
            TaskStatus.RENDERING,
            TaskStatus.PUBLISHING,
            TaskStatus.DONE,
        ]
        for old, new in zip(sequence, sequence[1:]):
            ensure_task_transition(old, new)

    @staticmethod
    def test_happy_path_approval_download() -> None:
        sequence = [
            TaskStatus.CREATED,
            TaskStatus.QUEUED,
            TaskStatus.PREPARING,
            TaskStatus.RESEARCHING,
            TaskStatus.GENERATING,
            TaskStatus.RENDERING,
            TaskStatus.READY_FOR_APPROVAL,
            TaskStatus.DONE,
        ]
        for old, new in zip(sequence, sequence[1:]):
            ensure_task_transition(old, new)

    @staticmethod
    def test_retry_requeue_transitions() -> None:
        ensure_task_transition(TaskStatus.PREPARING, TaskStatus.QUEUED)
        ensure_task_transition(TaskStatus.RESEARCHING, TaskStatus.QUEUED)
        ensure_task_transition(TaskStatus.GENERATING, TaskStatus.QUEUED)

    def test_delivery_transitions_do_not_fall_back_to_content_queue(self) -> None:
        with self.assertRaises(BusinessRuleError):
            ensure_task_transition(TaskStatus.PUBLISHING, TaskStatus.QUEUED)
        with self.assertRaises(BusinessRuleError):
            ensure_task_transition(TaskStatus.READY_FOR_APPROVAL, TaskStatus.QUEUED)

    def test_illegal_backward_transitions_are_rejected(self) -> None:
        with self.assertRaises(BusinessRuleError):
            ensure_task_transition(TaskStatus.READY_FOR_APPROVAL, TaskStatus.PREPARING)
        with self.assertRaises(BusinessRuleError):
            ensure_task_transition(TaskStatus.PUBLISHING, TaskStatus.PREPARING)
        with self.assertRaises(BusinessRuleError):
            ensure_task_transition(TaskStatus.DONE, TaskStatus.QUEUED)
    def test_invalid_transition_raises(self) -> None:
        with self.assertRaises(BusinessRuleError):
            ensure_task_transition(TaskStatus.CREATED, TaskStatus.GENERATING)

    def test_final_state_helper(self) -> None:
        self.assertTrue(is_task_final(TaskStatus.DONE))
        self.assertTrue(is_task_final(TaskStatus.FAILED))
        self.assertTrue(is_task_final(TaskStatus.CANCELLED))
        self.assertFalse(is_task_final(TaskStatus.RENDERING))

if __name__ == "__main__":
    unittest.main()
