from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AdminCommandsConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "django_admin_commands"
    # Короткая метка приложения: право читается как admin_commands.run_command,
    # а таблицы получают префикс admin_commands_. Так же устроены contrib-приложения.
    label = "admin_commands"
    verbose_name = _("Запуск команд")

    def ready(self) -> None:
        from .registry import registry

        # Забираем admin_commands.py из установленных приложений — так же, как
        # админка забирает admin.py.
        registry.autodiscover()
