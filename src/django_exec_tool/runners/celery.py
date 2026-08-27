from __future__ import annotations

from typing import Any

from .base import BaseRunner


class CeleryRunner(BaseRunner):
    """Запуск супервизора celery-задачей.

    Очередь стоит выделить отдельную (``CommandSpec.queue``): иначе первый же
    длинный запуск займёт воркеров общей очереди и остановит письма, вебхуки и
    прочую фоновую рутину.
    """

    def enqueue(self, run: Any) -> None:
        from ..tasks import execute_run

        spec = run.spec()
        options = {"queue": spec.queue} if spec.queue else {}
        result = execute_run.apply_async(args=[str(run.pk)], **options)
        type(run).objects.filter(pk=run.pk).update(runner_ref=f"celery:{result.id}")
