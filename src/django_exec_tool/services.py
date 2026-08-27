"""Допуск запусков и запросы на остановку.

Здесь живут ограничения, которые накладываются не на того, *кто* запускает, а на
сам факт запуска: рубильник, лимиты параллелизма, ключ блокировки. Проверка прав
остаётся в ``policy``, проверка аргументов — в ``forms``.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .conf import exec_tool_settings
from .forms import build_argv
from .models import CommandState, EnqueueLock, Run, RunStatus, StopMode
from .policy import can_run
from .registry import CommandSpec, registry

#: Ключ единственной строки-блокировки, сериализующей допуск запусков.
LOCK_KEY = "enqueue"


class LaunchRejected(Exception):
    """Запуск не допущен: рубильник, лимит параллелизма или занятый ключ."""


def is_enabled(spec: CommandSpec) -> bool:
    state = CommandState.objects.filter(name=spec.name).first()
    return state.enabled if state else True


def blocking_runs(spec: CommandSpec, lock_key: str | None) -> list[Run]:
    """Активные запуски, из-за которых новый нельзя допустить."""
    blockers: list[Run] = []
    same_command = list(Run.objects.active().filter(command=spec.name))
    if len(same_command) >= spec.max_parallel:
        blockers.extend(same_command)
    if lock_key:
        blockers.extend(Run.objects.active().filter(lock_key=lock_key).exclude(command=spec.name))
    return blockers


def check_admission(spec: CommandSpec, lock_key: str | None) -> None:
    if not is_enabled(spec):
        raise LaunchRejected(_("Команда сейчас выключена администратором."))
    total_active = Run.objects.active().count()
    if total_active >= exec_tool_settings.MAX_PARALLEL_RUNS:
        raise LaunchRejected(
            _("Сейчас слишком много активных запусков (%(count)d). Попробуйте позже.")
            % {"count": total_active}
        )
    if blocking_runs(spec, lock_key):
        raise LaunchRejected(_("Другой запуск этой команды ещё не завершён."))


def launch(
    user: Any,
    spec: CommandSpec,
    arguments: dict[str, Any],
    reason: str = "",
) -> Run:
    """Проверить всё, создать ``Run`` и отдать его раннеру."""
    if not can_run(user, spec, arguments):
        raise PermissionDenied(_("У вас нет прав на запуск этой команды."))

    argv = build_argv(spec, arguments)
    lock_key = spec.resolve_lock_key(arguments) or ""

    with transaction.atomic():
        # Критическая секция: без неё два одновременных запуска оба увидят
        # «слот свободен» и лимит окажется бесполезным.
        EnqueueLock.objects.get_or_create(key=LOCK_KEY)
        EnqueueLock.objects.select_for_update().filter(key=LOCK_KEY).first()
        check_admission(spec, lock_key)
        run = Run.objects.create(
            command=spec.name,
            arguments=arguments,
            argv=argv,
            lock_key=lock_key,
            timeout=spec.effective_timeout(),
            requested_by=user if getattr(user, "pk", None) else None,
            requested_by_repr=str(user),
            reason=reason,
        )

    runner = exec_tool_settings.RUNNER()
    # Раннера дёргаем после коммита: иначе он может взять строку, которой в БД
    # ещё нет.
    transaction.on_commit(lambda: runner.enqueue(run))
    return run


def request_stop(run: Run, user: Any, mode: str = StopMode.SOFT) -> Run:
    """Записать запрос на остановку. Сигнал пошлёт супервизор, увидев его.

    Ничего не рассылаем сами: супервизор может работать на другом хосте, и
    единственный канал, который надёжно достаёт до него отовсюду, — та же строка
    в БД, которую он и так перечитывает.
    """
    if not can_run(user, registry.get(run.command), run.arguments):
        raise PermissionDenied(_("У вас нет прав на остановку этого запуска."))
    if not run.is_active:
        raise LaunchRejected(_("Этот запуск уже завершён."))

    updates = {
        "stop_requested_at": run.stop_requested_at or timezone.now(),
        "stop_requested_by": user if getattr(user, "pk", None) else None,
        "stop_mode": mode,
    }
    Run.objects.filter(pk=run.pk).update(**updates)
    if run.status == RunStatus.PENDING:
        # Ещё не стартовал — можно отменить сразу, не дожидаясь супервизора.
        Run.objects.filter(pk=run.pk, status=RunStatus.PENDING).update(
            status=RunStatus.CANCELED, finished_at=timezone.now()
        )
    run.refresh_from_db()
    return run


def reap_lost_runs(now: Any | None = None) -> int:
    """Перевести запуски с протухшим heartbeat в статус ``unknown``.

    Именно ``unknown``, а не ``failed``: мы не знаем, доработала команда,
    упала или продолжает жить осиротевшей. Врать оператору инструмент аудита
    не имеет права.
    """
    now = now or timezone.now()
    lost = Run.objects.stale(now)
    return lost.update(
        status=RunStatus.UNKNOWN,
        finished_at=now,
        error=_("Супервизор перестал отчитываться; реальный результат неизвестен."),
    )
