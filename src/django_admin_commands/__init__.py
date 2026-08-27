"""django-admin-commands — безопасный запуск management-команд из админки."""

from __future__ import annotations

# Реестр импортируется сразу: он не трогает модели, поэтому безопасен до
# готовности приложений Django. Имя ``registry`` намеренно перекрывает
# одноимённый подмодуль — снаружи нужен именно объект реестра.
from .registry import CommandNotRegistered, CommandSpec, registry

__all__ = ["CommandNotRegistered", "CommandSpec", "registry"]
__version__ = "0.1.0"
