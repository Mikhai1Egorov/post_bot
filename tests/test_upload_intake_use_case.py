from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.upload_intake import UploadIntakeCommand, UploadIntakeUseCase  # noqa: E402
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import UploadStatus  # noqa: E402

class UploadIntakeUseCaseTests(unittest.TestCase):
    def test_creates_received_upload_and_stores_payload(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()
        use_case = UploadIntakeUseCase(uow=uow, file_storage=storage, logger=logging.getLogger("test.upload_intake"))

        result = use_case.execute(
            UploadIntakeCommand(user_id=10, original_filename="tasks.xlsx", payload=b"xlsx-bytes")
        )

        self.assertEqual(result.upload_id, 1)
        upload = uow.uploads.uploads[result.upload_id]
        self.assertEqual(upload.upload_status, UploadStatus.RECEIVED)
        self.assertEqual(upload.user_id, 10)
        self.assertEqual(upload.original_filename, "tasks.xlsx")
        self.assertEqual(storage.read_bytes(result.storage_path), b"xlsx-bytes")

if __name__ == "__main__":
    unittest.main()