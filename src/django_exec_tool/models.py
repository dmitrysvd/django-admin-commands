"""Слой хранения.

``Run`` — единственный источник правды о выполнении. Интерфейс никогда не
спрашивает у воркера «что ты сейчас делаешь»: такой ответ был бы медленным,
ненадёжным и вовсе недоступным после смерти воркера. Вместо этого строка
создаётся *до* старта команды, обновляется супервизором по ходу выполнения и
живёт дольше самой команды.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .conf import exec_tool_settings


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Ожидает")
    RUNNING = "running", _("Выполняется")
    SUCCEEDED = "succeeded", _("Успешно")
    FAILED = "failed", _("Ошибка")
    CANCELED = "canceled", _("Отменён до старта")
    STOPPED = "stopped", _("Остановлен оператором")
    TIMED_OUT = "timed_out", _("Превышен таймаут")
    #: Супервизор исчез. Мы честно не знаем, доработала команда, упала или
    #: продолжает жить осиротевшей, — и инструмент аудита не должен делать вид,
    #: что знает.
    UNKNOWN = "unknown", _("Потерян — результат неизвестен")

    @classmethod
    def active(cls) -> list[str]:
        return [cls.PENDING.value, cls.RUNNING.value]

    @classmethod
    def final(cls) -> list[str]:
        return [status for status in cls.values if status not in cls.active()]


class StopMode(models.TextChoices):
    SOFT = "soft", _("Мягкая (SIGTERM, через паузу SIGKILL)")
    HARD = "hard", _("Жёсткая (SIGKILL сразу)")


class CommandState(models.Model):
    """Рубильник в БД для команды из белого списка.

    Эта таблица умеет только *отнимать* возможности. Сам белый список остаётся в
    коде: будь он редактируемым отсюда, любой, у кого есть доступ к инструменту,
    выдал бы себе запуск произвольных команд.
    """

    name = models.CharField(_("команда"), max_length=255, unique=True)
    enabled = models.BooleanField(_("включена"), default=True)
    disabled_reason = models.TextField(_("причина выключения"), blank=True)
    updated_at = models.DateTimeField(_("изменено"), auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("кем изменено"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = _("состояние команды")
        verbose_name_plural = _("состояния команд")
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({'enabled' if self.enabled else 'disabled'})"


class EnqueueLock(models.Model):
    """Строка для ``select_for_update``, сериализующая допуск новых запусков.

    Лимиты параллелизма имеют смысл, только если два одновременных запуска не
    могут оба увидеть «слот свободен»; захват этой блокировки делает допуск
    критической секцией. Запуски редки, так что сериализация ничего не стоит.
    """

    key = models.CharField(max_length=32, primary_key=True)

    class Meta:
        verbose_name = _("блокировка допуска")
        verbose_name_plural = _("блокировки допуска")

    def __str__(self) -> str:
        return self.key


class RunQuerySet(models.QuerySet):
    def active(self) -> RunQuerySet:
        return self.filter(status__in=RunStatus.active())

    def stale(self, now: datetime | None = None) -> RunQuerySet:
        """Запуски, супервизор которых перестал отчитываться."""
        now = now or timezone.now()
        deadline = now - timedelta(
            seconds=exec_tool_settings.HEARTBEAT_INTERVAL * exec_tool_settings.HEARTBEAT_MISS_FACTOR
        )
        return self.filter(status=RunStatus.RUNNING, heartbeat_at__lt=deadline)


class RunManager(models.Manager["Run"]):
    """Менеджер объявлен явно, а не через ``as_manager()``.

    ``as_manager`` строит класс на лету, и статический анализ о методах
    квersetа ничего не знает. Пара проброшенных методов дешевле молчаливой
    дыры в проверке типов.
    """

    def get_queryset(self) -> RunQuerySet:
        return RunQuerySet(self.model, using=self._db)

    def active(self) -> RunQuerySet:
        return self.get_queryset().active()

    def stale(self, now: datetime | None = None) -> RunQuerySet:
        return self.get_queryset().stale(now)


class Run(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    command = models.CharField(_("команда"), max_length=255, db_index=True)
    arguments = models.JSONField(_("аргументы"), default=dict, blank=True)
    argv = models.JSONField(_("argv"), default=list, blank=True)
    lock_key = models.CharField(_("ключ блокировки"), max_length=255, blank=True, db_index=True)
    timeout = models.PositiveIntegerField(_("таймаут, с"), default=0)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("кто запустил"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exec_tool_runs",
    )
    #: Снимок инициатора; переживает удаление аккаунта — журнал аудита обязан.
    requested_by_repr = models.CharField(_("кто запустил (снимок)"), max_length=255, blank=True)
    reason = models.TextField(_("причина запуска"), blank=True)

    status = models.CharField(
        _("статус"),
        max_length=16,
        choices=RunStatus.choices,
        default=RunStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(_("создан"), default=timezone.now, db_index=True)
    started_at = models.DateTimeField(_("начат"), null=True, blank=True)
    finished_at = models.DateTimeField(_("завершён"), null=True, blank=True)
    heartbeat_at = models.DateTimeField(_("последний heartbeat"), null=True, blank=True)

    hostname = models.CharField(_("хост"), max_length=255, blank=True)
    pid = models.PositiveIntegerField(_("PID"), null=True, blank=True)
    runner_ref = models.CharField(_("ссылка раннера"), max_length=255, blank=True)
    exit_code = models.IntegerField(_("код возврата"), null=True, blank=True)
    error = models.TextField(_("ошибка супервизора"), blank=True)

    stop_requested_at = models.DateTimeField(_("остановка запрошена"), null=True, blank=True)
    stop_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("кем запрошена остановка"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    stop_mode = models.CharField(
        _("режим остановки"), max_length=8, choices=StopMode.choices, blank=True
    )

    output_tail = models.TextField(_("хвост вывода"), blank=True)
    output_bytes = models.BigIntegerField(_("объём вывода"), default=0)
    log_path = models.CharField(_("архив лога"), max_length=500, blank=True)

    objects = RunManager()

    class Meta:
        verbose_name = _("запуск")
        verbose_name_plural = _("запуски")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["command", "status"])]
        permissions = [("run_command", _("Может запускать команды из белого списка"))]

    def __str__(self) -> str:
        return f"{self.command} @ {self.created_at:%Y-%m-%d %H:%M:%S} [{self.status}]"

    # -- производное состояние -------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.status in RunStatus.active()

    @property
    def stop_requested(self) -> bool:
        return self.stop_requested_at is not None

    @property
    def status_display(self) -> str:
        """Читаемый статус без ``get_status_display``, которого не видит анализатор."""
        return str(RunStatus(self.status).label)

    @property
    def duration(self) -> timedelta | None:
        if self.started_at is None:
            return None
        return (self.finished_at or timezone.now()) - self.started_at

    @property
    def command_line(self) -> str:
        return " ".join(["manage.py", self.command, *self.argv])

    def spec(self) -> Any:
        from .registry import registry

        return registry.get(self.command)

    def spec_or_none(self) -> Any | None:
        """Команду могли убрать из белого списка — журнал должен пережить это."""
        from .registry import CommandNotRegistered, registry

        try:
            return registry.get(self.command)
        except CommandNotRegistered:
            return None


class RunOutputChunk(models.Model):
    """Живой вывод, дописываемый по мере печати команды.

    Чанки лежат в БД, а не на локальном диске воркера, ровно по той же причине,
    по которой там бесполезен PID: страницу админки обслуживает другой процесс,
    обычно на другом хосте. По завершении запуска полный лог уезжает в
    ``default_storage``, а чанки можно вычистить.
    """

    if TYPE_CHECKING:
        # Атрибут ``<fk>_id`` Django создаёт динамически; объявляем явно, чтобы
        # он не был дырой в проверке типов.
        run_id: uuid.UUID

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="chunks")
    seq = models.PositiveIntegerField(_("порядковый номер"))
    text = models.TextField(_("текст"))
    created_at = models.DateTimeField(_("создан"), default=timezone.now)

    class Meta:
        verbose_name = _("чанк вывода")
        verbose_name_plural = _("чанки вывода")
        ordering = ("run", "seq")
        unique_together = [("run", "seq")]

    def __str__(self) -> str:
        return f"{self.run_id}#{self.seq}"
