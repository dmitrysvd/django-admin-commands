"""Белый список команд.

Реестр живёт в коде сознательно: конфигурация в БД может только *сужать*
возможности инструмента (см. рубильник ``CommandState``), но не расширять их.
Поэтому добавление команды проходит через ревью кода и оставляет след в истории
версий.

Регистрировать команды следует в модуле ``exec_commands.py`` любого приложения::

    from django_exec_tool import CommandSpec, registry

    registry.register(
        CommandSpec(
            name="recalculate_stats",
            title="Recalculate statistics",
            timeout=3600,
            nice=10,
            lock_key=lambda args: args.get("tenant"),
        )
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from django.core.management import get_commands, load_command_class
from django.utils.module_loading import autodiscover_modules


class CommandNotRegistered(LookupError):
    """Команды нет в белом списке."""


@dataclass(frozen=True)
class CommandSpec:
    """Объявленные свойства одной запускаемой команды.

    Спека — это декларация безопасности, а не просто имя: инструмент не может
    сделать небезопасную команду безопасной, он может только не дать её запустить.
    """

    name: str
    title: str = ""
    description: str = ""
    #: Ограничение по времени в секундах; ``None`` — берётся ``DEFAULT_TIMEOUT``.
    timeout: int | None = None
    #: Сколько запусков *этой* команды может быть активно одновременно.
    max_parallel: int = 1
    #: ``(arguments) -> str | None``; запуски с одним ключом не пересекаются.
    lock_key: Callable[[dict[str, Any]], str | None] | None = None
    #: Можно ли прерывать команду, не рискуя целостностью данных.
    interruptible: bool = False
    #: Безопасен ли повторный запуск с самого начала.
    idempotent: bool = False
    #: Требовать ввод имени команды в форме перед запуском.
    confirm: bool = False
    #: Аргументы, зафиксированные спекой: в форме не видны, применяются всегда.
    fixed_arguments: dict[str, Any] = field(default_factory=dict)
    #: Аргументы, скрытые из формы (остаются со значением по умолчанию).
    hidden_arguments: Iterable[str] = ()
    #: Базовые опции Django (``verbosity``, ``traceback``, ...), которые всё же показать.
    extra_base_options: Iterable[str] = ()
    #: Приоритет CPU / IO для дочернего процесса.
    nice: int = 0
    ionice: int | None = None
    #: Очередь Celery, используется только Celery-раннером.
    queue: str | None = None
    #: Дополнительное право поверх общего, например ``"billing.run_payouts"``.
    permission: str | None = None

    @property
    def label(self) -> str:
        return self.title or self.name

    def effective_timeout(self) -> int:
        from .conf import exec_tool_settings

        return self.timeout or exec_tool_settings.DEFAULT_TIMEOUT

    def resolve_lock_key(self, arguments: dict[str, Any]) -> str | None:
        if self.lock_key is None:
            return None
        return self.lock_key(arguments)

    def load_command(self) -> Any:
        """Импортировать реальный ``BaseCommand``, стоящий за спекой."""
        app_name = get_commands().get(self.name)
        if app_name is None:
            raise CommandNotRegistered(
                f"Команда {self.name!r} зарегистрирована в django-exec-tool, "
                f"но отсутствует в проекте."
            )
        if not isinstance(app_name, str):  # уже готовый экземпляр BaseCommand
            return app_name
        return load_command_class(app_name, self.name)


class Registry:
    """Белый список запускаемых команд, живущий в памяти процесса."""

    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}
        self._discovered = False

    def register(self, spec: CommandSpec) -> CommandSpec:
        if spec.name in self._specs:
            raise ValueError(f"Команда {spec.name!r} уже зарегистрирована")
        self._specs[spec.name] = spec
        return spec

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)

    def clear(self) -> None:
        self._specs.clear()
        self._discovered = False

    def autodiscover(self) -> None:
        if not self._discovered:
            self._discovered = True
            autodiscover_modules("exec_commands")

    def get(self, name: str) -> CommandSpec:
        self.autodiscover()
        try:
            return self._specs[name]
        except KeyError:
            raise CommandNotRegistered(
                f"Команда {name!r} не входит в белый список запускаемых из админки."
            ) from None

    def all(self) -> list[CommandSpec]:
        self.autodiscover()
        return sorted(self._specs.values(), key=lambda spec: spec.label.lower())

    def __contains__(self, name: object) -> bool:
        self.autodiscover()
        return name in self._specs


registry = Registry()
