"""Команда с разнообразными аргументами — проверка генерации формы."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Печатает переданные аргументы: видно, как форма собирает argv."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("target", help="Позиционный аргумент.")
        parser.add_argument("--limit", type=int, default=100, help="Числовое поле.")
        parser.add_argument("--ratio", type=float, default=1.0, help="Дробное поле.")
        parser.add_argument("--dry-run", action="store_true", help="Флаг.")
        parser.add_argument("--tag", action="append", help="Повторяемый аргумент.")
        parser.add_argument("--fields", nargs="*", default=[], help="Список значений.")

    def handle(self, *args: Any, **options: Any) -> None:
        for key in ("target", "limit", "ratio", "dry_run", "tag", "fields"):
            self.stdout.write(f"{key} = {options[key]!r}")
