from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from ...services import reap_lost_runs


class Command(BaseCommand):
    help = "Перевести запуски с протухшим heartbeat в статус «неизвестно»."

    def handle(self, *args: Any, **options: Any) -> None:
        # Запускать периодически (cron / celery beat): без этого потерянные
        # запуски навсегда останутся висеть в статусе «выполняется».
        count = reap_lost_runs()
        self.stdout.write(f"Помечено потерянными: {count}.")
