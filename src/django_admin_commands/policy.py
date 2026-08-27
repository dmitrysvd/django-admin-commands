"""Единственная точка проверки прав доступа.

Сегодня все команды из белого списка закрыты одним правом. Когда этого станет
мало («Пете — только эти три»), правка ляжет сюда и больше никуда, потому что
вьюхам запрещено проверять права самостоятельно.
"""

from __future__ import annotations

from typing import Any

from .conf import app_settings
from .registry import CommandSpec


def default_policy(user: Any, spec: CommandSpec, arguments: dict[str, Any]) -> bool:
    if not (user and user.is_active and user.is_authenticated):
        return False
    if not user.has_perm(app_settings.PERMISSION):
        return False
    return not (spec.permission and not user.has_perm(spec.permission))


def can_run(user: Any, spec: CommandSpec, arguments: dict[str, Any] | None = None) -> bool:
    """Спросить настроенную политику, можно ли ``user`` запускать ``spec``."""
    return bool(app_settings.POLICY(user, spec, arguments or {}))


def can_use_tool(user: Any) -> bool:
    """Грубая проверка: показывать ли инструмент вообще."""
    return bool(
        user and user.is_active and user.is_authenticated and user.has_perm(app_settings.PERMISSION)
    )
