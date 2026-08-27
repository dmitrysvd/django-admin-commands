"""Тесты супервизора. Работают с настоящими дочерними процессами.

Мокать здесь почти нечего: проверяется именно то, что процесс запускается,
печатает, получает сигналы и умирает. Подмена subprocess превратила бы тест в
проверку моков.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from django.core.files.storage import default_storage

from django_admin_commands.executor import Supervisor, build_child_env, execute
from django_admin_commands.models import Run, RunOutputChunk, RunStatus, StopMode
from django_admin_commands.registry import registry
from django_admin_commands.services import launch, request_stop

from .conftest import wait_finished, wait_for

pytestmark = pytest.mark.django_db(transaction=True)


def run_now(operator: Any, name: str, arguments: dict) -> Run:
    run = launch(operator, registry.get(name), arguments)
    return wait_finished(run)


def test_successful_run_captures_output(operator: Any) -> None:
    run = run_now(operator, "demo_slow", {"seconds": 1, "fail": False, "mode": "fast"})
    assert run.status == RunStatus.SUCCEEDED
    assert run.exit_code == 0
    assert "готово" in run.output_tail
    assert run.started_at and run.finished_at and run.heartbeat_at
    assert run.hostname and run.pid
    assert run.output_bytes > 0


def test_failing_run_is_marked_failed(operator: Any) -> None:
    run = run_now(operator, "demo_slow", {"seconds": 1, "fail": True, "mode": "fast"})
    assert run.status == RunStatus.FAILED
    assert run.exit_code == 3
    # stderr тоже попадает в лог: перехватывается пайп, а не self.stdout.
    assert "падаем по требованию" in run.output_tail


def test_output_is_archived_and_chunks_purged(operator: Any) -> None:
    run = run_now(operator, "demo_slow", {"seconds": 1, "fail": False, "mode": "fast"})
    assert run.log_path
    assert default_storage.exists(run.log_path)
    with default_storage.open(run.log_path) as handle:
        assert "готово" in handle.read().decode("utf-8")
    assert RunOutputChunk.objects.filter(run=run).count() == 0
    default_storage.delete(run.log_path)


def test_archiving_can_be_disabled(operator: Any, settings: Any) -> None:
    settings.ADMIN_COMMANDS = {**settings.ADMIN_COMMANDS, "ARCHIVE_OUTPUT": False}
    run = run_now(operator, "demo_slow", {"seconds": 1, "fail": False, "mode": "fast"})
    assert run.log_path == ""
    assert RunOutputChunk.objects.filter(run=run).exists()


def test_chunks_survive_when_purging_disabled(operator: Any, settings: Any) -> None:
    settings.ADMIN_COMMANDS = {**settings.ADMIN_COMMANDS, "PURGE_CHUNKS_AFTER_ARCHIVE": False}
    run = run_now(operator, "demo_slow", {"seconds": 1, "fail": False, "mode": "fast"})
    assert RunOutputChunk.objects.filter(run=run).exists()
    default_storage.delete(run.log_path)


def test_hard_stop_kills_the_process(operator: Any) -> None:
    run = launch(
        operator, registry.get("demo_slow"), {"seconds": 60, "fail": False, "mode": "fast"}
    )
    assert wait_for(lambda: bool(Run.objects.get(pk=run.pk).pid))
    request_stop(Run.objects.get(pk=run.pk), operator, StopMode.HARD)
    run = wait_finished(run)
    assert run.status == RunStatus.STOPPED
    assert run.exit_code == -9


def test_soft_stop_escalates_to_sigkill(operator: Any, settings: Any, override_spec: Any) -> None:
    settings.ADMIN_COMMANDS = {
        **settings.ADMIN_COMMANDS,
        "TERMINATE_GRACE": 2,
        "HEARTBEAT_INTERVAL": 1,
    }
    override_spec("demo_stubborn", timeout=120, interruptible=False)
    run = launch(operator, registry.get("demo_stubborn"), {"seconds": 120})
    assert wait_for(lambda: bool(Run.objects.get(pk=run.pk).pid))
    request_stop(Run.objects.get(pk=run.pk), operator, StopMode.SOFT)
    run = wait_finished(run, timeout=40)
    # Команда проигнорировала SIGTERM — мягкая остановка обязана дойти до SIGKILL.
    assert run.status == RunStatus.STOPPED
    assert run.exit_code == -9
    assert "SIGTERM проигнорирован" in run.output_tail


def test_timeout_stops_the_run(operator: Any, settings: Any, override_spec: Any) -> None:
    settings.ADMIN_COMMANDS = {
        **settings.ADMIN_COMMANDS,
        "HEARTBEAT_INTERVAL": 1,
        "TERMINATE_GRACE": 2,
    }
    override_spec("demo_slow", timeout=2, interruptible=True)
    run = launch(
        operator, registry.get("demo_slow"), {"seconds": 60, "fail": False, "mode": "fast"}
    )
    run = wait_finished(run, timeout=40)
    assert run.status == RunStatus.TIMED_OUT


def test_stop_before_start_cancels_the_run(operator: Any) -> None:
    run = Run.objects.create(command="demo_slow", status=RunStatus.PENDING, argv=[])
    Run.objects.filter(pk=run.pk).update(stop_requested_at="2020-01-01T00:00:00Z")
    assert execute(run.pk) == RunStatus.CANCELED


def test_already_finished_run_is_not_restarted() -> None:
    run = Run.objects.create(command="demo_slow", status=RunStatus.SUCCEEDED, argv=[])
    # Повторная доставка задачи не должна запускать команду второй раз.
    assert execute(run.pk) == RunStatus.SUCCEEDED


def test_supervisor_failure_is_recorded(monkeypatch: Any) -> None:
    run = Run.objects.create(command="demo_slow", status=RunStatus.PENDING, argv=[])

    def boom(self: Supervisor) -> None:
        raise RuntimeError("не смогли запустить")

    monkeypatch.setattr(Supervisor, "start_process", boom)
    with pytest.raises(RuntimeError):
        execute(run.pk)
    run.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert "не смогли запустить" in run.error


def test_child_env_carries_settings_and_run_id(settings: Any) -> None:
    settings.ADMIN_COMMANDS = {**settings.ADMIN_COMMANDS, "CHILD_ENV": {"DEMO_FLAG": "1"}}
    run = Run.objects.create(command="demo_slow", argv=[])
    env = build_child_env(run, registry.get("demo_slow"))
    assert env["DJANGO_SETTINGS_MODULE"] == os.environ.get(
        "DJANGO_SETTINGS_MODULE", "demo.settings"
    )
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["ADMIN_COMMANDS_RUN_ID"] == str(run.pk)
    assert env["DEMO_FLAG"] == "1"


def test_signal_group_falls_back_to_single_process(monkeypatch: Any) -> None:
    from django_admin_commands import executor

    calls: list = []
    monkeypatch.setattr(executor.os, "killpg", lambda *a: (_ for _ in ()).throw(ProcessLookupError))
    monkeypatch.setattr(executor.os, "kill", lambda *a: calls.append(a))
    executor._signal_group(12345, 15)
    assert calls == [(12345, 15)]

    # Процесса уже нет — падать нельзя.
    monkeypatch.setattr(executor.os, "kill", lambda *a: (_ for _ in ()).throw(OSError))
    executor._signal_group(12345, 15)
