from __future__ import annotations

import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from post_bot.application.use_cases.cleanup_non_final_artifacts import (  # noqa: E402
    CleanupNonFinalArtifactsCommand,
    CleanupNonFinalArtifactsUseCase,
)
from post_bot.infrastructure.testing.in_memory import InMemoryFileStorage, InMemoryUnitOfWork  # noqa: E402
from post_bot.shared.enums import ArtifactType  # noqa: E402

class CleanupNonFinalArtifactsUseCaseTests(unittest.TestCase):
    def test_cleanup_deletes_only_non_final_artifacts(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        non_final_path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.PREVIEW,
            file_name="tmp_preview.txt",
            content=b"temp",
        )
        final_path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.HTML,
            file_name="final.html",
            content=b"<article></article>",
        )

        non_final = uow.artifacts.add_artifact(
            task_id=1,
            upload_id=10,
            artifact_type=ArtifactType.PREVIEW,
            storage_path=non_final_path,
            file_name="tmp_preview.txt",
            mime_type="text/plain",
            size_bytes=4,
            is_final=False,
        )
        final = uow.artifacts.add_artifact(
            task_id=1,
            upload_id=10,
            artifact_type=ArtifactType.HTML,
            storage_path=final_path,
            file_name="final.html",
            mime_type="text/html",
            size_bytes=20,
            is_final=True,
        )

        use_case = CleanupNonFinalArtifactsUseCase(
            uow=uow,
            artifact_storage=storage,
            logger=logging.getLogger("test.cleanup"),
        )

        result = use_case.execute(CleanupNonFinalArtifactsCommand(dry_run=False))

        self.assertEqual(result.scanned_count, 1)
        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(result.deleted_artifact_ids, (non_final.id,))

        self.assertIsNone(uow.artifacts.get_by_id(non_final.id))
        self.assertIsNotNone(uow.artifacts.get_by_id(final.id))

        with self.assertRaises(KeyError):
            storage.read_bytes(non_final_path)
        self.assertEqual(storage.read_bytes(final_path), b"<article></article>")

    def test_cleanup_dry_run(self) -> None:
        uow = InMemoryUnitOfWork()
        storage = InMemoryFileStorage()

        path = storage.save_task_artifact(
            task_id=1,
            artifact_type=ArtifactType.PREVIEW,
            file_name="tmp_preview.txt",
            content=b"temp",
        )
        artifact = uow.artifacts.add_artifact(
            task_id=1,
            upload_id=10,
            artifact_type=ArtifactType.PREVIEW,
            storage_path=path,
            file_name="tmp_preview.txt",
            mime_type="text/plain",
            size_bytes=4,
            is_final=False,
        )

        use_case = CleanupNonFinalArtifactsUseCase(
            uow=uow,
            artifact_storage=storage,
            logger=logging.getLogger("test.cleanup"),
        )
        result = use_case.execute(CleanupNonFinalArtifactsCommand(dry_run=True))

        self.assertEqual(result.scanned_count, 1)
        self.assertEqual(result.deleted_count, 0)
        self.assertIsNotNone(uow.artifacts.get_by_id(artifact.id))
        self.assertEqual(storage.read_bytes(path), b"temp")


if __name__ == "__main__":
    unittest.main()