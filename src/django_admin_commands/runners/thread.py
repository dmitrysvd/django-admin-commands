from __future__ import annotations

import threading
from typing import Any

from .base import BaseRunner


class ThreadRunner(BaseRunner):
    """Запуск супервизора в фоновом потоке текущего процесса.

    Годится для разработки и небольших установок: тяжёлую работу всё равно
    делает отдельный процесс, поток лишь ждёт его и обновляет строку. Минус —
    рестарт веб-процесса оставит запуск без супервизора; heartbeat это заметит и
    честно переведёт запуск в ``unknown``.
    """

    def enqueue(self, run: Any) -> None:
        from ..executor import execute

        thread = threading.Thread(
            target=execute, args=(run.pk,), name=f"admin-commands-{run.pk}", daemon=True
        )
        thread.start()
