"""Супервизор одного запуска.

Команда исполняется не внутри воркера, а как настоящий дочерний процесс. Это
даёт то, чего нельзя получить вызовом ``call_command()`` в процессе воркера:

* у выполнения есть PID и своя process group — значит, есть кому послать
  SIGTERM, а потом SIGKILL, не задев ни воркер, ни чужие задачи;
* stdout/stderr — обычные пайпы, поэтому ловится *весь* вывод, включая
  traceback и печать сторонних библиотек, а не только ``self.stdout``;
* обёртка жива всё время выполнения и гарантированно доводит строку ``Run`` до
  терминального статуса с настоящим кодом возврата;
* OOM или утечка в команде убивают команду, а не воркер.

Канал управления — та же строка в БД: супервизор периодически перечитывает
``stop_requested_at``. Отдельный механизм рассылки сигналов (revoke, control
broadcast) не нужен, и работает это одинаково на любом числе хостов.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections, transaction
from django.utils import timezone

from .conf import app_settings
from .models import Run, RunOutputChunk, RunStatus, StopMode
from .registry import CommandSpec

#: Как часто супервизор просыпается: сливает вывод и проверяет управление.
POLL_INTERVAL = 0.25

#: prctl(PR_SET_PDEATHSIG) — «убей меня, когда умрёт родитель».
PR_SET_PDEATHSIG = 1


def build_child_argv(run: Run) -> list[str]:
    """Команда дочернего процесса.

    ``python -m django`` вместо пути к manage.py: путь к manage.py на проде
    угадать нельзя, а модуль есть всегда.
    """
    return [sys.executable, "-m", "django", run.command, *run.argv]


def build_child_env(run: Run, spec: CommandSpec) -> dict[str, str]:
    env = dict(os.environ)
    if settings.SETTINGS_MODULE:
        env.setdefault("DJANGO_SETTINGS_MODULE", settings.SETTINGS_MODULE)
    # Без этого вывод придёт большими блоками в конце, а не по ходу дела.
    env["PYTHONUNBUFFERED"] = "1"
    env["ADMIN_COMMANDS_RUN_ID"] = str(run.pk)
    env.update({str(k): str(v) for k, v in app_settings.CHILD_ENV.items()})
    return env


def _child_setup(spec: CommandSpec) -> None:  # pragma: no cover - выполняется в потомке
    """Выполняется в дочернем процессе между fork и exec."""
    os.setsid()
    if app_settings.PARENT_DEATH_SIGNAL and sys.platform.startswith("linux"):
        with contextlib.suppress(OSError):
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if spec.nice:
        os.nice(spec.nice)


def _signal_group(pid: int, sig: int) -> None:
    """Послать сигнал всей группе процессов команды.

    Группе, а не процессу: команда могла сама наплодить потомков, и оставить их
    жить — ровно та ситуация «остановить не получается», от которой всё это.
    """
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(OSError):
            os.kill(pid, sig)


class OutputCollector:
    """Читает пайп потомка в отдельном потоке и складывает вывод чанками в БД."""

    def __init__(self, run: Run) -> None:
        self.run = run
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._total = 0
        self._tail = ""
        self._tail_limit = app_settings.TAIL_BYTES

    def reader(self, stream: Any) -> None:
        for line in iter(stream.readline, b""):
            text = line.decode("utf-8", errors="replace")
            with self._lock:
                self._buffer.append(text)
        stream.close()

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            text = "".join(self._buffer)
            self._buffer.clear()
        self._seq += 1
        self._total += len(text)
        self._tail = (self._tail + text)[-self._tail_limit :]
        RunOutputChunk.objects.create(run=self.run, seq=self._seq, text=text)

    @property
    def total_bytes(self) -> int:
        return self._total

    @property
    def tail(self) -> str:
        return self._tail

    def archive(self) -> str:
        """Сложить полный лог в default_storage и вернуть путь."""
        if not app_settings.ARCHIVE_OUTPUT or not self._total:
            return ""
        chunks = RunOutputChunk.objects.filter(run=self.run).order_by("seq")
        content = "".join(chunk.text for chunk in chunks.iterator())
        path = f"{app_settings.ARCHIVE_DIR.rstrip('/')}/{self.run.pk}.log"
        saved = default_storage.save(path, ContentFile(content.encode("utf-8")))
        if app_settings.PURGE_CHUNKS_AFTER_ARCHIVE:
            RunOutputChunk.objects.filter(run=self.run).delete()
        return saved


class Supervisor:
    """Запускает команду и ведёт её строку ``Run`` до терминального статуса."""

    def __init__(self, run: Run) -> None:
        self.run = run
        self.spec: CommandSpec = run.spec()
        self.collector = OutputCollector(run)
        self.timed_out = False
        self.signalled_at: float | None = None
        self.escalated = False
        self.stop_mode = ""

    # -- жизненный цикл ---------------------------------------------------

    def start_process(self) -> subprocess.Popen:
        kwargs: dict[str, Any] = {}
        if os.name == "posix":
            kwargs["preexec_fn"] = lambda: _child_setup(self.spec)
        else:  # pragma: no cover - под Windows группа процессов делается иначе
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            build_child_argv(self.run),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=build_child_env(self.run, self.spec),
            close_fds=True,
            **kwargs,
        )

    def mark_running(self, proc: subprocess.Popen) -> None:
        now = timezone.now()
        self.run.status = RunStatus.RUNNING
        self.run.started_at = now
        self.run.heartbeat_at = now
        self.run.hostname = socket.gethostname()
        self.run.pid = proc.pid
        self.run.save(update_fields=["status", "started_at", "heartbeat_at", "hostname", "pid"])

    def heartbeat(self) -> None:
        now = timezone.now()
        Run.objects.filter(pk=self.run.pk).update(
            heartbeat_at=now,
            output_tail=self.collector.tail,
            output_bytes=self.collector.total_bytes,
        )
        self.run.heartbeat_at = now

    def check_control(self, proc: subprocess.Popen) -> None:
        """Перечитать запрос на остановку и при необходимости послать сигнал."""
        row = Run.objects.filter(pk=self.run.pk).values("stop_requested_at", "stop_mode").first()
        if row and row["stop_requested_at"] and self.signalled_at is None:
            self.stop_mode = row["stop_mode"] or StopMode.SOFT
            sig = signal.SIGKILL if self.stop_mode == StopMode.HARD else signal.SIGTERM
            _signal_group(proc.pid, sig)
            self.signalled_at = time.monotonic()

    def check_timeout(self, proc: subprocess.Popen, started: float) -> None:
        limit = self.run.timeout or self.spec.effective_timeout()
        if not self.timed_out and time.monotonic() - started > limit:
            self.timed_out = True
            _signal_group(proc.pid, signal.SIGTERM)
            self.signalled_at = time.monotonic()

    def check_escalation(self, proc: subprocess.Popen) -> None:
        """Мягкая остановка не бесконечна: не умерло за grace — SIGKILL."""
        if self.signalled_at is None or self.escalated:
            return
        if self.stop_mode == StopMode.HARD:
            return
        if time.monotonic() - self.signalled_at > app_settings.TERMINATE_GRACE:
            _signal_group(proc.pid, signal.SIGKILL)
            self.escalated = True

    def final_status(self, exit_code: int) -> str:
        if self.timed_out:
            return RunStatus.TIMED_OUT
        if self.signalled_at is not None:
            return RunStatus.STOPPED
        return RunStatus.SUCCEEDED if exit_code == 0 else RunStatus.FAILED

    def finalize(self, status: str, exit_code: int | None, error: str = "") -> None:
        self.collector.flush()
        log_path = ""
        try:
            log_path = self.collector.archive()
        except Exception:  # pragma: no cover - сбой архивации не должен терять статус
            error = (error + "\n" + traceback.format_exc()).strip()
        Run.objects.filter(pk=self.run.pk).update(
            status=status,
            exit_code=exit_code,
            finished_at=timezone.now(),
            heartbeat_at=timezone.now(),
            output_tail=self.collector.tail,
            output_bytes=self.collector.total_bytes,
            log_path=log_path,
            error=error,
        )

    def run_to_completion(self) -> str:
        proc = self.start_process()
        self.mark_running(proc)
        reader = threading.Thread(target=self.collector.reader, args=(proc.stdout,), daemon=True)
        reader.start()

        started = time.monotonic()
        last_beat = 0.0
        interval = app_settings.HEARTBEAT_INTERVAL
        try:
            while True:
                exit_code = proc.poll()
                self.collector.flush()
                now = time.monotonic()
                if now - last_beat >= interval:
                    last_beat = now
                    self.heartbeat()
                    self.check_control(proc)
                self.check_timeout(proc, started)
                self.check_escalation(proc)
                if exit_code is not None:
                    break
                time.sleep(POLL_INTERVAL)
        finally:
            reader.join(timeout=5)

        exit_code = proc.wait()
        status = self.final_status(exit_code)
        self.finalize(status, exit_code)
        return status


def execute(run_id: Any) -> str:
    """Точка входа раннера: довести запуск ``run_id`` до терминального статуса."""
    close_old_connections()
    try:
        with transaction.atomic():
            run = Run.objects.select_for_update().get(pk=run_id)
            if run.status != RunStatus.PENDING:
                # Повторная доставка задачи не должна запускать команду второй раз.
                return run.status
            if run.stop_requested_at is not None:
                run.status = RunStatus.CANCELED
                run.finished_at = timezone.now()
                run.save(update_fields=["status", "finished_at"])
                return run.status
            run.runner_ref = f"{socket.gethostname()}:{os.getpid()}"
            run.save(update_fields=["runner_ref"])

        supervisor = Supervisor(run)
        try:
            return supervisor.run_to_completion()
        except Exception:
            supervisor.finalize(RunStatus.FAILED, None, error=traceback.format_exc())
            raise
    finally:
        close_old_connections()
