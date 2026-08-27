"""Генерация формы запуска из argparse-парсера команды.

Описание аргументов у management-команды уже есть — в ``create_parser()``. Писать
его второй раз не нужно: типы, choices, обязательность и help берутся оттуда.

Смысл не в экономии кода. Права доступа защищают от злоупотребления, а
типизированная форма — от ошибки: забытого ``--dry-run``, лишнего нуля в
``--limit``, даты не того года. Именно так роняют прод люди с доступом, а не
злоумышленники. Плюс аргументы попадают в журнал структурой, а не строкой, и
запуски становится можно сравнивать между собой.
"""

from __future__ import annotations

import argparse
import shlex
from typing import Any, Iterable

from django import forms
from django.utils.translation import gettext_lazy as _

from .registry import CommandSpec

#: Служебные опции Django, которые не имеет смысла показывать оператору.
BASE_OPTION_DESTS = frozenset(
    {
        "help",
        "version",
        "verbosity",
        "settings",
        "pythonpath",
        "traceback",
        "no_color",
        "force_color",
        "skip_checks",
    }
)

FIELD_PREFIX = "arg_"


class UnsupportedCommand(Exception):
    """Парсер команды использует конструкции, которые форма не умеет отобразить."""


def get_parser(spec: CommandSpec) -> argparse.ArgumentParser:
    command = spec.load_command()
    return command.create_parser("manage.py", spec.name)


def visible_actions(spec: CommandSpec) -> list[argparse.Action]:
    """Действия argparse, которые нужно показать в форме."""
    actions = []
    for action in get_parser(spec)._actions:
        if isinstance(action, argparse._SubParsersAction):
            raise UnsupportedCommand(
                f"Команда {spec.name!r} использует субпарсеры — форма запуска не умеет "
                f"их отображать. Заведите под неё отдельную команду."
            )
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            continue
        if action.dest == argparse.SUPPRESS or action.dest in spec.fixed_arguments:
            continue
        if action.dest in spec.hidden_arguments:
            continue
        if action.dest in BASE_OPTION_DESTS and action.dest not in spec.extra_base_options:
            continue
        actions.append(action)
    return actions


def all_actions(spec: CommandSpec) -> dict[str, argparse.Action]:
    """Все действия парсера по dest — нужны для сборки argv, включая скрытые."""
    return {action.dest: action for action in get_parser(spec)._actions}


def _help_text(action: argparse.Action) -> str:
    if not action.help:
        return ""
    try:
        # В help нередко встречается %(default)s — подставляем, а не падаем.
        return action.help % vars(action)
    except (KeyError, TypeError, ValueError):
        return action.help


def _is_positional(action: argparse.Action) -> bool:
    return not action.option_strings


def _is_list(action: argparse.Action) -> bool:
    return action.nargs in ("*", "+") or isinstance(action.nargs, int)


def _field_required(action: argparse.Action) -> bool:
    if _is_positional(action):
        return action.nargs not in ("?", "*")
    return bool(action.required)


def field_for_action(action: argparse.Action) -> forms.Field:
    """Подобрать поле формы под одно действие argparse."""
    label = str(action.metavar or action.dest.replace("_", " "))
    help_text = _help_text(action)
    required = _field_required(action)

    if isinstance(action, argparse._CountAction):
        return forms.IntegerField(
            label=label,
            help_text=help_text,
            required=False,
            min_value=0,
            initial=action.default or 0,
        )

    if isinstance(
        action,
        (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._StoreConstAction),
    ):
        return forms.BooleanField(
            label=label, help_text=help_text, required=False, initial=bool(action.default)
        )

    if isinstance(action, argparse._AppendAction):
        return forms.CharField(
            label=label,
            help_text=f"{help_text} {_('По одному значению в строке.')}".strip(),
            required=required,
            widget=forms.Textarea(attrs={"rows": 3}),
        )

    if action.choices and not _is_list(action):
        choices = [(str(choice), str(choice)) for choice in action.choices]
        if not required:
            choices.insert(0, ("", "—"))
        return forms.ChoiceField(
            label=label,
            help_text=help_text,
            required=required,
            choices=choices,
            initial=action.default,
        )

    if _is_list(action):
        default = action.default if isinstance(action.default, (list, tuple)) else ()
        return forms.CharField(
            label=label,
            help_text=f"{help_text} {_('Через пробел; значения с пробелами — в кавычках.')}".strip(),  # noqa: E501
            required=required,
            initial=" ".join(str(item) for item in default),
        )

    if action.type is int:
        return forms.IntegerField(
            label=label, help_text=help_text, required=required, initial=action.default
        )
    if action.type is float:
        return forms.FloatField(
            label=label, help_text=help_text, required=required, initial=action.default
        )
    return forms.CharField(
        label=label, help_text=help_text, required=required, initial=action.default
    )


class BaseLaunchForm(forms.Form):
    """Базовая форма запуска: причина, подтверждение и поля аргументов."""

    spec: CommandSpec
    actions: list[argparse.Action]

    reason = forms.CharField(
        label=_("Причина"),
        required=False,
        widget=forms.TextInput(attrs={"size": 60}),
        help_text=_(
            "Свободная пометка, сохраняется вместе с запуском: номер тикета, инцидент, что угодно."
        ),
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.spec.confirm and cleaned.get("confirmation") != self.spec.name:
            self.add_error(
                "confirmation",
                _("Введите имя команды в точности, чтобы подтвердить."),
            )
        return cleaned

    def arguments(self) -> dict[str, Any]:
        """Значения аргументов по dest, включая зафиксированные спекой."""
        values: dict[str, Any] = dict(self.spec.fixed_arguments)
        for action in self.actions:
            values[action.dest] = self.cleaned_data.get(FIELD_PREFIX + action.dest)
        return values


def build_form_class(spec: CommandSpec) -> type:
    """Собрать класс формы запуска для конкретной команды."""
    actions = visible_actions(spec)
    attrs: dict[str, Any] = {"spec": spec, "actions": actions}
    if spec.confirm:
        attrs["confirmation"] = forms.CharField(
            label=_("Подтверждение"),
            help_text=_("Команда помечена как опасная. Введите её имя, чтобы продолжить."),
        )
    for action in actions:
        attrs[FIELD_PREFIX + action.dest] = field_for_action(action)
    return type("LaunchForm", (BaseLaunchForm,), attrs)


def _split_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return shlex.split(str(value))


def _flag(action: argparse.Action) -> str:
    # Длинная форма опции читается в журнале лучше короткой.
    return max(action.option_strings, key=len)


def _argv_for_action(action: argparse.Action, value: Any) -> list[str]:
    if isinstance(action, argparse._StoreTrueAction):
        return [_flag(action)] if value else []
    if isinstance(action, argparse._StoreFalseAction):
        return [] if value else [_flag(action)]
    if isinstance(action, argparse._StoreConstAction):
        return [_flag(action)] if value else []
    if isinstance(action, argparse._CountAction):
        return [_flag(action)] * int(value or 0)
    if isinstance(action, argparse._AppendAction):
        items = [line.strip() for line in str(value or "").splitlines() if line.strip()]
        return [part for item in items for part in (_flag(action), item)]
    if _is_list(action):
        items = _split_list(value)
        if _is_positional(action):
            return items
        return [_flag(action), *items] if items else []
    if value in (None, ""):
        return []
    if _is_positional(action):
        return [str(value)]
    return [_flag(action), str(value)]


def build_argv(spec: CommandSpec, arguments: dict[str, Any]) -> list[str]:
    """Собрать argv дочернего процесса из значений аргументов.

    Опции идут первыми, позиционные — последними: так ``nargs="*"`` не съедает
    значения соседних опций.
    """
    actions = all_actions(spec)
    optionals: list[str] = []
    positionals: list[str] = []
    for dest, value in arguments.items():
        action = actions.get(dest)
        if action is None:
            raise UnsupportedCommand(
                f"У команды {spec.name!r} нет аргумента {dest!r}; проверьте fixed_arguments."
            )
        target = positionals if _is_positional(action) else optionals
        target.extend(_argv_for_action(action, value))
    return [*optionals, *positionals]


def describe_arguments(arguments: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    """Пары «аргумент — значение» для показа в журнале."""
    for dest, value in sorted(arguments.items()):
        if value not in (None, "", False):
            yield dest, value


def parse_or_none(spec: CommandSpec, argv: list[str]) -> argparse.Namespace | None:
    """Прогнать argv через настоящий парсер команды — проверка перед запуском."""
    parser = get_parser(spec)
    parser.exit_on_error = False  # type: ignore[attr-defined]
    try:
        return parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return None
