"""Команда, игнорирующая SIGTERM, — проверка эскалации мягкой остановки."""

from __future__ import annotations

import signal
import time
from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ловит SIGTERM и продолжает работать; останавливается только SIGKILL."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--seconds", type=int, default=60)

    def handle(self, *args: Any, **options: Any) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stdout.write("SIGTERM проигнорирован"))
        self.stdout.write("упрямая команда стартовала")
        self.stdout.flush()
        for _tick in range(options["seconds"]):
            time.sleep(0.2)
