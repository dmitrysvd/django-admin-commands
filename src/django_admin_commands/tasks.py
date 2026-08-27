"""Celery-задача. Импортируется только когда используется CeleryRunner."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from .executor import execute


@shared_task(
    name="django_admin_commands.execute_run",
    # Подтверждаем задачу сразу: при acks_late убийство воркера привело бы к
    # повторной доставке, то есть к повторному запуску неидемпотентной команды
    # на проде. Потерянный запуск безопаснее продублированного — его поймает
    # heartbeat и переведёт в unknown.
    acks_late=False,
    ignore_result=True,
)
def execute_run(run_id: str) -> Any:
    return execute(run_id)
