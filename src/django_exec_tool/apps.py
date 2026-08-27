from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExecToolConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "django_exec_tool"
    verbose_name = _("Запуск команд")

    def ready(self) -> None:
        from .registry import registry

        # Забираем exec_commands.py из установленных приложений — так же, как
        # админка забирает admin.py.
        registry.autodiscover()
