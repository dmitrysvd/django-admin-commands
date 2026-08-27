"""Команда с субпарсерами — форма запуска такое отобразить не умеет."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Демонстрация неподдерживаемой формы аргументов."

    def add_arguments(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="action")
        sub.add_parser("start")
        sub.add_parser("stop")

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(str(options.get("action")))
