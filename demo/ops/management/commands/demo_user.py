"""Заводит учётки демо-стенда: администратора и оператора с одним правом."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import Permission, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Создаёт admin/admin и operator/operator для ручной проверки."

    def handle(self, *args: Any, **options: Any) -> None:
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", "admin")
            self.stdout.write("создан admin/admin")

        operator, created = User.objects.get_or_create(
            username="operator", defaults={"is_staff": True}
        )
        if created:
            operator.set_password("operator")
            operator.save()
        # Оператору выдаётся ровно одно право — то самое, ради которого
        # инструмент и делался.
        operator.user_permissions.add(Permission.objects.get(codename="run_command"))
        self.stdout.write("оператор operator/operator готов")
