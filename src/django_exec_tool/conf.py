"""Доступ к настройкам django-exec-tool.

Все параметры лежат в одном словаре ``EXEC_TOOL`` в настройках проекта, чтобы
не засорять глобальное пространство имён::

    EXEC_TOOL = {
        "RUNNER": "django_exec_tool.runners.thread.ThreadRunner",
        "DEFAULT_TIMEOUT": 900,
    }
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

DEFAULTS: dict[str, Any] = {
    # Путь импорта раннера, который забирает pending-запуск и начинает исполнение.
    "RUNNER": "django_exec_tool.runners.thread.ThreadRunner",
    # Потолок времени выполнения, если команда не объявила собственный таймаут.
    "DEFAULT_TIMEOUT": 900,
    # Период записи heartbeat; он же интервал опроса запроса на остановку.
    "HEARTBEAT_INTERVAL": 5,
    # Запуск считается потерянным, если heartbeat старше
    # HEARTBEAT_INTERVAL * HEARTBEAT_MISS_FACTOR.
    "HEARTBEAT_MISS_FACTOR": 4,
    # Пауза между SIGTERM и SIGKILL при жёсткой остановке.
    "TERMINATE_GRACE": 20,
    # Максимум одновременно активных запусков по всем командам.
    "MAX_PARALLEL_RUNS": 3,
    # Сколько байт вывода хранить прямо в строке Run для быстрого просмотра.
    "TAIL_BYTES": 64 * 1024,
    # Складывать полный лог в default_storage по завершении запуска.
    "ARCHIVE_OUTPUT": True,
    "ARCHIVE_DIR": "exec_tool/logs",
    # Удалять чанки живого вывода после архивации полного лога.
    "PURGE_CHUNKS_AFTER_ARCHIVE": True,
    # Просить ядро убить потомка вместе с супервизором (только Linux).
    "PARENT_DEATH_SIGNAL": True,
    # Дополнительные переменные окружения для дочернего процесса.
    "CHILD_ENV": {},
    # Право, без которого инструмент недоступен вообще.
    "PERMISSION": "django_exec_tool.run_command",
    # Вызываемое ``(user, spec, arguments) -> bool``; единственная точка проверки прав.
    "POLICY": "django_exec_tool.policy.default_policy",
}


class ExecToolSettings:
    """Ленивое представление ``settings.EXEC_TOOL`` с подставленными умолчаниями."""

    _IMPORT_STRINGS = frozenset({"RUNNER", "POLICY"})

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        if name not in DEFAULTS:
            raise AttributeError(f"Unknown EXEC_TOOL setting: {name!r}")
        if name in self._cache:
            return self._cache[name]
        value = getattr(settings, "EXEC_TOOL", {}).get(name, DEFAULTS[name])
        if name in self._IMPORT_STRINGS and isinstance(value, str):
            value = import_string(value)
        self._cache[name] = value
        return value

    def reset(self) -> None:
        self._cache.clear()


exec_tool_settings = ExecToolSettings()


@receiver(setting_changed)
def _reset_settings(sender: Any, setting: str, **kwargs: Any) -> None:
    # Нужно, чтобы override_settings в тестах не подсовывал устаревший кэш.
    if setting == "EXEC_TOOL":
        exec_tool_settings.reset()
