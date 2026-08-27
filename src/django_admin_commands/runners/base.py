from __future__ import annotations

from typing import Any


class BaseRunner:
    """Интерфейс раннера.

    Раннер отвечает только за доставку: «возьми этот Run и начни его исполнять».
    Вся логика выполнения живёт в ``executor``, поэтому смена раннера
    (нить → Celery) не меняет ни поведение, ни статусы.
    """

    def enqueue(self, run: Any) -> None:
        raise NotImplementedError
