"""Долгая команда — на ней видно живой вывод, таймаут и остановку."""

from __future__ import annotations

import sys
import time
from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Тикает раз в секунду; заготовка для проверки остановки и таймаутов."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--seconds", type=int, default=30, help="Сколько тикать.")
        parser.add_argument("--fail", action="store_true", help="Упасть в конце.")
        parser.add_argument(
            "--mode",
            choices=["fast", "slow"],
            default="fast",
            help="Демонстрация выбора из списка.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        for tick in range(1, options["seconds"] + 1):
            self.stdout.write(f"тик {tick}/{options['seconds']} ({options['mode']})")
            self.stdout.flush()
            time.sleep(1)
        if options["fail"]:
            self.stderr.write("падаем по требованию")
            sys.exit(3)
        self.stdout.write("готово")
